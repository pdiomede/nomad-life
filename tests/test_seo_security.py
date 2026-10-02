"""Security headers and files from the 2026-10 URL report, and per page link previews and
structured data for search engines."""
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


class SecurityTests(AppTestCase):
    def test_permissions_policy_everywhere(self):
        for path in ("/", "/login", "/robots.txt", "/static/css/style.css"):
            resp = self.client.get(path)
            self.assertEqual(resp.headers["Permissions-Policy"], appmod.PERMISSIONS_POLICY, path)
        self.assertIn("camera=()", appmod.PERMISSIONS_POLICY)
        self.assertIn("geolocation=()", appmod.PERMISSIONS_POLICY)

    def test_security_txt(self):
        self.app.config["APP_BASE_URL"] = "https://nomadlife.pro"
        resp = self.client.get("/.well-known/security.txt")
        self.assertEqual(resp.mimetype, "text/plain")
        fields = dict(line.split(": ", 1) for line in resp.get_data(as_text=True).splitlines())
        self.assertEqual(fields["Contact"], f"mailto:{appmod.SUPPORT_EMAIL}")
        self.assertEqual(fields["Canonical"], "https://nomadlife.pro/.well-known/security.txt")
        self.assertEqual(fields["Policy"], "https://nomadlife.pro/security")
        policy = self.client.get("/security").get_data(as_text=True)
        self.assertIn("<h1>Security Policy</h1>", policy)
        self.assertIn(f'href="mailto:{appmod.SUPPORT_EMAIL}"', policy)
        expires = datetime.strptime(fields["Expires"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        # RFC 9116: in the future, and less than a year ahead.
        self.assertGreater(expires, now + timedelta(days=30))
        self.assertLess(expires, now + timedelta(days=365))

    def test_too_many_attempts_says_when_to_retry(self):
        with mock.patch.object(appmod, "take_attempt", return_value=(3, "ip", None)):
            resp = self.client.post("/signup", data={"email": "a@example.com",
                                                     "password": "password1",
                                                     "confirm": "password1"})
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.headers["Retry-After"], "180")
        self.assertNotIn("Retry-After", self.client.get("/login").headers)


class SeoTests(AppTestCase):
    def meta(self, html, attr, name):
        return re.search(rf'<meta {attr}="{name}" content="([^"]*)"', html)[1]

    def ld(self, html):
        return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                                    html, re.S)[1])

    def test_docs_has_its_own_link_preview(self):
        html = self.client.get("/docs").get_data(as_text=True)
        for attr, name in (("property", "og:title"), ("name", "twitter:title")):
            self.assertEqual(self.meta(html, attr, name), appmod.DOCS_TITLE)
        for attr, name in (("property", "og:description"), ("name", "twitter:description"),
                           ("name", "description")):
            self.assertEqual(self.meta(html, attr, name), appmod.DOCS_DESCRIPTION)
        self.assertIn(f"<title>{appmod.DOCS_TITLE}</title>", html)

    def test_landing_keeps_the_site_preview(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertEqual(self.meta(html, "property", "og:title"), appmod.SITE_TITLE)
        self.assertEqual(self.meta(html, "name", "twitter:title"), appmod.SITE_TITLE)
        self.assertIn("max-snippet:-1", self.meta(html, "name", "robots"))

    def test_structured_data(self):
        landing = self.ld(self.client.get("/").get_data(as_text=True))["@graph"]
        org = next(x for x in landing if x["@type"] == "Organization")
        self.assertTrue(org["logo"]["url"].endswith("/static/img/icon-512.png"))
        site = next(x for x in landing if x["@type"] == "WebSite")
        self.assertEqual(site["publisher"]["@id"], org["@id"])
        docs = self.ld(self.client.get("/docs").get_data(as_text=True))["@graph"]
        crumbs = next(x for x in docs if x["@type"] == "BreadcrumbList")["itemListElement"]
        self.assertEqual([c["name"] for c in crumbs], ["Nomad Life", "Documentation"])
        self.assertTrue(crumbs[1]["item"].endswith("/docs"))

    def test_private_pages_stay_out_of_search(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertEqual(self.meta(html, "name", "robots"), "noindex, follow")


if __name__ == "__main__":
    unittest.main()
