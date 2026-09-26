"""Regression tests for the 1.2.2 bug hunt."""
import unittest
from unittest import mock

import app as appmod
import package
from tests.helpers import AppTestCase


class AdminAndAccountTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.victim = self.signup("v@example.com", client=self.app.test_client())
        self.signup("admin@example.com")

    def uid(self, email):
        with self.db() as conn:
            return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0]

    def test_enabling_again_does_not_bring_old_sessions_back(self):
        uid = self.uid("v@example.com")
        self.client.post(f"/admin/users/{uid}", data={"action": "disable"})
        self.client.post(f"/admin/users/{uid}", data={"action": "enable"})
        self.assertIn("/login", self.victim.get("/app").headers["Location"])
        fresh = self.app.test_client()
        self.assertEqual(fresh.post("/login", data={"email": "v@example.com",
                                                    "password": "password1"}).status_code, 302)
        self.assertNotIn("/login", fresh.get("/app").headers.get("Location", ""))

    def test_disabled_accounts_get_no_reset_and_cannot_use_links(self):
        self.age_emails()
        self.victim.post("/account", data={"action": "email", "email": "new@example.com",
                                           "current_password": "password1"})
        change = self.last_link("new@example.com", kind="account/email")
        anon = self.app.test_client()
        self.age_emails()
        anon.post("/forgot", data={"email": "v@example.com"})
        reset = self.last_link("v@example.com", kind="reset")
        self.client.post(f"/admin/users/{self.uid('v@example.com')}", data={"action": "disable"})
        self.age_emails()
        before = len(self.outbox)
        anon.post("/forgot", data={"email": "v@example.com"})
        self.assertEqual(len(self.outbox), before)
        self.assertIn(b"account is disabled", anon.get(change, follow_redirects=True).data)
        self.assertIn(b"account is disabled", anon.get(reset, follow_redirects=True).data)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT email FROM users WHERE id = ?",
                                          (self.uid("v@example.com"),)).fetchone()[0],
                             "v@example.com")


class PendingChangeTests(AppTestCase):
    def test_a_pending_email_change_can_be_cancelled(self):
        self.signup()
        self.age_emails()
        self.client.post("/account", data={"action": "email", "email": "me@x.cm",
                                           "current_password": "password1"})
        link = self.last_link("me@x.cm", kind="account/email")
        self.assertIn(b"Waiting for confirmation", self.client.get("/account").data)
        html = self.client.post("/account", data={"action": "cancel_email"},
                                follow_redirects=True).data.decode()
        self.assertIn("was cancelled", html)
        self.assertIn(b"no longer valid", self.app.test_client().get(link, follow_redirects=True).data)
        # typing your own address again cancels too
        self.age_emails()
        self.client.post("/account", data={"action": "email", "email": "me@x.cm",
                                           "current_password": "password1"})
        html = self.client.post("/account", data={"action": "email", "email": "a@example.com",
                                                  "current_password": "password1"},
                                follow_redirects=True).data.decode()
        self.assertIn("Your pending email change was cancelled.", html)


class SignInTests(AppTestCase):
    def test_a_reset_lifts_the_lock(self):
        self.signup()
        self.client.post("/logout")
        anon = self.app.test_client()
        for _ in range(5):
            anon.post("/login", data={"email": "a@example.com", "password": "wrong"})
        self.age_emails()
        anon.post("/forgot", data={"email": "a@example.com"})
        anon.post(self.last_link("a@example.com", kind="reset"),
                  data={"password": "brandnew1", "confirm": "brandnew1"})
        resp = anon.post("/login", data={"email": "a@example.com", "password": "brandnew1"})
        self.assertEqual(resp.status_code, 302)

    def test_failed_resend_does_not_claim_an_email_went_out(self):
        self.client.post("/signup", data={"email": "p@example.com", "password": "password1",
                                          "confirm": "password1"})
        self.age_emails()
        self.send_email.side_effect = lambda *a, **k: False
        self.client.post("/login", data={"email": "p@example.com", "password": "password1"})
        self.send_email.side_effect = self._record_email
        html = self.client.post("/login", data={"email": "p@example.com",
                                                "password": "password1"}).data.decode()
        self.assertIn("We sent a new link", html)

    def test_sign_up_keeps_a_disabled_or_data_holding_pending_account(self):
        self.client.post("/signup", data={"email": "old@example.com", "password": "password1",
                                          "confirm": "password1"})
        with self.db() as conn:
            uid = conn.execute("SELECT id FROM users WHERE email = 'old@example.com'").fetchone()[0]
            conn.execute("INSERT INTO years (user_id, year, base_city, base_country) "
                         "VALUES (?, 2026, 'Lisbon', 'Portugal')", (uid,))
        self.age_emails()
        self.app.test_client().post("/signup", data={"email": "old@example.com",
                                                     "password": "stranger1", "confirm": "stranger1"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM years").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT id FROM users WHERE email = 'old@example.com'")
                             .fetchone()[0], uid)


class DataTests(AppTestCase):
    def test_package_note_for_a_year_without_stays(self):
        self.signup()
        self.new_year(2027)  # a future year (today is 2026 in these tests' clock)
        with self.db() as conn:
            row = conn.execute("SELECT * FROM years WHERE year = 2027").fetchone()
        with self.app.test_request_context():
            text = package.summary_text(appmod.build_package_data(row, False))
        self.assertIn("count toward the base country once they have passed", text)
        self.assertNotIn("Every day counts toward the base country", text)

    def test_new_movement_after_the_year_was_deleted(self):
        self.signup()
        self.new_year(2026)
        other = self.app.test_client()
        other.post("/login", data={"email": "a@example.com", "password": "password1"})
        real = appmod.validate_movement

        def delete_year_first(form, year, current_notes=None):
            other.post("/year/2026/base", data={"action": "delete_year", "confirm_delete": "2",
                                                "confirm_year": "2026"})
            return real(form, year, current_notes)

        with mock.patch.object(appmod, "validate_movement", delete_year_first):
            resp = self.client.post("/year/2026/movements/new", data={
                "city": "Rome", "country": "Italy", "start_date": "2026-03-01",
                "end_date": "2026-03-02"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(b"no longer exists", self.client.get("/app", follow_redirects=True).data)


if __name__ == "__main__":
    unittest.main()
