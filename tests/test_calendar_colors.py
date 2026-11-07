"""Calendar colors (1.12.16): a 16 color palette, a country's color chosen in the Nomad Life
legend for every year, and the Google tab's events in the same colors."""
import json
import os
import re

import app as appmod
from tests.helpers import AppTestCase
from tests.test_calendar_tabs import TabsCase, ev

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


def legend_color(html, country):
    m = re.search(r'class="cal-swatch cal-color-btn (cal-[a-z0-9]+)" aria-haspopup="true" '
                  r'aria-expanded="false" aria-label="Color of ' + re.escape(country) + ':', html)
    return m.group(1) if m else None


class PaletteTests(AppTestCase):
    def test_fifteen_colors_in_every_theme(self):
        # 2.0.0: the user's palette, the same in every theme, in the picker 5 a row.
        self.assertEqual(len(appmod.CALENDAR_PALETTE), 15)
        self.assertEqual(sorted(appmod.CALENDAR_COLOR_NAMES), sorted(appmod.CALENDAR_PALETTE))
        self.assertEqual(list(appmod.CALENDAR_COLOR_NAMES.values()),
                         ["Red", "Orange", "Yellow", "Lime", "Green", "Teal", "Cyan", "Blue", "Indigo",
                          "Purple", "Magenta", "Pink", "Raspberry", "Brown", "Slate"])
        css = read("static", "css", "style.css")
        wanted = {"#D94848", "#F08935", "#D9B630", "#8CAE3A", "#2B9E62", "#169D91", "#28B7D2", "#448DD2",
                  "#4756C4", "#7650BE", "#BC40B0", "#EC7AA2", "#A94169", "#A76A43", "#617988"}
        found = set()
        for key in appmod.CALENDAR_PALETTE:
            values = re.findall(rf"--{key}: (#[0-9A-F]{{6}});", css)
            self.assertEqual(len(values), 3, key)  # light, dark, dark by system
            self.assertEqual(len(set(values)), 1, key)
            found.add(values[0])
            self.assertRegex(css, rf"\.{key} {{ background: var\(--{key}\); color: #(1B2030|FFFFFF); }}")
        self.assertEqual(found, wanted)
        self.assertIn("grid-template-columns: repeat(5, 22px);", css)


class ColorPickerTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2027, "Lisbon", "Portugal")
        self.add_movement(2027, "Bangkok", "Thailand", "2027-01-18", "2027-01-30")
        self.add_movement(2027, "Rome", "Italy", "2027-03-01", "2027-03-04")

    def html(self, year=2027, query=""):
        return self.client.get(f"/year/{year}{query}").get_data(as_text=True)

    def pick(self, country, color, client=None, accept_json=True, year=2027):
        return (client or self.client).post("/calendar/colors", data={
            "country": country, "color": color, "year": str(year),
            "back": f"/year/{year}?cal=local#calendar"},
            headers={"Accept": "application/json"} if accept_json else {})

    def test_the_picker_is_in_the_nomad_life_legend(self):
        html = self.html()
        self.assertEqual(legend_color(html, "Thailand"), "cal-1")
        form = html[html.index('data-key="thailand"'):]
        form = form[:form.index("</form>")]
        self.assertEqual(form.count('class="cal-color-opt '), 15)
        self.assertIn('<button type="submit" name="color" value="cal-1" class="cal-color-opt cal-1" aria-label="Blue" title="Blue" aria-pressed="true">', form)
        self.assertIn('>Automatic</button>', form)
        self.assertIn('data-c="thailand"', html)

    def test_a_chosen_color_applies_in_every_year(self):
        resp = self.pick("Thailand", "cal-9")
        answer = resp.get_json()
        self.assertEqual(answer["colors"]["thailand"], "cal-9")
        self.assertEqual(answer["names"]["cal-9"], "Lime")
        html = self.html()
        self.assertEqual(legend_color(html, "Thailand"), "cal-9")
        self.assertIn('class="cal-day cal-9" data-tip="Mon 18 Jan 2027 \U0001F1F9\U0001F1ED Thailand" data-c="thailand"', html)
        self.new_year(2028, "Lisbon", "Portugal")
        self.add_movement(2028, "Chiang Mai", "Thailand", "2028-02-01", "2028-02-03")
        self.assertEqual(legend_color(self.html(2028), "Thailand"), "cal-9")

    def test_automatic_colors_skip_a_chosen_one(self):
        self.pick("Thailand", "cal-2")  # Italy's automatic color
        html = self.html()
        self.assertEqual(legend_color(html, "Thailand"), "cal-2")
        self.assertNotEqual(legend_color(html, "Italy"), "cal-2")
        self.assertEqual(self.pick("Thailand", "auto").get_json()["colors"]["thailand"], "cal-1")
        self.assertEqual(legend_color(self.html(), "Thailand"), "cal-1")

    def test_the_answer_recolors_the_whole_year(self):
        # Picking Italy's automatic color for Thailand moves Italy: the answer says so.
        colors = self.pick("Thailand", "cal-2").get_json()["colors"]
        self.assertEqual(colors["thailand"], "cal-2")
        self.assertNotEqual(colors["italy"], "cal-2")
        self.assertEqual(colors["portugal"], "cal-base")

    def test_refused(self):
        self.assertEqual(self.pick("Thailand", "red").status_code, 400)
        self.assertEqual(self.pick("", "cal-3").status_code, 400)
        resp = self.client.post("/calendar/colors", data={"country": "Thailand", "color": "cal-3",
                                                          "back": "https://evil.example/year/2027"})
        self.assertEqual(resp.headers["Location"], "/app")

    def test_other_accounts_are_not_affected(self):
        self.pick("Thailand", "cal-9")
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.new_year(2027, "Lisbon", "Portugal", client=other)
        other.post("/year/2027/movements/new", data={"country": "Thailand", "city": "", "start_date": "2027-01-18",
                                                     "end_date": "2027-01-30", "notes": ""})
        self.assertEqual(legend_color(other.get("/year/2027").get_data(as_text=True), "Thailand"), "cal-1")

    def test_the_cap(self):
        with self.db() as conn:
            uid = conn.execute("SELECT id FROM users").fetchone()[0]
            conn.executemany("INSERT INTO country_colors (user_id, country, color) VALUES (?, ?, 'cal-1')",
                             [(uid, f"Place {i}") for i in range(appmod.COLORS_MAX)])
        self.assertEqual(self.pick("Thailand", "cal-9").status_code, 409)
        self.assertEqual(self.pick("Place 1", "cal-9").status_code, 200)  # changing one is fine

    def test_without_javascript_back_on_an_open_calendar(self):
        resp = self.pick("Thailand", "cal-9", accept_json=False)
        self.assertEqual(resp.headers["Location"], "/year/2027?cal=local#calendar")
        self.assertIn('<details class="card fold-card cal-card" open>', self.html(query="?cal=local"))

    def test_assets(self):
        css = read("static", "css", "style.css")
        self.assertIn('.cal-color:hover .cal-color-menu, .cal-color:not(.js-picker):focus-within .cal-color-menu,\n'
                      '.cal-color-btn[aria-expanded="true"] + .cal-color-menu { display: grid; }', css)
        self.assertIn(".cal-color.is-closed .cal-color-menu { display: none; }", css)
        js = read("static", "js", "theme.js")
        block = js[js.index("function setupColorPicker(form)"):]
        block = block[:block.index("  // Calendar card tabs")]
        self.assertIn("Object.keys(j.colors || {}).forEach", block)
        self.assertIn("td[data-c=", block)
        self.assertIn('if (e.key === "Escape")', block)
        self.assertIn('document.dispatchEvent(new CustomEvent("nl:calcolors", { detail: { key: form.getAttribute("data-key") } }));', block)
        self.assertIn('field.name = "color"; field.value = choice.value;', block)
        self.assertIn('document.addEventListener("nl:calcolors"', js)


