"""v1.2.10: sign out ends the session for good, breached passwords, receipt content check."""
import hashlib
import io
import unittest
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64


class SessionTests(AppTestCase):
    def cookies(self, client):
        return {c.key: c.value for c in client._cookies.values()}

    def test_sign_out_ends_copied_cookies(self):
        self.signup()
        self.client.post("/logout")
        victim = self.app.test_client()
        victim.post("/login", data={"email": "a@example.com", "password": "password1",
                                    "remember": "1"})
        stolen = self.cookies(victim)
        thief = self.app.test_client()
        for name, value in stolen.items():
            thief.set_cookie(name, value)
        self.assertEqual(thief.get("/account").status_code, 200)  # the copy works...
        victim.post("/logout")
        self.assertEqual(victim.get("/account").status_code, 302)
        self.assertEqual(thief.get("/account").status_code, 302)  # ...until the owner signs out
        remember_only = self.app.test_client()  # the remember cookie alone is dead too
        remember_only.set_cookie("nomadlife_remember", stolen["nomadlife_remember"])
        self.assertEqual(remember_only.get("/account").status_code, 302)

    def test_signing_out_one_browser_keeps_the_others(self):
        self.signup()
        other = self.app.test_client()
        other.post("/login", data={"email": "a@example.com", "password": "password1"})
        other.post("/logout")
        self.assertEqual(self.client.get("/account").status_code, 200)
        html = self.client.get("/account").get_data(as_text=True)
        self.assertIn("signed in on 1 browser or device", html)

    def test_sign_out_everywhere_else_and_password_change_forget_the_rows(self):
        self.signup()
        for _ in range(2):
            self.app.test_client().post("/login", data={"email": "a@example.com",
                                                        "password": "password1"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0], 3)
        self.client.post("/account", data={"action": "sessions"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0], 1)
        self.assertEqual(self.client.get("/account").status_code, 200)
        self.client.post("/account", data={"action": "password", "current_password": "password1",
                                           "password": "newpass123", "confirm": "newpass123"})
        self.assertEqual(self.client.get("/account").status_code, 200)  # same row kept
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0], 1)

    def test_sessions_from_older_versions_still_work(self):
        self.signup()
        with self.db() as conn:
            row = conn.execute("SELECT * FROM users").fetchone()
        legacy = self.app.test_client()
        with legacy.session_transaction() as sess:
            sess["_user_id"] = f"{row['id']}:{appmod.password_fingerprint(row['password_hash'])}"
            sess["_fresh"] = True
        self.assertEqual(legacy.get("/account").status_code, 200)

    def test_idle_sessions_are_pruned(self):
        self.signup()
        idle = self.app.test_client()
        idle.post("/login", data={"email": "a@example.com", "password": "password1"})
        with self.db() as conn:  # the browser signed in last is not used for 40 days
            conn.execute("UPDATE user_sessions SET last_seen_at = datetime('now', '-40 days') "
                         "WHERE rowid = (SELECT MAX(rowid) FROM user_sessions)")
        with mock.patch.object(appmod, "PURGE_EVERY_SECONDS", 0):
            self.client.get("/account")  # any request prunes (at most once a minute)
        self.assertEqual(idle.get("/account").status_code, 302)
        self.assertEqual(self.client.get("/account").status_code, 200)


class BreachedPasswordTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["PWNED_CHECK"] = True
        self.calls = []

    def fake_api(self, breached=("password1",), fail=False):
        suffixes = {hashlib.sha1(p.encode()).hexdigest().upper()[5:] for p in breached}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(req, timeout):
            self.calls.append(req.full_url)
            if fail:
                raise OSError("offline")
            lines = [f"{s}:12345" for s in suffixes] + ["0" * 35 + ":0"]
            return Resp("\r\n".join(lines).encode())

        return mock.patch.object(appmod.urllib.request, "urlopen", urlopen)

    def test_breached_password_is_refused_at_sign_up(self):
        with self.fake_api():
            html = self.client.post("/signup", data={"email": "a@example.com", "password":
                                                     "password1", "confirm": "password1"},
                                    follow_redirects=True).get_data(as_text=True)
        self.assertIn("appears 12,345 times in known data breaches", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        # Only the first 5 characters of the SHA-1 leave the server.
        self.assertEqual(self.calls, [appmod.PWNED_URL + hashlib.sha1(b"password1").hexdigest()
                                      .upper()[:5]])

    def test_other_passwords_pass_and_an_outage_never_blocks(self):
        with self.fake_api():
            self.client.post("/signup", data={"email": "a@example.com", "password": "tr0ub4dor&3x",
                                              "confirm": "tr0ub4dor&3x"})
        with self.fake_api(fail=True):
            self.client.post("/signup", data={"email": "b@example.com", "password": "password1",
                                              "confirm": "password1"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)

    def test_same_answer_for_known_addresses(self):
        self.app.config["PWNED_CHECK"] = False
        self.signup(password="safe-pass-123")
        self.client.post("/logout")
        self.app.config["PWNED_CHECK"] = True
        with self.fake_api():
            html = self.app.test_client().post("/signup", data={
                "email": "a@example.com", "password": "password1", "confirm": "password1"},
                follow_redirects=True).get_data(as_text=True)
        self.assertIn("known data breaches", html)

    def test_account_page_and_reset_check_too(self):
        self.app.config["PWNED_CHECK"] = False
        self.signup(password="safe-pass-123")
        self.age_emails()
        self.app.config["PWNED_CHECK"] = True
        with self.fake_api():
            html = self.client.post("/account", data={
                "action": "password", "current_password": "safe-pass-123", "password": "password1",
                "confirm": "password1"}, follow_redirects=True).get_data(as_text=True)
            self.assertIn("known data breaches", html)
            anon = self.app.test_client()
            anon.post("/forgot", data={"email": "a@example.com"})
            html = anon.post(self.last_link("a@example.com", kind="reset"), data={
                "password": "password1", "confirm": "password1"}).get_data(as_text=True)
            self.assertIn("known data breaches", html)
        self.assertEqual(self.app.test_client().post("/login", data={
            "email": "a@example.com", "password": "safe-pass-123"}).status_code, 302)

    def test_rate_limited_sign_ups_never_reach_the_service(self):
        with self.fake_api():
            for i in range(12):
                self.client.post("/signup", data={"email": f"s{i}@example.com",
                                                  "password": "password1", "confirm": "password1"})
        self.assertEqual(len(self.calls), 10)


class ContentTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def count(self):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def test_real_files_are_accepted(self):
        for name, body in [("a.png", PNG), ("b.jpg", JPG), ("c.jpeg", JPG), ("d.webp", WEBP),
                           ("e.heic", HEIC), ("f.pdf", PDF), ("g.pdf", b"junk\n" + PDF)]:
            self.upload("/year/2026/base", name, body)
        self.assertEqual(self.count(), 7)

    def test_renamed_files_are_refused(self):
        for name, body in [("page.pdf", b"<html><script>alert(1)</script>"),
                           ("page.png", b"<svg onload=alert(1)>"), ("x.jpg", PNG),
                           ("x.webp", b"RIFF\x00\x00\x00\x00AVI "), ("x.heic", PNG)]:
            html = self.upload("/year/2026/base", name, body).get_data(as_text=True)
            resp = self.client.get("/year/2026/base").get_data(as_text=True)
            self.assertIn(f"{name} is not a real", html + resp, name)
        self.assertEqual(self.count(), 0)


if __name__ == "__main__":
    unittest.main()
