"""Fixes of 1.11.1, from a review of two-factor sign in (authenticator app and passkeys)."""
import base64
import json
import os
import re
import time
import zlib

import app as appmod
from tests.helpers import AppTestCase
from tests.test_passkeys import PasskeyCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def code_now(secret):
    return appmod.totp_code(secret, int(time.time() // appmod.TOTP_STEP_SECONDS))


def cookie_payload(client):
    """What anyone holding the session cookie can read, without the server's key."""
    value = client.get_cookie("nomadlife_session").value
    compressed = value.startswith(".")
    data = value.lstrip(".").split(".")[0]
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    return json.loads(zlib.decompress(raw) if compressed else raw)


class SetupKeyTests(AppTestCase):
    def setup_key(self, client=None):
        html = (client or self.client).get("/settings").get_data(as_text=True)
        return re.search(r'<code class="tfa-key">([\w ]+)</code>', html).group(1).replace(" ", "")

    def test_the_setup_key_is_not_in_the_cookie(self):
        self.signup()
        secret = self.setup_key()
        self.assertNotIn(secret, json.dumps(cookie_payload(self.client)))
        self.assertNotIn("totp_setup", cookie_payload(self.client))
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT totp_setup FROM user_sessions").fetchone()[0],
                             secret)
        self.assertEqual(self.setup_key(), secret)  # the same key while setting up

    def test_one_key_per_browser_gone_after_use_and_at_sign_out(self):
        self.signup()
        other = self.app.test_client()
        other.post("/login", data={"email": "a@example.com", "password": "password1"})
        secret = self.setup_key()
        self.assertNotEqual(self.setup_key(other), secret)  # someone else's browser
        self.client.post("/settings", data={"action": "2fa_enable", "code": code_now(secret),
                                            "current_password": "password1"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT totp_secret FROM users").fetchone()[0], secret)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions WHERE totp_setup "
                                          "IS NOT NULL").fetchone()[0], 0)

    def test_a_key_kept_in_an_old_cookie_counts_for_nothing(self):
        self.signup()
        self.setup_key()
        planted = "JBSWY3DPEHPK3PXP"
        with self.client.session_transaction() as s:
            s["totp_setup"] = {"uid": 1, "secret": planted}  # as pages before 1.11.1 kept it
        self.client.post("/settings", data={"action": "2fa_enable", "code": code_now(planted),
                                            "current_password": "password1"})
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT totp_secret FROM users").fetchone()[0])
        self.assertNotIn("totp_setup", cookie_payload(self.client))


class CodeFieldTests(AppTestCase):
    def test_code_fields_take_full_width_digits(self):
        # The server accepts them (1.2.10); the field's pattern made the browser refuse them.
        for name in ("templates/auth/login_code.html", "templates/settings.html"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn('autocomplete="one-time-code"', text)
            self.assertNotIn('pattern="[0-9 ]*"', text, name)
        secret = "JBSWY3DPEHPK3PXP"
        wide = code_now(secret).translate(str.maketrans("0123456789", "０１２３４５６７８９"))
        self.assertIsNotNone(appmod.totp_match(secret, wide))


class WordingTests(PasskeyCase):
    def test_adding_an_app_next_to_passkeys(self):
        self.add_passkey()
        html = self.client.get("/settings").get_data(as_text=True)
        secret = re.search(r'<code class="tfa-key">([\w ]+)</code>', html).group(1).replace(" ", "")
        html = self.client.post("/settings", data={"action": "2fa_enable",
                                                   "code": code_now(secret),
                                                   "current_password": "password1"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("The authenticator app was added", html)
        self.assertNotIn("Two-factor sign in is on.", html)
        self.assertIn("An authenticator app was added", self.outbox[-1]["text"])
        self.assertNotIn("was turned on", self.outbox[-1]["text"])


class ChallengeTests(PasskeyCase):
    def test_asking_again_keeps_one_challenge(self):
        self.add_passkey()
        client = self.password_step()
        for _ in range(20):
            client.post("/login/passkey/options")
            self.options()
        with self.db() as conn:
            rows = conn.execute("SELECT kind, COUNT(*) FROM passkey_challenges "
                                "GROUP BY kind ORDER BY kind").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("register", 1), ("sign_in", 1)])

    def test_the_newest_prompt_still_works(self):
        auth, _ = self.add_passkey()
        client = self.password_step()
        client.post("/login/passkey/options")
        resp, _ = self.passkey_step(client, auth)  # asks again, then signs
        self.assertTrue(self.signed_in(client))
