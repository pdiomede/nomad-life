"""Regression tests for the v0.0.10 bug hunt."""
import io
import os
import re
import unittest
from unittest import mock

import app as appmod
import package
from tests.helpers import AppTestCase


class SignInTests(AppTestCase):
    def sign_in(self, next_value):
        self.signup()
        self.client.post("/logout")
        return self.client.post("/login", query_string={"next": next_value},
                                data={"email": "a@example.com", "password": "password1"})

    def test_next_with_tab_or_newline_stays_on_site(self):
        for value in ["/\t/evil.com", "/\n/evil.com", "/\r/evil.com", "/\\evil.com"]:
            with self.subTest(value=repr(value)):
                resp = self.sign_in(value)
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(resp.headers["Location"], "/app")

    def test_next_to_a_page_still_works(self):
        self.assertEqual(self.sign_in("/year/2026").headers["Location"], "/year/2026")

    def test_form_posts_are_not_used_as_next(self):
        resp = self.client.post("/documents/1/delete", data={"confirm_delete": "2"})
        self.assertEqual(resp.headers["Location"], "/login")
        resp = self.client.get("/year/2026")
        self.assertEqual(resp.headers["Location"], "/login?next=%2Fyear%2F2026")

    def test_sign_out_without_a_session_goes_to_sign_in(self):
        resp = self.client.post("/logout")
        self.assertEqual(resp.headers["Location"], "/login")


class CsrfTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["WTF_CSRF_ENABLED"] = True

    def test_form_tokens_do_not_expire_after_an_hour(self):
        self.assertIsNone(self.app.config["WTF_CSRF_TIME_LIMIT"])

    def test_sign_out_with_a_stale_page_still_signs_out(self):
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.signup()
        self.app.config["WTF_CSRF_ENABLED"] = True
        resp = self.client.post("/logout", data={"csrf_token": "stale"})
        self.assertEqual(resp.headers["Location"], "/login")
        self.assertIn("/login", self.client.get("/app").headers["Location"])


