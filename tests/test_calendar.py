"""The dashboard calendar: the selected year's months, each day in the color of the country it
counts for, from the same rules as Days per Country; folded on every page load."""
import os
import re
from collections import Counter

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = re.compile(r'<td class="cal-day (cal-[a-z0-9]+)( cal-today)?" title="([^"]*)"'
                 r'(?: aria-current="date")?><time datetime="(\d{4}-\d{2}-\d{2})">')


class CalendarTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def page(self, year):
        return self.client.get(f"/year/{year}").get_data(as_text=True)

    def days(self, year):
        """{iso date: (color class, hover text)} for every day cell of the year's page."""
        return {m.group(4): (m.group(1), m.group(3)) for m in DAY.finditer(self.page(year))}

    def test_the_card_is_folded_between_movements_and_map(self):
        self.new_year(2027)
        html = self.page(2027)
        self.assertIn('<details class="card fold-card cal-card">', html)  # no "open"
        self.assertIn('<h2 id="calendar-title" class="title-icon icon-calendar">Calendar (2027)</h2>', html)
        self.assertLess(html.index('id="movements"'), html.index('id="calendar-title"'))
        self.assertLess(html.index('id="calendar-title"'), html.index('id="map-title"'))

    def test_the_card_heads_name_the_year_of_the_tab(self):
        self.new_year(2027)
        self.new_year(2028)
        for year, other in ((2027, 2028), (2028, 2027)):
            html = self.page(year)
            self.assertIn(f'<span class="muted small">Your {year} day-by-day plan</span>', html)
            self.assertIn(f'<span class="muted small">Your {year} locations on the map</span>', html)
            self.assertNotIn(f"Your {other} day-by-day plan", html)
            self.assertNotIn(f"Your {other} locations on the map", html)

    def test_only_the_selected_year_with_its_weekdays(self):
        self.new_year(2027)
        self.new_year(2028)
        days = self.days(2027)
        self.assertEqual(len(days), 365)
        self.assertTrue(all(d.startswith("2027-") for d in days))
        html = self.page(2027)
        january = html[html.index("<caption>January</caption>"):html.index("<caption>February</caption>")]
        body = january[january.index("<tbody>"):]
        first_week = body[:body.index("</tr>")]
        self.assertEqual(first_week.count("<td></td>"), 4)  # 1 January 2027 is a Friday
        self.assertEqual(sum(d.startswith("2028-02-") for d in self.days(2028)), 29)

    def test_each_country_has_its_color_and_shared_days_go_to_the_later_stay(self):
        self.new_year(2030, "Sofia", "Bulgaria")  # a future year: nothing has passed yet
        self.add_movement(2030, "Rome", "Italy", "2030-03-01", "2030-03-05")
        self.add_movement(2030, "Madrid", "Spain", "2030-03-05", "2030-03-08")
        self.add_movement(2030, "Milan", "Italy", "2030-03-10", "2030-03-12")
        days = self.days(2030)
        italy, spain = days["2030-03-03"][0], days["2030-03-07"][0]
        self.assertNotEqual(italy, spain)
        self.assertEqual(days["2030-03-11"][0], italy)  # one color per country
        self.assertEqual(days["2030-03-05"][0], spain)  # the travel day: the later stay
        self.assertEqual(days["2030-03-05"][1], "Tue 5 Mar 2030: Spain")
        self.assertEqual(days["2030-03-09"], ("cal-none", "Sat 9 Mar 2030: not logged"))

    def test_screen_readers_hear_where_each_day_counts(self):
        # The color alone tells them nothing, and the hover text is for a mouse only.
        self.new_year(2030, "Sofia", "Bulgaria")
        self.add_movement(2030, "Rome", "Italy", "2030-03-01", "2030-03-05")
        html = self.page(2030)
        self.assertIn('<time datetime="2030-03-02">2</time><span class="visually-hidden">, '
                      'Italy</span></td>', html)
        self.assertIn('<time datetime="2030-03-09">9</time><span class="visually-hidden">, '
                      'not logged</span></td>', html)

    def test_days_without_a_stay_are_not_logged_also_in_the_past(self):
        # Only logged days count: a passed day without a movement is not the base either.
        self.new_year(2020, "Lisbon", "Portugal")
        self.add_movement(2020, "Paris", "France", "2020-06-01", "2020-06-10")
        self.add_movement(2020, "Lisbon", "Portugal", "2020-01-10", "2020-01-20")
        days = self.days(2020)
        self.assertEqual(len(days), 366)
        self.assertEqual(days["2020-01-05"], ("cal-none", "Sun 5 Jan 2020: not logged"))
        self.assertEqual(days["2020-01-15"], ("cal-base", "Wed 15 Jan 2020: Portugal"))
        self.assertEqual(Counter(c for c, _ in days.values())["cal-none"], 366 - 10 - 11)
        self.assertIn("Not logged <span class=\"muted\">345 days</span>", self.page(2020))

    def test_the_colors_count_the_same_days_as_days_per_country(self):
        year = self.new_year(2030, "Sofia", "Bulgaria")
        self.add_movement(2030, "Rome", "Italy", "2030-01-01", "2030-01-20")
        self.add_movement(2030, "Paris", "France", "2030-01-15", "2030-02-02")
        self.add_movement(2030, "Lyon", "france", "2030-02-02", "2030-02-04")
        with self.db() as conn:
            year_row = conn.execute("SELECT * FROM years WHERE id = ?", (year["id"],)).fetchone()
            movements = conn.execute("SELECT * FROM movements WHERE year_id = ?",
                                     (year["id"],)).fetchall()
        stats = appmod.compute_stats(year_row, movements)
        calendar = appmod.year_calendar(year_row, movements, stats)
        cells = Counter(d["color"] for m in calendar["months"] for d in m["days"])
        for entry in calendar["legend"]:
            self.assertEqual(cells[entry["color"]], entry["days"], entry["country"])
        self.assertEqual(cells["cal-none"], calendar["not_logged"])
        self.assertEqual(calendar["not_logged"], stats["not_logged"])
        self.assertEqual([e["country"] for e in calendar["legend"]], ["France", "Italy"])

    def test_more_countries_than_colors_repeat_the_palette(self):
        year = self.new_year(2030)
        countries = ["Spain", "Italy", "France", "Japan", "Chile", "Peru", "Kenya", "Egypt",
                     "Nepal", "Fiji"]
        for i, country in enumerate(countries):
            self.add_movement(2030, "Town", country, f"2030-01-{i * 2 + 1:02d}",
                              f"2030-01-{i * 2 + 2:02d}")
        html = self.page(2030)
        self.assertEqual(html.count('<i class="cal-swatch cal-1"'), 2)  # the 1st and the 9th
        self.assertNotIn("cal-9", html)


