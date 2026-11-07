"""2.0.0 fixes: a bug hunt in the new AUTO movements (Google Calendar sync)."""
import time
from unittest import mock

import app as appmod
from tests.test_calendar_sync import SyncCase, gev
from tests.test_fixes_158 import other_connection


class PagedGoogle:
    """One event per page, with a next page token: more pages than GOOGLE_EVENTS_PAGES."""
    def __init__(self, google, items):
        self.google, self.items = google, items

    def __call__(self, method, url, data=None, token=None):
        if url.startswith("https://www.googleapis.com/calendar/v3/calendars/"):
            page = int(url.split("pageToken=")[1].split("&")[0]) if "pageToken=" in url else 0
            answer = {"items": [self.items[page]]}
            if page + 1 < len(self.items):
                answer["nextPageToken"] = str(page + 1)
            return answer
        return self.google(method, url, data, token)


class SyncFixTests(SyncCase):
    def setUp(self):
        super().setUp()
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"),
                    gev("g2", "Spain", "2026-11-01", "2026-11-04"))
        self.turn_auto()

    def ids(self):
        return [r["google_event_id"] for r in self.rows()]

    def test_1_a_truncated_read_removes_nothing(self):
        appmod._events_cache.clear()
        paged = PagedGoogle(self.google, [gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"),
                                          gev("g2", "Spain", "2026-11-01", "2026-11-04")])
        with mock.patch.object(appmod, "GOOGLE_EVENTS_PAGES", 1), \
                mock.patch.object(appmod, "google_api", side_effect=paged):
            self.dashboard()
        self.assertEqual(self.ids(), ["g1", "g2"])  # g2 was not read, not deleted

    def test_2_another_calendar_needs_a_new_review(self):
        mid = self.rows()[0]["id"]
        self.client.post(f"/movements/{mid}", data={"action": "update", "notes": "Important"})
        self.client.post("/settings", data={"action": "calendar_unchoose"})
        self.choose("ada@gmail.com")
        self.events(gev("x1", "Rome, Italy", "2026-02-01", "2026-02-06"))
        html = self.dashboard()
        self.assertIn("2026 is back to MANUAL", html)
        self.assertEqual(self.mode(), "manual")
        self.assertEqual(self.rows()[0]["notes"], "Important")  # nothing replaced
        self.assertEqual(self.ids(), ["g1", "g2"])

    def test_3_movements_typed_while_disconnected_are_reviewed(self):
        self.client.post("/settings", data={"action": "calendar_disconnect"})
        self.dashboard()  # back to MANUAL
        self.add_movement(2026, "", "France", "2026-03-01", "2026-03-10")
        self.connect()
        self.choose()
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"), gev("g2", "Spain", "2026-11-01", "2026-11-04"),
                    gev("g3", "France", "2026-03-01", "2026-03-11"))
        self.dashboard()
        self.assertEqual(sum(1 for r in self.rows() if r["country"] == "France"), 1)  # no copy added
        self.assertIn('value="link:', self.review())  # the review links the typed one

    def test_4_a_calendar_gone_is_not_asked_again_at_each_view(self):
        def gone(method, url, data=None, token=None):
            if url.startswith("https://www.googleapis.com/calendar/v3/calendars/"):
                appmod.g.google_status, appmod.g.google_reason = 404, "notFound"
                return None
            return self.google(method, url, data, token)
        appmod._events_cache.clear()
        with mock.patch.object(appmod, "google_api", side_effect=gone) as api:
            html = self.dashboard()
            calls = api.call_count
            self.dashboard()
            self.assertEqual(api.call_count, calls)  # MANUAL now: Google is not asked again
        self.assertIn("2026 is back to MANUAL", html)
        self.assertIn("+ Add movement", self.dashboard())

    def test_5_a_failing_google_is_asked_once_a_minute(self):
        self.events()
        self.google.events_error = True
        clock = [1000.0]
        with mock.patch.object(appmod.time, "monotonic", side_effect=lambda: clock[0]):
            self.dashboard()
            before = len(self.google.calls)
            clock[0] += 11  # past the 10 seconds of a refresh, inside the minute
            self.dashboard()
            self.assertEqual(len(self.google.calls), before)

    def test_6_a_view_with_nothing_to_change_needs_no_write_lock(self):
        self.dashboard()  # the read is cached and applied
        blocker = other_connection(self)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with mock.patch.object(appmod.db, "transaction", side_effect=AssertionError("locked")):
                self.assertEqual(self.client.get("/year/2026").status_code, 200)
        finally:
            blocker.rollback()
            blocker.close()

    def test_7_an_older_read_is_never_applied_after_a_newer_one(self):
        with self.app.test_request_context():
            year_id = appmod.db.query("SELECT id FROM years", one=True)["id"]
            newer = appmod.sync_events(2026, [{"title": "Rome, Italy", "start_date": "2026-02-01",
                                               "end_date": "2026-02-05", "gid": "g1"},
                                              {"title": "Spain", "start_date": "2026-11-01",
                                               "end_date": "2026-11-03", "gid": "g2"},
                                              {"title": "Peru", "start_date": "2026-12-01",
                                               "end_date": "2026-12-02", "gid": "g3"}])
            appmod.apply_sync(year_id, newer, read_at=time.time() + 100)
            stale = newer[:2]  # another server process's copy from before g3 was added
            appmod.apply_sync(year_id, stale, read_at=time.time())
        self.assertEqual(self.ids(), ["g1", "g2", "g3"])


