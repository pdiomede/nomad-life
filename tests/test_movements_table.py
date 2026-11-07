"""The dashboard's Movements table (1.12.15): sortable columns with the newest dates first by
default, dates as 18-Jan-2027, and the place as flag, country in bold, then the city."""
import os
import re

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


class MovementsTableTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2027, "Lisbon", "Portugal")
        self.ids = {
            "rome": self.add_movement(2027, "Rome", "Italy", "2027-03-01", "2027-03-04"),
            "bangkok": self.add_movement(2027, "Bangkok", "Thailand", "2027-01-18", "2027-01-30"),
            "paris": self.add_movement(2027, "", "France", "2027-07-01", "2027-07-02"),
        }

    def table(self, query=""):
        html = self.client.get(f"/year/2027{query}").get_data(as_text=True)
        return html[html.index('<table class="table">'):html.index("</table>")]

    def order(self, table):
        return re.findall(r"<strong>([^<]+)</strong>", table.split("<tbody>")[1])

    def test_newest_dates_first_by_default(self):
        table = self.table()
        self.assertEqual(self.order(table), ["France", "Italy", "Thailand"])
        self.assertIn('<th scope="col" aria-sort="descending">', table)
        self.assertIn('href="/year/2027?sort=dates&amp;dir=asc#movements">Dates', table)

    def test_every_column_sorts_and_the_choice_stays(self):
        self.assertEqual(self.order(self.table("?sort=dates&dir=asc")), ["Thailand", "Italy", "France"])
        self.assertEqual(self.order(self.table()), ["Thailand", "Italy", "France"])  # remembered
        self.assertEqual(self.order(self.table("?sort=location&dir=asc")), ["France", "Italy", "Thailand"])
        self.assertEqual(self.order(self.table("?sort=days&dir=desc")), ["Thailand", "Italy", "France"])
        self.assertEqual(self.order(self.table("?sort=docs&dir=desc&page=1")), ["France", "Italy", "Thailand"])
        # Anything else keeps the remembered order.
        self.assertEqual(self.order(self.table("?sort=bogus")), ["France", "Italy", "Thailand"])

    def test_location_sorts_a_to_z_first(self):
        table = self.table()
        self.assertIn('href="/year/2027?sort=location&amp;dir=asc#movements">Location', table)
        self.assertIn('href="/year/2027?sort=days&amp;dir=desc#movements">Days', table)

    def test_dates_read_easily(self):
        table = self.table()
        self.assertIn('<time class="nowrap" datetime="2027-01-18">18-Jan-2027</time> <span class="muted">to</span> '
                      '<time class="nowrap" datetime="2027-01-30">30-Jan-2027</time>', table)
        self.assertEqual(appmod.day_date("2027-11-05"), "05-Nov-2027")
        self.assertEqual(appmod.day_date("not a date"), "not a date")

    def test_flag_country_then_city(self):
        table = self.table()
        # Screen readers hear "Thailand, Bangkok" from the label; a copy has no hidden comma.
        self.assertRegex(table, r'<a class="loc-link" href="/movements/\d+" aria-label="Thailand, Bangkok">'
                                r'<span class="flag"[^>]*>[^<]*</span> <strong>Thailand</strong> '
                                r'<span class="loc-sep" aria-hidden="true">\|</span> <span class="loc-city">Bangkok</span></a>')
        self.assertNotIn('<span class="visually-hidden">,</span>', table)
        # Without a city: the flag and the country only.
        self.assertRegex(table, r'<strong>France</strong></a>')
        css = read("static", "css", "style.css")
        self.assertIn(".loc-city { font-weight: 400; }", css)

    def test_back_from_a_movement_lands_on_its_page_in_the_chosen_order(self):
        for i in range(12):
            self.add_movement(2027, f"Town{i:02d}", "Spain", f"2027-05-{i + 1:02d}", f"2027-05-{i + 1:02d}")
        self.table()  # newest first: Bangkok (January) is on page 2 of 15 movements
        html = self.client.get(f"/movements/{self.ids['bangkok']}").get_data(as_text=True)
        self.assertIn('href="/year/2027?page=2#movements"', html)
        self.table("?sort=dates&dir=asc")  # oldest first: page 1, and the order in the link
        html = self.client.get(f"/movements/{self.ids['bangkok']}").get_data(as_text=True)
        self.assertIn('href="/year/2027?sort=dates&amp;dir=asc#movements"', html)


class TypeFieldTests(AppTestCase):
    def test_the_type_label_fits_on_one_line(self):
        css = read("static", "css", "style.css")
        self.assertIn(".upload .field:first-of-type { width: 240px; }", css)
        self.assertIn(".select-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }", css)
        self.assertIn('("accommodation", "Hotel / Home rent")', read("app.py"))


class MovementsFoldTests(AppTestCase):
    """Movements folds like the Calendar and the Map, and starts folded (1.12.15)."""

    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2027, "Lisbon", "Portugal")
        self.add_movement(2027, "Rome", "Italy", "2027-03-01", "2027-03-04")

    def html(self, query=""):
        return self.client.get(f"/year/2027{query}").get_data(as_text=True)

    def test_folded_by_default_open_for_its_links(self):
        self.assertIn('<details class="card fold-card mv-card" id="movements">', self.html())
        for query in ("?page=1", "?per_page=25", "?sort=location&dir=asc"):
            self.assertIn('<details class="card fold-card mv-card" id="movements" open>', self.html(query))
        summary = self.html()
        summary = summary[summary.index('id="movements"'):]
        summary = summary[:summary.index("</summary>")]
        self.assertIn('<h2 id="movements-title" class="title-icon icon-plane">Movements</h2>', summary)
        self.assertIn('<span class="muted small">1 movement in 2027</span>', summary)

    def test_the_overlap_triangle_lies_outside_the_summary(self):
        self.add_movement(2027, "Milan", "Italy", "2027-03-02", "2027-03-05")
        html = self.html()
        summary = html[html.index('id="movements"'):]
        summary = summary[:summary.index("</summary>")]
        self.assertNotIn("data-overlap-open", summary)
        self.assertIn('<span class="fold-gear-space mv-alert-space" aria-hidden="true"></span>', summary)
        tools = html[html.index('<span class="fold-ghost title-icon icon-plane" aria-hidden="true">Movements</span>'):]
        self.assertLess(tools.index("data-overlap-open"), tools.index('id="movements"'))

    def test_a_link_to_movements_opens_it(self):
        js = read("static", "js", "theme.js")
        self.assertIn('window.location.hash === "#movements"', js)
        self.assertIn('window.addEventListener("hashchange", openMovements);', js)
        css = read("static", "css", "style.css")
        self.assertIn(".fold-tools .overlap-alert { pointer-events: auto; }", css)
