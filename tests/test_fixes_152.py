"""Regression tests for the bugs found in the 1.5.2 review of the overlap math and the overlap
alert."""
import os
import time
import unittest
from datetime import date

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mv(mid, city, start, end):
    return {"id": mid, "city": city, "country": "Spain", "start_date": start, "end_date": end}


def found(movements):
    return [(p["a"]["id"], p["b"]["id"], p["days"]) for p in appmod.movement_overlaps(movements)[0]]


class OverlapRuleTests(unittest.TestCase):
    def test_one_day_stays_on_the_same_day_are_reported(self):
        # Two one day stays on 5 January: one of them counts 0 days. Only "more than one shared
        # day" was reported, so this never was.
        self.assertEqual(found([mv(1, "Vienna", "2027-01-05", "2027-01-05"),
                                mv(2, "Sidney", "2027-01-05", "2027-01-05")]), [(1, 2, 1)])
        # A one day stop inside a longer stay shares one day too, and is not a travel day.
        self.assertEqual(found([mv(1, "Bali", "2027-02-01", "2027-02-20"),
                                mv(2, "Singapore", "2027-02-10", "2027-02-10")]), [(1, 2, 1)])

    def test_travel_days_stay_quiet(self):
        d = date.fromisoformat
        self.assertEqual(appmod.shares_days(d("2027-03-01"), d("2027-03-06"),
                                            d("2027-03-06"), d("2027-03-09")), 0)
        # A one day stop on the day the first stay ends is a travel day as well.
        self.assertEqual(found([mv(1, "Madrid", "2027-03-01", "2027-03-06"),
                                mv(2, "Airport", "2027-03-06", "2027-03-06")]), [])
        # Either order of the arguments gives the same answer.
        self.assertEqual(appmod.shares_days(d("2027-03-06"), d("2027-03-09"),
                                            d("2027-03-01"), d("2027-03-06")), 0)

    def test_many_overlaps_stay_fast_and_are_summed_up(self):
        ms = [mv(i, f"C{i}", "2027-01-01", "2027-12-31") for i in range(1000)]
        started = time.perf_counter()
        pairs, total = appmod.movement_overlaps(ms)
        self.assertLess(time.perf_counter() - started, 5)  # was about 2 minutes
        self.assertEqual((len(pairs), total), (appmod.OVERLAP_REPORT_MAX, 499500))
        stats = appmod.compute_stats({"year": 2027, "base_country": "Portugal"}, ms)
        text = appmod.overlap_report(2027, pairs, ms, stats["counted"], total)
        self.assertIn("Overlapping movements in 2027 (499500)", text)
        self.assertIn(f"... and {499500 - appmod.OVERLAP_REPORT_MAX} more.", text)


class SaveWarningTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.client.post("/year/new", data={"year": "2027", "base_city": "Lisbon",
                                            "base_country": "Portugal"})

    def add(self, city, start, end, follow=False):
        return self.client.post("/year/2027/movements/new", data={
            "city": city, "country": "Spain", "start_date": start, "end_date": end},
            follow_redirects=follow)

    def test_warning_names_at_most_five_stays_and_says_one_day(self):
        for i in range(8):
            self.add(f"Stay{i}", "2027-06-01", "2027-06-10")
        html = self.add("Last", "2027-06-05", "2027-06-05", follow=True).get_data(as_text=True)
        self.assertIn("This stay overlaps Stay0 (2027-06-01 to 2027-06-10, 1 shared day); ", html)
        self.assertIn("; 3 more. A shared day counts", html)
        self.assertNotIn("Stay5 (", html)
        # The session cookie stays small, so nobody is signed out by a long message.
        self.assertIn("/year/2027", self.client.get("/app").headers["Location"])


class SmallLayoutTests(AppTestCase):
    def test_settings_summary_breaks_only_between_its_parts(self):
        self.signup()
        html = self.client.get("/settings").get_data(as_text=True)
        # "0 B used of 500 MB" was split as "0" / "B used of 500 MB" on narrow screens.
        self.assertRegex(html, r'<span class="nowrap">0 B used of \d+ MB</span></p>')
        self.assertIn('<span class="nowrap">0 documents &middot;</span>', html)

    def test_accounts_search_is_half_as_wide_with_the_button_centered(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn('<div class="field grow admin-search">', html)
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".support-filters .admin-search { flex: 0 1 50%; }", css)
        self.assertIn(".support-filter-actions { display: flex; align-items: center; gap: 8px; "
                      "min-height: calc(1.5em + 20px); }", css)

    def test_security_activity_filter_is_half_as_wide_too(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")
        html = self.client.get("/admin").get_data(as_text=True)
        activity = html.split('id="activity"', 1)[1]
        self.assertIn('<div class="field grow admin-search">\n      <label for="activity-q">Search activity</label>', activity)
        # Like the accounts search: a Search button, and Clear once something is searched.
        self.assertIn('type="submit">Search</button>', activity)
        self.assertNotIn(">Clear</a>", activity)
        activity = self.client.get("/admin?q=admin@example.com").get_data(as_text=True).split('id="activity"', 1)[1]
        self.assertIn('href="/admin#activity">Clear</a>', activity)