class RobustnessTests(AppTestCase):
    def test_ids_too_large_for_sqlite_are_404(self):
        self.signup()
        for url in ["/year/99999999999999999999", "/movements/99999999999999999999",
                    "/documents/99999999999999999999"]:
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_https_base_url_marks_cookies_secure(self):
        https = appmod.create_app({
            "TESTING": True, "WTF_CSRF_ENABLED": False, "SECRET_KEY": "x",
            "APP_BASE_URL": "https://nomad.example.com",
            "DATABASE_PATH": os.path.join(self.tmp, "https.db"), "PWNED_CHECK": False,
            "UPLOAD_DIR": os.path.join(self.tmp, "https-up")})
        c = https.test_client()
        resp = c.post("/signup", base_url="https://nomad.example.com",
                      data={"email": "s@example.com", "password": "password1",
                            "confirm": "password1"})
        cookie = resp.headers["Set-Cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertFalse(self.app.config.get("SESSION_COOKIE_SECURE"))

    def test_app_port_setting(self):
        for raw, port in [("", 5050), (" 8080 ", 8080), ("05060", 5060)]:
            with mock.patch.dict(os.environ, {"APP_PORT": raw}):
                self.assertEqual(appmod.port_setting(), port)
        for raw in ["abc", "0", "70000"]:
            with mock.patch.dict(os.environ, {"APP_PORT": raw}):
                with self.assertRaises(SystemExit):
                    appmod.port_setting()

    def test_decimal_megabytes_are_not_one_byte_short(self):
        with mock.patch.dict(os.environ, {"MAX_RECEIPT_MB": "1.2"}):
            size = appmod.mb_setting("MAX_RECEIPT_MB", 10)
        self.assertEqual(appmod.format_size(size, "down"), "1.2 MB")


class DataTests(AppTestCase):
    def test_countries_match_without_accents_or_curly_apostrophes(self):
        self.signup()
        self.new_year(2025, base_city="Hargeisa", base_country="Somalilánd")
        self.add_movement(2025, "Berbera", "Somaliland", "2025-03-01", "2025-03-10")
        self.add_movement(2025, "Hilo", "Hawai'i", "2025-05-01", "2025-05-02")
        self.add_movement(2025, "Kona", "Hawai’i", "2025-06-01", "2025-06-02")
        html = self.client.get("/year/2025").data.decode()
        abroad = re.search(r'Days abroad</p>\s*<p class="stat-value">(\d+)<', html).group(1)
        self.assertEqual(abroad, "4")  # Somaliland is the base; both Hawaii spellings are one
        self.assertIn("4 days (4 so far)", html)
        self.assertNotIn("2 days (2 so far)", html)

    def test_receipt_type_is_kept_after_a_date_error(self):
        self.signup()
        self.new_year(2026)
        html = self.client.post("/year/2026/movements/new", data={
            "city": "Rome", "country": "Italy", "start_date": "2026-03-10",
            "end_date": "2026-03-01", "kind": "flight"}).data.decode()
        self.assertIn('<option value="flight" selected>', html)

    def test_file_inputs_have_their_limits_as_label(self):
        self.signup()
        self.new_year(2026)
        html = self.client.get("/year/2026/base").data.decode()
        self.assertIn('<label for="file-base">File (PDF or image', html)
        self.assertIn('id="file-base"', html)
        self.assertIn('id="fname-base" role="status"', html)


class FileTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.year = self.new_year(2026)

    def test_rename_keeps_text_around_slashes(self):
        self.upload("/year/2026/base", "contract.pdf", b"%PDF-1.4 x")
        with self.db() as conn:
            doc_id = conn.execute("SELECT id FROM documents").fetchone()[0]
        for typed, saved in [("Rent 03/2026", "Rent 03-2026.pdf"),
                             ("Contract A\\B signed", "Contract A-B signed.pdf")]:
            self.client.post(f"/documents/{doc_id}/edit", data={"name": typed, "kind": "other"})
            with self.db() as conn:
                self.assertEqual(conn.execute("SELECT original_name FROM documents").fetchone()[0],
                                 saved)

    def test_long_names_with_a_dot_are_cut_to_the_limit(self):
        name = appmod.display_name("Booking No. ABC" + "X" * 200)
        self.assertEqual(len(name), 150)

    def test_storage_used_never_rounds_up_to_the_quota(self):
        self.app.config["USER_QUOTA_BYTES"] = 30 * appmod.MB
        self.app.config["MAX_RECEIPT_BYTES"] = 20 * appmod.MB
        self.app.config["MAX_CONTENT_LENGTH"] = 21 * appmod.MB  # set from the limit at startup
        self.upload("/year/2026/base", "a.pdf", b"%PDF-" + os.urandom(-1 + 15 * appmod.MB))
        self.upload("/year/2026/base", "b.pdf", b"%PDF-" + os.urandom(-1 + 15 * appmod.MB - 45000))
        html = self.client.get("/year/2026").data.decode()
        self.assertIn("29.9 MB used of 30 MB", html)

    def test_package_with_very_long_names_downloads(self):
        self.set_plan("pro")
        self.add_movement(2026, "Lorem ipsum " * 200, "Dolor sit amet " * 200,
                          "2026-03-01", "2026-03-03")
        resp, zf = self.package(2026)
        self.assertTrue(any(n.endswith("summary.pdf") for n in zf.namelist()))

    def test_page_count_placeholder_cannot_be_typed(self):
        self.assertNotEqual(package.PAGE_COUNT, "{nb}")
        self.assertNotIn(package.PAGE_COUNT, package.pdf_safe("Room {nb} \x00nb\x00 hostel"))
        self.assertEqual(len(package.clip_cell("x" * 500)), package.MAX_CELL)


if __name__ == "__main__":
    unittest.main()
