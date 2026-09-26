"""Email confirmation on sign up, cleanup of unconfirmed accounts and the HTML emails."""
import os
import shutil
import sqlite3
import tempfile
import unittest

import app as appmod
import mailer
from tests.helpers import AppTestCase

EMAIL = "new@example.com"


class SignupConfirmationTests(AppTestCase):
    def post_signup(self, email=EMAIL, password="password1", client=None):
        return (client or self.client).post("/signup", data={
            "email": email, "password": password, "confirm": password})

    def login(self, email=EMAIL, password="password1", client=None):
        return (client or self.client).post("/login", data={"email": email, "password": password})

    def user(self, email=EMAIL):
        with self.db() as conn:
            return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    def backdate(self, minutes, email=EMAIL):
        with self.db() as conn:
            conn.execute("UPDATE users SET created_at = datetime('now', ?) WHERE email = ?",
                         (f"-{minutes} minutes", email))

    def test_signup_sends_link_and_does_not_sign_in(self):
        resp = self.post_signup()
        self.assertEqual(resp.headers["Location"], "/login")
        self.assertIsNone(self.user()["verified_at"])
        self.assertEqual(self.client.get("/app").status_code, 302)
        mail = self.outbox[-1]
        self.assertEqual((mail["to"], mail["subject"]), (EMAIL, "Confirm your Nomad Life account"))
        self.assertIn("20 minutes", mail["text"])
        self.assertIn("cid:" + mailer.LOGO_CID, mail["html"])
        self.assertIn(self.last_link(EMAIL), mail["html"])

    def test_link_confirms_then_sign_in_works(self):
        self.post_signup()
        resp = self.client.get(self.last_link(EMAIL))
        self.assertEqual(resp.headers["Location"], "/login")
        self.assertIsNotNone(self.user()["verified_at"])
        self.assertEqual(self.login().headers["Location"], "/app")
        self.assertNotIn("/login", self.client.get("/app").headers["Location"])
        again = self.client.get(self.last_link(EMAIL), follow_redirects=True)
        self.assertIn("already confirmed", again.get_data(as_text=True))

    def test_sign_in_before_confirming_is_refused_and_resends(self):
        self.post_signup()
        self.assertEqual(len(self.outbox), 1)
        self.age_emails()
        html = self.login().get_data(as_text=True)
        self.assertIn("Please confirm your email first", html)
        self.assertEqual(len(self.outbox), 2)
        self.assertEqual(self.client.get("/app").status_code, 302)
        wrong = self.login(password="wrongpass1").get_data(as_text=True)
        self.assertIn("Invalid email or password", wrong)
        self.assertEqual(len(self.outbox), 2)

    def test_unconfirmed_account_is_deleted_after_20_minutes(self):
        self.post_signup()
        link = self.last_link(EMAIL)
        self.backdate(21)
        self.client.get("/login")
        self.appmod_purge_now()
        self.assertIsNone(self.user())
        resp = self.client.get(link, follow_redirects=True)
        self.assertIn("no longer valid", resp.get_data(as_text=True))
        self.assertEqual(self.post_signup().headers["Location"], "/login")
        self.assertIsNotNone(self.user())

    def appmod_purge_now(self):
        with self.app.app_context():
            appmod.purge_unverified()

    def test_auth_pages_ignore_expired_accounts_even_before_cleanup(self):
        self.post_signup()
        self.backdate(21)
        self.assertIn("Invalid email or password", self.login().get_data(as_text=True))
        self.assertIsNone(self.user())

    def test_confirmed_accounts_are_never_deleted(self):
        self.signup(EMAIL)
        self.backdate(60 * 24)
        self.appmod_purge_now()
        self.assertIsNotNone(self.user())

    def test_signing_up_again_replaces_the_pending_account(self):
        self.post_signup(password="password1")
        first_link, first_id = self.last_link(EMAIL), self.user()["id"]
        self.age_emails()
        self.post_signup(password="password2")
        self.assertNotEqual(self.user()["id"], first_id)
        resp = self.client.get(first_link, follow_redirects=True)
        self.assertIn("no longer valid", resp.get_data(as_text=True))
        self.client.get(self.last_link(EMAIL))
        self.assertEqual(self.login(password="password2").headers["Location"], "/app")

    def test_confirmed_email_cannot_sign_up_again(self):
        self.signup(EMAIL)
        self.client.post("/logout")
        html = self.post_signup().get_data(as_text=True)
        self.assertIn("An account with this email already exists", html)

    def test_reset_and_confirmation_tokens_are_not_interchangeable(self):
        self.post_signup()
        verify_token = self.last_link(EMAIL).rsplit("/", 1)[1]
        resp = self.client.get(f"/reset/{verify_token}", follow_redirects=True)
        self.assertIn("not valid", resp.get_data(as_text=True))
        self.age_emails()
        self.client.post("/forgot", data={"email": EMAIL})
        reset_token = self.last_link(EMAIL, kind="reset").rsplit("/", 1)[1]
        resp = self.client.get(f"/verify/{reset_token}", follow_redirects=True)
        self.assertIn("not valid", resp.get_data(as_text=True))
        self.assertIsNone(self.user()["verified_at"])

    def test_password_reset_confirms_a_pending_account(self):
        self.post_signup()
        self.age_emails()
        self.client.post("/forgot", data={"email": EMAIL})
        self.assertEqual(self.outbox[-1]["subject"], "Reset your Nomad Life password")
        self.client.post(self.last_link(EMAIL, kind="reset"),
                         data={"password": "password9", "confirm": "password9"})
        self.assertIsNotNone(self.user()["verified_at"])
        self.assertEqual(self.login(password="password9").headers["Location"], "/app")

    def test_failed_email_leaves_no_account(self):
        self.send_email.side_effect = None
        self.send_email.return_value = False
        html = self.post_signup().get_data(as_text=True)
        self.assertIn("could not send the confirmation email", html)
        self.assertIsNone(self.user())

    def test_verify_path_is_private(self):
        self.assertIn("Disallow: /verify/", self.client.get("/robots.txt").get_data(as_text=True))


