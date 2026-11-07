"""1.12.15 fixes: a review of the Movements widget, its new sorting and its folding (7 bugs)."""
import re

import app as appmod
from tests.helpers import AppTestCase


class MovementsReviewTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2027, "Lisbon", "Portugal")
        self.ids = [self.add_movement(2027, f"C{i:02d}", "Spain", f"2027-03-{i + 1:02d}", f"2027-03-{i + 1:02d}")
                    for i in range(15)]

    def html(self, query="", client=None):
        return (client or self.client).get(f"/year/2027{query}").get_data(as_text=True)

    def test_1_the_order_travels_with_the_page_links(self):
        # Another tab changing the session's order made "page 2" show another order: rows
        # twice, rows never.
        html = self.html("?sort=location&dir=asc")
        self.assertIn('href="/year/2027?page=2&amp;sort=location&amp;dir=asc#movements"', html)
        self.html("?sort=dates&dir=desc")  # "another tab"
        page2 = self.html("?page=2&sort=location&dir=asc")
        self.assertEqual(re.findall(r'<span class="loc-city">(C\d+)</span>', page2), ["C10", "C11", "C12", "C13", "C14"])
        # The default order adds nothing to the links.
        self.assertIn('href="/year/2027?page=2#movements"', self.html("?sort=dates&dir=desc"))

    def test_1_the_row_delete_form_sends_the_order(self):
        html = self.html("?sort=location&dir=asc&page=2")
        self.assertIn('<input type="hidden" name="sort" value="location"><input type="hidden" name="dir" value="asc">', html)
        self.html("?sort=dates&dir=desc")  # "another tab"
        resp = self.client.post(f"/movements/{self.ids[12]}", data={
            "action": "delete", "confirm_delete": "2", "page": "2", "sort": "location", "dir": "asc"})
        self.assertEqual(resp.headers["Location"], "/year/2027?page=2&sort=location&dir=asc")

    def test_2_after_deleting_a_movement_the_table_stays_open(self):
        resp = self.client.post(f"/movements/{self.ids[14]}", data={"action": "delete", "confirm_delete": "2"})
        self.assertEqual(resp.headers["Location"], "/year/2027")
        self.assertIn('<details class="card fold-card mv-card" id="movements" open>', self.html())
        self.assertIn('<details class="card fold-card mv-card" id="movements">', self.html())  # once only

    def test_4_sort_links_keep_the_calendar_tab(self):
        html = self.html("?cal=local")
        self.assertIn('href="/year/2027?sort=dates&amp;dir=asc&amp;cal=local#movements"', html)
        html = self.html("?cal=bogus")
        self.assertIn('href="/year/2027?sort=dates&amp;dir=asc#movements"', html)

    def test_5_no_hidden_text_in_the_place(self):
        html = self.html()
        self.assertIn('aria-label="Spain, C14">', html)
        self.assertNotIn('<span class="visually-hidden">,</span>', html)

    def test_7_the_overlap_report_reads_like_the_table(self):
        ms = [{"id": 1, "city": "Vienna", "country": "Austria", "start_date": "2027-01-01", "end_date": "2027-01-10"},
              {"id": 2, "city": "Sidney", "country": "Australia", "start_date": "2027-01-08", "end_date": "2027-01-10"}]
        stats = appmod.compute_stats({"year": 2027, "base_country": "Portugal"}, ms)
        text = appmod.overlap_report(2027, appmod.movement_overlaps(ms)[0], ms, stats["counted"])
        self.assertIn("1. Vienna, Austria, 01-Jan-2027 to 10-Jan-2027", text)
        self.assertIn("Shared: 3 days (08-Jan-2027 to 10-Jan-2027)", text)
        self.assertEqual(appmod.day_date(appmod.parse_date("2027-02-03")), "03-Feb-2027")
