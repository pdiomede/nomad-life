"""Script injection: the Content-Security-Policy and escaping of what users type (v1.2.8)."""
import glob
import os
import re
import unittest

import app as appmod
from tests.helpers import AppTestCase

PAYLOAD = '"><script>alert(1)</script><img src=x onerror=alert(1)>'


class CspTests(AppTestCase):
    def test_html_pages_only_run_the_apps_own_scripts(self):
        self.signup()
        self.new_year(2026)
        for path in ("/", "/login", "/year/2026", "/settings", "/does-not-exist"):
            csp = self.client.get(path).headers["Content-Security-Policy"]
            self.assertIn("script-src 'self';", csp, path)
            self.assertIn("object-src 'none'", csp, path)
            self.assertIn("base-uri 'self'", csp, path)
            self.assertIn("frame-ancestors 'none'", csp, path)

    def test_receipt_downloads_get_only_the_frame_policy(self):
        self.signup()
        self.new_year(2026)
        self.upload("/year/2026/base", "r.pdf", b"%PDF-1 x")
        resp = self.client.get("/documents/1")
        self.assertEqual(resp.mimetype, "application/zip")
        self.assertEqual(resp.headers["Content-Security-Policy"], "frame-ancestors 'none'")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_no_inline_scripts_or_event_attributes(self):
        # The policy refuses inline scripts, so none may exist: only JSON data blocks.
        files = glob.glob(os.path.join(appmod.BASE_DIR, "templates", "**", "*.html"),
                          recursive=True) + [os.path.join(appmod.BASE_DIR, "static", "404.html")]
        for path in files:
            if os.sep + "email" + os.sep in path:
                continue  # emails are not served by the app, and mail apps never run scripts
            html = open(path, encoding="utf-8").read()
            for tag in re.findall(r"<script\b[^>]*>", html):
                self.assertTrue("src=" in tag or "application/json" in tag
                                or "application/ld+json" in tag, f"{path}: {tag}")
            self.assertIsNone(re.search(r"\son[a-z]+\s*=", html), path)
            self.assertNotIn("javascript:", html, path)

    def test_theme_init_script_is_versioned_and_served(self):
        html = self.app.test_client().get("/login").get_data(as_text=True)
        self.assertRegex(html, r'<script src="/static/js/theme-init\.js\?v=\w+"></script>')
        self.assertEqual(self.app.test_client().get("/static/js/theme-init.js").status_code, 200)


class EscapingTests(AppTestCase):
    def test_typed_text_is_escaped_everywhere_it_is_shown(self):
        self.signup()
        self.new_year(2026, base_city="Lisbon" + PAYLOAD[:40])
        mid = self.add_movement(2026, "Rome" + PAYLOAD[:60], "Atlantis" + PAYLOAD[:60],
                                "2026-03-01", "2026-03-02")
        for path in ("/year/2026", f"/movements/{mid}", "/year/2026/base"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertNotIn("<script>alert(1)", html, path)
            self.assertNotIn("<img src=x", html, path)
            self.assertIn("&lt;script&gt;alert(1)", html, path)


class InvisibleCharacterTests(AppTestCase):
    def test_addresses_with_control_or_format_characters_are_refused(self):
        for email in ("\u202emoc.x@evil.com", "a\u200b@x.com", "a\x00b@x.com"):
            self.assertFalse(appmod.valid_email(email), repr(email))
        self.assertTrue(appmod.valid_email("o'brien+tag@example.com"))

    def test_place_names_lose_invisible_characters(self):
        self.signup()
        self.new_year(2026, base_city="Lis\u202ebon")
        self.add_movement(2026, "Ro\u200bme\u202e", "Ita\u202ely", "2026-03-01", "2026-03-02")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT base_city FROM years").fetchone()[0], "Lisbon")
            self.assertEqual(tuple(conn.execute("SELECT city, country FROM movements").fetchone()),
                             ("Rome", "Italy"))


if __name__ == "__main__":
    unittest.main()
