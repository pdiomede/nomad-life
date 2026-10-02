"""Public guides (/guides): indexable pages with their own titles, link previews and structured
data, in the sitemap and linked from the landing page, the footer and the docs."""
import json
import os
import re

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE_DIR = os.path.join(ROOT, "templates", "guides")
BASE = "https://nomadlife.pro"


class GuideTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["APP_BASE_URL"] = BASE

    def page(self, path):
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200, path)
        return resp.get_data(as_text=True)

    def meta(self, html, attr, name):
        return re.search(rf'<meta {attr}="{name}" content="([^"]*)"', html)[1]

    def ld(self, html):
        return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                                    html, re.S)[1])["@graph"]

    def test_every_guide_has_a_template_and_back(self):
        files = {f[:-5] for f in os.listdir(GUIDE_DIR)
                 if f.endswith(".html") and not f.startswith("_") and f != "index.html"}
        self.assertEqual(files, {g["slug"] for g in appmod.GUIDES})

    def test_each_guide_page(self):
        for g in appmod.GUIDES:
            url = f"{BASE}/guides/{g['slug']}"
            html = self.page(f"/guides/{g['slug']}")
            self.assertIn(f"<title>{g['seo_title']}</title>", html)
            self.assertIn(f"<h1>{g['title']}</h1>", html)
            self.assertEqual(self.meta(html, "name", "description"), g["description"])
            self.assertEqual(self.meta(html, "property", "og:title"), g["title"])
            self.assertEqual(self.meta(html, "property", "og:url"), url)
            self.assertIn("index, follow", self.meta(html, "name", "robots"))
            self.assertIn(f'<link rel="canonical" href="{url}">', html)
            graph = self.ld(html)
            article = next(x for x in graph if x["@type"] == "Article")
            self.assertEqual(article["headline"], g["title"])
            self.assertEqual(article["dateModified"], g["updated"])
            crumbs = next(x for x in graph if x["@type"] == "BreadcrumbList")["itemListElement"]
            self.assertEqual([c["name"] for c in crumbs], ["Nomad Life", "Guides", g["title"]])
            self.assertIn("It is not tax or legal advice", html)
            self.assertIn(f'href="/signup"', html)

    def test_index_lists_every_guide(self):
        html = self.page("/guides")
        for g in appmod.GUIDES:
            self.assertIn(f'href="/guides/{g["slug"]}"', html)
        self.assertIn(f'<link rel="canonical" href="{BASE}/guides">', html)
        self.assertEqual(next(x for x in self.ld(html) if x["@type"] == "CollectionPage")
                         ["hasPart"][0]["url"], f"{BASE}/guides/{appmod.GUIDES[0]['slug']}")

    def test_unknown_guide_is_not_found(self):
        self.assertEqual(self.client.get("/guides/nope").status_code, 404)
        self.assertEqual(self.client.get("/guides/_layout").status_code, 404)

    def test_sitemap(self):
        xml = self.client.get("/sitemap.xml").get_data(as_text=True)
        self.assertIn(f"<loc>{BASE}/guides</loc>", xml)
        for g in appmod.GUIDES:
            self.assertIn(f"<loc>{BASE}/guides/{g['slug']}</loc><lastmod>{g['updated']}</lastmod>",
                          xml)
        robots = self.client.get("/robots.txt").get_data(as_text=True)
        self.assertNotIn("Disallow: /guides", robots)

    def test_linked_from_landing_footer_and_docs(self):
        landing = self.page("/")
        for g in appmod.GUIDES:
            self.assertIn(f'href="/guides/{g["slug"]}"', landing)
        self.assertIn('<a href="/guides">Guides</a>', self.page("/login"))  # the footer
        docs = self.page("/docs")
        self.assertIn('href="/guides/183-day-rule"', docs)

    def test_links_inside_guides_work(self):
        for g in appmod.GUIDES:
            html = self.page(f"/guides/{g['slug']}")
            body = html.split('class="guide-body"', 1)[1].split('class="guide-note', 1)[0]
            for href in re.findall(r'href="(/[^"#]*)', body):
                self.assertEqual(self.client.get(href).status_code, 200, (g["slug"], href))

    def test_the_threshold_comes_from_the_app(self):
        # As on the docs page: the day line is the app's constant, never typed in the text.
        for name in os.listdir(GUIDE_DIR):
            with open(os.path.join(GUIDE_DIR, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(str(appmod.RESIDENCE_THRESHOLD), text, name)
            self.assertNotRegex(text, "[\u2013\u2014]", name)  # no en or em dashes
        html = self.page("/guides/183-day-rule")
        self.assertIn(f"{appmod.RESIDENCE_THRESHOLD} days or more", html)
