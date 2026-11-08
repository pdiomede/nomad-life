"""Delegates (2.1.0): an owner lets other accounts view theirs, read only."""

import io
import re
import zipfile
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase
from tests.test_fixes_158 import other_connection

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class DelegateCase(AppTestCase):
    """The owner (self.client, a@example.com) with 2026: a stay in Italy with a receipt and
    notes, and a base document. The delegate (self.d, d@example.com) has an account."""

    def setUp(self):
        super().setUp()
        self.signup("a@example.com")
        self.year = self.new_year(2026)
        self.mid = self.add_movement(2026, "Rome", "Italy", "2026-03-01", "2026-03-10",
                                     notes="Coworking near Termini")
        self.upload(f"/movements/{self.mid}", "hotel.pdf", PDF)
        self.upload("/year/2026/base", "lease.pdf", PDF)
        self.d = self.signup("d@example.com", client=self.app.test_client())

    def invite(self, email="d@example.com", password="password1", client=None):
        return (client or self.client).post("/settings", data={
            "action": "delegate_add", "delegate_email": email, "current_password": password},
            follow_redirects=True)

    def rows(self):
        with self.db() as conn:
            return conn.execute("SELECT * FROM delegates ORDER BY id").fetchall()

    def share_id(self):
        return self.rows()[0]["id"]

    def accept(self, client=None):
        sid = self.share_id()
        return (client or self.d).post(f"/shared/{sid}/accept", follow_redirects=True), sid

    def doc_id(self, movement=True):
        with self.db() as conn:
            return conn.execute("SELECT id FROM documents WHERE movement_id IS "
                                + ("NOT NULL" if movement else "NULL")).fetchone()[0]

    def text(self, resp):
        return resp.get_data(as_text=True)


