"""Admin account page: a public IP opens its location on ipinfo.io in a new tab."""
import unittest

import app as appmod
from tests.helpers import AppTestCase


class IpLookupTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.admin = self.app.test_client()
        self.signup("admin@example.com", client=self.admin)

    def test_url(self):
        self.assertEqual(appmod.ip_lookup_url("8.8.8.8"), "https://ipinfo.io/8.8.8.8")
        self.assertEqual(appmod.ip_lookup_url("2606:4700:4700::1111"),
                         "https://ipinfo.io/2606:4700:4700::1111")
        for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.2", "203.0.113.7", "2001:db8::42",
                   "::1", "unknown", "", None, "8.8.8.8/evil"):
            self.assertIsNone(appmod.ip_lookup_url(ip), ip)

    def test_account_page_links_public_ip(self):
        client = self.app.test_client()
        client.environ_base["REMOTE_ADDR"] = "8.8.8.8"
        self.signup("a@example.com", client=client)
        with self.db() as conn:
            uid = conn.execute("SELECT id FROM users WHERE email = 'a@example.com'").fetchone()[0]
        page = self.admin.get(f"/admin/users/{uid}").get_data(as_text=True)
        self.assertIn('<a href="https://ipinfo.io/8.8.8.8" target="_blank" '
                      'rel="noopener noreferrer"', page)
        self.assertEqual(page.count('href="https://ipinfo.io/8.8.8.8"'), 2)  # sign up, sign in
        self.assertIn('href="/admin?search=8.8.8.8#accounts">Same IP</a>', page)

    def test_accounts_table_links_public_ip(self):
        client = self.app.test_client()
        client.environ_base["REMOTE_ADDR"] = "8.8.8.8"
        self.signup("a@example.com", client=client)
        html = self.admin.get("/admin").get_data(as_text=True)
        row = html.split(">a@example.com</a>", 1)[1]
        cell = row[:row.index("</tr>")]
        self.assertIn('<a href="https://ipinfo.io/8.8.8.8" target="_blank" '
                      'rel="noopener noreferrer"', cell)
        self.assertIn('</a><br><span class="muted">at last sign in</span>', cell)
        # The admin signed up from the test client's 127.0.0.1: no location, plain text.
        admin_row = html.split(">admin@example.com</a>", 1)[1]
        admin_row = admin_row[:admin_row.index("</tr>")]
        self.assertIn('127.0.0.1<br><span class="muted">at last sign in</span>', admin_row)
        self.assertNotIn("ipinfo.io", admin_row)


if __name__ == "__main__":
    unittest.main()
