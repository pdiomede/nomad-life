"""The Gmail dot rule on Sign up: bots' dot trick addresses get the usual answer, but no account
and no email."""
import unittest

import app as appmod
from tests.helpers import AppTestCase


class GmailDotsTests(AppTestCase):
    def post(self, email):
        return self.client.post("/signup", data={"email": email, "password": "password1",
                                                 "confirm": "password1"},
                                follow_redirects=True)

    def users(self):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def test_rule(self):
        for email in ("t.o.n.yluu.5.5.95@gmail.com", "a.q.o.g.i.ke.73.9@googlemail.com",
                      "a.b.c.d@gmail.com"):
            self.assertTrue(appmod.dotted_gmail(email), email)
        for email in ("tonyluu5595@gmail.com", "first.middle.last@gmail.com",
                      "first.last+a.b.c@gmail.com", "a.b.c.d@example.com",
                      "a.b.c.d@gmail.com.example"):
            self.assertFalse(appmod.dotted_gmail(email), email)

    def test_signup_refused_silently(self):
        resp = self.post("t.o.n.yluu.5.5.95@gmail.com")
        self.assertIn(b"We sent an email to t.o.n.yluu.5.5.95@gmail.com", resp.data)
        self.assertEqual(self.users(), 0)
        self.assertEqual(self.outbox, [])
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM auth_events").fetchone()[0], 0)

    def test_two_dots_still_sign_up(self):
        self.post("first.middle.last@gmail.com")
        self.assertEqual(self.users(), 1)
        self.assertEqual(len(self.outbox), 1)


if __name__ == "__main__":
    unittest.main()
