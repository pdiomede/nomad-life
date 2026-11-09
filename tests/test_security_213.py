"""Security fixes of 2.1.3, from the audit of 2.1.2."""

import random
import time
from datetime import date, timedelta
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class SessionIdTests(AppTestCase):
    """A session cookie without the id of a user_sessions row is refused: it would never be
    looked up, so signing out could not end it, and the signing key plus a copy of the users
    table would forge one for any account."""

    def forged(self, with_sid):
        self.signup("a@example.com")
        with self.db() as conn:
            user = conn.execute("SELECT id, password_hash FROM users").fetchone()
            sid = conn.execute("SELECT id FROM user_sessions").fetchone()[0]
        value = f"{user['id']}:{appmod.password_fingerprint(user['password_hash'])}"
        if with_sid:
            value += f"/{sid}"
        browser = self.app.test_client()
        with browser.session_transaction() as sess:
            sess["_user_id"] = value
            sess["_fresh"] = True
        return browser

    def test_a_cookie_without_a_session_id_is_not_signed_in(self):
        resp = self.forged(with_sid=False).get("/settings")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_a_cookie_with_a_live_session_id_is_signed_in(self):
        self.assertEqual(self.forged(with_sid=True).get("/settings").status_code, 200)

    def test_signing_out_ends_the_cookie_with_its_id(self):
        browser = self.forged(with_sid=True)
        with self.db() as conn:
            conn.execute("DELETE FROM user_sessions")
        self.assertEqual(browser.get("/settings").status_code, 302)


class UndoAlertTests(AppTestCase):
    """The address an account moved away from by an undo hears about it."""

    def test_the_address_the_account_uses_now_is_alerted(self):
        self.signup("a@example.com")
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "b@example.com",
                                            "current_password": "password1"})
        self.client.post(self.last_link("b@example.com", kind="account/email"))
        undo = self.last_link("a@example.com", kind="account/email/undo")
        before = len(self.outbox)
        # Whoever reads the old mailbox presses the link.
        self.app.test_client().post(undo)
        sent = self.outbox[before:]
        alert = [m for m in sent if m["to"] == "b@example.com"]
        self.assertEqual(len(alert), 1, [m["to"] for m in sent])
        self.assertIn("undone from the previous address, a@example.com", alert[0]["text"])
        self.assertIn("support@nomadlife.pro", alert[0]["text"])
        # The old address still gets the way to choose a password.
        self.assertTrue([m for m in sent if m["to"] == "a@example.com"])

    def test_nobody_is_alerted_when_the_account_is_already_on_that_address(self):
        self.signup("a@example.com")
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "b@example.com",
                                            "current_password": "password1"})
        self.client.post(self.last_link("b@example.com", kind="account/email"))
        undo = self.last_link("a@example.com", kind="account/email/undo")
        self.app.test_client().post(undo)
        before = len(self.outbox)
        self.app.test_client().post(undo)  # pressed twice: nothing more is sent
        self.assertEqual(len(self.outbox), before)


class SyncWorkTests(AppTestCase):
    """Matching typed movements to events is bounded: a thousand stays against ten thousand
    events held the write lock for about a minute."""

    def rows(self, n_movements, n_events):
        rnd = random.Random(1)
        day = lambda d: (date(2026, 1, 1) + timedelta(days=d)).isoformat()
        movements = []
        for i in range(n_movements):
            a = rnd.randint(0, 300)
            movements.append({"id": i + 1, "google_event_id": None, "city": "", "country": "Italy",
                              "sync_missing": 0, "city_from_event": 0, "notes": "",
                              "start_date": day(a), "end_date": day(a + rnd.randint(0, 30))})
        events = []
        for i in range(n_events):
            a = rnd.randint(0, 300)
            events.append({"gid": f"g{i}", "title": "Italy", "city": "", "country": "Italy",
                           "start_date": day(a), "end_date": day(a + rnd.randint(0, 30))})
        return movements, events

    def test_a_huge_year_is_matched_in_bounded_time(self):
        movements, events = self.rows(1000, 10000)
        started = time.monotonic()
        changes = appmod.sync_changes(movements, {}, events, reviewing=True)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(changes), 1000 + 10000 - len([c for c in changes
                         if c["kind"] in ("link", "edit")]))

    def test_the_same_days_pass_still_matches_exactly(self):
        movements, events = self.rows(3, 0)
        events = [{"gid": "x", "title": "Italy", "city": "", "country": "Italy",
                   "start_date": movements[1]["start_date"],
                   "end_date": movements[1]["end_date"]}]
        changes = appmod.sync_changes(movements, {}, events, reviewing=True)
        linked = [c for c in changes if c["kind"] == "link"]
        self.assertEqual([c["movement"]["id"] for c in linked], [2])


class ReceiptCapTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup("a@example.com")
        self.new_year(2026)
        self.mid = self.add_movement(2026, "Rome", "Italy", "2026-03-01", "2026-03-10")

    def count(self):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def test_a_year_holds_at_most_so_many_receipts(self):
        with mock.patch.object(appmod, "RECEIPTS_YEAR_MAX", 2):
            self.upload(f"/movements/{self.mid}", "a.pdf", PDF)
            self.upload(f"/movements/{self.mid}", "b.pdf", PDF)
            page = self.upload(f"/movements/{self.mid}", "c.pdf", PDF)
            html = self.client.get(f"/movements/{self.mid}").get_data(as_text=True)
        self.assertEqual(self.count(), 2)
        self.assertIn("This year already holds 2 receipts", html + page.get_data(as_text=True))

    def test_an_account_holds_at_most_so_many_receipts(self):
        with mock.patch.object(appmod, "RECEIPTS_ACCOUNT_MAX", 1):
            self.upload(f"/movements/{self.mid}", "a.pdf", PDF)
            self.upload("/year/2026/base", "b.pdf", PDF)
            html = self.client.get("/year/2026/base").get_data(as_text=True)
        self.assertEqual(self.count(), 1)
        self.assertIn("Your account already holds 1 receipt,", html)

    def test_no_file_is_left_behind_by_a_refused_upload(self):
        import os
        with mock.patch.object(appmod, "RECEIPTS_YEAR_MAX", 1):
            self.upload(f"/movements/{self.mid}", "a.pdf", PDF)
            self.upload(f"/movements/{self.mid}", "b.pdf", PDF)
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        self.assertEqual(len(os.listdir(folder)), 1)

    def test_the_check_is_repeated_under_the_write_lock(self):
        # Two uploads that both passed the first look: the second is refused at the insert.
        real = appmod.receipts_full
        calls = []

        def only_the_locked_check_refuses(conn, year_id):
            calls.append(1)
            return None if len(calls) == 1 else "full"

        with mock.patch.object(appmod, "receipts_full", only_the_locked_check_refuses):
            self.upload(f"/movements/{self.mid}", "a.pdf", PDF)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.count(), 0)
        self.assertIs(appmod.receipts_full, real)


