"""Regression tests for the 1.2.6 app fixes."""
import io

import app as appmod
from tests.helpers import AppTestCase

EMAIL = "owner@example.com"


class PendingAccountTests(AppTestCase):
    def post_signup(self, password, client=None):
        return (client or self.client).post("/signup", data={
            "email": EMAIL, "password": password, "confirm": password})

    def test_signing_in_on_a_retried_pending_sign_up_sends_no_confirmation_link(self):
        stranger = self.app.test_client()
        self.post_signup("stranger1", client=stranger)
        self.age_emails()
        self.post_signup("owner1234")  # the owner: gets the choose-a-password link
        self.age_emails()
        before = len(self.outbox)
        html = stranger.post("/login", data={"email": EMAIL, "password": "stranger1"}).data.decode()
        self.assertIn("choose your password", html)
        self.assertEqual(len(self.outbox), before)  # no new confirmation link was minted

    def test_disabled_pending_accounts_stay_disabled(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.post_signup("password1")
        admin = self.signup("admin@example.com", client=self.app.test_client())
        with self.db() as conn:
            uid = conn.execute("SELECT id FROM users WHERE email = ?", (EMAIL,)).fetchone()[0]
        link = self.last_link(EMAIL)
        admin.post(f"/admin/users/{uid}", data={"action": "disable"})
        self.age_emails()
        before = len(self.outbox)
        html = self.client.post("/login", data={"email": EMAIL, "password": "password1"}).data.decode()
        self.assertIn("This account is disabled", html)
        self.assertEqual(len(self.outbox), before)
        self.assertIn(b"account is disabled", self.client.get(link, follow_redirects=True).data)
        with self.db() as conn:  # the purge keeps it, so signing up again cannot undo it
            conn.execute("UPDATE users SET created_at = datetime('now', '-30 minutes') WHERE id = ?",
                         (uid,))
        with self.app.test_request_context():
            appmod.purge_unverified()
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT disabled FROM users WHERE id = ?", (uid,))
                             .fetchone()[0], 1)


class RequestSizeTests(AppTestCase):
    def test_free_cap_applies_before_the_csrf_check_reads_the_form(self):
        self.signup()
        self.new_year(2026)
        self.app.config["WTF_CSRF_ENABLED"] = True
        resp = self.client.post("/year/2026/base", data={
            "action": "upload", "kind": "other", "csrf_token": "x",
            "file": (io.BytesIO(b"%PDF" + b"x" * (15 * appmod.MB)), "big.pdf")},
            content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"This form was too large to save. Receipts can be at most 10 MB each.",
                      resp.data)


class UiFixTests(AppTestCase):
    def read(self, *parts):
        import os
        return open(os.path.join(appmod.BASE_DIR, *parts), encoding="utf-8").read()

    def test_email_addresses_can_wrap_on_phones(self):
        for name in ("verify", "reset", "finish_signup", "change_email"):
            html = self.read("templates", "email", f"{name}.html")
            self.assertIn('<strong style="word-break:break-all;">{{ email }}</strong>', html, name)

    def test_zoom_limits_keep_the_focus_on_the_pressed_button(self):
        js = self.read("static", "js", "theme.js")
        self.assertIn('setAttribute("aria-disabled"', js)
        self.assertNotIn(".disabled = z", js)

    def test_layout_rules(self):
        css = self.read("static", "css", "style.css")
        self.assertIn(".plan-card .btn-block { white-space: normal;", css)
        self.assertIn(".table td.num .small { overflow-wrap: normal; }", css)

    def test_landing_does_not_claim_the_app_runs_on_your_computer(self):
        self.assertNotIn(b"Runs on your own computer", self.client.get("/").data)
