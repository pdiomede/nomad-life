"""1.12.17 fixes from reviews of the dashboard's number cards, the Map, receipts, Settings,
Support, the Admin page and signing in, out and up."""
import io
import os
import re
import zipfile
from datetime import date, datetime
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase, ref
from tests.test_calendar_tabs import TabsCase, ev
from tests.test_admin_activity_export import ActivityMenuCase
from tests.test_support import SupportBase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


class DashboardCardsReviewTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026, "Lisbon", "Portugal")

    def test_bars_and_map_in_high_contrast(self):
        css = read("static", "css", "style.css")
        self.assertIn(".meter span { forced-color-adjust: none; background: Highlight; }", css)
        self.assertIn(".map-land { forced-color-adjust: none; background: GrayText; }", css)
        self.assertIn(".map-pin-base, .map-dot-base { background: Highlight; color: HighlightText;", css)

    def test_dotted_spellings_are_one_country(self):
        self.assertEqual(appmod.normalize_country("U.S.A."), "United States")
        self.assertEqual(appmod.fold("St. Lucia"), "st lucia")
        self.assertIn('.replace(/&/g, " and ").replace(/\\./g, "")', read("static", "js", "theme.js"))

    def test_today_is_the_users(self):
        # 23:30 UTC on 31 Dec 2026 is already 1 Jan 2027 in Tokyo.
        moment = datetime(2026, 12, 31, 23, 30, tzinfo=appmod.timezone.utc)

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

        with self.app.test_request_context(headers={"Cookie": "nl_tz=Asia/Tokyo"}):
            with mock.patch.object(appmod, "datetime", FakeDatetime):
                self.assertEqual(appmod.user_today(), date(2027, 1, 1))
        with self.app.test_request_context(headers={"Cookie": "nl_tz=../../etc/passwd"}):
            self.assertEqual(appmod.user_today(), date.today())  # refused: the server's date
        self.assertIsNone(appmod.zone_named("Not/AZone"))
        self.assertIn('document.cookie = "nl_tz=" + encodeURIComponent(tz)', read("static", "js", "theme-init.js"))

    def test_the_outside_line_counts_stays_away_from_the_base(self):
        self.add_movement(2026, "Lisbon", "Portugal", "2026-01-01", "2026-02-01")
        self.add_movement(2026, "Rome", "Italy", "2026-03-01", "2026-03-04")
        html = self.client.get("/year/2026").get_data(as_text=True)
        self.assertIn("1 stay outside Base country", html)

    def test_ties_are_alphabetical(self):
        for i, country in enumerate(("Thailand", "bali", "Åland Islands")):
            self.add_movement(2026, "", country, f"2026-0{i + 4}-01", f"2026-0{i + 4}-05")
        with self.db() as conn:
            year_row = conn.execute("SELECT * FROM years").fetchone()
            movements = conn.execute("SELECT * FROM movements").fetchall()
        with self.app.app_context():
            rows = appmod.compute_stats(year_row, movements)["rows"]
        self.assertEqual([r["country"] for r in rows if r["days"] == 5], ["Åland Islands", "bali", "Thailand"])

    def test_pins_follow_the_maps_width(self):
        js = read("static", "js", "theme.js")
        self.assertIn("new ResizeObserver(function () {", js)
        css = read("static", "css", "style.css")
        self.assertIn("font: 400 13px/1.35 Inter, system-ui, sans-serif; font-variant-numeric: normal;", css)


