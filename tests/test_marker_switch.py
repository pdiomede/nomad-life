"""The "Show 183 marker" switch of the Days in Base Country card, and the renamed cards."""
import os

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


class MarkerSwitchTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def card(self, year):
        html = self.client.get(f"/year/{year}").get_data(as_text=True)
        start = html.index('<p class="stat-label">Days in Base Country</p>')
        return html, html[start:html.index('<div class="card stat">', start)]

    def test_the_card_has_the_switch_and_marks_what_it_hides(self):
        self.new_year(2030)  # a future year: short of the line
        html, card = self.card(2030)
        threshold = appmod.RESIDENCE_THRESHOLD
        self.assertIn('<button type="button" class="switch" role="switch" aria-checked="true" '
                      f'data-marker-switch hidden><span class="switch-label">Show {threshold} marker</span>',
                      card)
        self.assertIn('<i class="base-marker" style="left:', card)
        self.assertIn('<p class="pill pill-warn base-marker">', card)
        self.assertIn('<p class="stat-label">Days outside the Base Country</p>', html)
        self.assertNotIn("Days abroad", html)

    def test_the_pill_of_a_year_over_the_line_is_marked_too(self):
        self.new_year(2020)  # a past year: every day counts toward the base
        _, card = self.card(2020)
        self.assertIn('<p class="pill pill-ok base-marker">At least', card)


class MarkerSwitchAssetTests(AppTestCase):
    def test_off_hides_the_marker_before_the_page_is_drawn(self):
        css = read("static", "css", "style.css")
        self.assertIn('[data-marker="off"] .base-marker { display: none !important; }', css)
        self.assertIn('.switch[aria-checked="true"] .switch-thumb { transform: translateX(14px); }', css)
        self.assertIn('.switch[aria-checked="true"] .switch-track { background: var(--success); }', css)
        init = read("static", "js", "theme-init.js")
        self.assertIn('localStorage.getItem("nl_marker") === "off"', init)
        self.assertIn('setAttribute("data-marker", "off")', init)

    def test_the_switch_keeps_the_choice_in_this_browser(self):
        js = read("static", "js", "theme.js")
        block = js[js.index('document.querySelectorAll("[data-marker-switch]")'):][:900]
        self.assertIn("sw.hidden = false;", block)
        self.assertIn('localStorage.setItem("nl_marker", "off")', block)
        self.assertIn('localStorage.removeItem("nl_marker")', block)
        self.assertIn('sw.setAttribute("aria-checked"', block)
