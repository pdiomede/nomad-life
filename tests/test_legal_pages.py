"""Terms, Privacy and Refunds: public, indexable, naming the company, linked from every footer,
the sign up page and the sitemap; the footer credits Nemax Tech."""
import os
import re

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LegalPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["APP_BASE_URL"] = "https://nomadlife.pro"

    def test_pages(self):
        for endpoint, page in appmod.LEGAL_PAGES.items():
            html = self.client.get(f"/{endpoint}").get_data(as_text=True)
            self.assertIn(f"<h1>{page['title']}</h1>", html)
            self.assertIn(f"<title>{page['title']} | Nomad Life</title>", html)
            self.assertIn('<meta name="robots" content="index, follow">', html)
            self.assertIn(f'<link rel="canonical" href="https://nomadlife.pro/{endpoint}">', html)
            self.assertIn("Nemax Tech LLC</strong>, Sofia, Bulgaria. Company number (UIC) "
                          "207405380, VAT number BG207405380", html)
            body = html.split('class="guide-body"', 1)[1].split("</article>", 1)[0]
            self.assertNotRegex(body, "[\u2013\u2014]")

    def test_numbers_come_from_the_app(self):
        refunds = self.client.get("/refunds").get_data(as_text=True)
        self.assertIn(f"A full refund within {appmod.REFUND_DAYS} days", refunds)
        privacy = self.client.get("/privacy").get_data(as_text=True)
        self.assertIn(f"deleted after {appmod.AUDIT_DAYS} days", privacy)
        for name in os.listdir(os.path.join(ROOT, "templates", "legal")):
            with open(os.path.join(ROOT, "templates", "legal", name), encoding="utf-8") as fh:
                text = fh.read()
            for number in (str(appmod.AUDIT_DAYS), "207405380"):
                self.assertNotIn(number, text, name)  # from the app's settings, never typed

    def test_sitemap_and_sign_up(self):
        xml = self.client.get("/sitemap.xml").get_data(as_text=True)
        for endpoint in appmod.LEGAL_PAGES:
            self.assertIn(f"<loc>https://nomadlife.pro/{endpoint}</loc>", xml)
        signup = self.client.get("/signup").get_data(as_text=True)
        self.assertIn('agree to the <a href="/terms">Terms</a> and the '
                      '<a href="/privacy">Privacy Policy</a>', signup)

    def test_footer_everywhere(self):
        footer_links = ('<a href="/terms">Terms</a>', '<a href="/privacy">Privacy</a>',
                        '<a href="/refunds">Refunds</a>',
                        'Built by <a href="https://nemax.tech" target="_blank" '
                        'rel="noopener">Nemax Tech</a>')
        anonymous = self.client.get("/").get_data(as_text=True)
        self.signup()
        signed_in = self.client.get("/settings").get_data(as_text=True)
        for html in (anonymous, signed_in):
            footer = html.split('<footer class="footer">', 1)[1]
            for link in footer_links:
                self.assertIn(link, footer)
            self.assertNotIn("Paolo Diomede", footer)
        with open(os.path.join(ROOT, "static", "404.html"), encoding="utf-8") as fh:
            page404 = fh.read()
        for link in ('href="/terms"', 'href="/privacy"', 'href="/refunds"',
                     'href="https://nemax.tech"'):
            self.assertIn(link, page404)

    def test_structured_data_names_the_company(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('"parentOrganization": {"@type": "Organization"', html)
        self.assertIn('"name": "Nemax Tech LLC", "url": "https://nemax.tech"', html)
        self.assertIn('"vatID": "BG207405380"', html)
