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
        # Since the UI review it hangs from the title line, with the chip's tooltip (test_1 there).
        self.assertIn(".sync-chip[data-tip]::after, .year-title .sync-switch.switch-tip[data-tip]::after {\n"
                      "  content: attr(data-tip); display: none;", css)

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

    def test_14_a_synced_row_delete_says_the_event_goes(self):
        # Since 2.1.1 a synced movement can be deleted: its event in Google Calendar goes too.
        html = self.dashboard()
        self.assertIn("and its event in your Google calendar", html)

    def test_15_the_review_after_auto_goes_back_to_the_dashboard(self):
        resp = self.client.get("/year/2026/sync")
        self.assertEqual(resp.headers["Location"], "/year/2026")
        resp = self.client.post("/year/2026/sync", data={"plan": "x", "change": []}, follow_redirects=True)
        self.assertIn("2026 is already in AUTO mode.", resp.get_data(as_text=True))


def contrast(a, b):
    def lum(h):
        r, g, bl = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class LeftoverFixTests(SyncCase):
    """Three bugs of the 2.0.0 bug hunt left over by its cap."""

    def test_16_the_row_marks_explain_themselves(self):
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"))
        self.turn_auto()
        mid = self.rows()[0]["id"]
        self.add_receipt(mid)
        self.events()
        html = self.dashboard()
        self.assertIn('class="sync-row-icon" title="From Google Calendar" aria-label="From Google Calendar" role="img"', html)
        self.assertIn('Not in Google Calendar<span class="visually-hidden">: the event is no longer in Google Calendar.', html)
        self.assertIn("Its event is no longer in your Google calendar.", self.client.get(f"/movements/{mid}").get_data(as_text=True))
        css = open("static/css/style.css", encoding="utf-8").read()
        self.assertIn("@media (forced-colors: active) { .sync-row-icon { forced-color-adjust: none; background-color: CanvasText; } }", css)

    def test_17_right_to_left_names_keep_the_dates_in_place(self):
        self.events(gev("g1", "القاهرة, Egypt", "2026-02-01", "2026-02-05"))
        self.assertIn("<bdi>القاهرة</bdi>", self.review())

    def test_18_day_numbers_and_today_read_on_every_color(self):
        import re
        css = open("static/css/style.css", encoding="utf-8").read()
        for key in appmod.CALENDAR_PALETTE:
            bg = re.search(rf"--{key}: (#[0-9A-F]{{6}});", css).group(1)
            fg = re.search(rf"\.{key} {{ background: var\(--{key}\); color: (#[0-9A-F]{{6}}); }}", css).group(1)
            self.assertGreaterEqual(contrast(bg, fg), 4.5, key)
        self.assertIn(".cal-today { box-shadow: inset 0 0 0 2px currentColor; font-weight: 700; }", css)


class CardsAndLegendTests(SyncCase):
    """The Base card's gear, the numbers in line, the bar's tooltip and the legend (2.0.0)."""

    def test_no_base_tag_in_the_legend(self):
        html = self.dashboard()
        legend = html[html.index('class="cal-legend'):]
        legend = legend[:legend.index("</ul>")]
        self.assertNotIn('<span class="tag">Base</span>', legend)

    def test_manage_base_country_is_a_gear(self):
        html = self.dashboard()
        self.assertIn('<a class="fold-gear stat-gear" href="/year/2026/base" data-tip="Manage Base country" '
                      'aria-label="Manage Base country"></a>', html)
        self.assertNotIn(">Manage Base country</a>", html)

    def test_the_numbers_line_up(self):
        html = self.dashboard()
        stats = html[html.index('<section class="stats year-stats">'):html.index("</section>", html.index("year-stats"))]
        self.assertEqual(stats.count('class="stat-value"'), 4)
        self.assertEqual(stats.count('class="stat-foot"'), 4)
        css = open("static/css/style.css", encoding="utf-8").read()
        self.assertIn(".year-stats .stat { grid-row: span 3; display: grid; grid-template-rows: subgrid;", css)

    def test_the_line_is_the_bars_tooltip(self):
        html = self.dashboard()
        self.assertIn('<div class="meter-tip is-short" data-tip="', html)
        self.assertNotIn("pill-warn day-marker", html)
        css = open("static/css/style.css", encoding="utf-8").read()
        self.assertIn('[data-marker="off"] .meter-tip[data-tip]::after { display: none !important; }', css)


class UiReviewTests(SyncCase):
    """Found in a UI review of the recent features (after 2.0.0)."""

    def css(self):
        return open("static/css/style.css", encoding="utf-8").read()

    def test_1_the_chip_tooltip_takes_no_room_and_its_text_is_read(self):
        html = self.dashboard()
        self.assertIn('MANUAL<span class="visually-hidden">: Movements are added manually</span></span>', html)
        self.assertNotIn('aria-label="MANUAL', html)
        css = self.css()
        self.assertIn(".year-title { position: relative; display: flex;", css)
        self.assertIn("width: max-content; max-width: min(320px, 100%); white-space: normal; text-align: left;", css)
        self.assertIn(".sync-chip[data-tip]:focus::after, .year-title .sync-switch.switch-tip[data-tip]:focus::after { display: block; }", css)

    def test_2_the_gear_tooltip_is_not_squeezed(self):
        self.assertIn(".fold-gear.stat-gear[data-tip]::after { left: auto; right: 0; transform: translateY(-2px); }", self.css())

    def test_3_stacked_cards_keep_their_space(self):
        self.assertIn(".stats.year-stats > .stat { margin-bottom: 16px; }", self.css())

    def test_4_the_switch_says_why_it_is_off(self):
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.new_year(2026, client=other)
        html = other.get("/year/2026").get_data(as_text=True)
        self.assertIn('aria-describedby="sync-switch-tip"', html)
        self.assertIn('<span class="visually-hidden" id="sync-switch-tip">Connect Google Calendar in Settings to turn on AUTO</span>', html)

    def test_5_city_country_events_share_their_countrys_color(self):
        self.add_movement(2026, "Lisbon", "Portugal", "2026-02-01", "2026-02-05")
        self.events(gev("g1", "Lisbon, Portugal", "2026-02-01", "2026-02-06"))
        html = self.client.get("/year/2026/calendar/google").get_data(as_text=True)
        self.assertIn('<input type="hidden" name="country" value="Portugal">', html)
        self.assertIn('class="cal-swatch cal-color-btn cal-base" aria-haspopup="true" aria-expanded="false" '
                      'aria-label="Color of Lisbon, Portugal:', html)
