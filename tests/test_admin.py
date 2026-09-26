"""Admin page: access, totals, quotas, disabling and deleting accounts, and the migration."""
import io
import os
import sqlite3
import tempfile
import unittest

import app as appmod
import db as dbmod
from tests.helpers import AppTestCase


class AdminTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.user = self.app.test_client()
        self.signup("user@example.com", client=self.user)
        self.new_year(2026, client=self.user)
        self.signup("admin@example.com")  # self.client is the admin

    def user_id(self, email="user@example.com"):
        with self.db() as conn:
            return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0]

    def act(self, action, **data):
        return self.client.post(f"/admin/users/{self.user_id()}", data={"action": action, **data},
                                follow_redirects=True)

    def test_only_admins_see_the_page(self):
        self.assertEqual(self.user.get("/admin").status_code, 404)
        self.assertEqual(self.user.post(f"/admin/users/{self.user_id()}",
                                        data={"action": "disable"}).status_code, 404)
        anon = self.app.test_client()
        self.assertIn("/login", anon.get("/admin").headers["Location"])
        html = self.client.get("/admin").data.decode()
        self.assertIn("user@example.com", html)
        self.assertIn(">Accounts<", html)
        self.assertIn('href="/admin">Admin</a>', html)
        self.assertNotIn('href="/admin"', self.user.get("/year/2026").data.decode())

    def test_unconfirmed_accounts_are_marked(self):
        self.app.test_client().post("/signup", data={
            "email": "new@example.com", "password": "password1", "confirm": "password1"})
        html = self.client.get("/admin").data.decode()
        self.assertEqual(html.count("Email not confirmed"), 1)
        self.assertIn("new@example.com", html)

    def test_totals(self):
        html = self.client.get("/admin").data.decode()
        self.assertRegex(html, r'Accounts</p>\s*<p class="stat-value">2<')
        self.assertRegex(html, r'Years</p>\s*<p class="stat-value">1<')

    def test_custom_quota_is_used_for_uploads_and_can_be_reset(self):
        html = self.act("quota", quota_mb="1").data.decode()
        self.assertIn("Storage quota for user@example.com set to 1 MB.", html)
        resp = self.user.post("/year/2026/base", data={"action": "upload", "kind": "other",
                              "file": (io.BytesIO(b"%PDF" + b"x" * (2 * appmod.MB)), "big.pdf")},
                              content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Not enough storage left", resp.data)
        self.assertIn(b"of 1 MB", self.user.get("/year/2026").data)
        html = self.act("quota", quota_mb="").data.decode()
        self.assertIn("now uses the default storage quota", html)
        self.assertIn(b"of 500 MB", self.user.get("/year/2026").data)

    def test_quota_must_be_sensible(self):
        for bad in ["0", "-5", "abc", "99999999"]:
            html = self.act("quota", quota_mb=bad).data.decode()
            self.assertIn("Enter a storage quota between 1 and", html)
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT quota_bytes FROM users WHERE email = ?",
                                           ("user@example.com",)).fetchone()[0])

    def test_disable_signs_out_and_blocks_sign_in(self):
        self.act("disable")
        self.assertIn("/login", self.user.get("/year/2026").headers["Location"])
        fresh = self.app.test_client()
        resp = fresh.post("/login", data={"email": "user@example.com", "password": "password1"})
        self.assertIn(b"This account is disabled", resp.data)
        self.act("enable")
        resp = fresh.post("/login", data={"email": "user@example.com", "password": "password1"})
        self.assertEqual(resp.status_code, 302)

    def test_delete_needs_both_confirmations_and_removes_everything(self):
        self.user.post("/year/2026/base", data={"action": "upload", "kind": "other",
                       "file": (io.BytesIO(b"%PDF-1 x"), "r.pdf")}, content_type="multipart/form-data")
        uid = self.user_id()
        folder = os.path.join(self.app.config["UPLOAD_DIR"], str(uid))
        self.assertTrue(os.listdir(folder))
        self.act("delete", confirm_email="user@example.com")  # no confirm_delete
        html = self.act("delete", confirm_delete="2", confirm_email="wrong@example.com").data.decode()
        self.assertIn("Type the email address to confirm", html)
        html = self.act("delete", confirm_delete="2", confirm_email="USER@example.com ").data.decode()
        self.assertIn("were deleted", html)
        with self.db() as conn:
            for table in ("users", "years", "documents"):
                n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE "
                                 f"{'id' if table == 'users' else 'user_id'} = ?", (uid,)).fetchone()[0]
                self.assertEqual(n, 0, table)
        self.assertFalse(os.path.exists(folder))

    def test_admin_cannot_disable_or_delete_themselves(self):
        me = self.user_id("admin@example.com")
        for action in ("disable", "delete"):
            html = self.client.post(f"/admin/users/{me}", data={
                "action": action, "confirm_delete": "2", "confirm_email": "admin@example.com"},
                follow_redirects=True).data.decode()
            self.assertIn("You cannot disable or delete your own admin account.", html)
        self.assertEqual(self.client.get("/admin").status_code, 200)

    def test_last_sign_in_is_recorded(self):
        with self.db() as conn:
            self.assertIsNotNone(conn.execute("SELECT last_login_at FROM users WHERE email = ?",
                                              ("user@example.com",)).fetchone()[0])

    def test_admin_emails_setting(self):
        self.assertEqual(appmod.admin_emails(" A@x.com, b@y.com;c@z.com ,"),
                         frozenset({"a@x.com", "b@y.com", "c@z.com"}))
        self.assertEqual(appmod.admin_emails(""), frozenset())


class MigrationTests(unittest.TestCase):
    def test_old_database_gets_the_new_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT "
                         "NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL "
                         "DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("INSERT INTO users (email, password_hash) VALUES ('old@x.com', 'h')")
            conn.commit()
            conn.close()
            dbmod.init_db(path)
            dbmod.init_db(path)  # repeatable
            conn = sqlite3.connect(path)
            row = conn.execute("SELECT email, quota_bytes, disabled, last_login_at FROM users").fetchone()
            conn.close()
            self.assertEqual(row, ("old@x.com", None, 0, None))


if __name__ == "__main__":
    unittest.main()
