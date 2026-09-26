"""Account page (password, email, delete) and the limits on password guessing."""
import io
import os
import unittest
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


class AccountTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)
        self.age_emails()  # the sign up email went out just now; allow the next one

    def post(self, client=None, **data):
        return (client or self.client).post("/account", data=data, follow_redirects=True)

    def test_page_and_links(self):
        html = self.client.get("/account").data.decode()
        self.assertIn("<h1>a@example.com</h1>", html)
        self.assertIn("1 year", html)
        dash = self.client.get("/year/2026").data.decode()
        self.assertIn('href="/account"', dash)
        self.assertIn("/login", self.app.test_client().get("/account").headers["Location"])

    def test_change_password(self):
        other = self.app.test_client()
        other.post("/login", data={"email": "a@example.com", "password": "password1"})
        self.assertEqual(other.get("/year/2026").status_code, 200)
        html = self.post(action="password", current_password="wrong", password="newpass123",
                         confirm="newpass123").data.decode()
        self.assertIn("Your current password is not correct.", html)
        html = self.post(action="password", current_password="password1", password="newpass123",
                         confirm="newpass124").data.decode()
        self.assertIn("Passwords do not match.", html)
        html = self.post(action="password", current_password="password1", password="newpass123",
                         confirm="newpass123").data.decode()
        self.assertIn("Your password has been changed.", html)
        self.assertEqual(self.client.get("/year/2026").status_code, 200)  # still signed in here
        self.assertIn("/login", other.get("/year/2026").headers["Location"])  # signed out there
        fresh = self.app.test_client()
        self.assertEqual(fresh.post("/login", data={"email": "a@example.com",
                                                    "password": "newpass123"}).status_code, 302)

    def test_change_email_needs_the_link(self):
        html = self.post(action="email", email="New@Example.com",
                         current_password="password1").data.decode()
        self.assertIn("We sent a confirmation link to new@example.com", html)
        self.assertEqual(self.outbox[-1]["to"], "new@example.com")
        self.assertIn("a@example.com", self.outbox[-1]["text"])
        with self.db() as conn:  # nothing changes until the link is opened
            self.assertEqual(conn.execute("SELECT email FROM users").fetchone()[0], "a@example.com")
        link = self.last_link("new@example.com", kind="account/email")
        html = self.client.get(link, follow_redirects=True).data.decode()
        self.assertIn("Your email address is now new@example.com.", html)
        fresh = self.app.test_client()
        self.assertEqual(fresh.post("/login", data={"email": "new@example.com",
                                                    "password": "password1"}).status_code, 302)

    def test_change_email_refusals(self):
        self.signup("b@example.com", client=self.app.test_client())
        self.age_emails()
        for email, message in [("not-an-email", "Please enter a valid email address."),
                               ("a@example.com", "That is already your email address."),
                               ("b@example.com", "Another account already uses this email")]:
            html = self.post(action="email", email=email, current_password="password1").data.decode()
            self.assertIn(message, html)

    def test_email_link_is_stale_after_a_password_change(self):
        self.post(action="email", email="new@example.com", current_password="password1")
        link = self.last_link("new@example.com", kind="account/email")
        self.post(action="password", current_password="password1", password="newpass123",
                  confirm="newpass123")
        html = self.client.get(link, follow_redirects=True).data.decode()
        self.assertIn("This link is no longer valid.", html)

    def test_delete_account(self):
        self.upload("/year/2026/base", "r.pdf", b"%PDF-1 x")
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        self.assertTrue(os.listdir(folder))
        base = dict(action="delete", confirm_delete="2", confirm_email="a@example.com",
                    current_password="password1")
        self.post(**{**base, "confirm_delete": ""})  # dialog not confirmed
        self.assertIn("Type your email", self.post(**{**base, "confirm_email": "x@y.com"}).data.decode())
        self.assertIn("not correct", self.post(**{**base, "current_password": "nope"}).data.decode())
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        html = self.post(**base).data.decode()
        self.assertIn("Your account and all its data have been deleted.", html)
        with self.db() as conn:
            for table in ("users", "years", "documents"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertFalse(os.path.exists(folder))
        self.assertIn("/login", self.client.get("/year/2026").headers["Location"])


class LimitTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.anon = self.app.test_client()

    def sign_in(self, password, email="a@example.com"):
        return self.anon.post("/login", data={"email": email, "password": password})

    def test_five_wrong_passwords_lock_the_account_for_a_while(self):
        for _ in range(5):
            self.assertIn(b"Invalid email or password.", self.sign_in("wrong").data)
        resp = self.sign_in("password1")  # even the right password waits
        self.assertEqual(resp.status_code, 429)
        self.assertIn(b"Too many failed sign in attempts for this account. Please wait 15 minutes", resp.data)
        with self.db() as conn:  # the window passes
            conn.execute("UPDATE auth_events SET created_at = datetime('now', '-16 minutes')")
        self.assertEqual(self.sign_in("password1").status_code, 302)

    def test_a_successful_sign_in_resets_the_count(self):
        for _ in range(4):
            self.sign_in("wrong")
        self.assertEqual(self.sign_in("password1").status_code, 302)
        self.anon.post("/logout")
        for _ in range(4):
            self.sign_in("wrong")
        self.assertEqual(self.sign_in("password1").status_code, 302)

    def test_many_accounts_from_one_address_are_limited(self):
        for i in range(20):
            self.sign_in("wrong", email=f"guess{i}@example.com")
        self.assertEqual(self.sign_in("wrong", email="other@example.com").status_code, 429)

    def test_wrong_current_passwords_on_the_account_page_count(self):
        for _ in range(5):
            self.client.post("/account", data={"action": "password", "current_password": "x",
                                               "password": "newpass123", "confirm": "newpass123"})
        html = self.client.post("/account", data={
            "action": "password", "current_password": "password1", "password": "newpass123",
            "confirm": "newpass123"}, follow_redirects=True).data.decode()
        self.assertIn("Too many wrong passwords for this account.", html)

    def test_reset_requests_are_limited_per_address(self):
        for i in range(5):
            self.anon.post("/forgot", data={"email": f"x{i}@example.com"})
        resp = self.anon.post("/forgot", data={"email": "a@example.com"})
        self.assertEqual(resp.status_code, 429)
        self.assertIn(b"Too many reset requests", resp.data)

    def test_proxy_count_setting(self):
        for raw, n in [("", 0), ("1", 1), (" 2 ", 2)]:
            with mock.patch.dict(os.environ, {"PROXY_COUNT": raw}):
                self.assertEqual(appmod.proxy_count(), n)
        for raw in ["-1", "abc", "11"]:
            with mock.patch.dict(os.environ, {"PROXY_COUNT": raw}):
                with self.assertRaises(SystemExit):
                    appmod.proxy_count()


if __name__ == "__main__":
    unittest.main()