class InviteTests(DelegateCase):
    def test_invite_needs_the_password(self):
        page = self.text(self.invite(password="wrong-one"))
        self.assertIn("password", page.lower())
        self.assertEqual(self.rows(), [])

    def test_invite_sends_an_email_naming_the_owner_by_email_only(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET first_name = 'Click evil.example' WHERE email = ?",
                         ("a@example.com",))
        page = self.text(self.invite())
        self.assertIn("d@example.com was invited", page)
        [row] = self.rows()
        self.assertEqual((row["email"], row["delegate_id"]), ("d@example.com", None))
        mail = self.outbox[-1]
        self.assertEqual(mail["to"], "d@example.com")
        self.assertIn("a@example.com", mail["subject"])
        self.assertNotIn("evil", mail["text"] + mail["html"])
        self.assertIn("/shared", mail["text"])

    def test_address_is_lowercased_and_checked(self):
        self.invite("D@Example.COM ")
        self.assertEqual(self.rows()[0]["email"], "d@example.com")
        self.assertIn("Type the email address", self.text(self.invite("not-an-address")))
        self.assertIn("your own email address", self.text(self.invite("a@example.com")))
        self.assertIn("already one of your delegates", self.text(self.invite("d@example.com")))
        self.assertEqual(len(self.rows()), 1)

    def test_at_most_five(self):
        for n in range(appmod.DELEGATES_MAX):
            self.invite(f"p{n}@example.com")
        page = self.text(self.invite("one-more@example.com"))
        self.assertIn("the most an account can have", page)
        self.assertEqual(len(self.rows()), appmod.DELEGATES_MAX)
        settings = self.text(self.client.get("/settings"))
        self.assertNotIn('value="delegate_add"', settings)

    def test_the_cap_holds_against_a_parallel_invite(self):
        for n in range(appmod.DELEGATES_MAX - 1):
            self.invite(f"p{n}@example.com")
        real = appmod.take_attempt

        def meanwhile(kind, *args, **kwargs):
            if kind != "delegate":  # the password check counts too
                return real(kind, *args, **kwargs)
            conn = other_connection(self)
            with conn:
                conn.execute("INSERT INTO delegates (owner_id, email) SELECT id, "
                             "'other-tab@example.com' FROM users WHERE email = 'a@example.com'")
            conn.close()
            return real(kind, *args, **kwargs)

        with mock.patch.object(appmod, "take_attempt", side_effect=meanwhile):
            page = self.text(self.invite("late@example.com"))
        self.assertIn("the most an account can have", page)
        self.assertEqual(len(self.rows()), appmod.DELEGATES_MAX)

    def test_send_again_waits_a_minute(self):
        self.invite()
        sent = len(self.outbox)
        page = self.text(self.client.post("/settings", data={
            "action": "delegate_resend", "delegate_id": self.share_id()}, follow_redirects=True))
        self.assertIn("less than a minute ago", page)
        self.assertEqual(len(self.outbox), sent)
        with self.db() as conn:
            conn.execute("UPDATE delegates SET invited_at = datetime('now', '-2 minutes')")
        page = self.text(self.client.post("/settings", data={
            "action": "delegate_resend", "delegate_id": self.share_id()}, follow_redirects=True))
        self.assertIn("sent to d@example.com again", page)
        self.assertEqual(len(self.outbox), sent + 1)

    def test_invitation_emails_are_limited(self):
        limit = appmod.LIMITS[("delegate", "email")]
        for n in range(limit):
            self.invite(f"p{n}@example.com")
            if n % 4 == 3:  # stay under the cap of 5
                with self.db() as conn:
                    conn.execute("DELETE FROM delegates")
        page = self.text(self.invite("over@example.com"))
        self.assertIn("Too many invitations", page)

    def test_remove_needs_the_two_step_confirmation(self):
        self.invite()
        sid = self.share_id()
        self.client.post("/settings", data={"action": "delegate_remove", "delegate_id": sid})
        self.assertEqual(len(self.rows()), 1)
        page = self.text(self.client.post("/settings", data={
            "action": "delegate_remove", "delegate_id": sid, "confirm_delete": "2"},
            follow_redirects=True))
        self.assertIn("invitation to d@example.com was withdrawn", page)
        self.assertEqual(self.rows(), [])

    def test_settings_lists_delegates(self):
        self.invite()
        page = self.text(self.client.get("/settings"))
        self.assertIn('href="#delegates"', page)
        self.assertIn("d@example.com", page)
        self.assertIn("Invited", page)
        self.assertIn("Send again", page)
        self.accept()
        page = self.text(self.client.get("/settings"))
        self.assertIn("Can view", page)
        self.assertNotIn("Send again", page)
        with self.db() as conn:
            events = [r[0] for r in conn.execute("SELECT event FROM audit_log WHERE user_id = "
                                                 "(SELECT id FROM users WHERE email = "
                                                 "'a@example.com')")]
        self.assertIn("delegate_invited", events)
        self.assertIn("Delegate invited", page)


class AcceptTests(DelegateCase):
    def test_only_the_invited_address_sees_and_accepts(self):
        self.invite()
        stranger = self.signup("s@example.com", client=self.app.test_client())
        self.assertNotIn("a@example.com", self.text(stranger.get("/shared")))
        resp, sid = self.accept(client=stranger)
        self.assertIn("no longer waiting", self.text(resp))
        self.assertIsNone(self.rows()[0]["delegate_id"])
        self.assertEqual(stranger.get(f"/shared/{sid}/year/2026").status_code, 404)
        page = self.text(self.d.get("/shared"))
        self.assertIn("a@example.com", page)
        self.assertIn("Accept", page)

    def test_accept_alerts_the_owner_without_the_delegates_address(self):
        self.invite()
        resp, sid = self.accept()
        self.assertIn("You can now view the account of a@example.com", self.text(resp))
        self.assertEqual(self.rows()[0]["delegate_id"], self.user_id("d@example.com"))
        alert = self.outbox[-1]
        self.assertEqual(alert["to"], "a@example.com")
        self.assertIn("d@example.com accepted your invitation", alert["text"])
        self.assertNotIn("From network address", alert["text"])
        with self.db() as conn:
            events = conn.execute("SELECT user_id, event, actor FROM audit_log WHERE event = "
                                  "'delegate_accepted' ORDER BY id").fetchall()
        self.assertEqual([e["user_id"] for e in events],
                         [self.user_id("a@example.com"), self.user_id("d@example.com")])

    def test_withdrawn_meanwhile(self):
        self.invite()
        sid = self.share_id()
        self.client.post("/settings", data={"action": "delegate_remove", "delegate_id": sid,
                                            "confirm_delete": "2"})
        resp = self.d.post(f"/shared/{sid}/accept", follow_redirects=True)
        self.assertIn("no longer waiting", self.text(resp))
        self.assertEqual(self.rows(), [])

    def test_decline_removes_the_invitation(self):
        self.invite()
        sid = self.share_id()
        page = self.text(self.d.post(f"/shared/{sid}/decline", follow_redirects=True))
        self.assertIn("You declined", page)
        self.assertEqual(self.rows(), [])

    def test_menu_item_and_badge(self):
        self.assertNotIn("Shared with you", self.text(self.d.get("/settings")))
        self.invite()
        page = self.text(self.d.get("/settings"))
        self.assertIn("Shared with you", page)
        self.assertIn("1<span class=\"visually-hidden\"> invitation waiting", page)

    def test_app_sends_a_viewer_without_years_to_shared(self):
        self.assertIn("/year/new", self.d.get("/app").headers["Location"])
        self.invite()
        # A waiting invitation alone does not keep a new account from its own year.
        self.assertIn("/year/new", self.d.get("/app").headers["Location"])
        self.accept()
        self.assertTrue(self.d.get("/app").headers["Location"].endswith("/shared"))

    def user_id(self, email):
        with self.db() as conn:
            return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0]