class CalendarStyleTests(AppTestCase):
    def test_every_color_is_set_in_the_three_theme_blocks(self):
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as f:
            css = f.read()
        light = css[css.index(":root {"):css.index('[data-theme="dark"] {')]
        dark = css[css.index(':root[data-theme="dark"] {'):css.index("@media (prefers-color-scheme: dark)")]
        system = css[css.index("@media (prefers-color-scheme: dark)"):]
        system = system[:system.index("\n}\n")]
        names = ["--cal-text", "--cal-none", "--cal-base"] + [
            f"--cal-{n}" for n in range(1, appmod.CALENDAR_COLORS + 1)]
        for block in (light, dark, system):
            for name in names:
                self.assertIn(name + ":", block, name)

    def test_four_months_a_row_then_fewer(self):
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".cal-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));", css)
        # Not stretched to the tallest month of the row: a 5 week month would spread the extra
        # height over its weeks, so its days were taller than a 6 week month's.
        self.assertIn("gap: 20px 24px; align-items: start; }", css)
        self.assertIn("@media (max-width: 1000px) { .cal-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }", css)
        self.assertIn("@media (max-width: 640px) { .cal-grid { grid-template-columns: repeat(2, minmax(0, 1fr));", css)
        self.assertIn("@media (max-width: 400px) { .cal-grid { grid-template-columns: minmax(0, 1fr); } }", css)
