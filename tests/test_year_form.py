"""The year tabs and the "Set up your year" page: tabs from the earliest year, a way back, the
Year field as wide as Base country, and a Create year button as wide as its label."""
import os
import re

from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class YearTabsTests(AppTestCase):
    def test_the_tabs_run_from_the_earliest_year(self):
        self.signup()
        for y in (2027, 2025, 2026):
            self.new_year(y)
        html = self.client.get("/year/2026").get_data(as_text=True)
        tabs = re.findall(r'class="year-tab[^"]*" href="/year/(\d{4})"', html)
        self.assertEqual(tabs, ["2025", "2026", "2027"])
        self.assertIn('<a class="year-tab add tip tip-end" href="/year/new?back=2026" data-tip="Add a year" '
                      'aria-label="Add a year">+ Year</a>', html)


class SetUpYearPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def page(self, url="/year/new"):
        return self.client.get(url).get_data(as_text=True)

    def test_a_first_year_has_no_way_back(self):
        html = self.page()
        self.assertNotIn('class="back"', html)
        self.assertNotIn(">Cancel</a>", html)

    def test_back_to_the_year_it_came_from_or_the_dashboard(self):
        self.new_year(2026)
        html = self.page("/year/new?back=2026")
        self.assertIn('<a class="back" href="/year/2026">&larr; Back to 2026</a>', html)
        self.assertIn('<a class="btn btn-ghost" href="/year/2026">Cancel</a>', html)
        # A year that is not the account's (or no year given): the dashboard.
        for url in ("/year/new?back=1999", "/year/new"):
            self.assertIn('<a class="back" href="/app">&larr; Back to your dashboard</a>', self.page(url))
        # A failed save keeps the way back.
        html = self.client.post("/year/new?back=2026", data={"year": "abc", "base_city": "Rome",
                                                              "base_country": "Italy"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Back to 2026", html)

    def test_the_layout(self):
        html = self.page()
        with open(os.path.join(ROOT, "templates", "year_new.html"), encoding="utf-8") as f:
            self.assertIn('<div class="grid-2">{# the Year field as wide as Base country #}', f.read())
        year = html[html.index('<label for="year">'):]
        self.assertLess(year.index('<input id="year"'), year.index('<label for="base_country">'))
        # In a row, so the button is as wide as its label, on the left.
        self.assertIn('<div class="row gap-sm form-buttons">\n        <button class="btn btn-primary" type="submit">Create year</button>', html)