class GoogleColorsTests(TabsCase):
    def setUp(self):
        super().setUp()
        self.add_movement(2026, "Bangkok", "Thailand", "2026-01-18", "2026-01-30")
        self.connect()
        self.choose()

    def fragment(self):
        return self.client.get("/year/2026/calendar/google").get_data(as_text=True)

    def google_color(self, html, title):
        # Since 1.12.19 the Google legend has the picker (aria-label "Color of <title>...").
        m = re.search(r'class="cal-swatch cal-color-btn (cal-[a-z0-9]+)" aria-haspopup="true" '
                      r'aria-expanded="false" aria-label="Color of ' + re.escape(title) + r'[: (]', html)
        return m.group(1) if m else None

    def nomad_color(self, key):
        # With Google connected the Nomad Life legend has plain squares that follow it.
        m = re.search(r'<i class="cal-swatch (cal-[a-z0-9]+)" aria-hidden="true" data-legend-key="'
                      + re.escape(key) + '"', self.dashboard())
        return m.group(1) if m else None

    def test_events_take_their_countrys_color(self):
        self.google.events = [ev("Thailand", "2026-02-01", "2026-02-03"), ev("Bangkok", "2026-03-01", "2026-03-02"),
                              ev("Lisbon", "2026-04-01", "2026-04-02"), ev("Dentist", "2026-05-01", "2026-05-01")]
        html = self.fragment()
        nomad = self.nomad_color("thailand")
        self.assertIsNotNone(nomad)
        self.assertEqual(self.google_color(html, "Thailand"), nomad)
        self.assertEqual(self.google_color(html, "Bangkok"), nomad)  # a city of the year's stays
        dentist = self.google_color(html, "Dentist")
        self.assertNotIn(dentist, (nomad, "cal-base", None))

    def test_a_country_code_matches_too(self):
        self.google.events = [ev("TH", "2026-02-01", "2026-02-03")]
        self.assertEqual(self.google_color(self.fragment(), "TH"), self.nomad_color("thailand"))

    def test_a_changed_color_shows_in_the_next_google_view(self):
        self.google.events = [ev("Thailand", "2026-02-01", "2026-02-03")]
        self.fragment()
        self.client.post("/calendar/colors", data={"country": "Thailand", "color": "cal-12", "year": "2026"},
                         headers={"Accept": "application/json"})
        self.assertEqual(self.google_color(self.fragment(), "Thailand"), "cal-12")  # the cached events, new colors


class ColorReviewTests(ColorPickerTests):
    """Found in a review of the color features (1.12.16)."""

    def test_1_a_click_opens_and_says_so(self):
        html = self.html()
        self.assertIn('aria-haspopup="true" aria-expanded="false" aria-label="Color of Thailand', html)
        js = read("static", "js", "theme.js")
        block = js[js.index("function setupColorPicker(form)"):]
        block = block[:block.index("  // Calendar card tabs")]
        self.assertIn('btn.addEventListener("click", function () { setOpen(btn.getAttribute("aria-expanded") !== "true"); });', block)
        self.assertNotIn('btn.addEventListener("focus", place)', block)  # focus alone no longer opens
        self.assertIn('form.classList.add("js-picker");', block)

    def test_2_one_row_per_country_whatever_the_spelling(self):
        self.add_movement(2027, "", "Atlantis", "2027-05-01", "2027-05-02")
        self.new_year(2028, "Lisbon", "Portugal")
        self.add_movement(2028, "", "atlantis", "2028-05-01", "2028-05-02")
        self.pick("Atlantis", "cal-9")
        self.pick("atlantis", "cal-12", year=2028)
        with self.db() as conn:
            self.assertEqual([tuple(r) for r in conn.execute("SELECT country, color FROM country_colors")],
                             [("atlantis", "cal-12")])
        answer = self.pick("atlantis", "auto", year=2028).get_json()
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM country_colors").fetchone()[0], 0)
        self.assertNotIn(answer["colors"]["atlantis"], ("cal-9", "cal-12"))

    def test_3_a_city_of_two_countries_matches_neither(self):
        palette = appmod.calendar_palette(
            [{"city": "Santiago", "country": "Spain"}, {"city": "Santiago", "country": "Chile"},
             {"city": "Rome", "country": "Italy"}],
            {"spain": "cal-1", "chile": "cal-2", "italy": "cal-3"})
        self.assertNotIn("santiago", palette)
        self.assertEqual(palette["rome"], "cal-3")

    def test_4_pink_chosen_for_another_country_moves_the_base(self):
        colors = self.pick("Thailand", "cal-base").get_json()["colors"]
        self.assertEqual(colors["thailand"], "cal-base")
        self.assertNotEqual(colors["portugal"], "cal-base")
        self.assertEqual(len(set(colors.values())), len(colors))

    def test_5_an_error_without_javascript_keeps_the_message_in_view(self):
        resp = self.pick("Thailand", "red", accept_json=False)
        self.assertEqual(resp.headers["Location"], "/year/2027?cal=local")

    def test_6_a_huge_year_is_no_year(self):
        resp = self.client.post("/calendar/colors", data={"country": "Thailand", "color": "cal-9", "year": "9" * 30},
                                headers={"Accept": "application/json"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["colors"], {})
