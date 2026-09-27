"""Regression tests for the v1.2.7 security fixes."""
import os
import stat
import time
import unittest
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


def sign_up(client, email, password="password1"):
    return client.post("/signup", data={"email": email, "password": password,
                                        "confirm": password}, follow_redirects=True)


class SignupTests(AppTestCase):
    def test_sign_ups_are_limited_per_network(self):
        anon = self.app.test_client()
        for i in range(10):
            self.assertEqual(anon.post("/signup", data={
                "email": f"s{i}@example.com", "password": "password1",
                "confirm": "password1"}).status_code, 302)
        resp = anon.post("/signup", data={"email": "late@example.com", "password": "password1",
                                          "confirm": "password1"})
        self.assertEqual(resp.status_code, 429)
        self.assertIn(b"Too many sign ups from this network.", resp.data)

    def test_no_password_hash_for_an_address_that_has_an_account(self):
        self.signup()
        self.age_emails()
        with mock.patch.object(appmod, "generate_password_hash",
                               side_effect=AssertionError("hashed")):
            resp = self.app.test_client().post("/signup", data={
                "email": "a@example.com", "password": "password2", "confirm": "password2"})
        self.assertEqual(resp.status_code, 302)

    def test_same_answer_for_new_and_existing_addresses(self):
        self.signup()
        self.age_emails()
        known = sign_up(self.app.test_client(), "a@example.com").get_data(as_text=True)
        new = sign_up(self.app.test_client(), "b@example.com").get_data(as_text=True)
        self.assertIn("We sent an email to a@example.com. Open the link in it within 20 minutes",
                      known)
        self.assertIn("We sent an email to b@example.com. Open the link in it within 20 minutes",
                      new)
        self.assertEqual(self.outbox[-2]["to"], "a@example.com")
        self.assertIn("already has an account", self.outbox[-2]["text"])

    def test_email_change_does_not_reveal_other_accounts(self):
        self.signup("b@example.com", client=self.app.test_client())
        self.signup()
        self.age_emails()
        html = self.client.post("/account", data={
            "action": "email", "email": "b@example.com", "current_password": "password1"},
            follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("Another account", html)
        self.assertIn("We sent a confirmation link to b@example.com", html)
        link = self.last_link("b@example.com", kind="account/email")
        html = self.client.get(link, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Another account started using this email address", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT email FROM users WHERE id = 2").fetchone()[0],
                             "a@example.com")


