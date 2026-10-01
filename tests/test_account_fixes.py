"""Regression tests for the 1.2.1 account page fixes."""
import os
import unittest
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


class EmailLinkTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.age_emails()

    def request_change(self, email):
        self.age_emails()
        return self.client.post("/settings", data={"action": "email", "email": email,
                                                  "current_password": "password1"},
                                follow_redirects=True).data.decode()

    def email(self):
        with self.db() as conn:
            return conn.execute("SELECT email FROM users ORDER BY id LIMIT 1").fetchone()[0]

    def test_an_older_link_cannot_take_the_account(self):
        self.request_change("typo@exampel.com")
        typo = self.last_link("typo@exampel.com", kind="account/email")
        self.request_change("y@example.com")
        self.assertIn("no longer valid", self.app.test_client().post(typo, follow_redirects=True)
                      .data.decode())  # a newer request cancels it
        self.client.post(self.last_link("y@example.com", kind="account/email"))
        self.assertEqual(self.email(), "y@example.com")
        self.app.test_client().post(typo)  # and after the change it can never switch back
        self.assertEqual(self.email(), "y@example.com")

    def test_reset_links_to_the_old_address_stop_working(self):
        anon = self.app.test_client()
        anon.post("/forgot", data={"email": "a@example.com"})
        old_reset = self.last_link("a@example.com", kind="reset")
        self.request_change("y@example.com")
        self.client.post(self.last_link("y@example.com", kind="account/email"))
        html = anon.post(old_reset, data={"password": "hacked123", "confirm": "hacked123"},
                         follow_redirects=True).data.decode()
        self.assertIn("This reset link is no longer valid.", html)
        self.assertEqual(anon.post("/login", data={"email": "y@example.com",
                                                   "password": "hacked123"}).status_code, 200)

    def test_an_unconfirmed_sign_up_does_not_block_the_address(self):
        self.app.test_client().post("/signup", data={
            "email": "squat@example.com", "password": "password1", "confirm": "password1"})
        self.assertIn("We sent a confirmation link to squat@example.com", self.request_change(
            "squat@example.com"))
        self.client.post(self.last_link("squat@example.com", kind="account/email"))
        self.assertEqual(self.email(), "squat@example.com")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_link_opened_by_another_signed_in_user(self):
        self.request_change("new@example.com")
        link = self.last_link("new@example.com", kind="account/email")
        other = self.signup("b@example.com", client=self.app.test_client())
        html = other.post(link, follow_redirects=True).data.decode()
        self.assertIn("That link was for another account", html)
        self.assertNotIn("Your email address is now", html)

    def test_a_failed_send_does_not_start_the_wait(self):
        self.send_email.side_effect = lambda *a, **k: False
        self.assertIn("We could not send", self.request_change("n@example.com"))
        self.send_email.side_effect = self._record_email
        html = self.client.post("/settings", data={"action": "email", "email": "n@example.com",
                                                  "current_password": "password1"},
                                follow_redirects=True).data.decode()
        self.assertIn("We sent a confirmation link to n@example.com", html)


class LimitMessageTests(AppTestCase):
    def test_network_limit_is_not_blamed_on_the_user(self):
        self.signup()
        anon = self.app.test_client()
        for i in range(20):
            anon.post("/login", data={"email": f"g{i}@example.com", "password": "x"})
        html = self.client.post("/settings", data={
            "action": "password", "current_password": "password1", "password": "newpass123",
            "confirm": "newpass123"}, follow_redirects=True).data.decode()
        self.assertIn("Too many wrong passwords from this network.", html)
        resp = anon.post("/login", data={"email": "a@example.com", "password": "password1"})
        self.assertIn(b"Too many failed sign in attempts from this network.", resp.data)
        self.assertNotIn(b"reset your password", resp.data)

    def test_attempt_is_counted_before_the_password_is_checked(self):
        self.signup()
        calls = []
        real = appmod.check_password_hash

        def spy(h, p):
            with self.db() as conn:
                calls.append(conn.execute("SELECT COUNT(*) FROM auth_events "
                                          "WHERE kind = 'fail'").fetchone()[0])
            return real(h, p)

        with mock.patch.object(appmod, "check_password_hash", spy):
            self.app.test_client().post("/login", data={"email": "a@example.com", "password": "x"})
        self.assertEqual(calls, [2])  # the account and the network rows exist already


if __name__ == "__main__":
    unittest.main()
