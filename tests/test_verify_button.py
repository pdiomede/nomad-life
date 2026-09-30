"""Email confirmation: opening the link shows a button, only pressing it confirms, so company
mail scanners that open every link cannot confirm accounts a bot signed up."""
import os
import tempfile

import app as appmod
from tests.helpers import AppTestCase

EMAIL = "new@example.com"


class VerifyButtonTests(AppTestCase):
    def sign_up(self, client=None):
        (client or self.client).post("/signup", data={"email": EMAIL, "password": "password1",
                                                      "confirm": "password1"})
        return self.last_link(EMAIL)

    def verified(self):
        with self.db() as conn:
            row = conn.execute("SELECT verified_at FROM users WHERE email = ?", (EMAIL,)).fetchone()
        return row and row[0] is not None

    def test_opening_the_link_does_not_confirm(self):
        link = self.sign_up()
        scanner = self.app.test_client()  # another machine, no session
        for _ in range(3):
            page = scanner.get(link)
            self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Confirm my account", html)
        self.assertIn(EMAIL, html)
        self.assertIn('method="post"', html)
        self.assertFalse(self.verified())
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'account_confirmed'").fetchone()[0], 0)

    def test_pressing_the_button_confirms(self):
        link = self.sign_up()
        resp = self.client.post(link)
        self.assertEqual(resp.headers["Location"], "/login")
        self.assertTrue(self.verified())
        self.assertIn("already confirmed", self.client.get(link, follow_redirects=True)
                      .get_data(as_text=True))

    def test_an_account_only_opened_is_removed_after_the_wait(self):
        link = self.sign_up()
        self.app.test_client().get(link)
        with self.db() as conn:
            conn.execute("UPDATE users SET created_at = datetime('now', '-30 minutes')")
        # Opening it again later (the scanner, or the owner) runs the purge first.
        self.app.test_client().get(link)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_bad_links_answer_the_same_on_both_methods(self):
        link = self.sign_up()
        for method in ("get", "post"):
            resp = getattr(self.client, method)("/verify/not-a-token", follow_redirects=True)
            self.assertIn("This confirmation link is not valid.", resp.get_data(as_text=True))
        with self.db() as conn:
            conn.execute("UPDATE users SET disabled = 1")
        for method in ("get", "post"):
            resp = getattr(self.client, method)(link, follow_redirects=True)
            self.assertIn("This account is disabled", resp.get_data(as_text=True))
        self.assertFalse(self.verified())

    def test_the_button_needs_the_form_token(self):
        tmp = tempfile.mkdtemp()
        strict = appmod.create_app({
            "TESTING": True, "DATABASE_PATH": os.path.join(tmp, "t.db"),
            "UPLOAD_DIR": os.path.join(tmp, "u"), "GMAIL_USER": "", "GMAIL_APP_PASSWORD": "",
            "PWNED_CHECK": False})  # CSRF on, as in production
        client = strict.test_client()
        page = client.get("/signup").get_data(as_text=True)
        token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        client.post("/signup", data={"email": EMAIL, "password": "password1",
                                     "confirm": "password1", "csrf_token": token})
        with strict.app_context():
            row = appmod.db.query("SELECT * FROM users WHERE email = ?", (EMAIL,), one=True)
            with strict.test_request_context():
                link = appmod.url_for("verify", token=appmod.serializer("email-verify").dumps(
                    {"uid": row["id"], "h": appmod.password_fingerprint(row["password_hash"]),
                     "v": row["session_version"], "c": row["created_at"]}))
        visitor = strict.test_client()
        page = visitor.get(link).get_data(as_text=True)
        form_token = page.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        visitor.post(link)  # a forged or replayed press without the page's token
        with strict.app_context():
            self.assertIsNone(appmod.db.query("SELECT verified_at FROM users", one=True)[0])
        self.assertEqual(visitor.post(link, data={"csrf_token": form_token}).headers["Location"],
                         "/login")
        with strict.app_context():
            self.assertIsNotNone(appmod.db.query("SELECT verified_at FROM users", one=True)[0])
