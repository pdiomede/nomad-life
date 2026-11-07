"""The city of a movement is optional: the country is what counts. Every place a movement is
named shows "Rome, Italy" or just "Italy"; the base keeps its required city."""
import io

from tests.helpers import AppTestCase


class OptionalCityTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026, "Lisbon", "Portugal")

    def add(self, country, city="", start="2026-03-01", end="2026-03-05"):
        return self.client.post("/year/2026/movements/new", data={
            "country": country, "city": city, "start_date": start, "end_date": end,
            "notes": ""}, follow_redirects=True).get_data(as_text=True)

    def test_a_movement_without_a_city_is_saved_and_named_by_its_country(self):
        html = self.add("Italy")
        self.assertIn("Movement to Italy added.", html)
        with self.db() as conn:
            row = conn.execute("SELECT city, country FROM movements").fetchone()
        self.assertEqual(tuple(row), ("", "Italy"))
        page = self.client.get("/year/2026").get_data(as_text=True)
        self.assertIn("<strong>Italy</strong></a></td>", page)
        self.assertIn('aria-label="Edit movement to Italy"', page)
        self.assertIn('data-confirm-delete="the movement to Italy"', page)
        movement = self.client.get("/movements/1").get_data(as_text=True)
        self.assertIn("<title>Italy | Nomad Life</title>", movement)
        self.assertIn('<label for="city">City (optional)</label>', movement)
        self.assertNotIn('placeholder="Bali" required', movement)

    def test_a_city_still_shows_with_its_country(self):
        self.add("Italy", "Rome")
        page = self.client.get("/year/2026").get_data(as_text=True)
        # Flag, the country in bold, then the city (1.12.15).
        self.assertIn('<strong>Italy</strong> <span class="loc-sep" aria-hidden="true">|</span> '
                      '<span class="loc-city">Rome</span></a>', page)
        self.assertIn('data-confirm-delete="the movement to Rome"', page)

    def test_the_country_is_still_required(self):
        html = self.add("", "Rome")
        self.assertIn("Country is required.", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM movements").fetchone()[0], 0)

    def test_calendar_map_and_overlaps_use_the_country(self):
        self.add("Italy")
        saved = self.add("Spain", start="2026-03-04", end="2026-03-08")  # shares 2 days
        self.assertIn("This stay shares days with Italy (2 days).", saved)
        page = self.client.get("/year/2026").get_data(as_text=True)
        self.assertIn('data-tip="Wed 4 Mar 2026: \U0001F1EA\U0001F1F8 Spain"', page)
        self.assertIn('aria-label="Italy, 3 days">', page)  # no empty ": " for missing cities
        self.assertNotIn('<span class="map-tip-cities"></span>', page)

    def test_the_package_names_folders_and_stays_by_country(self):
        self.set_plan("pro")
        self.add("Italy")
        self.client.post("/movements/1", data={"action": "upload", "kind": "other",
                                               "file": (io.BytesIO(b"%PDF-1.4 x"), "a.pdf")},
                         content_type="multipart/form-data")
        _, zf = self.package(2026)
        names = zf.namelist()
        self.assertTrue(any("/receipts/01_2026-03-01_Italy/" in n for n in names), names)
        self.assertFalse(any("_city_" in n or "/city" in n for n in names), names)

    def test_the_base_city_is_still_required(self):
        html = self.client.post("/year/new", data={"year": "2027", "base_city": "",
                                                    "base_country": "Portugal"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Base country and city are required.", html)
