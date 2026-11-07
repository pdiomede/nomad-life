"""2.0.1 fixes: a bug hunt of switching a year between MANUAL and AUTO (10 bugs), and the 4
left over by the caps of the bug hunts."""
import re
from unittest import mock

import app as appmod
from tests.test_calendar_sync import SyncCase, gev
from tests.test_fixes_158 import other_connection


def read(*parts):
    with open("/".join(parts), encoding="utf-8") as fh:
        return fh.read()


class SwitchCase(SyncCase):
    def setUp(self):
        super().setUp()
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"),
                    gev("g2", "Spain", "2026-03-01", "2026-03-04"))
        self.turn_auto()

    def by_country(self):
        return {r["country"]: r for r in self.rows()}

    def no_write_lock(self):
        return mock.patch.object(appmod.db, "transaction", side_effect=AssertionError("write lock"))


class ReviewMatchingTests(SwitchCase):
    def test_1_a_movement_of_another_calendar_is_matched_not_replaced(self):
        rome = self.by_country()["Italy"]["id"]
        spain = self.by_country()["Spain"]["id"]
        self.client.post(f"/movements/{rome}", data={"action": "update", "notes": "Flat in Trastevere"})
        self.add_receipt(spain)
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        # The same trips under new ids (another calendar, or the events made again).
        self.events(gev("x1", "Rome, Italy", "2026-02-02", "2026-02-07"), gev("x2", "Spain", "2026-03-01", "2026-03-04"))
        html = self.review()
        self.assertIn(f'value="edit:{rome}:x1"', html)
        self.assertIn(f'value="link:{spain}:x2"', html)
        self.assertNotIn("add:", html)
        self.apply(html)
        rows = self.by_country()
        self.assertEqual((rows["Italy"]["notes"], rows["Italy"]["google_event_id"]), ("Flat in Trastevere", "x1"))
        self.assertEqual((rows["Spain"]["google_event_id"], rows["Spain"]["sync_missing"]), ("x2", 0))
        self.assertEqual(len(self.rows()), 2)

    def test_1_an_unmatched_one_with_receipts_is_listed_unticked(self):
        spain = self.by_country()["Spain"]["id"]
        self.add_receipt(spain)
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"))
        html = self.review()
        self.assertIn(f'value="del:{spain}">', html)  # listed, not flagged silently
        self.apply(html)
        self.assertEqual(self.by_country()["Spain"]["google_event_id"], None)  # kept, as typed
        self.assertEqual(self.by_country()["Spain"]["sync_missing"], 0)


class StateTitleTests(SyncCase):
    def test_2_a_city_and_its_state(self):
        place = appmod.event_place
        for title in ("Chicago, IL", "Boston, MA", "Adelaide, SA", "Perth, WA"):
            self.assertIsNone(place(title), title)  # a country's code too: not guessed
        self.assertEqual(place("New York, NY"), ("New York", "United States"))
        self.assertEqual(place("Austin, Texas"), ("Austin", "United States"))
        self.assertEqual(place("Atlanta, Georgia"), ("Atlanta", "United States"))
        self.assertEqual(place("Tbilisi, Georgia"), ("Tbilisi", "Georgia"))
        self.assertEqual(place("Toronto, ON"), ("Toronto", "Canada"))
        self.assertEqual(place("Sydney, New South Wales"), ("Sydney", "Australia"))
        self.assertEqual(place("Rome, IT"), ("Rome", "Italy"))
        self.assertEqual(place("London, UK"), ("London", "United Kingdom"))