class ReceiptsReviewTests(AppTestCase):
    """Found in a review of receipt storage (1.12.17)."""

    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def doc(self):
        with self.db() as conn:
            return conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchone()

    def member(self, row):
        path = os.path.join(self.app.config["UPLOAD_DIR"], str(row["user_id"]), row["stored_name"])
        with zipfile.ZipFile(path) as zf:
            return zf.namelist()[0], zf.read(zf.namelist()[0])

    def test_1_a_big_photo_loses_its_location_even_when_not_smaller(self):
        from PIL import Image
        img = Image.effect_noise((2000, 1500), 40).convert("RGB")
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (45.0, 0.0, 0.0), 3: "E", 4: (9.0, 0.0, 0.0)}
        out = io.BytesIO()
        img.save(out, "JPEG", quality=70, exif=exif.tobytes())
        data = out.getvalue()
        self.assertGreater(len(data), 1024 * 1024)
        self.upload("/year/2026/base", "Dinner.jpg", data)
        _name, inner = self.member(self.doc())
        self.assertNotEqual(inner, data)
        with Image.open(io.BytesIO(inner)) as stored:
            self.assertFalse(stored.getexif().get_ifd(0x8825))

    def test_2_an_old_receipt_dated_before_1980_downloads(self):
        y = self.new_year(2025)
        stored = self.insert_document(y, "Old.pdf", b"%PDF-1.4\nold")
        os.utime(os.path.join(self.app.config["UPLOAD_DIR"], str(y["user_id"]), stored), (0, 0))
        with self.db() as conn:
            doc_id = conn.execute("SELECT id FROM documents WHERE stored_name = ?", (stored,)).fetchone()[0]
        resp = self.client.get(f"/documents/{doc_id}")
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            self.assertEqual(zf.read("Old.pdf"), b"%PDF-1.4\nold")

    def test_3_a_rename_never_goes_past_the_quota(self):
        self.upload("/year/2026/base", "a.pdf", b"%PDF-1.7\n" + os.urandom(3000))
        row = self.doc()
        with self.db() as conn:
            conn.execute("UPDATE users SET quota_bytes = ?", (row["size"],))
        long_name = "a" * 140 + ".pdf"
        self.client.post(f"/documents/{row['id']}/edit", data={"name": long_name, "kind": "other"})
        after = self.doc()
        self.assertEqual(after["original_name"], long_name)  # the rename is saved
        self.assertEqual(after["size"], row["size"])  # but the file keeps its size
        self.assertEqual(self.member(after)[0], "a.pdf")
        # With room left, the file inside is renamed too.
        with self.db() as conn:
            conn.execute("UPDATE users SET quota_bytes = NULL")
        self.client.post(f"/documents/{row['id']}/edit", data={"name": "b.pdf", "kind": "other"})
        self.assertEqual(self.member(self.doc())[0], "b.pdf")

    def test_4_over_the_quota_the_used_size_rounds_up(self):
        self.assertEqual(appmod.used_size(500 * 1024 * 1024 + 50 * 1024, 500 * 1024 * 1024), "500.1 MB")
        self.assertEqual(appmod.used_size(1536, 10 * 1024 * 1024), appmod.format_size(1536, "down"))
        for name in ("dashboard.html", "settings.html", "plan.html", "admin.html", "admin_account.html"):
            html = read("templates", name)
            self.assertNotIn("used | filesize('down')", html, name)
            self.assertIn(".used | usedsize(", html, name)

    def test_5_a_name_without_a_visible_stem(self):
        self.upload("/year/2026/base", ".pdf", b"%PDF-1.7\nx")
        self.assertEqual(self.doc()["original_name"], "receipt.pdf")
        self.upload("/year/2026/base", "ㅤ.pdf", b"%PDF-1.7\ny")
        self.assertEqual(self.doc()["original_name"], "receipt.pdf")

    def test_6_windows_device_names(self):
        self.assertEqual(appmod.zip_member_name("CON.pdf", "pdf"), "_CON.pdf")
        self.assertEqual(appmod.zip_member_name("nul.tar.jpg", "jpg"), "_nul.tar.jpg")
        self.assertEqual(appmod.zip_member_name("Console.pdf", "pdf"), "Console.pdf")
        self.upload("/year/2026/base", "CON.pdf", b"%PDF-1.7\nx")
        row = self.doc()
        self.assertEqual(self.member(row)[0], "_CON.pdf")
        self.assertIn("_CON.zip", self.client.get(f"/documents/{row['id']}").headers["Content-Disposition"])


class GoogleTooltipFlagTests(TabsCase):
    def test_flags_in_the_google_tooltips(self):
        self.add_movement(2026, "Bangkok", "Thailand", "2026-01-18", "2026-01-30")
        self.connect()
        self.choose()
        self.google.events = [ev("Italy", "2026-02-01", "2026-02-01"), ev("Bangkok", "2026-03-01", "2026-03-01"),
                              ev("Dentist", "2026-05-01", "2026-05-01")]
        html = self.client.get("/year/2026/calendar/google").get_data(as_text=True)
        self.assertIn('data-tip="Sun 1 Feb 2026 \U0001F1EE\U0001F1F9 Italy"', html)
        self.assertIn('data-tip="Sun 1 Mar 2026 \U0001F1F9\U0001F1ED Bangkok"', html)  # a city of the stays
        self.assertIn('data-tip="Fri 1 May 2026 Dentist"', html)
        self.assertIn('data-tip="Mon 2 Feb 2026 no event"', html)