class EmailChangeConfirmTests(AppTestCase):
    """Whoever reads the new mailbox must also be the account's owner: signed in to it, or
    knowing its password (a mistyped address belongs to a stranger)."""

    def setUp(self):
        super().setUp()
        self.signup("a@example.com")
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "typo@example.com",
                                            "current_password": "password1"})
        self.link = self.last_link("typo@example.com", kind="account/email")
        self.stranger = self.app.test_client()

    def email(self):
        with self.db() as conn:
            return conn.execute("SELECT email FROM users").fetchone()[0]

    def test_the_page_asks_a_stranger_for_the_password(self):
        html = self.stranger.get(self.link).get_data(as_text=True)
        self.assertIn('name="current_password"', html)

    def test_the_signed_in_owner_is_not_asked(self):
        self.assertNotIn('name="current_password"',
                         self.client.get(self.link).get_data(as_text=True))
        self.client.post(self.link)
        self.assertEqual(self.email(), "typo@example.com")

    def test_a_stranger_without_the_password_changes_nothing(self):
        resp = self.stranger.post(self.link)
        self.assertIn("Your current password is not correct.", resp.get_data(as_text=True))
        self.assertEqual(self.email(), "a@example.com")
        resp = self.stranger.post(self.link, data={"current_password": "wrong-one"})
        self.assertIn("Your current password is not correct.", resp.get_data(as_text=True))
        self.assertEqual(self.email(), "a@example.com")

    def test_the_owner_on_another_device_confirms_with_the_password(self):
        self.stranger.post(self.link, data={"current_password": "password1"})
        self.assertEqual(self.email(), "typo@example.com")

    def test_wrong_passwords_count_as_failed_sign_ins(self):
        for _ in range(5):
            self.stranger.post(self.link, data={"current_password": "wrong-one"})
        html = self.stranger.post(self.link, data={"current_password": "password1"}
                                  ).get_data(as_text=True)
        self.assertIn("Too many wrong passwords", html)
        self.assertEqual(self.email(), "a@example.com")


class CaptchaLimitTests(AppTestCase):
    def test_made_up_answers_stop_reaching_hcaptcha(self):
        self.app.config.update(HCAPTCHA_SITEKEY="site", HCAPTCHA_SECRET="secret")
        with mock.patch.object(appmod, "hcaptcha_verify", return_value=False) as verify:
            for _ in range(appmod.LIMITS[("captcha", "ip")] + 10):
                self.app.test_client().post("/forgot", data={
                    "email": "a@example.com", "h-captcha-response": "made-up"})
        self.assertEqual(verify.call_count, appmod.LIMITS[("captcha", "ip")])

    def test_an_empty_answer_is_not_counted(self):
        self.app.config.update(HCAPTCHA_SITEKEY="site", HCAPTCHA_SECRET="secret")
        with mock.patch.object(appmod, "hcaptcha_verify", return_value=True) as verify:
            for _ in range(appmod.LIMITS[("captcha", "ip")] + 5):
                self.app.test_client().post("/forgot", data={"email": "a@example.com"})
            self.assertEqual(verify.call_count, 0)
            self.app.test_client().post("/forgot", data={
                "email": "a@example.com", "h-captcha-response": "real"})
            self.assertEqual(verify.call_count, 1)


class InviteAnswerTests(AppTestCase):
    """The AUTO sync leaves out invitations the owner declined or never answered."""

    def read(self, items):
        with self.app.app_context():
            with mock.patch.object(appmod, "google_api", return_value={"items": items}):
                return appmod.google_events("token", "cal", 2026)

    def item(self, gid, summary, guests=None):
        item = {"id": gid, "summary": summary, "status": "confirmed",
                "start": {"date": "2026-03-01"}, "end": {"date": "2026-03-10"}}
        if guests is not None:
            item["attendees"] = guests
        return item

    def test_the_request_asks_only_for_the_owners_own_answer(self):
        seen = []

        def fake(method, url, **kwargs):
            seen.append(url)
            return {"items": []}

        with self.app.app_context(), mock.patch.object(appmod, "google_api", fake):
            appmod.google_events("token", "cal", 2026)
        self.assertIn("attendees%28self%2CresponseStatus%29", seen[0])
        self.assertIn("maxAttendees=1", seen[0])

    def test_each_answer_is_read(self):
        got = self.read([
            self.item("own", "Italy"),
            self.item("yes", "Italy", [{"self": True, "responseStatus": "accepted"}]),
            self.item("maybe", "Italy", [{"self": True, "responseStatus": "tentative"}]),
            self.item("no", "Thailand", [{"self": True, "responseStatus": "declined"}]),
            self.item("new", "Thailand", [{"self": True, "responseStatus": "needsAction"}]),
            self.item("other", "Spain", [{"self": False, "responseStatus": "declined"}]),
        ])
        self.assertEqual({s["gid"]: s["invite"] for s in got["stays"]},
                         {"own": "", "yes": "", "maybe": "", "no": "declined",
                          "new": "needsAction", "other": ""})

    def test_declined_and_unanswered_invitations_do_not_become_movements(self):
        got = self.read([
            self.item("yes", "Italy", [{"self": True, "responseStatus": "accepted"}]),
            self.item("no", "Thailand", [{"self": True, "responseStatus": "declined"}]),
            self.item("new", "Bangkok, Thailand", [{"self": True, "responseStatus": "needsAction"}]),
        ])
        found = appmod.sync_events(2026, got["stays"])
        self.assertEqual([e["gid"] for e in found], ["yes"])


