"""Regression tests for the bugs found in the 1.5.3 review of the admin page and the movement
pages."""
import os
import re
import unittest

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MB = 1024 * 1024


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class AdminFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")

    def user(self, email, **extra):
        with self.db() as conn:
            uid = conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                               "VALUES (?, 'x', '2026-01-01')", (email,)).lastrowid
            for column, value in extra.items():
                conn.execute(f"UPDATE users SET {column} = ? WHERE id = ?", (value, uid))
        return uid

    def test_an_address_taken_over_does_not_show_the_old_owners_events(self):
        a = self.user("new@example.com")  # used old@ before an email change
        b = self.user("old@example.com")  # signed up later with the freed address
        with self.db() as conn:
            conn.execute("INSERT INTO audit_log (user_id, email, event) "
                         "VALUES (?, 'old@example.com', 'password_changed')", (a,))
            conn.execute("INSERT INTO audit_log (user_id, email, event) "
                         "VALUES (?, 'old@example.com', 'sign_in')", (b,))
        page = self.client.get(f"/admin/users/{b}").get_data(as_text=True)
        activity = page.split('id="activity-title"', 1)[1]
        self.assertIn("Signed in", activity)
        self.assertNotIn("Password changed", activity)
        history = self.client.get("/admin?q=old@example.com").get_data(as_text=True)
        self.assertNotIn("Password changed", history.split('id="activity"', 1)[1])
        # And A's own history keeps it, from before its email change.
        history = self.client.get("/admin?q=new@example.com").get_data(as_text=True)
        self.assertIn("Password changed", history.split('id="activity"', 1)[1])

    def test_activity_search_finds_part_of_an_email_or_an_ip(self):
        with self.db() as conn:
            conn.execute("INSERT INTO audit_log (email, event, ip) "
                         "VALUES ('ghost@corp.example', 'sign_in_failed', '72.145.83.93')")
        for q in ("corp.example", "72.145.", "GHOST"):
            section = self.client.get("/admin", query_string={"q": q}).get_data(
                as_text=True).split('id="activity"', 1)[1]
            self.assertIn("ghost@corp.example", section, q)
        section = self.client.get("/admin?q=nothing-like-this").get_data(as_text=True)
        self.assertIn("No events for &ldquo;nothing-like-this&rdquo;.", section)

    def test_a_whole_ip_finds_only_that_ip(self):
        self.user("one@example.com", last_login_ip="10.0.0.1")
        self.user("fifteen@example.com", last_login_ip="10.0.0.15")
        self.user("hundred@example.com", signup_ip="110.0.0.1")
        html = self.client.get("/admin?search=10.0.0.1").get_data(as_text=True)
        accounts = html.split('id="activity"', 1)[0]
        self.assertIn(">one@example.com</a>", accounts)
        self.assertNotIn(">fifteen@example.com</a>", accounts)
        self.assertNotIn(">hundred@example.com</a>", accounts)
        # Part of an IP still finds the whole network.
        accounts = self.client.get("/admin?search=10.0.0.").get_data(as_text=True)
        self.assertIn(">fifteen@example.com</a>", accounts.split('id="activity"', 1)[0])

    def test_price_form_keeps_the_view_and_its_message_in_sight(self):
        html = self.client.get("/admin?search=a&sort=email&q=x@example.com").get_data(as_text=True)
        form = html[html.index('action="/admin/plans/pro"'):]
        form = form[:form.index("</form>")]
        self.assertIn('<input type="hidden" name="search" value="a">', form)
        self.assertIn('<input type="hidden" name="q" value="x@example.com">', form)
        resp = self.client.post("/admin/plans/pro", data={
            "month": "2", "year": "20", "search": "a", "sort": "email", "q": "x@example.com"})
        self.assertEqual(resp.headers["Location"], "/admin?search=a&sort=email&q=x@example.com")

    def test_default_prices_typed_in_are_no_custom_price(self):
        self.client.post("/admin/plans/pro", data={"month": "0.99", "year": "9.99"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM plan_prices").fetchone()[0], 0)
            events = conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = 'admin_price'"
                                  ).fetchone()[0]
        self.assertNotIn('<span class="tag">Custom price</span>',
                         self.client.get("/admin").get_data(as_text=True))
        # A real override, then the defaults typed again: the override goes.
        self.client.post("/admin/plans/pro", data={"month": "2", "year": "20"})
        html = self.client.post("/admin/plans/pro", data={"month": "0.99", "year": "9.99"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Pro is back to its default price", html)
        # Clearing a plan that has no custom price changes nothing and records nothing.
        html = self.client.post("/admin/plans/plus", data={"month": "", "year": ""},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Nomad+ already uses its default price", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'admin_price'").fetchone()[0], events + 2)

    def test_quota_field_shows_what_was_saved(self):
        uid = self.user("q@example.com")
        for typed, shown in (("1.25", "1.25"), ("1.04", "1.04"), ("500", "500")):
            self.client.post(f"/admin/users/{uid}", data={"action": "quota", "quota_mb": typed})
            with self.db() as conn:
                saved = conn.execute("SELECT quota_bytes FROM users WHERE id = ?",
                                     (uid,)).fetchone()[0]
            page = self.client.get(f"/admin/users/{uid}").get_data(as_text=True)
            self.assertIn(f'value="{shown}"', page)
            # Saving the form again as it is keeps the same quota.
            self.client.post(f"/admin/users/{uid}", data={"action": "quota", "quota_mb": shown})
            with self.db() as conn:
                self.assertEqual(conn.execute("SELECT quota_bytes FROM users WHERE id = ?",
                                              (uid,)).fetchone()[0], saved, typed)

    def test_selected_filter_count_is_readable(self):
        css = read("static", "css", "style.css")
        self.assertIn(".admin-filter a[aria-current] .admin-filter-count { background: "
                      "var(--error-text); color: var(--primary-text); }", css)


class MovementFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.client.post("/year/new", data={"year": "2027", "base_city": "Lisbon",
                                            "base_country": "Portugal"})

    def add(self, city, country="Spain", start="2027-03-01", end="2027-03-05", **extra):
        return self.client.post("/year/2027/movements/new", data=dict(
            city=city, country=country, start_date=start, end_date=end, **extra))

    def count(self):
        with self.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0]

    def test_a_form_sent_twice_saves_one_movement(self):
        first = self.add("Rome", "Italy")
        second = self.add("Rome", "Italy")
        self.assertEqual(self.count(), 1)
        self.assertEqual(second.headers["Location"], first.headers["Location"])
        html = self.client.get(second.headers["Location"]).get_data(as_text=True)
        self.assertIn("This movement to Rome is already saved.", html)
        # Other dates are a new movement.
        self.add("Rome", "Italy", "2027-04-01", "2027-04-05")
        self.assertEqual(self.count(), 2)
        # The forms are sent once by the browser too.
        page = self.client.get("/year/2027/movements/new").get_data(as_text=True)
        self.assertIn('enctype="multipart/form-data" data-submit-once>', page)
        page = self.client.get(first.headers["Location"]).get_data(as_text=True)
        self.assertIn('<form class="upload" method="post" enctype="multipart/form-data" '
                      'data-submit-once>', page)

    def test_invisible_places_are_refused_and_joiners_kept(self):
        for city in ("ㅤ", "⠀", "́", "️", " ​ "):
            html = self.add(city).get_data(as_text=True)
            self.assertEqual(self.count(), 0, repr(city))
        self.assertIn("Country and city are required.",
                      self.client.post("/year/2027/movements/new", data={
                          "city": "ㅤ", "country": "Spain", "start_date": "2027-03-01",
                          "end_date": "2027-03-05"}).get_data(as_text=True))
        self.add("خرم‌آباد", "Iran")  # Khorramabad
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT city FROM movements").fetchone()[0],
                             "خرم‌آباد")

    def test_report_blames_only_the_stay_that_gave_up_the_days(self):
        ms = [{"id": 1, "city": "Vienna", "country": "Austria", "start_date": "2027-01-01",
               "end_date": "2027-01-10"},
              {"id": 2, "city": "Sidney", "country": "Australia", "start_date": "2027-01-08",
               "end_date": "2027-01-12"},
              {"id": 3, "city": "Tokyo", "country": "Japan", "start_date": "2027-01-12",
               "end_date": "2027-01-20"}]
        stats = appmod.compute_stats({"year": 2027, "base_country": "Portugal"}, ms)
        pairs, total = appmod.movement_overlaps(ms)
        text = appmod.overlap_report(2027, pairs, ms, stats["counted"], total)
        self.assertIn("counted for Sidney, Australia.\n   Vienna, Austria counts 7 of its 10 "
                      "days.", text)
        self.assertNotIn("Sidney, Australia counts", text)


class CountryPickerTests(unittest.TestCase):
    def test_opening_the_list_highlights_nothing_unless_a_country_is_chosen(self):
        js = read("static", "js", "theme.js")
        # A click on an empty or free text field opened the list with Afghanistan highlighted,
        # so Enter replaced the place with "Afghanistan" instead of submitting.
        self.assertIn("render(entries, current ? entries.indexOf(current) : -1);", js)
        self.assertIn("if (items.length && highlight >= 0) setActive(highlight);", js)