class EndAutoTests(SwitchCase):
    def test_3_disconnecting_ends_auto_at_once(self):
        resp = self.client.post("/settings", data={"action": "calendar_disconnect"}, follow_redirects=True)
        self.assertIn("2026 is back to MANUAL, with all its movements kept.", resp.get_data(as_text=True))
        self.assertEqual(self.mode(), "manual")
        self.add_movement(2026, "", "France", "2026-05-01", "2026-05-03")
        self.connect()
        self.choose()
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"), gev("g3", "France", "2026-05-01", "2026-05-04"))
        self.dashboard()
        self.assertEqual(self.mode(), "manual")  # never resumed without the review
        self.assertEqual(sum(r["country"] == "France" for r in self.rows()), 1)

    def test_3_unchoosing_and_choosing_again_never_resumes_by_itself(self):
        self.client.post("/settings", data={"action": "calendar_unchoose"})
        self.assertEqual(self.mode(), "manual")
        mid = self.by_country()["Italy"]["id"]
        self.client.post(f"/movements/{mid}", data={"action": "update", "city": "Milan", "country": "Italy",
                                                     "start_date": "2026-02-10", "end_date": "2026-02-12", "notes": ""})
        self.choose()
        self.dashboard()
        self.assertEqual(self.by_country()["Italy"]["city"], "Milan")  # not reverted silently

    def test_3_another_calendar_chosen_on_a_stale_page(self):
        self.choose("ada@gmail.com")
        self.assertEqual(self.mode(), "manual")


class ReviewReadTests(SwitchCase):
    def test_4_the_review_page_takes_no_write_lock(self):
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        blocker = other_connection(self)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with self.no_write_lock():
                self.assertEqual(self.client.get("/year/2026/sync").status_code, 200)
        finally:
            blocker.rollback()
            blocker.close()

    def test_5_events_past_the_cap_are_a_note_not_a_sync_at_every_view(self):
        self.dashboard()  # shows the message of turning AUTO on
        with mock.patch.object(appmod, "MOVEMENTS_MAX", 2):
            self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"), gev("g2", "Spain", "2026-03-01", "2026-03-04"),
                        gev("g3", "Peru", "2026-06-01", "2026-06-02"))
            with self.no_write_lock():
                html = self.dashboard()
                resp = self.client.get("/year/2026/calendar/google")
        self.assertIn("1 event of your Google calendar is left out: a year holds at most 2 movements.", html)
        self.assertNotIn("Synced with Google Calendar", html)
        self.assertIsNone(resp.headers.get("X-Nomad-Synced"))

    def test_5_the_review_says_what_will_not_fit(self):
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"), gev("g2", "Spain", "2026-03-01", "2026-03-04"),
                    gev("g3", "Peru", "2026-06-01", "2026-06-02"))
        with mock.patch.object(appmod, "MOVEMENTS_MAX", 2):
            self.assertIn("1 of the additions below will not fit and are left out.", self.review())

    def test_10_an_older_read_takes_no_write_lock(self):
        with self.app.test_request_context():
            year_id = appmod.db.query("SELECT id FROM years", one=True)["id"]
            events = appmod.sync_events(2026, [{"title": "Peru", "start_date": "2026-06-01",
                                                "end_date": "2026-06-02", "gid": "g9"}])
            with self.no_write_lock():
                self.assertEqual(appmod.apply_sync(year_id, events, read_at=1.0)["added"], 0)


