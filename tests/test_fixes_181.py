"""Fixes of 1.8.1: hCaptcha and the honeypot on Sign up and Forgot password."""
import io
import json
import os
import unittest
import urllib.error
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase

SITEKEY = "10000000-ffff-ffff-ffff-000000000001"
SECRET = "0x0000000000000000000000000000000000000000"
FORM = {"email": "new@example.com", "password": "password1", "confirm": "password1"}


def http_error(code):
    return urllib.error.HTTPError(appmod.HCAPTCHA_VERIFY_URL, code, "x", {}, io.BytesIO(b""))


class VerifyAnswerTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config.update(HCAPTCHA_SITEKEY=SITEKEY, HCAPTCHA_SECRET=SECRET)

    def test_a_refusal_by_status_does_not_let_the_form_through(self):
        # A bot posting made up answers can make hCaptcha answer 429: that must fail, not pass.
        with self.app.test_request_context("/signup", method="POST"):
            for code, expected in ((429, False), (400, False), (403, False), (503, None)):
                with mock.patch.object(appmod.urllib.request, "urlopen",
                                       side_effect=http_error(code)):
                    self.assertIs(appmod.hcaptcha_verify("tok"), expected, code)

    def test_429_refuses_the_sign_up(self):
        with mock.patch.object(appmod.urllib.request, "urlopen", side_effect=http_error(429)):
            resp = self.client.post("/signup", data={**FORM, "h-captcha-response": "tok"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.outbox, [])

    def test_wrong_keys_are_logged_as_errors(self):
        answer = io.BytesIO(json.dumps({"success": False,
                                        "error-codes": ["invalid-input-secret"]}).encode())
        with self.app.test_request_context("/signup", method="POST"):
            with mock.patch.object(appmod.urllib.request, "urlopen", return_value=answer):
                with self.assertLogs(self.app.logger, "ERROR") as logs:
                    self.assertIs(appmod.hcaptcha_verify("tok"), False)
        self.assertIn("HCAPTCHA_SECRET", logs.output[0])


class LogTests(AppTestCase):
    def test_refusals_reach_the_log(self):
        # The app logs from WARNING up: refusals logged as info never appeared.
        with self.assertLogs(self.app.logger, "WARNING") as logs:
            self.client.post("/signup", data={**FORM, appmod.HONEYPOT_FIELD: "x"})
            self.client.post("/signup", data={**FORM, "email": "a.b.c.d@gmail.com"})
            self.client.post("/forgot", data={"email": "a@example.com",
                                              appmod.HONEYPOT_FIELD: "x"})
        self.assertEqual(len(logs.output), 3)


class FormTests(AppTestCase):
    def test_honeypot_is_not_a_field_password_managers_fill(self):
        page = self.client.get("/signup").get_data(as_text=True)
        field = page.split('name="hp_check"', 1)[1].split(">", 1)[0]
        self.assertEqual(appmod.HONEYPOT_FIELD, "hp_check")
        self.assertNotIn('name="website"', page)
        for attr in ("data-1p-ignore", 'data-lpignore="true"', "data-bwignore"):
            self.assertIn(attr, field)

    def test_forms_ignore_a_second_submit(self):
        for path in ("/signup", "/forgot"):
            page = self.client.get(path).get_data(as_text=True)
            self.assertIn('<form method="post" class="stack" data-submit-once>', page, path)

    def test_forgot_keeps_the_email_after_a_failed_check(self):
        self.app.config.update(HCAPTCHA_SITEKEY=SITEKEY, HCAPTCHA_SECRET=SECRET)
        resp = self.client.post("/forgot", data={"email": "kept@example.com"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b'value="kept@example.com"', resp.data)

    def test_widget_follows_theme_changes(self):
        with open(os.path.join(self.app.static_folder, "js", "captcha.js")) as fh:
            js = fh.read()
        self.assertIn("render=explicit&onload=nlCaptchaReady", js)
        self.assertIn('attributeFilter: ["data-theme"]', js)
        self.assertIn("api.getResponse(widget)", js)  # a finished check is never drawn again


class OwnAppTests(unittest.TestCase):
    def test_tests_with_their_own_app_blank_the_keys(self):
        # config.env may hold real keys: a test app that posts /signup must turn the captcha off.
        root = os.path.dirname(os.path.abspath(__file__))
        for name in ("test_verify_button.py", "test_fixes.py"):
            with open(os.path.join(root, name)) as fh:
                self.assertIn('"HCAPTCHA_SITEKEY": "", "HCAPTCHA_SECRET": ""', fh.read(), name)


if __name__ == "__main__":
    unittest.main()
