"""Admin page: sign up and last sign in IP addresses, and the Admin link only for admins."""
import os
import sqlite3
import tempfile
import unittest

import db as dbmod
from tests.helpers import AppTestCase


class AdminIpTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.admin = self.app.test_client()
        self.signup("admin@example.com", client=self.admin)

    def client_from(self, ip):
        client = self.app.test_client()
        client.environ_base["REMOTE_ADDR"] = ip
        return client

    def user(self, email):
        with self.db() as conn:
            return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    def table_row(self, email):
        html = self.admin.get("/admin").get_data(as_text=True)
        row = html.split(f">{email}</a>", 1)[1]
        return row[:row.index("</tr>")]

    def test_sign_up_and_sign_in_ips_are_recorded_and_shown(self):
        joiner = self.client_from("203.0.113.7")
        joiner.post("/signup", data={"email": "a@example.com", "password": "password1",
                                     "confirm": "password1"})
        self.assertEqual(self.user("a@example.com")["signup_ip"], "203.0.113.7")
        joiner.post(self.last_link("a@example.com"))
        # Before any sign in the table shows the sign up IP, marked as such.
        self.assertIn('203.0.113.7<br><span class="muted">at sign up</span>', self.table_row("a@example.com"))
        traveller = self.client_from("2001:db8::42")
        traveller.post("/login", data={"email": "a@example.com", "password": "password1"})
        self.assertEqual(self.user("a@example.com")["last_login_ip"], "2001:db8::42")
        row = self.table_row("a@example.com")
        self.assertIn('2001:db8::42<br><span class="muted">at last sign in</span>', row)
        self.assertNotIn("at sign up", row)
        page = self.admin.get(f"/admin/users/{self.user('a@example.com')['id']}").get_data(as_text=True)
        self.assertIn('>203.0.113.7</a>', page)
        self.assertIn('>2001:db8::42</a>', page)
        self.assertIn('href="/admin?search=203.0.113.7#accounts"', page)

    def test_accounts_from_before_show_not_recorded(self):
        with self.db() as conn:
            conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                         "VALUES ('old@example.com', 'x', '2026-01-01')")
        self.assertIn("not recorded", self.table_row("old@example.com"))

    def test_search_by_part_of_an_ip(self):
        for email, ip in (("a@example.com", "198.51.100.10"), ("b@example.com", "198.51.100.11"),
                          ("c@example.com", "192.0.2.5")):
            self.signup(email, client=self.client_from(ip))
        html = self.admin.get("/admin?search=198.51.100.").get_data(as_text=True)
        self.assertIn(">a@example.com</a>", html)
        self.assertIn(">b@example.com</a>", html)
        self.assertNotIn(">c@example.com</a>", html)

    def test_admin_link_only_for_admins(self):
        self.signup("user@example.com")
        for path in ("/plan", "/settings", "/support"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertNotIn('href="/admin"', html, path)  # neither footer nor menu
        footer = self.admin.get("/plan").get_data(as_text=True).split("<footer", 1)[1]
        self.assertIn('href="/admin">Admin</a>', footer)


class LastLoginIpMigrationTests(unittest.TestCase):
    def test_old_accounts_get_their_last_sign_in_ip_from_the_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.executescript(dbmod.SCHEMA.replace(
                ",\n    signup_ip TEXT NOT NULL DEFAULT '',\n    last_login_ip TEXT NOT NULL DEFAULT ''", ""))
            conn.execute("INSERT INTO users (email, password_hash) VALUES ('a@x.com', 'h')")
            conn.execute("INSERT INTO users (email, password_hash) VALUES ('b@x.com', 'h')")
            conn.executemany("INSERT INTO audit_log (user_id, event, ip) VALUES (?, ?, ?)",
                             [(1, "sign_in", "192.0.2.1"), (1, "sign_in_failed", "192.0.2.9"),
                              (1, "sign_in", "192.0.2.2"), (2, "account_confirmed", "192.0.2.3")])
            conn.commit()
            conn.close()
            dbmod.init_db(path)
            dbmod.init_db(path)  # repeatable
            conn = sqlite3.connect(path)
            self.assertEqual(conn.execute("SELECT email, signup_ip, last_login_ip FROM users "
                                          "ORDER BY id").fetchall(),
                             [("a@x.com", "", "192.0.2.2"), ("b@x.com", "", "")])
            conn.close()


if __name__ == "__main__":
    unittest.main()


class BehindNginxTests(AppTestCase):
    """PROXY_COUNT=1 as on the server: nginx appends the visitor's address to X-Forwarded-For
    ($proxy_add_x_forwarded_for), and only that last entry may be trusted."""

    def setUp(self):
        super().setUp()
        import app as appmod
        self.app = appmod.create_app(dict(self.app.config, PROXY_COUNT=1))
        self.client = self.app.test_client()
        self.client.environ_base["REMOTE_ADDR"] = "127.0.0.1"  # nginx on the same machine

    def ips(self, email="a@example.com"):
        with self.db() as conn:
            row = conn.execute("SELECT signup_ip, last_login_ip FROM users WHERE email = ?",
                               (email,)).fetchone()
            events = [r[0] for r in conn.execute("SELECT ip FROM audit_log ORDER BY id")]
        return tuple(row), events

    def test_a_forged_header_cannot_choose_the_recorded_ip(self):
        # The visitor sends their own X-Forwarded-For; nginx adds the real address after it.
        forged = {"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}
        self.client.post("/signup", data={"email": "a@example.com", "password": "password1",
                                          "confirm": "password1"}, headers=forged)
        self.client.post(self.last_link("a@example.com"), headers=forged)
        self.client.post("/login", data={"email": "a@example.com", "password": "password1"},
                         headers=forged)
        (signup_ip, last_ip), events = self.ips()
        self.assertEqual((signup_ip, last_ip), ("203.0.113.9", "203.0.113.9"))
        self.assertEqual(set(events), {"203.0.113.9"})
        self.assertNotIn("6.6.6.6", str(events))

    def test_two_factor_sign_in_records_the_ip_of_the_code_step(self):
        import app as appmod
        self.signup()
        self.client.post("/logout")
        secret = appmod.new_totp_secret()
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = ?", (secret,))
        via = {"X-Forwarded-For": "198.51.100.77"}
        self.client.post("/login", data={"email": "a@example.com", "password": "password1"},
                         headers=via)
        import time
        self.client.post("/login/code", data={"code": appmod.totp_code(secret, int(time.time()) // 30)},
                         headers=via)
        self.assertEqual(self.ips()[0][1], "198.51.100.77")