class SettingsReviewTests(AppTestCase):
    """Found in a review of the Settings page (1.12.17)."""

    def setUp(self):
        super().setUp()
        self.signup()
        with self.db() as conn:
            self.uid = conn.execute("SELECT id FROM users").fetchone()[0]

    def settings(self):
        return self.client.get("/settings").get_data(as_text=True)

    def add_passkey(self, name="Work laptop"):
        with self.db() as conn:
            conn.execute("INSERT INTO passkeys (user_id, credential_id, public_key, name) VALUES (?, ?, ?, ?)",
                         (self.uid, os.urandom(8).hex(), b"k", name))

    def test_1_an_admins_address_is_not_shown_to_the_user(self):
        with self.db() as conn:
            conn.execute("INSERT INTO audit_log (user_id, email, actor, event, ip) VALUES "
                         "(?, 'a@example.com', 'admin@example.com', 'admin_plan', '198.51.100.7')", (self.uid,))
            conn.execute("INSERT INTO audit_log (user_id, email, event, ip) VALUES "
                         "(?, 'a@example.com', 'login', '203.0.113.9')", (self.uid,))
        html = self.settings()
        self.assertNotIn("198.51.100.7", html)
        self.assertIn("203.0.113.9", html)

    def test_2_forms_that_end_sessions_submit_once(self):
        html = self.settings()
        for action in ("profile", "password", "email", "sessions", "2fa_enable"):
            i = html.index(f'name="action" value="{action}"')
            form = html[html.rindex("<form", 0, i):i]
            self.assertIn("data-submit-once", form, action)

    def test_3_a_settings_view_does_not_write_once_the_key_exists(self):
        self.settings()  # the first view makes this browser's setup key
        with mock.patch.object(appmod.db, "execute", wraps=appmod.db.execute) as execute:
            self.settings()
        self.assertFalse([c for c in execute.call_args_list if "totp_setup" in c.args[0]])

    def test_5_a_long_address_wraps(self):
        self.assertIn(".card-intro { overflow-wrap: anywhere; }", read("static", "css", "style.css"))

    def test_6_a_password_change_says_the_email_change_ended(self):
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "new@example.com",
                                            "current_password": "password1"})
        html = self.client.post("/settings", data={
            "action": "password", "current_password": "password1", "password": "another-pass-77",
            "confirm": "another-pass-77"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Your change of email to new@example.com was cancelled: ask for it again.", html)

    def test_7_removing_a_removed_app_names_the_passkeys(self):
        html = self.client.post("/settings", data={"action": "2fa_disable", "code": "123456",
                                                   "current_password": "password1"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("The authenticator app was already removed. Two-factor sign in is off.", html)
        self.add_passkey()
        html = self.client.post("/settings", data={"action": "2fa_disable", "code": "123456",
                                                   "current_password": "password1"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Your passkeys still ask for a second step.", html)

    def test_8_a_huge_passkey_id_is_no_passkey(self):
        self.add_passkey()
        for action in ("passkey_rename", "passkey_remove"):
            resp = self.client.post("/settings", data={"action": action, "passkey_id": "9" * 25, "name": "x",
                                                       "current_password": "password1", "confirm_delete": "2"})
            self.assertEqual(resp.status_code, 302, action)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT name FROM passkeys").fetchone()[0], "Work laptop")

    def test_9_each_passkeys_buttons_name_it(self):
        self.add_passkey("Work laptop")
        html = self.settings()
        self.assertIn('aria-label="Rename the passkey Work laptop">Rename</summary>', html)
        self.assertIn('aria-label="Remove the passkey Work laptop">Remove</summary>', html)


class SupportReviewTests(SupportBase):
    """Found in a review of Support and the admin's Support tickets (1.12.17)."""

    def count(self, sql):
        with self.db() as conn:
            return conn.execute(sql).fetchone()[0]

    def test_a1_a_double_submit_at_the_limit_is_still_sent(self):
        for i in range(4):
            self.assertEqual(self.open_ticket(subject=f"T{i}").status_code, 302)
        self.assertEqual(self.open_ticket(subject="T4").status_code, 302)
        resp = self.open_ticket(subject="T4")  # the same form again: not "too many"
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.count("SELECT COUNT(*) FROM tickets"), 5)
        self.assertEqual(self.count("SELECT COUNT(*) FROM auth_events WHERE kind = 'ticket'"), 5)

    def test_a1_a_reply_sent_twice_counts_once(self):
        self.open_ticket()
        with mock.patch.object(appmod, "take_attempt", wraps=appmod.take_attempt) as take:
            for _ in range(2):
                resp = self.client.post(f"/support/{ref(1)}", data={"action": "reply", "body": "Still broken"})
                self.assertEqual(resp.status_code, 302)
        self.assertEqual(take.call_count, 1)
        self.assertEqual(len(self.messages()), 2)

    def test_a2_emoji_newer_than_the_server_are_kept(self):
        new = "\U0003FFFD"  # unknown to this Python, as the newest emoji are on a server
        self.assertEqual(self.open_ticket(subject=new, body=f"so tired {new} now").status_code, 302)
        self.assertEqual(self.ticket()["subject"], new)
        self.assertEqual(self.messages()[0]["body"], f"so tired {new} now")
        self.assertEqual(appmod.clean_name("Ada ‮￿"), "Ada")  # format and noncharacters go

    def test_a3_a_refused_reply_says_when_to_retry(self):
        self.open_ticket()
        with mock.patch.object(appmod, "take_attempt", return_value=(120, "account", None)):
            resp = self.client.post(f"/support/{ref(1)}", data={"action": "reply", "body": "Hello"})
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Retry-After", resp.headers)

    def test_b1_owner_deleted_while_the_answer_is_refused(self):
        self.open_ticket()
        real_flash = appmod.flash

        def close_first(value):  # the user closes the ticket while the admin writes
            with self.db() as conn:
                conn.execute("UPDATE tickets SET status = 'closed'")
            return original(value)

        def flash_then_delete(message, category="message"):
            real_flash(message, category)
            if message.startswith("This ticket was closed meanwhile"):
                with self.db() as conn:
                    conn.execute("PRAGMA foreign_keys = ON")
                    conn.execute("DELETE FROM users WHERE email = 'a@example.com'")

        original = appmod.clean_ticket_body
        with mock.patch.object(appmod, "clean_ticket_body", side_effect=close_first), \
                mock.patch.object(appmod, "flash", side_effect=flash_then_delete):
            resp = self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reply", "body": "Fixed"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("no longer exists", self.admin.get(resp.headers["Location"]).get_data(as_text=True))

    def test_b2_close_waits_for_a_message_written_meanwhile(self):
        self.open_ticket()
        html = self.admin.get(f"/admin/support/{ref(1)}").get_data(as_text=True)
        seen = re.search(r'name="seen" value="(\d+)"', html).group(1)
        self.client.post(f"/support/{ref(1)}", data={"action": "reply", "body": "Still broken"})
        html = self.admin.post(f"/admin/support/{ref(1)}", data={"action": "close", "seen": seen},
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn("The user wrote a new message meanwhile", html)
        self.assertEqual(self.ticket()["status"], "open")
        last = re.search(r'name="seen" value="(\d+)"', html).group(1)
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "close", "seen": last})
        self.assertEqual(self.ticket()["status"], "closed")


class AdminPurgeReviewTests(ActivityMenuCase):
    """Found in a review of the Admin page (1.12.17): the purge."""

    def test_1_the_purge_is_recorded(self):
        self.purge("all")
        with self.db() as conn:
            rows = [tuple(r) for r in conn.execute("SELECT event, actor, detail FROM audit_log")]
        self.assertEqual(rows, [("admin_purge", "admin@example.com", "4 events, all")])
        self.assertEqual(appmod.AUDIT_LABELS["admin_purge"], "Security activity purged by an admin")

    def test_3_read_in_chunks_into_a_file(self):
        with mock.patch.object(appmod, "AUDIT_EXPORT_CHUNK", 1):
            rows = self.parse(self.purge("all"))
        self.assertEqual(len(rows) - 1, 4)
        self.assertIn("|| Date.now() - started > 135000) {", read("static", "js", "theme.js"))


class AdminReviewTests(SupportBase):
    """Found in a review of the Admin page (1.12.17)."""

    def test_2_disabling_an_account_that_pays_warns(self):
        with mock.patch.object(appmod, "charging_subscription", return_value=True), \
                mock.patch.object(appmod, "open_subscription", return_value=True):
            html = self.admin.post("/admin/users/1", data={"action": "disable"},
                                   follow_redirects=True).get_data(as_text=True)
        self.assertIn("It still pays for a plan with Stripe and can no longer reach Manage billing", html)
        self.assertIn("It still pays for a plan with Stripe. Cancel the subscription in Stripe first", html)

    def test_4_the_stripe_note_has_a_line_of_its_own(self):
        css = read("static", "css", "style.css")
        self.assertIn(".admin-quota { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }", css)
        self.assertIn(".admin-plan-note { flex-basis: 100%; margin: 0; }", css)

    def test_5_names_sort_a_to_z_first(self):
        html = self.admin.get("/admin").get_data(as_text=True)
        self.assertIn('href="/admin?sort=email&amp;dir=asc#accounts"', html)
        self.assertIn('asort=event&amp;adir=asc#activity"', html)
        self.assertIn('asort=account&amp;adir=asc#activity"', html)


class AuthReviewTests(SupportBase):
    """Found in a review of signing in, out and up (1.12.17)."""

    def test_in_1_errors_are_announced(self):
        fresh = self.app.test_client()
        html = fresh.post("/login", data={"email": "a@example.com", "password": "wrong"}).get_data(as_text=True)
        self.assertIn('<div class="flash flash-error" role="alert">', html)
        self.assertIn('value="a@example.com" required>', html)  # no autofocus over the error
        self.assertIn('required autofocus>', fresh.get("/login").get_data(as_text=True))
        self.assertIn('class="flash flash-info" role="status"',
                      fresh.get("/settings", follow_redirects=True).get_data(as_text=True))

    def test_in_2_a_planted_form_token_ends_at_sign_in(self):
        fresh = self.app.test_client()
        with fresh.session_transaction() as sess:
            sess["csrf_token"] = "planted"
        fresh.post("/login", data={"email": "a@example.com", "password": "password1"})
        with fresh.session_transaction() as sess:
            self.assertNotEqual(sess.get("csrf_token"), "planted")
            self.assertIn("_user_id", sess)

    def test_in_3_sign_in_wording(self):
        html = self.app.test_client().get("/settings", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Please sign in to see this page.", html)
        self.assertNotIn("Please log in", html)

    def test_out_1_the_device_cookie_keeps_no_address(self):
        cookie = self.client.get_cookie("nomadlife_device").value
        self.assertNotIn("a@example.com", cookie)
        with self.app.test_request_context():
            data = appmod.serializer("device").loads(cookie)
            self.assertEqual(data["e"], appmod.device_email_tag("a@example.com"))
        with self.app.test_request_context(headers={"Cookie": f"nomadlife_device={cookie}"}):
            self.assertEqual(appmod.device_for("a@example.com"), data["d"])
            self.assertIsNone(appmod.device_for("b@example.com"))

    def test_out_2_another_site_cannot_sign_people_out(self):
        self.app.config["WTF_CSRF_ENABLED"] = True
        resp = self.client.post("/logout", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(resp.headers["Location"], "/app")
        self.assertNotIn("nomadlife_session", resp.headers.get("Set-Cookie", ""))
        self.assertEqual(self.client.get("/settings").status_code, 200)  # still signed in
        resp = self.client.post("/logout", headers={"Origin": "https://evil.example"})
        self.assertEqual(self.client.get("/settings").status_code, 200)
        # A stale page of ours still signs out.
        resp = self.client.post("/logout", data={"csrf_token": "stale"},
                                headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(resp.headers["Location"], "/login")
        self.assertEqual(self.client.get("/settings").status_code, 302)

    def test_up_1_a_blocked_sign_up_works_again_once_enabled(self):
        fresh = self.app.test_client()
        fresh.post("/signup", data={"email": "p@example.com", "password": "password1", "confirm": "password1"})
        link = self.last_link("p@example.com")
        with self.db() as conn:
            uid = conn.execute("SELECT id FROM users WHERE email = 'p@example.com'").fetchone()[0]
        self.admin.post(f"/admin/users/{uid}", data={"action": "disable"})
        self.admin.post(f"/admin/users/{uid}", data={"action": "enable"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT session_version FROM users WHERE id = ?", (uid,)).fetchone()[0], 0)
        html = fresh.post("/login", data={"email": "p@example.com", "password": "password1"}).get_data(as_text=True)
        self.assertNotIn("to choose your password", html)
        self.assertIn("Please confirm your email first", html)
        fresh.post(link)  # the first link works again
        with self.db() as conn:
            self.assertIsNotNone(conn.execute("SELECT verified_at FROM users WHERE id = ?", (uid,)).fetchone()[0])

