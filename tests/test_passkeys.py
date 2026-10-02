"""Passkeys as a second sign in step: adding, renaming and removing them on the Settings page,
signing in with one, and every place two-factor sign in is checked or cleared. A software
authenticator (tests/soft_authenticator.py) makes real signatures, so py_webauthn's checks run."""
import json
import os
import sqlite3
from unittest import mock

from click.testing import CliRunner

import app as appmod
from tests.helpers import AppTestCase
from tests.soft_authenticator import SoftAuthenticator

BASE = "https://nomad.example.com"
EMAIL, PASSWORD = "a@example.com", "password1"


class PasskeyCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["APP_BASE_URL"] = BASE
        self.signup()

    def keys(self):
        with self.db() as conn:
            return conn.execute("SELECT * FROM passkeys ORDER BY id").fetchall()

    def events(self, event):
        with self.db() as conn:
            return conn.execute("SELECT detail FROM audit_log WHERE event = ? ORDER BY id",
                                (event,)).fetchall()

    def options(self, client=None, password=PASSWORD):
        return (client or self.client).post("/settings/passkeys/options",
                                            data={"current_password": password})

    def add_passkey(self, auth=None, name="My Mac", client=None, origin=BASE):
        client = client or self.client
        auth = auth or SoftAuthenticator()
        options = self.options(client).get_json()
        credential = auth.register(options, origin)
        resp = client.post("/settings", data={"action": "passkey_add", "name": name,
                                              "current_password": PASSWORD,
                                              "credential": json.dumps(credential)})
        return auth, resp

    def password_step(self, client=None, email=EMAIL):
        client = client or self.app.test_client()
        resp = client.post("/login", data={"email": email, "password": PASSWORD})
        self.assertTrue(resp.headers["Location"].endswith("/login/code"), resp.headers)
        return client

    def passkey_step(self, client, auth, origin=BASE):
        options = client.post("/login/passkey/options").get_json()
        credential = auth.sign(options, origin)
        return client.post("/login/passkey", data={"credential": json.dumps(credential)}), \
            credential

    def signed_in(self, client):
        return client.get("/settings").status_code == 200


