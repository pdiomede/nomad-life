"""v1.2.9: security alerts, two-factor sign in, security history, admin protection."""
import os
import re
import time
import unittest
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


def current_code(secret, offset=0):
    return appmod.totp_code(secret, int(time.time() // appmod.TOTP_STEP_SECONDS) + offset)


class TotpTests(unittest.TestCase):
    def test_rfc_6238_vectors(self):
        # RFC 6238 appendix B, SHA-1, secret "12345678901234567890", last 6 digits.
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        for t, code in [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"),
                        (2000000000, "279037")]:
            self.assertEqual(appmod.totp_code(secret, t // 30), code)

    def test_codes_match_once_and_only_near_now(self):
        secret = appmod.new_totp_secret()
        now = int(time.time() // 30)
        self.assertIsNotNone(appmod.totp_match(secret, current_code(secret)))
        self.assertIsNotNone(appmod.totp_match(secret, current_code(secret, -1)))
        self.assertIsNone(appmod.totp_match(secret, current_code(secret, -3)))
        self.assertIsNone(appmod.totp_match(secret, current_code(secret), last_step=now + 1))
        self.assertIsNone(appmod.totp_match(secret, "12345"))
        self.assertIsNone(appmod.totp_match(None, "123456"))


class TwoFactorTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.age_emails()

    def enable(self, client=None):
        client = client or self.client
        with client.session_transaction() as sess:
            sess.pop("totp_setup", None)
        html = client.get("/settings").get_data(as_text=True)
        secret = re.search(r'<code class="tfa-key">([\w ]+)</code>', html).group(1).replace(" ", "")
        self.assertIn('src="data:image/svg+xml', html)  # the QR code
        resp = client.post("/settings", data={"action": "2fa_enable", "code": current_code(secret),
                                              "current_password": "password1"},
                           follow_redirects=True)
        self.assertIn("Two-factor sign in is on.", resp.get_data(as_text=True))
        return secret

    def sign_in(self, client, code=None, password="password1"):
        resp = client.post("/login", data={"email": "a@example.com", "password": password})
        if code is None:
            return resp
        return client.post("/login/code", data={"code": code})

    def test_turning_on_needs_a_right_code_and_password(self):
        html = self.client.get("/settings").get_data(as_text=True)
        secret = re.search(r'<code class="tfa-key">([\w ]+)</code>', html).group(1).replace(" ", "")
        for data, message in [({"code": "000000", "current_password": "password1"},
                               "That code is not correct."),
                              ({"code": current_code(secret), "current_password": "nope"},
                               "Your current password is not correct.")]:
            html = self.client.post("/settings", data={"action": "2fa_enable", **data},
                                    follow_redirects=True).get_data(as_text=True)
            self.assertIn(message, html)
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT totp_secret FROM users").fetchone()[0])

    def test_sign_in_asks_for_the_code(self):
        other = self.app.test_client()
        self.sign_in(other)  # signed in before two-factor was on
        secret = self.enable()
        self.assertEqual(other.get("/settings").status_code, 302)  # signed out by turning it on
        self.assertIn("Security alert", self.outbox[-1]["subject"])
        fresh = self.app.test_client()
        resp = self.sign_in(fresh)
        self.assertEqual(resp.headers["Location"], "/login/code")
        self.assertEqual(fresh.get("/settings").status_code, 302)  # password alone: not signed in
        resp = fresh.post("/login/code", data={"code": "000000"})
        self.assertIn(b"That code is not correct", resp.data)
        with self.db() as conn:  # the code from the enable step was used: wait for a new one
            conn.execute("UPDATE users SET totp_step = totp_step - 5")
        resp = fresh.post("/login/code", data={"code": current_code(secret)})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(fresh.get("/settings").status_code, 200)

    def test_a_code_works_only_once(self):
        secret = self.enable()
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_step = 0")
        code = current_code(secret)
        first, second = self.app.test_client(), self.app.test_client()
        self.assertEqual(self.sign_in(first, code).status_code, 302)
        self.assertIn(b"already used", self.sign_in(second, code).data)

    def test_code_step_expires_and_cannot_be_skipped(self):
        secret = self.enable()
        fresh = self.app.test_client()
        self.assertEqual(fresh.get("/login/code").status_code, 302)  # no password step
        self.sign_in(fresh)
        with fresh.session_transaction() as sess:
            sess["tfa"] = dict(sess["tfa"], t=int(time.time()) - appmod.TOTP_PENDING_SECONDS - 1)
        resp = fresh.post("/login/code", data={"code": current_code(secret)},
                          follow_redirects=True)
        self.assertIn(b"Please sign in again.", resp.data)

    def test_wrong_codes_are_limited(self):
        self.enable()
        fresh = self.app.test_client()
        self.sign_in(fresh)
        for _ in range(5):
            fresh.post("/login/code", data={"code": "000000"})
        self.assertEqual(fresh.post("/login/code", data={"code": "000000"}).status_code, 429)

    def test_turning_off_needs_a_code(self):
        secret = self.enable()
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_step = 0")
        html = self.client.post("/settings", data={
            "action": "2fa_disable", "code": "000000", "current_password": "password1"},
            follow_redirects=True).get_data(as_text=True)
        self.assertIn("That code is not correct.", html)
        html = self.client.post("/settings", data={
            "action": "2fa_disable", "code": current_code(secret), "current_password": "password1"},
            follow_redirects=True).get_data(as_text=True)
        self.assertIn("Two-factor sign in is off.", html)
        self.assertIn("turned off", self.outbox[-1]["text"])

    def test_operator_can_remove_it_from_the_command_line(self):
        self.enable()
        result = self.app.test_cli_runner().invoke(args=["reset-2fa", "A@example.com"])
        self.assertEqual(result.exit_code, 0, result.output)
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT totp_secret FROM users").fetchone()[0])
        self.assertIn("removed", self.outbox[-1]["text"])
        result = self.app.test_cli_runner().invoke(args=["reset-2fa", "nobody@example.com"])
        self.assertNotEqual(result.exit_code, 0)


class AdminTwoFactorTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com", "boss@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = True
        self.signup("admin@example.com")

    def test_admin_page_needs_two_factor(self):
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/settings")
        self.assertEqual(self.client.post("/admin/users/1", data={"action": "disable"})
                         .status_code, 302)
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = ?", (appmod.new_totp_secret(),))
        self.assertEqual(self.client.get("/admin").status_code, 200)

    def test_admins_cannot_disable_or_delete_other_admins(self):
        self.signup("boss@example.com", client=self.app.test_client())
        self.signup("user@example.com", client=self.app.test_client())
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = ?", (appmod.new_totp_secret(),))
            boss = conn.execute("SELECT id FROM users WHERE email = 'boss@example.com'").fetchone()[0]
        for data in ({"action": "disable"},
                     {"action": "delete", "confirm_delete": "2", "confirm_email": "boss@example.com"}):
            html = self.client.post(f"/admin/users/{boss}", data=data,
                                    follow_redirects=True).get_data(as_text=True)
            self.assertIn("boss@example.com is an admin. Remove it from ADMIN_EMAILS", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT disabled FROM users WHERE id = ?",
                                          (boss,)).fetchone()[0], 0)
        with self.db() as conn:
            user = conn.execute("SELECT id FROM users WHERE email = 'user@example.com'").fetchone()[0]
        self.assertNotIn('aria-label="Disable boss@example.com"',
                         self.client.get(f"/admin/users/{boss}").get_data(as_text=True))
        self.assertIn('aria-label="Disable user@example.com"',
                      self.client.get(f"/admin/users/{user}").get_data(as_text=True))


class AlertTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.age_emails()

    def alerts(self, to="a@example.com"):
        return [m for m in self.outbox if m["to"] == to and "Security alert" in m["subject"]]

    def test_password_change_and_reset_send_alerts(self):
        self.client.post("/settings", data={"action": "password", "current_password": "password1",
                                           "password": "newpass123", "confirm": "newpass123"})
        self.assertEqual(len(self.alerts()), 1)
        self.assertIn("password of your Nomad Life account was changed", self.alerts()[0]["text"])
        self.assertIn("/forgot", self.alerts()[0]["text"])
        self.age_emails()
        anon = self.app.test_client()
        anon.post("/forgot", data={"email": "a@example.com"})
        anon.post(self.last_link("a@example.com", kind="reset"),
                  data={"password": "newpass456", "confirm": "newpass456"})
        self.assertEqual(len(self.alerts()), 2)
        self.assertIn("was reset", self.alerts()[1]["text"])

    def test_email_change_alerts_the_old_address(self):
        self.client.post("/settings", data={"action": "email", "email": "new@example.com",
                                           "current_password": "password1"})
        self.client.get(self.last_link("new@example.com", kind="account/email"))
        alerts = self.alerts("a@example.com")
        self.assertEqual(len(alerts), 1)
        self.assertIn("changed from a@example.com to new@example.com", alerts[0]["text"])

    def test_alert_failure_does_not_break_the_change(self):
        with mock.patch.object(appmod, "send_email", return_value=False):
            html = self.client.post("/settings", data={
                "action": "password", "current_password": "password1", "password": "newpass123",
                "confirm": "newpass123"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Your password has been changed.", html)


class AuditTests(AppTestCase):
    def events(self, email=None):
        with self.db() as conn:
            sql = "SELECT event, email, actor, detail FROM audit_log"
            rows = conn.execute(sql + (" WHERE email = ?" if email else "") + " ORDER BY id",
                                (email,) if email else ()).fetchall()
        return [tuple(r) for r in rows]

    def test_security_events_are_recorded(self):
        self.signup()
        anon = self.app.test_client()
        anon.post("/login", data={"email": "a@example.com", "password": "wrong"})
        anon.post("/login", data={"email": "nobody@example.com", "password": "wrong"})
        self.client.post("/settings", data={"action": "sessions"})
        names = [e[0] for e in self.events("a@example.com")]
        self.assertEqual(names, ["account_confirmed", "sign_in", "sign_in_failed",
                                 "sessions_revoked"])
        self.assertEqual(self.events("nobody@example.com"), [])  # unknown emails are not stored

    def test_admin_actions_record_who_did_them(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("user@example.com", client=self.app.test_client())
        self.signup("admin@example.com")
        self.client.post("/admin/users/1", data={"action": "plan", "plan": "pro"})
        self.client.post("/admin/users/1", data={"action": "disable"})
        self.client.post("/admin/plans/pro", data={"month": "5", "year": "50"})
        events = [e for e in self.events() if e[0].startswith("admin_")]
        self.assertEqual(events[0], ("admin_plan", "user@example.com", "admin@example.com", "Pro"))
        self.assertEqual(events[1][:3], ("admin_disable", "user@example.com", "admin@example.com"))
        self.assertEqual(events[2][0], "admin_price")
        html = self.client.get("/admin?q=user@example.com").get_data(as_text=True)
        self.assertIn("Plan changed by an admin", html)
        self.assertIn("by <bdi>admin@example.com</bdi>", html)
        listed = html.split('id="activity"', 1)[1]
        self.assertIn('dir="auto">user@example.com</a></td>', listed)
        self.assertNotIn('dir="auto">admin@example.com</a></td>', listed)  # filtered to that account

    def test_account_page_shows_own_activity_only(self):
        self.signup()
        self.signup("b@example.com", client=self.app.test_client())
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("Recent security activity", html)
        self.assertEqual(html.count("<li><span>Signed in</span>"), 1)

    def test_old_events_are_pruned(self):
        self.signup()
        with self.db() as conn:
            conn.execute("UPDATE audit_log SET created_at = datetime('now', '-400 days')")
        with mock.patch.object(appmod, "PURGE_EVERY_SECONDS", 0):
            self.client.get("/settings")
        self.assertEqual(self.events(), [])


class DependencyTests(unittest.TestCase):
    def test_requirements_are_pinned_and_audited(self):
        lines = [l.strip() for l in open(os.path.join(appmod.BASE_DIR, "requirements.txt"),
                                         encoding="utf-8") if l.strip() and not l.startswith("#")]
        self.assertTrue(lines)
        for line in lines:
            self.assertRegex(line, r"^[A-Za-z0-9_.\-]+==[0-9][\w.]*$")
        workflow = open(os.path.join(appmod.BASE_DIR, ".github", "workflows", "security.yml"),
                        encoding="utf-8").read()
        self.assertIn("pip-audit -r requirements.txt", workflow)
        self.assertIn("python -m unittest", workflow)


if __name__ == "__main__":
    unittest.main()
