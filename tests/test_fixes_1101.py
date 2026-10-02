"""Fixes of 1.10.1: hCaptcha checks and the IP location links."""
import io
import json
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs

import app as appmod
from tests.helpers import AppTestCase

SITEKEY = "10000000-ffff-ffff-ffff-000000000001"
SECRET = "0x0000000000000000000000000000000000000000"


class VerifyTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config.update(HCAPTCHA_SITEKEY=SITEKEY, HCAPTCHA_SECRET=SECRET)

    def sent(self, ip):
        answer = io.BytesIO(json.dumps({"success": True}).encode())
        with self.app.test_request_context("/signup", method="POST"):
            with mock.patch.object(appmod, "client_ip", return_value=ip), \
                 mock.patch.object(appmod.urllib.request, "urlopen",
                                   return_value=answer) as urlopen:
                self.assertIs(appmod.hcaptcha_verify("tok"), True)
        return parse_qs(urlopen.call_args[0][0].data.decode())

    def test_remoteip_only_when_the_address_is_known(self):
        # hCaptcha answers invalid-remoteip, even for a right answer, to remoteip=unknown.
        self.assertNotIn("remoteip", self.sent("unknown"))
        self.assertEqual(self.sent("8.8.8.8")["remoteip"], ["8.8.8.8"])
        self.assertEqual(self.sent("2001:DB8::1")["remoteip"], ["2001:db8::1"])

    def test_test_site_key_with_a_real_secret_is_a_config_error(self):
        answer = io.BytesIO(json.dumps({"success": False,
                                        "error-codes": ["not-using-dummy-secret"]}).encode())
        with self.app.test_request_context("/signup", method="POST"):
            with mock.patch.object(appmod.urllib.request, "urlopen", return_value=answer):
                with self.assertLogs(self.app.logger, "ERROR"):
                    self.assertIs(appmod.hcaptcha_verify("tok"), False)


class ScriptTests(unittest.TestCase):
    def test_a_blocked_hcaptcha_script_is_reported(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "static", "js", "captcha.js")) as fh:
            js = fh.read()
        self.assertIn("script.onerror = function", js)
        self.assertIn("could not load", js)
        with open(os.path.join(root, "static", "css", "style.css")) as fh:
            self.assertIn(".captcha-failed { color: var(--error-text);", fh.read())


class IpLinkTests(AppTestCase):
    def test_copying_the_link_gives_only_the_address(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        admin = self.app.test_client()
        self.signup("admin@example.com", client=admin)
        with self.db() as conn:
            conn.execute("INSERT INTO audit_log (event, email, ip) "
                         "VALUES ('login_failed', 'x@example.com', '8.8.8.8')")
        html = admin.get("/admin").get_data(as_text=True)
        self.assertIn('aria-label="8.8.8.8, location on ipinfo.io, opens in a new tab" '
                      'title="Location on ipinfo.io (opens in a new tab)">8.8.8.8</a>', html)
        self.assertNotIn("(location on ipinfo.io, opens in a new tab)</span>", html)


if __name__ == "__main__":
    unittest.main()