class ViewTests(DelegateCase):
    def setUp(self):
        super().setUp()
        self.invite()
        _resp, self.sid = self.accept()

    def test_home_opens_the_owners_year(self):
        resp = self.d.get(f"/shared/{self.sid}")
        self.assertTrue(resp.headers["Location"].endswith(f"/shared/{self.sid}/year/2026"))

    def test_dashboard_is_read_only(self):
        page = self.text(self.d.get(f"/shared/{self.sid}/year/2026"))
        self.assertIn("You are viewing the account of", page)
        self.assertIn("Rome", page)
        # The only form that posts is Sign out.
        posts = re.findall(r'<form[^>]*method="post"[^>]*>', page)
        self.assertEqual(len(posts), 1, posts)
        self.assertIn("/logout", posts[0])
        for gone in ("+ Add movement", "+ Year", "Receipt Storage", "sync-switch-form",
                     "Calendar Integration", "/movements/new", 'class="cal-color"'):
            self.assertNotIn(gone, page)
        self.assertIn(f"/shared/{self.sid}/movements/{self.mid}", page)
        self.assertIn(f"/shared/{self.sid}/year/2026/base", page)

    def test_never_syncs_nor_reads_google(self):
        with mock.patch.object(appmod, "auto_sync", side_effect=AssertionError("synced")), \
                mock.patch.object(appmod, "google_api", side_effect=AssertionError("google")):
            self.assertEqual(self.d.get(f"/shared/{self.sid}/year/2026").status_code, 200)

    def test_movement_and_base_pages(self):
        page = self.text(self.d.get(f"/shared/{self.sid}/movements/{self.mid}"))
        self.assertIn("Coworking near Termini", page)
        self.assertIn("hotel.pdf", page)
        self.assertNotIn('method="post" action="/documents', page)
        self.assertNotIn("Rename or change type", page)
        self.assertIn(f"/shared/{self.sid}/documents/", page)
        base = self.text(self.d.get(f"/shared/{self.sid}/year/2026/base"))
        self.assertIn("lease.pdf", base)
        self.assertNotIn("Delete year", base)

    def test_receipt_downloads(self):
        resp = self.d.get(f"/shared/{self.sid}/documents/{self.doc_id()}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/zip")
        self.assertEqual(zipfile.ZipFile(io.BytesIO(resp.data)).read("hotel.pdf"), PDF)
        self.assertEqual(resp.headers["Cache-Control"], "private, no-store")

    def test_other_accounts_ids_are_404(self):
        self.new_year(2026, client=self.d)  # the delegate's own year
        with self.db() as conn:
            own = conn.execute("SELECT id FROM years WHERE user_id = (SELECT id FROM users "
                               "WHERE email = 'd@example.com')").fetchone()[0]
        self.assertEqual(self.d.get(f"/shared/{self.sid}/year/2025").status_code, 404)
        self.assertEqual(self.d.get(f"/shared/{self.sid}/movements/999").status_code, 404)
        self.assertEqual(self.d.get(f"/shared/{self.sid}/documents/999").status_code, 404)
        self.assertEqual(self.d.get(f"/shared/{self.sid + 1}/year/2026").status_code, 404)
        self.assertTrue(own)
        # The owner cannot open their own share.
        self.assertEqual(self.client.get(f"/shared/{self.sid}/year/2026").status_code, 404)

    def test_write_routes_stay_closed(self):
        # Another account's id answers like a movement deleted meanwhile (a redirect with a
        # message), and changes nothing.
        self.d.post(f"/movements/{self.mid}", data={
            "action": "update", "country": "Spain", "city": "", "start_date": "2026-03-01",
            "end_date": "2026-03-02", "notes": ""})
        self.assertEqual(self.d.get(f"/documents/{self.doc_id()}").status_code, 404)
        self.d.post(f"/documents/{self.doc_id()}/delete", data={"confirm_delete": "2"})
        self.d.post("/year/2026/base", data={"action": "delete_year", "confirm_delete": "2",
                                             "confirm_year": "2026"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT country FROM movements").fetchone()[0], "Italy")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM years").fetchone()[0], 1)

    def test_package_follows_the_owners_plan(self):
        resp = self.d.get(f"/shared/{self.sid}/year/2026/package")
        self.assertEqual(resp.status_code, 302)
        page = self.text(self.d.get(f"/shared/{self.sid}/year/2026"))
        self.assertIn("Not part of this account's plan", page)
        self.set_plan("pro", "a@example.com")
        resp = self.d.get(f"/shared/{self.sid}/year/2026/package")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/zip")
        names = zipfile.ZipFile(io.BytesIO(resp.data)).namelist()
        self.assertTrue(any(n.endswith("hotel.pdf") for n in names), names)
        # The delegate's own plan does not matter.
        self.set_plan("pro", "d@example.com")
        self.set_plan("free", "a@example.com")
        self.assertEqual(self.d.get(f"/shared/{self.sid}/year/2026/package").status_code, 302)

    def test_last_viewed_is_stamped(self):
        self.d.get(f"/shared/{self.sid}/year/2026")
        self.assertIsNotNone(self.rows()[0]["last_viewed_at"])
        self.assertIn("last viewed", self.text(self.client.get("/settings")))


