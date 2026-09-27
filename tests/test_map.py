"""Dashboard map: one pin per country, the base in its own color, CSS tooltips."""
import unittest

import app as appmod
import countries
import countries_geo
from tests.helpers import AppTestCase


class MapTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026, "Sofia", "Bulgaria")

    def map_html(self, year=2026):
        html = self.client.get(f"/year/{year}").get_data(as_text=True)
        start = html.index('<section class="card map-card"')
        return html, html[start:html.index("</section>", start)]

    def test_map_is_under_movements_in_the_left_column(self):
        html, _ = self.map_html()
        col = html[html.index('<div class="main-col">'):html.index('<div class="side">')]
        self.assertLess(col.index('id="movements"'), col.index('id="map-title"'))

    def test_base_pin_without_movements(self):
        _, card = self.map_html()
        self.assertEqual(card.count('class="map-pin'), 1)
        self.assertIn("map-pin map-pin-base", card)
        self.assertIn('aria-label="Bulgaria (base): Sofia"', card)
        self.assertIn("\U0001F1E7\U0001F1EC", card)  # Bulgarian flag in the tooltip

    def test_one_pin_per_country_with_its_cities(self):
        self.add_movement(2026, "Rome", "Italy", "2026-03-01", "2026-03-05")
        self.add_movement(2026, "Milan", "italy", "2026-04-01", "2026-04-05")
        self.add_movement(2026, "Plovdiv", "Bulgaria", "2026-05-01", "2026-05-03")
        _, card = self.map_html()
        self.assertEqual(card.count('class="map-pin'), 2)
        self.assertIn('aria-label="Italy: Rome, Milan"', card)
        self.assertIn('aria-label="Bulgaria (base): Sofia, Plovdiv"', card)

    def test_free_text_country_is_listed_off_the_map(self):
        self.add_movement(2026, "Somewhere", "Atlantis", "2026-03-01", "2026-03-02")
        _, card = self.map_html()
        self.assertIn("Not on the map: Atlantis.", card)

    def test_only_this_years_stays(self):
        self.new_year(2025)
        self.add_movement(2025, "Tokyo", "Japan", "2025-03-01", "2025-03-02")
        _, card = self.map_html(2026)
        self.assertNotIn("Japan", card)

    def test_tooltip_stays_inside_the_map_near_edges(self):
        self.add_movement(2026, "Honolulu", "United States", "2026-03-01", "2026-03-02")
        self.add_movement(2026, "Auckland", "New Zealand", "2026-04-01", "2026-04-02")
        _, card = self.map_html()
        self.assertIn("tip-left", card)  # New Zealand, far right


class CountryPointTests(unittest.TestCase):
    def test_points_inside_the_map(self):
        codes = {code for _, code, _ in countries.COUNTRY_DATA}
        missing = codes - set(countries_geo.COUNTRY_POINTS)
        # Too scattered (US Minor Outlying Islands) or cropped out of the map (Antarctica).
        self.assertEqual(missing, {"UM", "AQ"})
        for code, (left, top) in countries_geo.COUNTRY_POINTS.items():
            self.assertTrue(0 <= left <= 100 and 0 <= top <= 100, code)


if __name__ == "__main__":
    unittest.main()
