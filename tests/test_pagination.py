"""Dashboard pagination: helpers, page contents, links, keeping the user's place."""
import re
import unittest
from datetime import date, timedelta

import app as appmod
from tests.helpers import AppTestCase


class HelperTests(unittest.TestCase):
    def test_short_lists_show_every_page(self):
        self.assertEqual(appmod.page_window(1, 1), [1])
        self.assertEqual(appmod.page_window(4, 7), [1, 2, 3, 4, 5, 6, 7])

    def test_long_lists_use_gaps(self):
        self.assertEqual(appmod.page_window(1, 20), [1, 2, 3, 4, 5, None, 20])
        self.assertEqual(appmod.page_window(10, 20), [1, None, 9, 10, 11, None, 20])
        self.assertEqual(appmod.page_window(20, 20), [1, None, 16, 17, 18, 19, 20])
        # A gap of one page shows that page instead of an ellipsis.
        self.assertEqual(appmod.page_window(5, 8), [1, None, 4, 5, 6, 7, 8])
        self.assertEqual(appmod.page_window(4, 8), [1, 2, 3, 4, 5, None, 8])

    def test_window_never_exceeds_seven_slots(self):
        for pages in range(1, 40):
            for page in range(1, pages + 1):
                window = appmod.page_window(page, pages)
                self.assertLessEqual(len(window), 7)
                self.assertIn(page, window)
                self.assertEqual(window[0], 1)
                self.assertEqual(window[-1], pages)

    def test_paginate_clamps_bad_pages(self):
        items = list(range(23))
        url = lambda n, size: f"{n}/{size}"
        for raw, page in [(None, 1), ("", 1), ("abc", 1), ("0", 1), ("-4", 1), ("2", 2),
                          ("3", 3), ("99", 3), ("2.5", 1)]:
            with self.subTest(raw=raw):
                p = appmod.paginate(items, raw, 10, url)
                self.assertEqual(p["page"], page)
        p = appmod.paginate(items, "3", 10, url)
        self.assertEqual((p["rows"], p["first"], p["last"], p["prev"], p["next"]),
                         ([20, 21, 22], 21, 23, 2, None))
        self.assertEqual(appmod.paginate([], "5", 10, url)["pages"], 1)

    def test_page_size_change_keeps_first_visible_item(self):
        p = appmod.paginate(list(range(60)), "3", 10, lambda n, size: (n, size))
        self.assertEqual(p["size_url"](25), (1, 25))   # item 21 is on page 1 of 25
        p = appmod.paginate(list(range(60)), "2", 25, lambda n, size: (n, size))
        self.assertEqual(p["size_url"](10), (3, 10))   # item 26 is on page 3 of 10

    def test_per_page_arg_allows_listed_sizes_only(self):
        for raw, size in [(None, 10), ("25", 25), ("50", 50), ("7", 10), ("1000", 10), ("x", 10)]:
            self.assertEqual(appmod.per_page_arg(raw), size)


class DashboardPaginationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.year = self.new_year(2025)

    def add_many(self, n):
        ids = []
        for i in range(n):
            start = date(2025, 1, 1) + timedelta(days=i * 6)
            ids.append(self.add_movement(2025, f"City{i + 1:02d}", "Spain", start.isoformat(),
                                         (start + timedelta(days=3)).isoformat()))
        self.assertEqual(len(set(ids)), n, "every movement should be saved")
        return ids

    def get(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp.data.decode()

    @staticmethod
    def cities(html):
        return re.findall(r"<strong>(City\d+)</strong>", html)

    @staticmethod
    def pager_links(html):
        nav = re.search(r'<nav class="pager".*?</nav>', html, re.S)
        return re.findall(r'<a href="([^"]+)"[^>]*aria-label="([^"]+)"', nav.group(0)) if nav else []

    def test_ten_per_page_in_dashboard_order(self):
        self.add_many(23)
        self.assertEqual(self.cities(self.get("/year/2025")),
                         [f"City{i:02d}" for i in range(1, 11)])
        self.assertEqual(self.cities(self.get("/year/2025?page=2")),
                         [f"City{i:02d}" for i in range(11, 21)])
        html = self.get("/year/2025?page=3")
        self.assertEqual(self.cities(html), ["City21", "City22", "City23"])
        self.assertIn("Showing 21 to 23 of 23 movements", html)
        self.assertIn("23 movements logged", html)

    def test_no_pager_when_everything_fits(self):
        self.add_many(10)
        html = self.get("/year/2025")
        self.assertEqual(len(self.cities(html)), 10)
        self.assertNotIn('class="pager"', html)
        self.assertNotIn("Showing", html)

    def test_eleven_movements_make_two_square_pages(self):
        self.add_many(11)
        html = self.get("/year/2025")
        labels = [label for _, label in self.pager_links(html)]
        self.assertEqual(labels, ["Page 1", "Page 2", "Next page"])
        self.assertIn('aria-current="page" aria-label="Page 1"', html)
        self.assertIn('<nav class="pager" aria-label="Movements pages">', html)
        self.assertIn('<span class="pager-off" aria-hidden="true">&lsaquo;</span>', html)

    def test_bad_params_are_clamped(self):
        self.add_many(23)
        for query, first in [("?page=abc", "City01"), ("?page=-1", "City01"), ("?page=0", "City01"),
                             ("?page=99", "City21"), ("?per_page=7", "City01"),
                             ("?per_page=25&page=2", "City01")]:
            with self.subTest(query=query):
                self.assertEqual(self.cities(self.get("/year/2025" + query))[0], first)

    def test_page_sizes(self):
        self.add_many(23)
        html = self.get("/year/2025?per_page=25")
        self.assertEqual(len(self.cities(html)), 23)
        self.assertNotIn('class="pager"', html)
        self.assertIn('aria-current="true" aria-label="Show 25 per page"', html)

    def test_stats_are_the_same_on_every_page(self):
        self.add_many(23)
        def stats(html):
            return (re.search(r'<section class="stats">.*?</section>', html, re.S).group(0),
                    re.search(r'<ul class="bars">.*?</ul>', html, re.S).group(0))
        first = stats(self.get("/year/2025"))
        for query in ["?page=2", "?page=3", "?per_page=50"]:
            self.assertEqual(stats(self.get("/year/2025" + query)), first)

    def test_counted_days_use_movements_on_other_pages(self):
        self.add_many(9)
        # Tenth row ends on the day the eleventh (first on page 2) starts: the shared day
        # counts toward the later stay, so page 1 must still show 4 counted of 5.
        self.add_movement(2025, "Edge", "France", "2025-06-01", "2025-06-05")
        self.add_movement(2025, "Next", "Italy", "2025-06-05", "2025-06-08")
        html = self.get("/year/2025")
        self.assertIn("<strong>Edge</strong>", html)
        self.assertNotIn("<strong>Next</strong>", html)
        self.assertIn("4 counted", html)

    def test_page_one_links_land_on_the_table(self):
        self.add_many(23)
        links = dict((label, href) for href, label in self.pager_links(self.get("/year/2025?page=2")))
        self.assertEqual(links["Page 1"], "/year/2025#movements")
        self.assertEqual(links["Previous page"], "/year/2025#movements")
        self.assertEqual(links["Next page"], "/year/2025?page=3#movements")
        self.assertIn('id="movements"', self.get("/year/2025"))

    def delete(self, movement_id, page=None):
        data = {"action": "delete", "confirm_delete": "2"}
        if page is not None:
            data["page"] = str(page)
        return self.client.post(f"/movements/{movement_id}", data=data)

    def test_delete_from_dashboard_keeps_the_page_and_shows_the_message(self):
        ids = self.add_many(23)
        resp = self.delete(ids[12], page=2)
        # No #movements anchor, so the "Movement deleted." message at the top stays in view.
        self.assertEqual(resp.headers["Location"], "/year/2025?page=2")
        html = self.get("/year/2025?page=2")
        self.assertIn("Movement deleted.", html)
        self.assertNotIn("City13", self.cities(html))

    def test_deleting_the_last_row_of_the_last_page_lands_on_the_new_last_page(self):
        ids = self.add_many(21)
        resp = self.delete(ids[20], page=3)
        html = self.get(resp.headers["Location"])
        self.assertIn('aria-current="page" aria-label="Page 2"', html)
        self.assertEqual(self.cities(html)[0], "City11")

    def test_delete_from_movement_page_returns_to_its_page(self):
        ids = self.add_many(23)
        self.assertEqual(self.delete(ids[21]).headers["Location"], "/year/2025?page=3")

    def test_back_link_targets_the_movement_page(self):
        ids = self.add_many(23)
        html = self.get(f"/movements/{ids[14]}")
        self.assertIn('<a class="back" href="/year/2025?page=2#movements">', html)
        html = self.get(f"/movements/{ids[0]}")
        self.assertIn('<a class="back" href="/year/2025#movements">', html)
        html = self.get("/year/2025/movements/new")
        self.assertIn('<a class="back" href="/year/2025">', html)

    def test_page_size_is_remembered_for_back_link_and_delete(self):
        ids = self.add_many(60)
        self.get("/year/2025?per_page=25")
        self.assertIn('<a class="back" href="/year/2025?page=2&amp;per_page=25#movements">',
                      self.get(f"/movements/{ids[30]}"))
        self.assertEqual(self.delete(ids[55]).headers["Location"], "/year/2025?page=3&per_page=25")
        html = self.get("/year/2025")
        self.assertEqual(len(self.cities(html)), 25)
        # Going back to 10 must say so explicitly, or the remembered 25 would win.
        size_links = re.findall(r'<a href="([^"]+)"[^>]*aria-label="Show (\d+) per page"', html)
        self.assertIn(("/year/2025?per_page=10#movements", "10"), size_links)
        self.get("/year/2025?per_page=10")
        self.assertEqual(len(self.cities(self.get("/year/2025"))), 10)


if __name__ == "__main__":
    unittest.main()