class EndTests(DelegateCase):
    def setUp(self):
        super().setUp()
        self.invite()
        _resp, self.sid = self.accept()
        self.url = f"/shared/{self.sid}/year/2026"

    def test_owner_removes(self):
        page = self.text(self.client.post("/settings", data={
            "action": "delegate_remove", "delegate_id": self.sid, "confirm_delete": "2"},
            follow_redirects=True))
        self.assertIn("d@example.com can no longer view your account", page)
        self.assertEqual(self.d.get(self.url).status_code, 404)

    def test_delegate_stops_viewing(self):
        page = self.text(self.d.post(f"/shared/{self.sid}/leave", data={"confirm_delete": "2"},
                                     follow_redirects=True))
        self.assertIn("You no longer view the account", page)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.d.get(self.url).status_code, 404)

    def test_disabled_owner(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET disabled = 1 WHERE email = 'a@example.com'")
        self.assertEqual(self.d.get(self.url).status_code, 404)
        self.assertNotIn("a@example.com", self.text(self.d.get("/shared")))

    def test_deleting_either_account_removes_the_row(self):
        with self.db() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM users WHERE email = 'd@example.com'")
        self.assertEqual(self.rows(), [])

    def test_delegate_changing_email_keeps_access(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET email = 'new@example.com' WHERE email = "
                         "'d@example.com'")
        self.d.post("/login", data={"email": "new@example.com", "password": "password1"})
        self.assertIn("new@example.com", self.text(self.client.get("/settings")))

    def test_email_change_undo_removes_delegates(self):
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "x@example.com",
                                            "current_password": "password1"})
        self.client.post(self.last_link("x@example.com", kind="account/email"))
        undo = self.last_link("a@example.com", kind="account/email/undo")
        self.app.test_client().post(undo)
        self.assertEqual(self.rows(), [])
