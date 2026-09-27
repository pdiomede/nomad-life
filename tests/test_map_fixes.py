"""Regression tests for the 1.2.5 map fixes."""
import os
import re

import app as appmod
from tests.helpers import AppTestCase


class MapFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2025, base_city="Lisbon", base_country="Portugal")

    def pins(self):
        with self.db() as conn:
            year = conn.execute("SELECT * FROM years").fetchone()
            moves = conn.execute("SELECT * FROM movements").fetchall()
        with self.app.test_request_context():
            return appmod.map_pins(year, moves)

    def test_cities_differing_only_in_case_are_listed_once(self):
        self.add_movement(2025, "lisbon", "Portugal", "2025-02-01", "2025-02-03")
        self.add_movement(2025, "Porto", "Portugal", "2025-03-01", "2025-03-02")
        pins, _ = self.pins()
        self.assertEqual(pins[0]["cities"], ["Lisbon", "Porto"])

    def test_off_map_places_follow_days_per_country(self):
        self.add_movement(2025, "Tiraspol", "Transnístria", "2025-04-01", "2025-04-02")
        self.add_movement(2025, "Bender", "Transnistria", "2025-05-01", "2025-05-02")
        _, missing = self.pins()
        self.assertEqual(missing, ["Transnístria"])

    def test_tooltips_open_on_tap_and_pins_can_move_apart(self):
        css = open(os.path.join(appmod.BASE_DIR, "static", "css", "style.css"), encoding="utf-8").read()
        self.assertIn(".map-pin:focus .map-tip", css)
        self.assertIn("var(--nudge-x, 0px)", css)
        js = open(os.path.join(appmod.BASE_DIR, "static", "js", "theme.js"), encoding="utf-8").read()
        self.assertIn('setProperty("--nudge-x"', js)