# ---- the rest of the audit (8 to 15) ----

import hashlib
import hmac
import json
import os
import smtplib

import mailer
from tests.test_billing import STRIPE, sign


class AccountExistsCooldownTests(AppTestCase):
    """Signing up with someone's address must not spend that account's email cooldown, or
    their Forgot password would silently send nothing."""

    def test_sign_ups_do_not_block_the_reset_email(self):
        self.signup("a@example.com")
        self.age_emails()
        for _ in range(5):
            self.app.test_client().post("/signup", data={
                "email": "a@example.com", "password": "password1", "confirm": "password1"})
        exists = [m for m in self.outbox if m["subject"] == "You already have a Nomad Life account"]
        self.assertEqual(len(exists), appmod.LIMITS[("exists", "email")])  # not one a minute
        before = len(self.outbox)
        self.app.test_client().post("/forgot", data={"email": "a@example.com"})
        reset = self.outbox[before:]
        self.assertEqual([m["to"] for m in reset], ["a@example.com"])
        self.assertIn("/reset/", reset[0]["text"])


class GoogleLinkEndsOnSecurityTests(AppTestCase):
    """Connecting Google needs only a session, so securing the account ends the link (and
    AUTO) made with a session someone else held."""

    def setUp(self):
        super().setUp()
        self.signup("a@example.com")
        self.new_year(2026)
        with self.db() as conn:
            conn.execute("INSERT INTO calendar_links (user_id, account, refresh_token, "
                         "calendar_id, calendar_name) VALUES (1, 'thief@gmail.com', 'rt', "
                         "'cal', 'Thief')")
            conn.execute("UPDATE years SET sync_mode = 'auto', sync_calendar = 'cal'")
        patcher = mock.patch.object(appmod, "google_revoke")
        self.revoke = patcher.start()
        self.addCleanup(patcher.stop)

    def state(self):
        with self.db() as conn:
            return (conn.execute("SELECT COUNT(*) FROM calendar_links").fetchone()[0],
                    conn.execute("SELECT sync_mode FROM years").fetchone()[0])

    def test_changing_the_password_ends_it(self):
        html = self.client.post("/settings", data={
            "action": "password", "current_password": "password1", "password": "new-password-2",
            "confirm": "new-password-2"}, follow_redirects=True).get_data(as_text=True)
        self.assertEqual(self.state(), (0, "manual"))
        self.assertIn("Google Calendar was disconnected too", html)
        self.revoke.assert_called_once_with("rt")

    def test_signing_out_everywhere_else_ends_it(self):
        self.client.post("/settings", data={"action": "sessions"})
        self.assertEqual(self.state(), (0, "manual"))

    def test_a_password_reset_ends_it(self):
        self.age_emails()
        self.app.test_client().post("/forgot", data={"email": "a@example.com"})
        self.app.test_client().post(self.last_link("a@example.com", kind="reset"),
                                    data={"password": "reset-password-3",
                                          "confirm": "reset-password-3"})
        self.assertEqual(self.state(), (0, "manual"))

    def test_nothing_else_touches_it(self):
        self.client.post("/settings", data={"action": "profile", "first_name": "Ada"})
        self.assertEqual(self.state(), (1, "auto"))