class AddPasskeyTests(PasskeyCase):
    def test_options_need_the_password(self):
        resp = self.options(password="wrong")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "Your current password is not correct.")
        options = self.options().get_json()
        self.assertEqual(options["rp"]["id"], "nomad.example.com")
        self.assertEqual(options["user"]["name"], EMAIL)
        self.assertEqual(options["excludeCredentials"], [])

    def test_adding_a_passkey(self):
        auth, resp = self.add_passkey(name="  My  Mac ")
        self.assertEqual(resp.status_code, 302)
        keys = self.keys()
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["credential_id"], auth.id)
        self.assertEqual(keys[0]["name"], "My Mac")
        self.assertEqual(json.loads(keys[0]["transports"]), ["internal", "hybrid"])
        self.assertEqual([r["detail"] for r in self.events("passkey_added")], ["My Mac"])
        self.assertTrue(self.signed_in(self.client))  # this browser stays signed in
        alert = self.outbox[-1]
        self.assertIn("A passkey was added", alert["text"])
        self.assertNotIn("My Mac", alert["text"])  # the name is chosen by whoever added it
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("My Mac", html)
        self.assertIn('<span class="pill pill-ok">On</span>', html)
        # The next options leave this passkey out (the browser will not make it twice).
        self.assertEqual([c["id"] for c in self.options().get_json()["excludeCredentials"]],
                         [auth.id])

    def test_other_devices_are_signed_out(self):
        other = self.app.test_client()
        other.post("/login", data={"email": EMAIL, "password": PASSWORD})
        self.assertTrue(self.signed_in(other))
        self.add_passkey()
        self.assertFalse(self.signed_in(other))

    def test_empty_name_uses_the_device(self):
        self.client.environ_base["HTTP_USER_AGENT"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
        self.add_passkey(name=" ")
        self.assertEqual(self.keys()[0]["name"], "Mac (Safari)")

    def test_a_request_works_once(self):
        auth = SoftAuthenticator()
        credential = auth.register(self.options().get_json(), BASE)
        data = {"action": "passkey_add", "current_password": PASSWORD,
                "credential": json.dumps(credential)}
        with self.client.session_transaction() as s:
            saved = dict(s["passkey_reg"])
        self.client.post("/settings", data=data)
        # The same request again, even with the old session cookie: the challenge is spent.
        auth2 = SoftAuthenticator()
        with self.client.session_transaction() as s:
            s["passkey_reg"] = saved
        data["credential"] = json.dumps(auth2.register({"challenge": saved["c"],
                                                        "rp": {"id": "nomad.example.com"}}, BASE))
        html = self.client.post("/settings", data=data, follow_redirects=True).get_data(
            as_text=True)
        self.assertIn("the request expired or was already used", html)
        self.assertEqual(len(self.keys()), 1)

    def test_a_passkey_made_for_another_site_is_refused(self):
        _auth, resp = self.add_passkey(origin="https://nomad-example.evil")
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn("The passkey could not be checked", html)
        self.assertEqual(self.keys(), [])

    def test_wrong_password_on_the_final_step(self):
        auth = SoftAuthenticator()
        credential = auth.register(self.options().get_json(), BASE)
        self.client.post("/settings", data={"action": "passkey_add", "current_password": "nope",
                                            "credential": json.dumps(credential)})
        self.assertEqual(self.keys(), [])

    def test_at_most_ten(self):
        with self.db() as conn:
            conn.executemany("INSERT INTO passkeys (user_id, credential_id, public_key, name) "
                             "VALUES (1, ?, x'00', 'k')", [(f"id{i}",) for i in range(10)])
        resp = self.options()
        self.assertEqual(resp.status_code, 400)
        self.assertIn("at most 10 passkeys", resp.get_json()["error"])
        self.assertIn("An account can have at most 10 passkeys",
                      self.client.get("/settings").get_data(as_text=True))

    def test_needs_sign_in(self):
        resp = self.app.test_client().post("/settings/passkeys/options",
                                           data={"current_password": PASSWORD})
        self.assertEqual(resp.status_code, 302)


class SignInTests(PasskeyCase):
    def test_sign_in_with_a_passkey(self):
        auth, _ = self.add_passkey()
        client = self.password_step()
        self.assertFalse(self.signed_in(client))  # nothing signed in before the passkey
        page = client.get("/login/code").get_data(as_text=True)
        self.assertIn("Use a passkey", page)
        self.assertNotIn('name="code"', page)  # no authenticator app on this account
        self.assertIn("js/passkeys.js", page)
        options = client.post("/login/passkey/options").get_json()
        self.assertEqual(options["rpId"], "nomad.example.com")
        self.assertEqual([c["id"] for c in options["allowCredentials"]], [auth.id])
        credential = auth.sign(options, BASE)
        resp = client.post("/login/passkey", data={"credential": json.dumps(credential)})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self.signed_in(client))
        self.assertEqual([r["detail"] for r in self.events("sign_in")][-1], "with passkey")
        self.assertIsNotNone(self.keys()[0]["last_used_at"])

    def test_a_signature_works_once(self):
        auth, _ = self.add_passkey()
        client = self.password_step()
        options = client.post("/login/passkey/options").get_json()
        with client.session_transaction() as s:
            saved = dict(s["tfa"])
        credential = json.dumps(auth.sign(options, BASE))
        client.post("/login/passkey", data={"credential": credential})
        self.assertTrue(self.signed_in(client))
        # Someone who copied the signature and the cookie of that moment gets nothing.
        thief = self.app.test_client()
        with thief.session_transaction() as s:
            s["tfa"] = saved
        thief.post("/login/passkey", data={"credential": credential})
        self.assertFalse(self.signed_in(thief))
        self.assertEqual(len(self.events("sign_in_passkey_failed")), 1)

    def test_wrong_site_key_or_account_is_refused(self):
        auth, _ = self.add_passkey()
        stranger = SoftAuthenticator()  # a passkey this account never added
        for signer, origin in ((auth, "https://nomad-example.evil"), (stranger, BASE)):
            client = self.password_step()
            resp, _ = self.passkey_step(client, signer, origin)
            self.assertIn("That passkey was not accepted", resp.get_data(as_text=True))
            self.assertFalse(self.signed_in(client))

    def test_another_accounts_passkey_is_refused(self):
        self.add_passkey()
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        theirs, _ = self.add_passkey(client=other)
        client = self.password_step()  # a@example.com's password...
        self.passkey_step(client, theirs)  # ...and b@example.com's passkey
        self.assertFalse(self.signed_in(client))

    def test_a_counter_going_back_is_refused(self):
        auth, _ = self.add_passkey(auth=SoftAuthenticator(counting=True))
        client = self.password_step()
        self.passkey_step(client, auth)
        self.assertTrue(self.signed_in(client))
        self.assertEqual(self.keys()[0]["sign_count"], 1)
        auth.sign_count = 0  # a copy of the authenticator, behind the real one
        client = self.password_step()
        self.passkey_step(client, auth)
        self.assertFalse(self.signed_in(client))

    def test_failures_count_like_wrong_codes(self):
        self.add_passkey()
        stranger = SoftAuthenticator()
        client = self.password_step()
        for _ in range(appmod.LIMITS[("code", "email")]):
            self.passkey_step(client, stranger)
        resp, _ = self.passkey_step(client, stranger)
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Retry-After", resp.headers)

    def test_both_methods_offer_both(self):
        self.add_passkey()
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = 'JBSWY3DPEHPK3PXP'")
        page = self.password_step().get("/login/code").get_data(as_text=True)
        self.assertIn("Use a passkey", page)
        self.assertIn('name="code"', page)

    def test_authenticator_only_has_no_passkey_button(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = 'JBSWY3DPEHPK3PXP'")
        page = self.password_step().get("/login/code").get_data(as_text=True)
        self.assertNotIn("Use a passkey", page)
        self.assertNotIn("passkeys.js", page)

    def test_options_without_a_pending_sign_in(self):
        resp = self.app.test_client().post("/login/passkey/options")
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.get_json()["reload"])
        self.assertEqual(resp.headers["Cache-Control"], "no-store")


