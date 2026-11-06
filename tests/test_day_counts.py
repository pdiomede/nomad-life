"""Only logged days count: a day without a movement counts for no country, past or future,
the base included (days at the base count through a movement there)."""
import unittest
from datetime import date, timedelta

import app as appmod
from tests.helpers import AppTestCase


class DayCountTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def stats(self, year):
        with self.db() as conn:
            year_row = conn.execute("SELECT * FROM years WHERE year = ?", (year,)).fetchone()
            movements = conn.execute("SELECT * FROM movements WHERE year_id = ?",
                                     (year_row["id"],)).fetchall()
        return appmod.compute_stats(year_row, movements)

    def test_future_year_counts_only_planned_stays(self):
        y = date.today().year + 1
        self.new_year(y, "Sofia", "Bulgaria")
        self.add_movement(y, "Rome", "Italy", f"{y}-01-01", f"{y}-01-10")
        stats = self.stats(y)
        self.assertEqual((stats["base_days"], stats["abroad_days"], stats["elapsed"]), (0, 10, 0))
        self.assertFalse(stats["base_ok"])
        rows = {r["country"]: (r["days"], r["so_far"]) for r in stats["rows"]}
        self.assertEqual(rows, {"Italy": (10, 0), "Bulgaria": (0, 0)})
        html = self.client.get(f"/year/{y}").get_data(as_text=True)
        self.assertIn("0 days (0 so far)", html)
        self.assertIn("183 days short of 183", html)

    def test_current_year_counts_only_logged_days(self):
        today = date.today()
        y = today.year
        self.new_year(y)
        if today.month == 12 and today.day == 31:
            self.skipTest("needs a day left in the year")
        tomorrow = today + timedelta(days=1)
        self.add_movement(y, "Rome", "Italy", tomorrow.isoformat(), tomorrow.isoformat())
        stats = self.stats(y)
        self.assertEqual(stats["base_days"], 0)  # passed days without a movement count nowhere
        self.assertEqual(stats["abroad_days"], 1)
        self.assertEqual(stats["base_days"] + stats["abroad_days"] + stats["not_logged"],
                         stats["total"])

    def test_past_year_counts_only_logged_days(self):
        y = date.today().year - 1
        self.new_year(y)
        self.add_movement(y, "Rome", "Italy", f"{y}-03-01", f"{y}-03-10")
        stats = self.stats(y)
        self.assertEqual((stats["base_days"], stats["abroad_days"], stats["not_logged"]),
                         (0, 10, stats["total"] - 10))

    def test_a_base_without_movements_has_no_days(self):
        for y in (date.today().year - 1, date.today().year):
            self.new_year(y)
            stats = self.stats(y)
            self.assertEqual((stats["base_days"], stats["abroad_days"], stats["not_logged"]),
                             (0, 0, stats["total"]), y)
            self.assertIn("0 days (0 so far)", self.client.get(f"/year/{y}").get_data(as_text=True))

    def test_a_movement_at_the_base_counts_for_it(self):
        y = date.today().year - 1
        self.new_year(y, "Lisbon", "Portugal")
        self.add_movement(y, "Lisbon", "Portugal", f"{y}-01-01", f"{y}-07-02")  # 183 days
        self.add_movement(y, "Rome", "Italy", f"{y}-07-02", f"{y}-07-05")  # 2 July goes to Italy
        stats = self.stats(y)
        self.assertEqual((stats["base_days"], stats["abroad_days"]), (182, 4))
        self.assertFalse(stats["base_ok"])

if __name__ == "__main__":
    unittest.main()