class TooLargeCrossSiteTests(AppTestCase):
    def post_big(self, **headers):
        return self.app.test_client().post(
            "/login", data={"email": "a@example.com", "password": "x" * (12 * 1024 * 1024)},
            headers=headers)

    def test_another_site_cannot_set_a_session_cookie_or_pick_the_redirect(self):
        resp = self.post_big(**{"Sec-Fetch-Site": "cross-site", "Referer": "https://evil.example/"})
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("evil.example", resp.headers["Location"])
        self.assertNotIn("Set-Cookie", resp.headers)

    def test_a_form_of_this_site_still_gets_its_message(self):
        resp = self.post_big(**{"Sec-Fetch-Site": "same-origin",
                                "Referer": "http://localhost/settings"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/settings"))
        self.assertIn("nomadlife_session", resp.headers.get("Set-Cookie", ""))


class ReceiptRatioAndPackageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup("a@example.com")
        self.set_plan("pro")
        self.new_year(2026)
        self.mid = self.add_movement(2026, "Rome", "Italy", "2026-03-01", "2026-03-10")

    def count(self):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def test_a_file_of_empty_space_is_refused(self):
        body = b"%PDF-1.4\n" + b"\0" * (4 * 1024 * 1024)
        html = self.upload(f"/movements/{self.mid}", "zeros.pdf", body, kind="receipt"
                           ).get_data(as_text=True)
        self.assertEqual(self.count(), 0)
        page = self.client.get(f"/movements/{self.mid}").get_data(as_text=True)
        self.assertIn("is mostly empty space", html + page)
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        self.assertEqual(os.listdir(folder) if os.path.isdir(folder) else [], [])

    def test_a_real_size_file_is_kept(self):
        body = b"%PDF-1.4\n" + os.urandom(2 * 1024 * 1024)
        self.upload(f"/movements/{self.mid}", "scan.pdf", body)
        self.assertEqual(self.count(), 1)

    def test_a_small_file_of_zeros_is_kept(self):
        self.upload(f"/movements/{self.mid}", "small.pdf", b"%PDF-1.4\n" + b"\0" * 4096)
        self.assertEqual(self.count(), 1)

    def test_package_downloads_are_limited(self):
        limit = appmod.LIMITS[("package", "email")]
        codes = [self.client.get("/year/2026/package").status_code for _ in range(limit + 1)]
        self.assertEqual(codes[:limit], [200] * limit)
        self.assertEqual(codes[limit], 302)
        html = self.client.get("/year/2026/package", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Too many package downloads", html)


class BillingLimitTests(AppTestCase):
    def test_checkout_and_portal_are_limited_per_account(self):
        self.app.config.update(STRIPE)
        self.signup("a@example.com")
        limit = appmod.LIMITS[("billing", "email")]
        with mock.patch.object(appmod, "stripe_api", return_value=None) as stripe:
            for _ in range(limit):
                self.client.post("/billing/checkout", data={"plan": "pro"})
            asked = stripe.call_count
            html = self.client.post("/billing/checkout", data={"plan": "pro"},
                                    follow_redirects=True).get_data(as_text=True)
            self.assertIn("Too many billing requests", html)
            self.assertEqual(stripe.call_count, asked)  # Stripe is not asked any more
            html = self.client.post("/billing/portal", follow_redirects=True).get_data(as_text=True)
            self.assertNotIn("Too many billing requests", html)  # no customer yet: nothing counted


class StripeSignatureTests(AppTestCase):
    def test_no_header_raises(self):
        now = int(time.time())
        good = sign(b"body")
        for header in (None, "", "t=", "t=²,v1=aa", f"t={now},v1=é", f"t={now},v1=",
                       "t=99999999999999999999999999,v1=aa", "t=1,t=2,v1=aa", "v1=aa", "t,v1",
                       f"t={now},v1=\x00", "t=١٢٣,v1=aa"):
            self.assertFalse(appmod.stripe_signature_ok(b"body", header, "whsec_test"), header)
        self.assertTrue(appmod.stripe_signature_ok(b"body", good, "whsec_test"))

    def test_the_webhook_answers_400_not_500(self):
        self.app.config.update(STRIPE)
        client = self.app.test_client()
        for header in ("t=²,v1=aa", f"t={int(time.time())},v1=é"):
            resp = client.post("/billing/webhook", data=b"{}", headers={"Stripe-Signature": header})
            self.assertEqual(resp.status_code, 400)


class LogRedactionTests(AppTestCase):
    def test_a_refused_recipient_is_logged_by_its_domain(self):
        exc = smtplib.SMTPRecipientsRefused({"ada@example.com": (550, b"<ada@example.com> gone")})
        text = mailer.smtp_failure("send", exc)
        self.assertNotIn("ada@", text)
        self.assertIn("@example.com", text)

    def test_signed_links_are_not_logged(self):
        with self.assertLogs(self.app.logger, "ERROR") as logged:
            self.app.logger.error("Exception on /reset/abc.DEF-1_2 [POST]")
            self.app.logger.error("Exception on /account/email/undo/xyz.1.2 [POST]")
            self.app.logger.error("Exception on /account/email/tok.en [GET]")
            self.app.logger.error("Exception on /verify/tok [GET]")
        out = "\n".join(logged.output)
        for secret in ("abc.DEF", "xyz.1", "tok.en", "/verify/tok "):
            self.assertNotIn(secret, out)
        self.assertEqual(out.count("<token>"), 4)

    def test_a_failed_alert_names_the_domain_only(self):
        self.signup("a@example.com")
        self.send_email.side_effect = lambda *a, **k: False
        with self.assertLogs(self.app.logger, "ERROR") as logged:
            self.client.post("/settings", data={
                "action": "password", "current_password": "password1",
                "password": "new-password-2", "confirm": "new-password-2"})
        out = "\n".join(logged.output)
        self.assertIn("Could not send the security alert to @example.com", out)
        self.assertNotIn("a@example.com", out)


class Ipv6LimitTests(AppTestCase):
    def test_an_address_stands_for_its_network(self):
        key = appmod.ip_limit_key
        self.assertEqual(key("2001:db8:1:2:aaaa:bbbb:cccc:dddd"), "2001:db8:1:2::/64")
        self.assertEqual(key("2001:db8:1:2::1"), key("2001:db8:1:2:ffff::9"))
        self.assertNotEqual(key("2001:db8:1:2::1"), key("2001:db8:1:3::1"))
        self.assertEqual(key("::ffff:203.0.113.5"), "203.0.113.5")
        self.assertEqual(key("203.0.113.5"), "203.0.113.5")
        self.assertEqual(key("unknown"), "unknown")

    def test_rotating_the_address_inside_a_network_does_not_reset_the_limit(self):
        limit = appmod.LIMITS[("fail", "ip")]
        for i in range(limit):
            self.app.test_client().post(
                "/login", data={"email": f"x{i}@example.com", "password": "nope-nope"},
                environ_base={"REMOTE_ADDR": f"2001:db8:1:2::{i + 1:x}"})
        resp = self.app.test_client().post(
            "/login", data={"email": "y@example.com", "password": "nope-nope"},
            environ_base={"REMOTE_ADDR": "2001:db8:1:2:aaaa::1"})
        self.assertIn("Too many", resp.get_data(as_text=True))
        resp = self.app.test_client().post(  # another network is not affected
            "/login", data={"email": "y@example.com", "password": "nope-nope"},
            environ_base={"REMOTE_ADDR": "2001:db8:1:3::1"})
        self.assertNotIn("Too many", resp.get_data(as_text=True))
