"""With Google Calendar connected, colors are chosen in the Google tab's legend and the Nomad
Life tab follows them; without it the Nomad Life legend keeps the picker."""
import re

from tests.test_calendar_colors import legend_color, read
from tests.test_calendar_tabs import TabsCase, ev


class GoogleDrivesColorsTests(TabsCase):
    def setUp(self):
        super().setUp()
        self.add_movement(2026, "Rome", "Italy", "2026-02-01", "2026-02-10")
        self.add_movement(2026, "Bangkok", "Thailand", "2026-03-01", "2026-03-05")
        self.connect()
        self.choose()
        self.google.events = [ev("Italy", "2026-02-01", "2026-02-10"), ev("Bangkok", "2026-03-01", "2026-03-05"),
                              ev("Dentist", "2026-05-04", "2026-05-04"), ev("France", "2026-06-01", "2026-06-03")]

    def fragment(self):
        return self.client.get("/year/2026/calendar/google").get_data(as_text=True)

    def pick(self, name, color, back="/year/2026?cal=google#calendar", accept_json=True):
        return self.client.post("/calendar/colors", data={"country": name, "color": color, "year": "2026", "back": back},
                                headers={"Accept": "application/json"} if accept_json else {})

    def google_color(self, html, label):
        m = re.search(r'class="cal-swatch cal-color-btn (cal-[a-z0-9]+)" aria-haspopup="true" '
                      r'aria-expanded="false" aria-label="Color of ' + re.escape(label) + ':', html)
        return m.group(1) if m else None

    def nomad_color(self, key, html=None):
        m = re.search(r'<i class="cal-swatch (cal-[a-z0-9]+)" aria-hidden="true" data-legend-key="' + key + '"',
                      html or self.dashboard())
        return m.group(1) if m else None

    def saved_as(self, html, label):
        for form in re.findall(r'<form class="cal-color".*?</form>', html, re.S):
            if f'aria-label="Color of {label}:' in form:
                return re.search(r'name="country" value="([^"]+)"', form).group(1)
        return None

    def test_the_picker_is_in_the_google_tab_only(self):
        html = self.fragment()
        self.assertIn("cal-color-btn", html)
        self.assertIn('name="back" value="/year/2026?cal=google#calendar"', html)
        dashboard = self.dashboard()
        local = dashboard[dashboard.index('data-cal-panel="local"'):dashboard.index('data-cal-panel="google"')]
        self.assertNotIn("cal-color-btn", local)
        self.assertIn('data-legend-key="italy"', local)
        self.assertIn('data-c="italy"', local)

    def test_what_each_event_saves(self):
        html = self.fragment()
        self.assertEqual(self.saved_as(html, "Italy"), "Italy")
        self.assertEqual(self.saved_as(html, "Bangkok (Thailand)"), "Thailand")  # a city of the stays
        self.assertEqual(self.saved_as(html, "Dentist"), "Dentist")
        self.assertEqual(self.saved_as(html, "France"), "France")  # a country without a stay

    def test_a_country_picked_in_google_colors_both_tabs(self):
        self.assertEqual(self.pick("Italy", "cal-9").status_code, 200)
        self.assertEqual(self.google_color(self.fragment(), "Italy"), "cal-9")
        self.assertEqual(self.nomad_color("italy"), "cal-9")
        self.assertIn('class="cal-day cal-9" data-tip="Sun 1 Feb 2026 \U0001F1EE\U0001F1F9 Italy" data-c="italy"',
                      self.dashboard())
        # Every year: 2027's Italy too.
        self.new_year(2027)
        self.add_movement(2027, "Milan", "Italy", "2027-02-01", "2027-02-03")
        self.assertEqual(self.nomad_color("italy", self.client.get("/year/2027").get_data(as_text=True)), "cal-9")

    def test_a_city_picks_its_country(self):
        self.pick("Thailand", "cal-12")
        self.assertEqual(self.google_color(self.fragment(), "Bangkok (Thailand)"), "cal-12")
        self.assertEqual(self.nomad_color("thailand"), "cal-12")

    def test_any_event_can_have_a_color(self):
        self.assertEqual(self.pick("Dentist", "cal-14").status_code, 200)
        html = self.fragment()
        self.assertEqual(self.google_color(html, "Dentist"), "cal-14")
        others = [self.google_color(html, label) for label in ("Italy", "Bangkok (Thailand)", "France")]
        self.assertNotIn("cal-14", others)  # automatic colors skip a chosen one
        self.assertNotIn("dentist", self.dashboard().split('data-cal-panel="google"')[0])

    def test_a_country_without_a_stay_keeps_its_choice(self):
        self.pick("France", "cal-3")
        self.assertEqual(self.google_color(self.fragment(), "France"), "cal-3")

    def test_automatic_again(self):
        before = self.google_color(self.fragment(), "Italy")
        self.pick("Italy", "cal-9")
        self.pick("Italy", "auto")
        self.assertEqual(self.google_color(self.fragment(), "Italy"), before)
        self.assertEqual(self.nomad_color("italy"), before)

    def test_without_javascript_back_on_the_google_tab(self):
        resp = self.pick("Italy", "cal-9", accept_json=False)
        self.assertEqual(resp.headers["Location"], "/year/2026?cal=google#calendar")
        self.assertIn("cal-color-btn", self.dashboard("?cal=google"))

    def test_assets(self):
        js = read("static", "js", "theme.js")
        self.assertIn("window.nlColorPickers(google);", js)
        self.assertIn('[data-legend-key="\' + safe + \'"]', js)
        self.assertIn("if (!google.hidden && loaded) recolored(e.detail && e.detail.key);", js)


class WithoutGoogleTests(TabsCase):
    def test_the_nomad_life_legend_keeps_the_picker(self):
        self.add_movement(2026, "Rome", "Italy", "2026-02-01", "2026-02-10")
        self.assertEqual(legend_color(self.dashboard(), "Italy"), "cal-1")


class GoogleLegendOrderTests(TabsCase):
    def test_the_base_leads_then_the_most_days(self):
        self.connect()
        self.choose()
        self.google.events = [ev("Italy", "2026-02-01", "2026-02-20"), ev("Portugal", "2026-04-01", "2026-04-02"),
                              ev("Spain", "2026-05-01", "2026-05-08")]
        html = self.client.get("/year/2026/calendar/google").get_data(as_text=True)
        legend = re.findall(r'aria-label="Color of ([^:"]+):', html)
        self.assertEqual(legend, ["Portugal", "Italy", "Spain"])  # base Portugal, 2 days only


class NomadLegendOrderTests(TabsCase):
    def test_the_base_leads_the_nomad_life_legend(self):
        self.add_movement(2026, "Rome", "Italy", "2026-02-01", "2026-03-20")  # more days than the base's stay
        self.add_movement(2026, "Lisbon", "Portugal", "2026-04-01", "2026-04-02")
        html = self.dashboard()
        legend = html[html.index('class="cal-legend'):]
        legend = legend[:legend.index("</ul>")]
        self.assertEqual(re.findall(r'<span dir="auto">([^<]+)</span>', legend)[:2], ["Portugal", "Italy"])
        self.assertIn('data-tip="Sun 1 Feb 2026 \U0001F1EE\U0001F1F9 Italy"', html)  # no colon
