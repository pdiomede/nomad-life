"""Regression tests for the bugs found in the 1.2.10 review of the security features."""
import re
import time
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


def code_now(secret, offset=0):
    return appmod.totp_code(secret, int(time.time() // appmod.TOTP_STEP_SECONDS) + offset)


class TwoFactorFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.age_emails()
        self.secret = appmod.new_totp_secret()
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = ?", (self.secret,))

    def password_step(self, client):
        return client.post("/login", data={"email": "a@example.com", "password": "password1"})

    def test_typing_the_password_again_does_not_reset_wrong_codes(self):
        anon = self.app.test_client()
        statuses = []
        for _ in range(3):  # 3 rounds of password + 2 wrong codes = 6 wrong codes
            self.password_step(anon)
            statuses += [anon.post("/login/code", data={"code": "000000"}).status_code
                         for _ in range(2)]
        self.assertEqual(statuses[-1], 429)
        self.assertIn(429, statuses[:6])

    def test_a_right_code_clears_the_wrong_ones(self):
        anon = self.app.test_client()
        self.password_step(anon)
        for _ in range(4):
            anon.post("/login/code", data={"code": "000000"})
        self.assertEqual(anon.post("/login/code", data={"code": code_now(self.secret)})
                         .status_code, 302)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM auth_events WHERE kind = 'code' AND key LIKE 'email:%'")
                             .fetchone()[0], 0)

    def test_full_width_digits_work_and_other_digits_do_not_crash(self):
        anon = self.app.test_client()
        self.password_step(anon)
        wide = code_now(self.secret).translate(str.maketrans("0123456789", "０１２３４５６７８９"))
        self.assertEqual(anon.post("/login/code", data={"code": wide}).status_code, 302)
        self.assertIsNone(appmod.totp_match(self.secret, "١٢٣٤٥٦"))  # no TypeError

    def test_turning_off_limits_wrong_codes(self):
        statuses = [self.client.post("/account", data={
            "action": "2fa_disable", "code": "000000", "current_password": "password1"})
            .status_code for _ in range(7)]
        html = self.client.get("/account").get_data(as_text=True)
        self.assertIn("Too many wrong codes for this account.", html)
        with self.db() as conn:
            self.assertIsNotNone(conn.execute("SELECT totp_secret FROM users").fetchone()[0])
            self.assertGreaterEqual(conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE event = 'code_failed'").fetchone()[0], 5)
        self.assertEqual(statuses, [302] * 7)

    def test_cli_refuses_an_account_without_two_factor(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = NULL")
        sent = len(self.outbox)
        result = self.app.test_cli_runner().invoke(args=["reset-2fa", "a@example.com"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not on", result.output)
        self.assertEqual(len(self.outbox), sent)


class SetupSecretTests(AppTestCase):
    def test_setup_key_is_never_shown_to_the_next_account(self):
        self.signup()
        first = re.search(r'tfa-key">(\w+)<', self.client.get("/account").get_data(as_text=True))
        self.client.post("/logout")
        with self.client.session_transaction() as sess:
            self.assertNotIn("totp_setup", sess)
        self.signup("b@example.com")
        second = re.search(r'tfa-key">(\w+)<', self.client.get("/account").get_data(as_text=True))
        self.assertNotEqual(first.group(1), second.group(1))

    def test_a_setup_key_of_another_account_is_refused(self):
        self.signup()
        html = self.client.get("/account").get_data(as_text=True)
        secret = re.search(r'tfa-key">(\w+)<', html).group(1)
        with self.client.session_transaction() as sess:
            sess["totp_setup"] = {"uid": 999, "secret": secret}
        html = self.client.post("/account", data={
            "action": "2fa_enable", "code": code_now(secret), "current_password": "password1"},
            follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("Two-factor sign in is on.", html)


class SessionFixTests(AppTestCase):
    def test_a_dead_remember_cookie_does_not_come_back(self):
        self.signup()
        self.client.post("/logout")
        kiosk = self.app.test_client()
        kiosk.post("/login", data={"email": "a@example.com", "password": "password1",
                                   "remember": "1"})
        # Signed out from elsewhere: the remember cookie stays in the kiosk, but is dead.
        other = self.app.test_client()
        other.post("/login", data={"email": "a@example.com", "password": "password1"})
        other.post("/account", data={"action": "sessions"})
        self.assertEqual(kiosk.get("/account").status_code, 302)
        # Someone signs in on the kiosk without "Remember me", then signs out elsewhere.
        kiosk.post("/login", data={"email": "a@example.com", "password": "password1"})
        kiosk.post("/account", data={"action": "sessions"})
        remember = kiosk.get_cookie("nomadlife_remember")
        closed = self.app.test_client()  # the browser is closed: only lasting cookies remain
        if remember is not None:
            closed.set_cookie("nomadlife_remember", remember.value)
        self.assertEqual(closed.get("/account").status_code, 302)

    def test_remembered_browsers_stay_remembered_after_a_password_change(self):
        self.signup()
        self.client.post("/logout")
        self.client.post("/login", data={"email": "a@example.com", "password": "password1",
                                         "remember": "1"})
        self.client.post("/account", data={"action": "password", "current_password": "password1",
                                           "password": "newpass123", "confirm": "newpass123"})
        closed = self.app.test_client()
        closed.set_cookie("nomadlife_remember", self.client.get_cookie("nomadlife_remember").value)
        self.assertEqual(closed.get("/account").status_code, 200)

    def test_an_idle_session_cannot_revive_itself(self):
        self.signup()
        with self.db() as conn:
            conn.execute("UPDATE user_sessions SET last_seen_at = datetime('now', '-40 days')")
        self.assertEqual(self.client.get("/account").status_code, 302)

    def test_admin_disable_forgets_the_sessions(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("user@example.com", client=self.app.test_client())
        self.signup("admin@example.com")
        self.client.post("/admin/users/1", data={"action": "disable"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id = 1")
                             .fetchone()[0], 0)


class AlertFixTests(AppTestCase):
    def test_email_change_alert_can_undo_the_change(self):
        self.signup()
        self.age_emails()
        self.client.post("/account", data={"action": "email", "email": "thief@example.com",
                                           "current_password": "password1"})
        self.client.get(self.last_link("thief@example.com", kind="account/email"))
        alert = self.outbox[-1]
        self.assertEqual(alert["to"], "a@example.com")
        self.assertIn("Account: thief@example.com", alert["text"])
        self.assertNotIn("/forgot", alert["text"])
        undo = self.last_link("a@example.com", kind="account/email/undo")
        owner = self.app.test_client()
        self.assertIn(b"Undo the change", owner.get(undo).data)  # a GET changes nothing
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT email FROM users").fetchone()[0],
                             "thief@example.com")
        html = owner.post(undo, follow_redirects=True).get_data(as_text=True)
        self.assertIn("The account uses a@example.com again", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT email FROM users").fetchone()[0], "a@example.com")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0], 0)
        self.assertEqual(self.client.get("/account").status_code, 302)  # the thief is out
        self.assertEqual(self.outbox[-1]["to"], "a@example.com")
        self.assertIn("/reset/", self.outbox[-1]["text"])
        self.assertIn(b"no longer valid", owner.post(undo, follow_redirects=True).data)

    def test_finishing_a_sign_up_is_not_a_password_reset(self):
        self.client.post("/signup", data={"email": "new@example.com", "password": "password1",
                                          "confirm": "password1"})
        self.age_emails()
        self.app.test_client().post("/signup", data={"email": "new@example.com",
                                                     "password": "password2",
                                                     "confirm": "password2"})
        link = self.last_link("new@example.com", kind="reset")
        self.client.post(link, data={"password": "password3", "confirm": "password3"})
        self.assertFalse(any("Security alert" in m["subject"] for m in self.outbox))
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT event FROM audit_log").fetchall()[-1][0],
                             "account_confirmed")

    def test_breach_count_grammar(self):
        with self.app.test_request_context(), \
                mock.patch.object(appmod, "breach_count", return_value=1):
            self.assertIn("appears 1 time in", appmod.password_problem("longenough", "longenough"))


class ReviewTwoTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def test_pdf_header_anywhere_in_the_first_kilobyte(self):
        self.upload("/year/2026/base", "late.pdf", b"x" * 1023 + b"%PDF-1.7\n")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)

    def test_an_image_under_the_wrong_image_extension_is_kept_as_what_it_is(self):
        webp = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
        self.upload("/year/2026/base", "photo.jpg", webp)
        with self.db() as conn:
            row = conn.execute("SELECT original_name, stored_name, mime FROM documents").fetchone()
        self.assertEqual(row[0], "photo.webp")
        self.assertTrue(row[1].endswith(".webp"))
        self.assertEqual(row[2], "image/webp")
        html = self.upload("/year/2026/base", "page.jpg", b"<html>").get_data(as_text=True)
        self.assertIn("page.jpg is not a real JPG file", html + self.client.get(
            "/year/2026/base").get_data(as_text=True))

    def test_admin_history_filter_follows_the_account_across_email_changes(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.age_emails()
        self.client.post("/account", data={"action": "email", "email": "new@example.com",
                                           "current_password": "password1"})
        self.client.get(self.last_link("new@example.com", kind="account/email"))
        admin = self.signup("admin@example.com", client=self.app.test_client())
        html = admin.get("/admin?q=new@example.com").get_data(as_text=True)
        listed = html.split('id="activity"', 1)[1]
        self.assertIn("Account confirmed", listed)  # recorded under a@example.com
        self.assertIn("Email address changed", listed)

    def test_admin_actions_keep_the_activity_filter(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        admin = self.signup("admin@example.com", client=self.app.test_client())
        html = admin.get("/admin?q=a@example.com").get_data(as_text=True)
        self.assertIn('name="q" value="a@example.com"', html)
        resp = admin.post("/admin/users/1", data={"action": "plan", "plan": "pro", "page": "1",
                                                  "q": "a@example.com"})
        self.assertEqual(resp.headers["Location"], "/admin?q=a@example.com")