class ReviewFixTests(SyncCase):
    def test_8_a_receipt_added_during_the_review_is_shown_again(self):
        mid = self.add_movement(2026, "", "France", "2026-04-01", "2026-04-03")
        self.events(gev("g1", "Italy", "2026-02-01", "2026-02-03"))
        html = self.review()
        self.assertIn(f'value="del:{mid}" checked', html)
        self.add_receipt(mid)  # in another tab
        resp = self.apply(html)
        self.assertEqual(resp.headers["Location"], "/year/2026/sync")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)

    def test_9_matching_prefers_the_same_city(self):
        rome = self.add_movement(2026, "Rome", "Italy", "2026-02-05", "2026-02-10")
        milan = self.add_movement(2026, "Milan", "Italy", "2026-02-01", "2026-02-04")
        self.events(gev("gm", "Milan, Italy", "2026-02-01", "2026-02-06"),
                    gev("gr", "Rome, Italy", "2026-02-06", "2026-02-11"))
        html = self.review()
        self.assertIn(f'value="edit:{rome}:gr"', html)
        self.assertIn(f'value="edit:{milan}:gm"', html)
        self.assertNotIn("del:", html)
        self.assertNotIn("add:", html)

    def test_10_an_event_read_twice_is_one_movement(self):
        self.events(gev("g1", "Italy", "2026-02-01", "2026-02-03"), gev("g1", "Italy", "2026-02-01", "2026-02-03"))
        self.assertEqual(self.review().count('value="add:g1"'), 1)
        self.turn_auto()
        self.assertEqual(self.client.get("/year/2026").status_code, 200)
        self.assertEqual(len(self.rows()), 1)
        # An id too long to keep whole is no id: never cut, so two long ids never become one.
        self.events(gev("x" * (appmod.GOOGLE_NAME_MAX + 1), "Spain", "2026-03-01", "2026-03-02"))
        self.dashboard()
        self.assertEqual([r["country"] for r in self.rows()], [])


class UiFixTests(SyncCase):
    def setUp(self):
        super().setUp()
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"))
        self.turn_auto()
        [self.mid] = [r["id"] for r in self.rows()]

    def test_11_the_switch_tooltip_takes_no_room_while_hidden(self):
        css = open("static/css/style.css", encoding="utf-8").read()
        self.assertIn(".sync-switch.switch-tip[data-tip]::after { display: none; left: 0; right: auto; "
                      "white-space: normal; width: max-content; max-width: min(260px, calc(100vw - 32px)); }", css)
        self.assertIn(".sync-switch.switch-tip[data-tip]:focus-visible::after { display: block; }", css)

    def test_12_no_date_picker_on_a_read_only_date(self):
        js = open("static/js/theme.js", encoding="utf-8").read()
        self.assertIn("if (input.disabled) btn.disabled = true;", js)

    def test_13_a_page_opened_in_auto_saved_after_manual_keeps_the_place(self):
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})  # another tab
        resp = self.client.post(f"/movements/{self.mid}", data={"action": "update", "notes": "Flat"},
                                follow_redirects=True)
        self.assertNotIn("Country is required.", resp.get_data(as_text=True))
        row = self.rows()[0]
        self.assertEqual((row["city"], row["country"], row["notes"]), ("Rome", "Italy", "Flat"))

    def test_14_no_row_delete_for_a_synced_movement(self):
        html = self.dashboard()
        self.assertNotIn('data-confirm-delete="the movement to', html)

    def test_15_the_review_after_auto_goes_back_to_the_dashboard(self):
        resp = self.client.get("/year/2026/sync")
        self.assertEqual(resp.headers["Location"], "/year/2026")
        resp = self.client.post("/year/2026/sync", data={"plan": "x", "change": []}, follow_redirects=True)
        self.assertIn("2026 is already in AUTO mode.", resp.get_data(as_text=True))
