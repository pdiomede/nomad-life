"""Overlapping movements: which pairs are reported, who their shared days count for, the report
text, and the red triangle with its dialog on the dashboard."""
import re
import unittest
from datetime import date

import app as appmod
from tests.helpers import AppTestCase


def mv(mid, city, country, start, end):
    return {"id": mid, "city": city, "country": country, "start_date": start, "end_date": end}


class OverlapMathTests(unittest.TestCase):
    def pairs(self, movements):
        return [(p["a"]["id"], p["b"]["id"], p["days"], p["owners"])
                for p in appmod.movement_overlaps(movements)[0]]

    def test_vienna_and_sidney(self):
        ms = [mv(1, "Vienna", "Austria", "2027-01-01", "2027-01-10"),
              mv(2, "Sidney", "Australia", "2027-01-08", "2027-01-10")]
        self.assertEqual(self.pairs(ms), [(1, 2, 3, {2: 3})])
        p = appmod.movement_overlaps(ms)[0][0]
        self.assertEqual((p["first"], p["last"]), (date(2027, 1, 8), date(2027, 1, 10)))

    def test_a_travel_day_or_touching_stays_are_not_reported(self):
        self.assertEqual(self.pairs([mv(1, "Madrid", "Spain", "2027-03-01", "2027-03-06"),
                                     mv(2, "Rome", "Italy", "2027-03-06", "2027-03-09")]), [])
        self.assertEqual(self.pairs([mv(1, "Madrid", "Spain", "2027-03-01", "2027-03-05"),
                                     mv(2, "Rome", "Italy", "2027-03-06", "2027-03-09")]), [])

    def test_side_trip_same_start_and_identical_dates(self):
        # A side trip inside a long stay: its days go to the trip, which started later.
        self.assertEqual(self.pairs([mv(1, "Bali", "Indonesia", "2027-02-01", "2027-02-28"),
                                     mv(2, "Singapore", "Singapore", "2027-02-10", "2027-02-12")]),
                         [(1, 2, 3, {2: 3})])
        # Same start day: the shorter stay wins, whatever the order they were added in.
        self.assertEqual(self.pairs([mv(1, "Oslo", "Norway", "2027-04-01", "2027-04-03"),
                                     mv(2, "Bergen", "Norway", "2027-04-01", "2027-04-20")]),
                         [(2, 1, 3, {1: 3})])
        # Identical dates: the newest entry.
        self.assertEqual(self.pairs([mv(5, "Kyoto", "Japan", "2027-05-01", "2027-05-04"),
                                     mv(9, "Osaka", "Japan", "2027-05-01", "2027-05-04")]),
                         [(5, 9, 4, {9: 4})])

    def test_three_way_overlap_names_the_real_owner(self):
        ms = [mv(1, "Lima", "Peru", "2027-06-01", "2027-06-20"),
              mv(2, "Cusco", "Peru", "2027-06-05", "2027-06-15"),
              mv(3, "La Paz", "Bolivia", "2027-06-10", "2027-06-12")]
        pairs = {(a, b): (days, owners) for a, b, days, owners in self.pairs(ms)}
        self.assertEqual(pairs[(1, 2)], (11, {2: 8, 3: 3}))
        self.assertEqual(pairs[(1, 3)], (3, {3: 3}))
        self.assertEqual(pairs[(2, 3)], (3, {3: 3}))
        # The owners agree with the counted days of the dashboard.
        stats = appmod.compute_stats({"year": 2027, "base_country": "Portugal"}, ms)
        self.assertEqual(stats["counted"], {1: 9, 2: 8, 3: 3})

    def test_report(self):
        ms = [mv(1, "Vienna", "Austria", "2027-01-01", "2027-01-10"),
              mv(2, "Sidney", "Australia", "2027-01-08", "2027-01-10")]
        stats = appmod.compute_stats({"year": 2027, "base_country": "Portugal"}, ms)
        text = appmod.overlap_report(2027, appmod.movement_overlaps(ms)[0], ms, stats["counted"])
        self.assertEqual(text.splitlines()[:3], [
            "Overlapping movements in 2027 (1)", "",
            "1. Vienna, Austria (2027-01-01 to 2027-01-10) and Sidney, Australia (2027-01-08 to "
            "2027-01-10) share 3 days, 2027-01-08 to 2027-01-10. They count for Sidney, "
            "Australia, so Vienna, Austria counts 7 of its 10 days."])
        self.assertIn("A travel day, when one movement ends the day the next begins, is normal", text)
        self.assertNotIn("\u2014", text)


class OverlapDashboardTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.client.post("/year/new", data={"year": "2027", "base_city": "Lisbon",
                                            "base_country": "Portugal"})

    def add(self, city, country, start, end):
        self.client.post("/year/2027/movements/new", data={
            "city": city, "country": country, "start_date": start, "end_date": end})

    def test_no_triangle_without_overlaps(self):
        self.add("Madrid", "Spain", "2027-03-01", "2027-03-06")
        self.add("Rome", "Italy", "2027-03-06", "2027-03-09")  # a travel day only
        html = self.client.get("/year/2027").get_data(as_text=True)
        self.assertNotIn("data-overlap-open", html)
        self.assertNotIn("overlap-dialog", html)

    def test_triangle_and_dialog(self):
        self.add("Vienna", "Austria", "2027-01-01", "2027-01-10")
        self.add("<b>Sidney</b>", "Australia", "2027-01-08", "2027-01-10")
        html = self.client.get("/year/2027").get_data(as_text=True)
        self.assertIn('class="overlap-alert" data-overlap-open aria-haspopup="dialog" '
                      'aria-controls="overlap-dialog" data-tip="Overlapping movements" hidden', html)
        self.assertIn('aria-label="Overlapping movements (1): show the report"', html)
        self.assertIn('<dialog class="overlap-dialog" id="overlap-dialog"', html)
        self.assertIn("<h2 id=\"overlap-title\">Overlapping movements in 2027</h2>", html)
        self.assertIn("&lt;b&gt;Sidney&lt;/b&gt;, Australia (2027-01-08 to 2027-01-10)", html)
        self.assertIn("data-overlap-copy>Copy</button>", html)
        self.assertIn("data-overlap-close>Close</button>", html)
        self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)", html))

    def test_pairs_on_other_pages_count_too(self):
        for day in range(1, 12):  # 11 movements, the table shows 10
            self.add(f"C{day}", "Spain", f"2027-02-{day:02d}", f"2027-02-{day:02d}")
        self.add("Late", "Italy", "2027-12-01", "2027-12-10")
        self.add("Later", "France", "2027-12-05", "2027-12-10")
        html = self.client.get("/year/2027").get_data(as_text=True)  # page 1: February only
        self.assertIn("data-overlap-open", html)
        self.assertIn("Late, Italy (2027-12-01 to 2027-12-10)", html)


class HeaderOrderTests(AppTestCase):
    def test_help_support_theme_then_the_name(self):
        self.signup()
        header = self.client.get("/settings").get_data(as_text=True).split("</header>", 1)[0]
        order = [header.index(k) for k in ("docs-btn", "support-btn", 'id="theme-toggle"',
                                            "user-menu-trigger")]
        self.assertEqual(order, sorted(order))
