"""Documentation page (/docs): public, indexable, linked from the header, footer and menu, with
contents that match its sections, images that exist and numbers taken from the app."""
import json
import os
import re

import app as appmod
from tests.helpers import AppTestCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


class DocsPageTests(AppTestCase):
    def page(self, client=None):
        resp = (client or self.app.test_client()).get("/docs")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_public_and_indexable(self):
        html = self.page()
        self.assertIn('<meta name="robots" content="index, follow', html)
        self.assertIn('<link rel="canonical" href="http://localhost:5050/docs">', html)
        self.assertIn('<meta property="og:url" content="http://localhost:5050/docs">', html)
        self.assertIn("<title>Documentation | Nomad Life</title>", html)
        sitemap = self.app.test_client().get("/sitemap.xml").get_data(as_text=True)
        self.assertIn("<loc>http://localhost:5050/docs</loc>", sitemap)
        robots = self.app.test_client().get("/robots.txt").get_data(as_text=True)
        self.assertNotIn("Disallow: /docs", robots)
        # Signed in it works too, and stays private to that browser's cache.
        self.signup()
        resp = self.client.get("/docs")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("no-store", resp.headers["Cache-Control"])

    def test_header_footer_and_menu_links(self):
        html = self.page()  # signed out: the header button and the footer link
        self.assertIn('class="icon-btn docs-btn" href="/docs" data-tip="Documentation" '
                      'aria-label="Documentation" aria-current="page"', html)
        self.assertIn('<a href="/docs">Docs</a>', html)
        landing = self.app.test_client().get("/").get_data(as_text=True)
        self.assertIn('data-tip="Documentation"', landing)
        self.assertIn('<a href="/docs">Docs</a>', landing)
        self.signup()
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('class="icon-btn docs-btn docs-btn-app" href="/docs" data-tip="Documentation"', html)
        self.assertIn('<a class="user-menu-item menu-docs" href="/docs">Documentation</a>', html)
        # The chat bubble names itself with the same tooltip, not a title.
        self.assertIn('href="/support" data-tip="Contact support"', html)
        self.assertNotIn('title="Contact support"', html)
        # Documentation comes right before the chat bubble.
        self.assertLess(html.index('docs-btn'), html.index('support-btn'))

    def test_contents_match_the_sections(self):
        html = self.page()
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids")
        for href in re.findall(r'href="#([^"]+)"', html):
            self.assertIn(href, ids, href)
        sections = re.findall(r'<section id="([^"]+)" class="doc-(?:section|sub)"', html)
        toc = [i for sid, _t, subs in appmod.DOCS_TOC for i in [sid] + [s for s, _ in subs]]
        self.assertEqual(sections, toc)  # every section listed, in page order
        # The contents and the search results use the same name for a section.
        titles = dict(re.findall(r'<section id="([^"]+)" class="doc-(?:section|sub)" '
                                 r'data-doc-title="([^"]+)"', html))
        for sid, title, subs in appmod.DOCS_TOC:
            for i, t in [(sid, title)] + subs:
                self.assertEqual(titles[i], t, i)

    def test_images_exist_with_sizes_and_alt_text(self):
        html = self.page()
        with open(os.path.join(STATIC, "img", "docs", "shots.json"), encoding="utf-8") as fh:
            sizes = json.load(fh)
        images = re.findall(r"<img [^>]*>", html)
        docs_images = [i for i in images if "/static/img/docs/" in i]
        self.assertGreaterEqual(len(docs_images), 16)
        for tag in docs_images:
            src = re.search(r'src="/static/(img/docs/[^"?]+)', tag)[1]
            self.assertTrue(os.path.exists(os.path.join(STATIC, src)), src)
            name = re.search(r"img/docs/(.+)-(?:light|dark)\.webp", src)[1]
            self.assertEqual(re.search(r'width="(\d+)" height="(\d+)"', tag).groups(),
                             tuple(str(n) for n in sizes[name]), name)
            self.assertRegex(tag, r'alt="[^"]{20,}"')
            self.assertIn('loading="lazy"', tag)

    def test_numbers_come_from_the_app(self):
        html = self.page()
        self.assertIn(f"Confirm within <strong>{appmod.VERIFY_MINUTES} minutes</strong>", html)
        self.assertIn(f"up to {appmod.PLACE_MAX} characters", html)
        self.assertIn(f"up to {appmod.MOVEMENTS_MAX:,} movements a year", html)
        self.assertIn(f"up to {appmod.NOTES_MAX:,} characters", html)
        self.assertIn(f"After {appmod.LIMITS[('fail', 'email')]} wrong passwords in "
                      f"{appmod.LIMIT_WINDOW_MINUTES} minutes", html)
        self.assertIn("The 183 day line", html)
        # The plans table follows the plans and their prices, also a changed price.
        for p in appmod.PLANS.values():
            self.assertIn(f"<th scope=\"row\">{p['name']}</th>", html)
        self.signup("admin@example.com")
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.client.post("/admin/plans/pro", data={"month": "5", "year": "50"})
        self.assertIn("<td>$50 a year</td>", self.page())

    def test_scripts_and_text_rules(self):
        html = self.page()
        self.assertIn('src="/static/js/docs.js?v=', html)
        self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/ld\+json)", html))
        self.assertIsNone(re.search(r"\son[a-z]+=", html))
        self.assertNotIn("\u2014", html)
        self.assertNotIn("\u2013", html)
        # The search stays hidden without JavaScript, which shows it.
        self.assertIn('class="docs-search" role="search" data-docs-search hidden', html)
        # The theme follows the page: both screenshots of each pair are there.
        self.assertEqual(html.count("-light.webp"), html.count("-dark.webp"))

    def test_docs_js_changes_the_asset_version(self):
        path = os.path.join(STATIC, "js", "docs.js")
        with open(path, encoding="utf-8") as fh:
            original = fh.read()
        version = re.search(r"docs\.js\?v=(\w+)", self.page())[1]
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n// changed\n")
            other = appmod.create_app(dict(self.app.config))
            html = other.test_client().get("/docs").get_data(as_text=True)
            self.assertNotEqual(re.search(r"docs\.js\?v=(\w+)", html)[1], version)
        finally:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(original)


class DocsTouchesTests(AppTestCase):
    def test_support_address_back_to_top_and_theme_tooltip(self):
        html = self.app.test_client().get("/docs").get_data(as_text=True)
        self.assertIn('Write to <a href="mailto:support@nomadlife.pro">support@nomadlife.pro</a> '
                      "from your account's email address.", html)
        self.assertIn('Still stuck? Write to <a href="mailto:support@nomadlife.pro">'
                      'support@nomadlife.pro</a></p>', html)
        self.assertNotIn("mailto:info@nomadlife.pro\">info@nomadlife.pro</a> from", html)
        # One "Back to the top" at the end of every main section, to the page's top.
        self.assertEqual(html.count('<p class="doc-top"><a href="#top">'), len(appmod.DOCS_TOC))
        self.assertIn('<div class="docs-head" id="top">', html)
        for section in re.findall(r'<section id="[^"]+" class="doc-section".*?\n      </section>',
                                  html, re.S):
            self.assertIn('Back to the top</a></p>\n      </section>', section[-80:])
        self.assertIn('id="theme-toggle" type="button" aria-label="Toggle light or dark mode" '
                      'data-tip="Light or dark mode"', html)
        self.assertNotIn('title="Toggle theme"', html)
        # The plans table shows the default prices.
        self.assertIn("<td>$9.99 a year</td>", html)
        self.assertIn("<td>$19.99 a year</td>", html)
