"""The "Show 183 marker" switch of the Days in Base country card, and the renamed cards."""
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
        start = html.index('<div class="stat-head">')
        return html, html[start:html.index('<div class="card stat">', start)]

    def test_the_card_has_the_switch_and_marks_what_it_hides(self):
        self.new_year(2030)  # a future year: short of the line
        html, card = self.card(2030)
        threshold = appmod.RESIDENCE_THRESHOLD
        self.assertIn('<button type="button" class="switch" role="switch" aria-checked="false" '
                      f'data-marker-switch hidden><span class="switch-label">Show {threshold} marker</span>',
                      card)
        self.assertIn('<i class="day-marker" style="left:', card)
        self.assertIn('<p class="pill pill-warn day-marker">', card)
        self.assertIn('<p class="stat-label">Days outside Base country</p>', html)
        # The same switch on Days per Country, one setting for both.
        self.assertEqual(html.count(f'data-marker-switch hidden><span class="switch-label">Show {threshold} marker</span>'), 2)
        self.assertNotIn("Days abroad", html)

    def test_the_switch_sits_above_the_title(self):
        self.new_year(2030)
        _, card = self.card(2030)
        self.assertLess(card.index("data-marker-switch"), card.index('<p class="stat-label">Days in Base country</p>'))
        css = read("static", "css", "style.css")
        self.assertIn(".stat-head .switch { align-self: flex-end; }", css)
        # The four top cards' titles look like the card titles below them (h2).
        self.assertIn(".year-stats .stat-label { font-size: 17px; font-weight: 600; letter-spacing: -0.01em; color: var(--text); }", css)
        self.assertIn(".stat-label { font-size: 13px; color: var(--muted); font-weight: 500; }", css)  # admin keeps its own

    def test_the_pill_of_a_year_over_the_line_is_marked_too(self):
        self.new_year(2020)
        self.add_movement(2020, "Lisbon", "Portugal", "2020-01-01", "2020-07-01")  # 183 days at the base
        _, card = self.card(2020)
        self.assertIn('<p class="pill pill-ok day-marker">At least', card)


class CountryMarkerTests(AppTestCase):
    def test_every_country_bar_tick_is_a_marker_and_the_help_is_short(self):
        self.signup()
        self.new_year(2030, "Sofia", "Bulgaria")
        self.add_movement(2030, "Rome", "Italy", "2030-03-01", "2030-03-05")
        html = self.client.get("/year/2030").get_data(as_text=True)
        card = html[html.index("<h2>Days per Country</h2>"):]
        card = card[:card.index("</section>")]
        self.assertIn("data-marker-switch hidden", card)
        self.assertEqual(card.count('<i class="day-marker" style="left:'), 2)  # Bulgaria, Italy
        self.assertEqual(card.count("<i "), 2)
        self.assertIn('<p class="muted small">A day shared by two stays, such as a travel day, '
                      'counts toward the stay that started later.</p>', card)
        self.assertNotIn("The marker shows", html)


class MarkerSwitchAssetTests(AppTestCase):
    def test_off_hides_the_marker_before_the_page_is_drawn(self):
        css = read("static", "css", "style.css")
        self.assertIn('[data-marker="off"] .day-marker { display: none !important; }', css)
        self.assertIn('.switch[aria-checked="true"] .switch-thumb { transform: translateX(14px); }', css)
        self.assertIn('.switch[aria-checked="true"] .switch-track { background: var(--success); }', css)
        init = read("static", "js", "theme-init.js")
        # Hidden until switched on in this browser.
        self.assertIn('localStorage.getItem("nl_marker") !== "on"', init)
        self.assertIn('setAttribute("data-marker", "off")', init)

    def test_back_shows_the_saved_theme_and_marker(self):
        # A page restored by the Back button kept the theme and marker it had then: switching
        # either one on another page and going back showed the old choice until a reload.
        js = read("static", "js", "theme.js")
        block = js[js.index('window.addEventListener("pageshow", function (e) {\n    if (!e.persisted) return;\n    var theme'):][:700]
        self.assertIn('localStorage.getItem("theme")', block)
        self.assertIn('localStorage.getItem("nl_marker") !== "on"', block)
        self.assertIn('root.setAttribute("data-theme", theme); else root.removeAttribute("data-theme");', block)
        self.assertIn('root.setAttribute("data-marker", "off"); else root.removeAttribute("data-marker");', block)
        self.assertIn("syncMarkerSwitches();", block)

    def test_the_switch_keeps_the_choice_in_this_browser(self):
        js = read("static", "js", "theme.js")
        block = js[js.index('var markerSwitches = document.querySelectorAll("[data-marker-switch]");'):][:2200]
        self.assertIn("sw.hidden = false;", block)
        # Flipping one switch moves every switch on the page.
        self.assertIn('markerSwitches.forEach(function (sw) { sw.setAttribute("aria-checked", on); });', block)
        self.assertIn('localStorage.setItem("nl_marker", off ? "off" : "on");', block)
        self.assertIn("syncMarkerSwitches();", block)