class MigrationTests(unittest.TestCase):
    def test_accounts_from_older_versions_are_confirmed(self):
        tmp = tempfile.mkdtemp(prefix="nomadlife-test-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "old.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT "
                     "NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT "
                     "CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO users (email, password_hash, created_at) VALUES "
                     "('old@example.com', 'x', '2025-01-01 10:00:00')")
        conn.commit()
        conn.close()
        for _ in range(2):  # the migration runs on every start
            appmod.create_app({"TESTING": True, "SECRET_KEY": "k", "DATABASE_PATH": path,
                               "UPLOAD_DIR": os.path.join(tmp, "uploads")})
        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT email, verified_at, email_sent_at FROM users").fetchall()
        conn.close()
        self.assertEqual(rows, [("old@example.com", "2025-01-01 10:00:00", None)])


class MessageTests(unittest.TestCase):
    def test_html_email_has_text_html_and_inline_logo(self):
        logo = os.path.join(appmod.BASE_DIR, "static", "img", "logo-mark.png")
        msg = mailer.build_message("me@gmail.com", "you@example.com", "Hi", "plain",
                                   f'<img src="cid:{mailer.LOGO_CID}">', logo)
        self.assertEqual(msg.get_content_type(), "multipart/alternative")
        text, related = msg.get_payload()
        self.assertEqual(text.get_content(), "plain\n")
        self.assertEqual(related.get_content_type(), "multipart/related")
        html, image = related.get_payload()
        self.assertIn(mailer.LOGO_CID, html.get_content())
        self.assertEqual(image["Content-ID"], f"<{mailer.LOGO_CID}>")
        self.assertEqual(image.get_content_type(), "image/png")
        self.assertTrue(msg["Message-ID"].endswith("@gmail.com>"))


if __name__ == "__main__":
    unittest.main()


class BugHuntTests(AppTestCase):
    """Regression tests for the bugs found after adding email confirmation."""

    def post_signup(self, email=EMAIL, password="password1"):
        return self.client.post("/signup", data={
            "email": email, "password": password, "confirm": password})

    def test_sign_up_cannot_flood_an_inbox(self):
        for _ in range(5):
            self.post_signup()
        self.assertEqual(len(self.outbox), 1)
        self.age_emails()
        self.post_signup()
        self.assertEqual(len(self.outbox), 2)

    def test_sign_in_before_confirming_cannot_flood_an_inbox(self):
        self.post_signup()
        for _ in range(5):
            html = self.client.post("/login", data={"email": EMAIL, "password": "password1"})
        self.assertEqual(len(self.outbox), 1)
        self.assertIn("a moment ago", html.get_data(as_text=True))

    def test_forgot_password_cannot_flood_an_inbox(self):
        self.signup(EMAIL)
        self.client.post("/logout")
        self.age_emails()
        sent = len(self.outbox)
        for _ in range(5):
            self.client.post("/forgot", data={"email": EMAIL})
        self.assertEqual(len(self.outbox), sent + 1)

    def test_cleanup_never_deletes_an_account_with_data(self):
        # For example an account made by a server still running the previous version after the
        # database was migrated: it has no confirmation date, but it has years and stays.
        self.signup(EMAIL)
        self.new_year(2026)
        with self.db() as conn:
            conn.execute("UPDATE users SET verified_at = NULL, created_at = '2026-01-01 10:00:00'")
        with self.app.app_context():
            appmod.purge_unverified()
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM years").fetchone()[0], 1)

    def test_one_minute_left_is_singular(self):
        self.post_signup()
        with self.db() as conn:
            conn.execute("UPDATE users SET created_at = datetime('now', '-19 minutes', "
                         "'-30 seconds'), email_sent_at = NULL")
        html = self.client.post("/login", data={"email": EMAIL, "password": "password1"})
        self.assertIn("it works for 1 more minute.", html.get_data(as_text=True))
        self.assertIn("within 1 minute:", self.outbox[-1]["text"])

    def test_old_link_after_signing_up_again_points_to_the_newest_email(self):
        self.post_signup(password="password1")
        old = self.last_link(EMAIL)
        self.age_emails()
        self.post_signup(password="password2")
        html = self.client.get(old, follow_redirects=True).get_data(as_text=True)
        self.assertIn("newest email", html)
