"""hCaptcha and the honeypot field on Sign up and Forgot password: bots are refused before any
account or email exists."""
import io
import json
import os
import unittest
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase

SITEKEY = "10000000-ffff-ffff-ffff-000000000001"
SECRET = "0x0000000000000000000000000000000000000000"
FORM = {"email": "bot@example.com", "password": "password1", "confirm": "password1"}


class CaptchaOffTests(AppTestCase):
    def test_off_by_default(self):
        resp = self.client.get("/signup")
        self.assertNotIn(b"h-captcha", resp.data)
        self.assertNotIn("hcaptcha", resp.headers["Content-Security-Policy"])
        # The honeypot is there even without the captcha.
        self.assertIn(b'name="website"', resp.data)
        self.client.post("/signup", data=FORM)
        self.assertEqual(len(self.outbox), 1)

    def test_honeypot_on_signup(self):
        resp = self.client.post("/signup", data={**FORM, "website": "http://spam.example"},
                                follow_redirects=True)
        self.assertIn(b"We sent an email to bot@example.com", resp.data)
        self.assertEqual(self.outbox, [])
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM users WHERE email = ?",
                                           (FORM["email"],)).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM auth_events").fetchone()[0], 0)

    def test_honeypot_on_forgot(self):
        self.signup()
        self.age_emails()
        self.outbox.clear()
        resp = self.app.test_client().post("/forgot", data={"email": "a@example.com",
                                                             "website": "x"},
                                           follow_redirects=True)
        self.assertIn(b"a reset link is on its way", resp.data)
        self.assertEqual(self.outbox, [])

    def test_honeypot_is_off_screen(self):
        with open(os.path.join(self.app.static_folder, "css", "style.css")) as fh:
            css = fh.read()
        self.assertIn(".hp-field { position: absolute; left: -10000px;", css)

    def test_settings_need_both_keys(self):
        for env in ({"HCAPTCHA_SITEKEY": SITEKEY, "HCAPTCHA_SECRET": ""},
                    {"HCAPTCHA_SITEKEY": "", "HCAPTCHA_SECRET": SECRET}):
            with mock.patch.dict(os.environ, env):
                with self.assertRaises(SystemExit):
                    appmod.hcaptcha_settings()
        with mock.patch.dict(os.environ, {"HCAPTCHA_SITEKEY": "", "HCAPTCHA_SECRET": " "}):
            self.assertEqual(appmod.hcaptcha_settings(), ("", ""))
        with mock.patch.dict(os.environ, {"HCAPTCHA_SITEKEY": SITEKEY, "HCAPTCHA_SECRET": SECRET}):
            self.assertEqual(appmod.hcaptcha_settings(), (SITEKEY, SECRET))


class CaptchaOnTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config.update(HCAPTCHA_SITEKEY=SITEKEY, HCAPTCHA_SECRET=SECRET)
        patcher = mock.patch.object(appmod, "hcaptcha_verify", return_value=True)
        self.verify = patcher.start()
        self.addCleanup(patcher.stop)

    def users(self):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def test_pages_show_the_widget_and_allow_hcaptcha(self):
        for path in ("/signup", "/forgot"):
            resp = self.client.get(path)
            self.assertIn(f'data-sitekey="{SITEKEY}"'.encode(), resp.data)
            self.assertIn(b"js/captcha.js", resp.data)
            csp = resp.headers["Content-Security-Policy"]
            self.assertIn("script-src 'self' https://hcaptcha.com https://*.hcaptcha.com", csp)
            self.assertIn("frame-src https://hcaptcha.com https://*.hcaptcha.com", csp)
            self.assertIn("connect-src 'self' https://hcaptcha.com", csp)
        for path in ("/login", "/"):
            self.assertEqual(self.client.get(path).headers["Content-Security-Policy"],
                             appmod.CSP)

    def test_missing_answer_is_refused_without_asking_hcaptcha(self):
        resp = self.client.post("/signup", data=FORM)
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"not a robot", resp.data)
        self.assertIn(b"h-captcha", resp.data)
        self.assertIn("hcaptcha", resp.headers["Content-Security-Policy"])
        self.verify.assert_not_called()
        self.assertEqual(self.users(), 0)
        self.assertEqual(self.outbox, [])
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM auth_events").fetchone()[0], 0)

    def test_wrong_answer_is_refused(self):
        self.verify.return_value = False
        resp = self.client.post("/signup", data={**FORM, "h-captcha-response": "bad"})
        self.assertEqual(resp.status_code, 400)
        self.verify.assert_called_once_with("bad")
        self.assertEqual(self.users(), 0)
        self.assertEqual(self.outbox, [])

    def test_right_answer_signs_up(self):
        self.client.post("/signup", data={**FORM, "h-captcha-response": "good"})
        self.assertEqual(self.users(), 1)
        self.assertEqual(len(self.outbox), 1)

    def test_unreachable_hcaptcha_lets_the_form_through(self):
        self.verify.return_value = None
        self.client.post("/signup", data={**FORM, "h-captcha-response": "good"})
        self.assertEqual(self.users(), 1)

    def test_bad_email_is_reported_before_the_captcha(self):
        resp = self.client.post("/signup", data={**FORM, "email": "nope"})
        self.assertIn(b"valid email", resp.data)
        self.verify.assert_not_called()

    def test_forgot(self):
        self.app.config.update(HCAPTCHA_SITEKEY="", HCAPTCHA_SECRET="")
        self.signup()
        self.app.config.update(HCAPTCHA_SITEKEY=SITEKEY, HCAPTCHA_SECRET=SECRET)
        self.age_emails()
        self.outbox.clear()
        other = self.app.test_client()
        resp = other.post("/forgot", data={"email": "a@example.com"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"not a robot", resp.data)
        self.assertEqual(self.outbox, [])
        other.post("/forgot", data={"email": "a@example.com", "h-captcha-response": "good"})
        self.assertEqual(len(self.outbox), 1)


class VerifyTests(AppTestCase):
    """hcaptcha_verify itself, with a fake hCaptcha answer."""
    def setUp(self):
        super().setUp()
        self.app.config.update(HCAPTCHA_SITEKEY=SITEKEY, HCAPTCHA_SECRET=SECRET)

    def answer(self, payload):
        return mock.patch.object(appmod.urllib.request, "urlopen",
                                 return_value=io.BytesIO(json.dumps(payload).encode()))

    def test_answers(self):
        with self.app.test_request_context("/signup", method="POST"):
            with self.answer({"success": True}) as urlopen:
                self.assertIs(appmod.hcaptcha_verify("tok"), True)
            sent = urlopen.call_args[0][0]
            self.assertEqual(sent.full_url, appmod.HCAPTCHA_VERIFY_URL)
            self.assertIn(b"secret=" + SECRET.encode(), sent.data)
            self.assertIn(b"response=tok", sent.data)
            with self.answer({"success": False, "error-codes": ["invalid-input-response"]}):
                self.assertIs(appmod.hcaptcha_verify("tok"), False)
            with self.answer({"success": "yes"}):
                self.assertIs(appmod.hcaptcha_verify("tok"), False)
            with mock.patch.object(appmod.urllib.request, "urlopen", side_effect=OSError("down")):
                self.assertIsNone(appmod.hcaptcha_verify("tok"))


if __name__ == "__main__":
    unittest.main()
