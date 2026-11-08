"""Every tooltip of the app is a CSS one (class "tip" and data-tip), never the browser's own
title attribute (2.1.1)."""
import os
import re

from tests.helpers import AppTestCase
from tests.test_calendar_integration import ROOT, read

# A title attribute on any element; the <title> element and meta tags are not tooltips.
TITLE_ATTR = re.compile(r"<(?!title\b|meta\b)[a-z][a-z0-9]*\b[^<>]*\stitle=", re.I)


class NoTitleTooltipTests(AppTestCase):
    def test_no_template_uses_the_title_attribute(self):
        found = []
        for folder, _dirs, files in os.walk(os.path.join(ROOT, "templates")):
            for name in files:
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                found += [f"{os.path.relpath(path, ROOT)}: {m.group(0)[:80]}" for m in TITLE_ATTR.finditer(text)]
        found += [f"static/404.html: {m.group(0)[:80]}" for m in TITLE_ATTR.finditer(read("static", "404.html"))]
        self.assertEqual(found, [])

    def test_no_script_sets_a_title(self):
        for name in os.listdir(os.path.join(ROOT, "static", "js")):
            js = read("static", "js", name)
            self.assertNotRegex(js, r"\.title\s*=", name)
            self.assertNotIn('title="', js, name)
            self.assertNotIn('"title"', js, name)

    def test_the_shared_tooltip_rules(self):
        css = read("static", "css", "style.css")
        self.assertIn(".tip[data-tip]::after {\n  content: attr(data-tip); display: none; position: absolute;", css)
        self.assertIn("@media (hover: hover) { .tip[data-tip]:hover::after { display: block; } }", css)
        self.assertIn(".tip[data-tip]:focus-visible::after, .tip[tabindex][data-tip]:focus::after { display: block; }", css)
        self.assertIn(".tip-end[data-tip]::after { left: auto; right: 0; }", css)
        self.assertIn(".user-menu[open] > .user-menu-trigger.tip::after { display: none !important; }", css)

    def test_masked_icons_carry_the_tooltip_on_a_wrapper(self):
        js = read("static", "js", "theme.js")
        self.assertIn('homeTip.setAttribute("data-tip", "Your base country");', js)
        self.assertIn('starTip.setAttribute("data-tip", on ? "Remove from favorites" : "Add to favorites");', js)
        self.assertIn('short.className = "tip"; short.setAttribute("data-tip", name);', js)


class RenderedTooltipTests(AppTestCase):
    def test_the_dashboard_and_receipts(self):
        self.signup()
        self.new_year(2026)
        self.add_movement(2026, "Rome", "Italy", "2026-02-01", "2026-02-10")
        self.add_movement(2026, "", "Spain", "2026-02-05", "2026-02-12")
        html = self.client.get("/year/2026").get_data(as_text=True)
        self.assertIn('<div class="muted small tip tip-end counted-note" tabindex="0" data-tip="A day shared', html)
        self.assertIn('<th scope="col" aria-label="Monday"><span class="tip" data-tip="Monday" aria-hidden="true">M</span></th>', html)
        self.assertIn('class="map-zoom-btn tip tip-end" data-map-zoom="in" aria-label="Zoom in" data-tip="Zoom in"', html)
        self.assertIn('class="user-menu-trigger tip tip-end"', html)
        self.assertIsNone(TITLE_ATTR.search(html))
