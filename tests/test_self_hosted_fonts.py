"""The site's fonts (Inter, Outfit) come from static/fonts/web, never from Google Fonts."""
import os
import re

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class SelfHostedFontTests(AppTestCase):
    def test_no_page_asks_google(self):
        for path in ("/", "/docs", "/guides", "/login"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertNotIn("fonts.googleapis.com", html, path)
            self.assertNotIn("fonts.gstatic.com", html, path)
        self.assertNotIn("googleapis", read("static", "404.html"))
        for policy in (appmod.CSP, appmod.CAPTCHA_CSP):
            self.assertIn("font-src 'self';", policy)
            self.assertNotIn("google", policy)

    def test_every_declared_font_file_exists_and_is_served(self):
        css = read("static", "css", "style.css")
        files = re.findall(r'url\("\.\./fonts/web/([\w-]+\.woff2)"\)', css)
        self.assertEqual(len(files), 9)
        for name in files:
            resp = self.client.get(f"/static/fonts/web/{name}")
            self.assertEqual(resp.status_code, 200, name)
            self.assertEqual(resp.mimetype, "font/woff2")
            self.assertEqual(resp.data[:4], b"wOF2")
        for name in ("LICENSE-Inter.txt", "LICENSE-Outfit.txt"):
            self.assertIn("SIL Open Font License", read("static", "fonts", "web", name))

    def test_the_text_font_is_preloaded(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertRegex(html, r'<link rel="preload" href="/static/fonts/web/inter-latin\.woff2" '
                               r'as="font" type="font/woff2" crossorigin>')