class SignInTests(AppTestCase):
    def test_unknown_email_still_checks_a_password_hash(self):
        calls = []
        real = appmod.check_password_hash

        def spy(h, p):
            calls.append(h)
            return real(h, p)

        with mock.patch.object(appmod, "check_password_hash", spy):
            self.app.test_client().post("/login", data={"email": "nobody@example.com",
                                                        "password": "password1"})
        self.assertEqual(len(calls), 1)

    def test_strangers_cannot_lock_the_owner_out(self):
        self.signup()
        self.client.post("/logout")
        for i in range(5):  # a stranger fails on purpose, from other networks
            self.app.test_client().post("/login", data={"email": "a@example.com",
                                                        "password": "wrong"},
                                        environ_base={"REMOTE_ADDR": f"198.51.100.{i}"})
        stranger = self.app.test_client()
        self.assertEqual(stranger.post("/login", data={"email": "a@example.com",
                                                       "password": "password1"},
                                       environ_base={"REMOTE_ADDR": "198.51.100.9"}).status_code,
                         429)
        # The owner's browser signed in before, so its device cookie gets it in.
        resp = self.client.post("/login", data={"email": "a@example.com", "password": "password1"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.get("/account").status_code, 200)

    def test_a_device_is_still_limited(self):
        self.signup()
        self.client.post("/logout")
        for _ in range(5):
            self.client.post("/login", data={"email": "a@example.com", "password": "wrong"})
        resp = self.client.post("/login", data={"email": "a@example.com", "password": "password1"})
        self.assertEqual(resp.status_code, 429)
        self.assertIn(b"on this device", resp.data)

    def test_device_cookie_of_another_account_does_not_help(self):
        self.signup("b@example.com")
        self.client.post("/logout")
        self.signup(client=self.app.test_client())
        for i in range(5):
            self.app.test_client().post("/login", data={"email": "a@example.com",
                                                        "password": "wrong"})
        resp = self.client.post("/login", data={"email": "a@example.com", "password": "password1"})
        self.assertEqual(resp.status_code, 429)


class SessionTests(AppTestCase):
    def test_sign_out_everywhere(self):
        self.signup()
        other = self.app.test_client()
        other.post("/login", data={"email": "a@example.com", "password": "password1",
                                   "remember": "1"})
        self.assertEqual(other.get("/account").status_code, 200)
        html = self.client.post("/account", data={"action": "sessions"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Every other browser and device has been signed out.", html)
        self.assertEqual(self.client.get("/account").status_code, 200)
        self.assertEqual(other.get("/account").status_code, 302)

    def test_remember_me_lasts_a_month(self):
        self.assertEqual(self.app.config["REMEMBER_COOKIE_DURATION"].days, 30)
        self.assertEqual(self.app.config["PERMANENT_SESSION_LIFETIME"].days, 30)


class HeaderTests(AppTestCase):
    def test_private_pages_are_never_cached(self):
        self.signup()
        self.new_year(2026)
        for path in ("/app", "/year/2026", "/account", "/plan"):
            self.assertEqual(self.client.get(path).headers["Cache-Control"], "private, no-store",
                             path)
        self.assertNotIn("no-store", self.client.get("/static/css/style.css")
                         .headers.get("Cache-Control", ""))

    def test_pages_cannot_be_framed_or_sniffed(self):
        resp = self.app.test_client().get("/")
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        self.assertEqual(resp.headers["Content-Security-Policy"], "frame-ancestors 'none'")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["Referrer-Policy"], "same-origin")


class SizeTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def test_place_names_are_limited(self):
        long = "x" * (appmod.PLACE_MAX + 1)
        html = self.client.post("/year/2026/movements/new", data={
            "city": long, "country": "Italy", "start_date": "2026-03-01",
            "end_date": "2026-03-02"}).get_data(as_text=True)
        self.assertIn("Country and city can be at most 100 characters.", html)
        html = self.client.post("/year/2026/base", data={
            "action": "update", "base_city": "Lisbon", "base_country": long},
            follow_redirects=True).get_data(as_text=True)
        self.assertIn("Base country and city can be at most 100 characters.", html)
        html = self.client.post("/year/new", data={
            "year": "2027", "base_city": long, "base_country": "Portugal"},
            follow_redirects=True).get_data(as_text=True)
        self.assertIn("Base country and city can be at most 100 characters.", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM years").fetchone()[0], 1)

    def test_movements_per_year_are_limited(self):
        with mock.patch.object(appmod, "MOVEMENTS_MAX", 2):
            for day in (1, 2):
                self.add_movement(2026, "Rome", "Italy", f"2026-03-0{day}", f"2026-03-0{day}")
            html = self.client.post("/year/2026/movements/new", data={
                "city": "Rome", "country": "Italy", "start_date": "2026-03-05",
                "end_date": "2026-03-05"}).get_data(as_text=True)
        self.assertIn("A year can hold at most 2 movements.", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0], 2)

    def test_map_stays_fast_with_many_stays(self):
        year = {"year": 2026, "base_city": "Lisbon", "base_country": "Portugal"}
        moves = [{"id": i, "city": f"City {i}", "country": "Italy" if i % 2 else f"Place {i}",
                  "start_date": "2026-03-01", "end_date": "2026-03-01"} for i in range(4000)]
        stats = {"rows": []}
        started = time.monotonic()
        pins, missing = appmod.map_pins(year, moves, stats)
        self.assertLess(time.monotonic() - started, 2)
        italy = next(p for p in pins if p["country"] == "Italy")
        self.assertEqual(len(italy["cities"]), 2000)
        self.assertEqual(len(missing), 2000)


@unittest.skipUnless(os.name == "posix", "file modes")
class FileModeTests(AppTestCase):
    def test_database_and_receipt_folders_are_private(self):
        mode = stat.S_IMODE(os.stat(self.app.config["DATABASE_PATH"]).st_mode)
        self.assertEqual(mode, 0o600)
        self.signup()
        self.new_year(2026)
        old = os.umask(0o022)
        try:
            self.upload("/year/2026/base", "r.pdf", b"%PDF-1 x")
        finally:
            os.umask(old)
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)

    def test_start_script_and_server_use_a_private_umask(self):
        script = open(os.path.join(appmod.BASE_DIR, "runWebApp.sh"), encoding="utf-8").read()
        self.assertIn("umask 077", script.split("# 5. Start", 1)[1])
        source = open(os.path.join(appmod.BASE_DIR, "app.py"), encoding="utf-8").read()
        self.assertIn("os.umask(0o077)", source.split('if __name__ == "__main__":', 1)[1])


if __name__ == "__main__":
    unittest.main()
