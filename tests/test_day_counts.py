"""Days without a stay count toward the base country only once they have passed."""
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

    def test_current_year_counts_past_base_days_and_all_stay_days(self):
        today = date.today()
        y = today.year
        self.new_year(y)
        if today.month == 12 and today.day == 31:
            self.skipTest("needs a day left in the year")
        tomorrow = today + timedelta(days=1)
        self.add_movement(y, "Rome", "Italy", tomorrow.isoformat(), tomorrow.isoformat())
        stats = self.stats(y)
        self.assertEqual(stats["base_days"], stats["elapsed"])
        self.assertEqual(stats["abroad_days"], 1)
        self.assertEqual(stats["base_days"] + stats["abroad_days"] + stats["upcoming"],
                         stats["total"])

    def test_past_year_is_unchanged(self):
        y = date.today().year - 1
        self.new_year(y)
        self.add_movement(y, "Rome", "Italy", f"{y}-03-01", f"{y}-03-10")
        stats = self.stats(y)
        self.assertEqual((stats["base_days"], stats["abroad_days"], stats["upcoming"]),
                         (stats["total"] - 10, 10, 0))


if __name__ == "__main__":
    unittest.main()