class FallBackTests(SwitchCase):
    def test_6_the_google_tab_reloads_the_page_also_with_a_503(self):
        self.client.post("/settings", data={"action": "calendar_unchoose"})
        with self.db() as conn:  # a year left AUTO (the fall back runs on the next view)
            conn.execute("UPDATE years SET sync_mode = 'auto'")
        self.choose("ada@gmail.com")
        with self.db() as conn:
            conn.execute("UPDATE years SET sync_mode = 'auto'")
        self.google.events_error = True
        appmod._events_cache.clear()
        resp = self.client.get("/year/2026/calendar/google?refresh=1")
        self.assertEqual(resp.headers.get("X-Nomad-Synced"), "1")
        js = read("static", "js", "theme.js")
        self.assertIn("if (target.pathname + target.search === window.location.pathname + window.location.search) {", js)
        self.assertIn("window.location.reload();", js)

    def test_7_each_lost_state_says_what_happened(self):
        def gone(method, url, data=None, token=None):
            if url.startswith("https://www.googleapis.com/calendar/v3/calendars/"):
                appmod.g.google_status, appmod.g.google_reason = 404, "notFound"
                return None
            return self.google(method, url, data, token)
        appmod._events_cache.clear()
        with mock.patch.object(appmod, "google_api", side_effect=gone):
            html = self.dashboard()
            self.assertIn("2026 is back to MANUAL: the Google calendar “Work” is no longer available", html)
            appmod._events_failures.clear()
            resp = self.client.get("/year/2026/sync", follow_redirects=True)
        self.assertIn("AUTO was not turned on: the Google calendar “Work” is no longer available",
                      resp.get_data(as_text=True))
        self.assertNotIn("Try again in a moment", resp.get_data(as_text=True))

    def test_8_a_page_opened_in_manual_saved_in_auto_says_what_was_kept(self):
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        mid = self.add_movement(2026, "", "Peru", "2026-06-01", "2026-06-02")
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"), gev("g2", "Spain", "2026-03-01", "2026-03-04"),
                    gev("g3", "Peru", "2026-06-01", "2026-06-03"))
        self.turn_auto()  # links the Peru movement to g3
        html = self.client.post(f"/movements/{mid}", data={
            "action": "update", "city": "", "country": "Peru", "start_date": "2026-06-01",
            "end_date": "2026-06-20", "notes": "Lima"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Only the notes were saved: 2026 is in AUTO mode", html)
        self.assertNotIn("Movement updated.", html)

    def test_9_the_flag_shows_only_in_auto(self):
        spain = self.by_country()["Spain"]["id"]
        self.add_receipt(spain)
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"))
        self.assertIn("Not in Google Calendar", self.dashboard())
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        self.assertNotIn("Not in Google Calendar", self.dashboard())
        self.assertNotIn("Its event is no longer in your Google calendar",
                         self.client.get(f"/movements/{spain}").get_data(as_text=True))


class LeftoverTests(SwitchCase):
    def test_l1_a_double_click_says_things_once(self):
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        html = self.client.post("/year/2026/sync-mode", data={"mode": "manual"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertEqual(html.count("is in MANUAL mode"), 1)
        self.assertIn('data-sync-switch data-submit-once>', html)
        page = self.review()
        self.apply(page)
        html = self.apply(page).get_data(as_text=True)  # the same review sent again
        self.assertNotIn("already in AUTO mode", self.client.get("/year/2026").get_data(as_text=True))
        self.assertTrue(html)

    def test_l2_a_year_deleted_during_the_review(self):
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        page = self.review()
        real = appmod.db.transaction

        def delete_first():
            with self.db() as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("DELETE FROM years")
            return real()
        with mock.patch.object(appmod.db, "transaction", side_effect=delete_first):
            resp = self.apply(page)
        self.assertEqual(resp.headers["Location"], "/app")
        html = self.client.get("/year/new").get_data(as_text=True)
        self.assertIn("The year 2026 no longer exists.", html)
        self.assertNotIn("changed meanwhile", html)

    def test_l3_the_bar_tooltip_by_keyboard_and_touch(self):
        js = read("static", "js", "theme.js")
        self.assertIn('bar.setAttribute("tabindex", "0");', js)
        self.assertIn('bar.setAttribute("aria-label", bar.getAttribute("data-tip"));', js)
        css = read("static", "css", "style.css")
        self.assertIn(".meter-tip[data-tip]:focus::after { display: block; }", css)
        self.assertIn('data-marker-text>', self.dashboard())

    def test_l4_numbers_in_line_without_subgrid(self):
        self.assertIn("@supports not (grid-template-rows: subgrid) { .year-stats .stat-foot { min-height: 1.5em; } }",
                      read("static", "css", "style.css"))