class ManageTests(PasskeyCase):
    def test_rename(self):
        self.add_passkey()
        key = self.keys()[0]
        self.client.post("/settings", data={"action": "passkey_rename", "passkey_id": key["id"],
                                            "name": " Work laptop "})
        self.assertEqual(self.keys()[0]["name"], "Work laptop")
        html = self.client.post("/settings", data={"action": "passkey_rename",
                                                   "passkey_id": key["id"], "name": "ㅤ"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Type a name for the passkey.", html)
        self.assertEqual(self.keys()[0]["name"], "Work laptop")

    def test_rename_or_remove_another_accounts_passkey(self):
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.add_passkey(client=other, name="Theirs")
        key = self.keys()[0]
        self.client.post("/settings", data={"action": "passkey_rename", "passkey_id": key["id"],
                                            "name": "Mine"})
        self.client.post("/settings", data={"action": "passkey_remove", "passkey_id": key["id"],
                                            "current_password": PASSWORD, "confirm_delete": "2"})
        self.assertEqual([k["name"] for k in self.keys()], ["Theirs"])

    def test_remove_needs_both_confirmations_and_the_password(self):
        self.add_passkey()
        key = self.keys()[0]
        base = {"action": "passkey_remove", "passkey_id": key["id"]}
        self.client.post("/settings", data=dict(base, current_password=PASSWORD))
        self.client.post("/settings", data=dict(base, current_password="nope", confirm_delete="2"))
        self.assertEqual(len(self.keys()), 1)
        html = self.client.post("/settings", data=dict(base, current_password=PASSWORD,
                                                       confirm_delete="2"),
                                follow_redirects=True).get_data(as_text=True)
        self.assertEqual(self.keys(), [])
        self.assertIn("Two-factor sign in is off.", html)
        self.assertIn("Two-factor sign in is now off.", self.outbox[-1]["text"])
        # Signing in asks for the password alone again.
        resp = self.app.test_client().post("/login", data={"email": EMAIL, "password": PASSWORD})
        self.assertFalse(resp.headers["Location"].endswith("/login/code"))

    def test_removing_the_authenticator_app_keeps_passkeys_on(self):
        self.add_passkey()
        secret = "JBSWY3DPEHPK3PXP"
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = ?", (secret,))
        import time
        code = appmod.totp_code(secret, int(time.time() // appmod.TOTP_STEP_SECONDS))
        html = self.client.post("/settings", data={"action": "2fa_disable", "code": code,
                                                   "current_password": PASSWORD},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("The authenticator app was removed.", html)
        self.assertIn('<span class="pill pill-ok">On</span>', html)
        self.password_step()  # still asks for the second step


class ClearedTests(PasskeyCase):
    def test_undoing_an_email_change_removes_passkeys(self):
        self.add_passkey()  # say, the intruder's
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "thief@example.com",
                                            "current_password": PASSWORD})
        self.client.post(self.last_link("thief@example.com", kind="account/email"))
        undo = self.last_link(EMAIL, kind="account/email/undo")
        self.app.test_client().post(undo)
        self.assertEqual(self.keys(), [])

    def test_operator_reset_removes_passkeys(self):
        self.add_passkey()
        result = CliRunner().invoke(self.app.cli, ["reset-2fa", EMAIL])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.keys(), [])
        self.assertEqual(len(self.events("2fa_reset")), 1)
        result = CliRunner().invoke(self.app.cli, ["reset-2fa", EMAIL])
        self.assertNotEqual(result.exit_code, 0)

    def test_deleting_the_account_removes_passkeys(self):
        self.add_passkey()
        self.client.post("/settings", data={"action": "delete", "confirm_email": EMAIL,
                                            "current_password": PASSWORD, "confirm_delete": "2"})
        self.assertEqual(self.keys(), [])


class AdminTests(PasskeyCase):
    def test_a_passkey_opens_the_admin_page(self):
        self.app.config.update(ADMIN_EMAILS=frozenset({EMAIL}), ADMIN_REQUIRE_2FA=True)
        self.assertEqual(self.client.get("/admin").status_code, 302)
        self.add_passkey()
        self.assertEqual(self.client.get("/admin").status_code, 200)
        page = self.client.get("/admin/users/1").get_data(as_text=True)
        self.assertIn("On <span class=\"muted\">(1 passkey)</span>", page)
        self.assertIn('<span class="tag">Two-factor on</span>',
                      self.client.get("/admin").get_data(as_text=True))


class StaticTests(PasskeyCase):
    def test_script_is_versioned_and_page_specific(self):
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertRegex(html, r"js/passkeys\.js\?v=[0-9a-f]{10}")
        self.assertNotIn("passkeys.js", self.client.get("/app").get_data(as_text=True))

    def test_requirements_pin_webauthn(self):
        with open(os.path.join(appmod.BASE_DIR, "requirements.txt"), encoding="utf-8") as fh:
            text = fh.read()
        for package in ("webauthn==", "cryptography==", "cbor2==", "pyOpenSSL=="):
            self.assertIn(package, text)
