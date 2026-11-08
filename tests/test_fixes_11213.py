"""1.12.13 fixes: a review of the Nomad Life and Google Calendar tabs (10 bugs), and two found
in use (the Google tab's "Connect again" on a working connection, Refresh list next to a
chosen calendar)."""
from unittest import mock

import app as appmod
from tests.test_calendar_integration import read
from tests.test_calendar_tabs import BOTH, TabsCase, ev


class TabsReviewTests(TabsCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.choose()

    def test_1_the_tooltip_takes_no_room_until_shown(self):
        # A hidden (opacity 0) tooltip still made phone pages scroll sideways with a long
        # calendar name, such as an email address.
        css = read("static", "css", "style.css")
        rule = css[css.index(".cal-tab[data-tip]::after, .cal-icon[data-tip]::after {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("display: none;", rule)
        self.assertIn("max-width: min(300px, calc(100vw - 48px));", rule)
        self.assertIn("white-space: normal;", rule)
        self.assertIn('.cal-tab[data-cal-tab="google"]::after, .cal-icon[data-tip]::after { left: auto; right: 0; }', css)
        self.assertIn(".cal-tab[data-tip]:focus-visible::after, .cal-icon[data-tip]:focus-visible::after { display: block; }", css)

    def test_2_reloads_do_not_wait_on_google(self):
        # The address kept ?cal=google after a tab click, so every reload read Google before
        # answering; and each click read it again.
        js = read("static", "js", "theme.js")
        block = js[js.index('document.querySelectorAll("[data-cal-tabs]")'):]
        block = block[:block.index("  // Map: zoom")]
        self.assertIn('url.searchParams.delete("cal");', block)
        self.dashboard("?cal=google")
        reads = sum("/events" in c[1] for c in self.google.calls)
        self.client.get("/year/2026/calendar/google")
        self.dashboard("?cal=google")
        self.assertEqual(sum("/events" in c[1] for c in self.google.calls), reads)  # kept a minute

    def test_2_the_cache_follows_the_connection(self):
        self.client.get("/year/2026/calendar/google")
        self.choose("trips@group.calendar.google.com")
        self.client.get("/year/2026/calendar/google")
        self.assertIn("trips%40group.calendar.google.com/events", self.google.calls[-1][1])

    def test_3_the_tabs_come_before_the_content(self):
        html = self.dashboard()
        self.assertLess(html.index("data-cal-tabs"), html.index('<details class="card fold-card cal-card"'))
        self.assertLess(html.index("data-cal-tabs"), html.index('data-cal-panel="local"'))

    def test_4_the_head_stacks_before_the_title_wraps(self):
        css = read("static", "css", "style.css")
        self.assertIn(".fold-head-grid > h2, .fold-start { white-space: nowrap; }", css)
        self.assertIn("@media (max-width: 820px) {\n  .fold-head-grid { grid-template-columns: minmax(0, 1fr) auto; }", css)
        self.assertNotIn("@media (max-width: 720px) {\n  .fold-head-grid", css)

    def test_5_the_page_and_the_tab_stay_together(self):
        # A tab link dropped the movements page, and the pager dropped the tab.
        for i in range(12):
            self.add_movement(2026, "Rome", "Italy", f"2026-04-{i + 1:02d}", f"2026-04-{i + 1:02d}")
        html = self.dashboard("?page=2&cal=google")
        self.assertIn('href="/year/2026?page=2&amp;cal=local#calendar" data-cal-tab="local"', html)
        self.assertIn('href="/year/2026?cal=google#movements"', html)  # page 1 of the pager

    def test_6_the_nomad_life_link_opens_the_card(self):
        html = self.dashboard("?cal=local")
        self.assertIn('<details class="card fold-card cal-card" open>', html)
        self.assertIn('<div class="cal-panel" data-cal-panel="local">', html)
        # Open by default since 2.1.0, on the Nomad Life tab, whatever the link asks.
        self.assertIn('<details class="card fold-card cal-card" open>', self.dashboard())
        self.assertIn('<details class="card fold-card cal-card" open>', self.dashboard("?cal=other"))

    def test_7_an_event_from_last_year_started_earlier(self):
        # Clipped to January 1 first, an event that began in December looked as if it started
        # with one beginning on January 1, and the shorter one took the shared days.
        self.google.events = [ev("Winter", "2025-12-30", "2026-01-03"),
                              ev("Lisbon", "2026-01-01", "2026-01-11")]
        html = self.client.get("/year/2026/calendar/google").get_data(as_text=True)
        self.assertIn('<span dir="auto">Lisbon</span> <span class="muted">10 days</span>', html)
        self.assertNotIn('<span dir="auto">Winter</span>', html)

    def test_8_event_titles_get_no_flags(self):
        self.google.events = [ev("PT", "2026-03-02", "2026-03-03")]
        html = self.client.get("/year/2026/calendar/google").get_data(as_text=True)
        legend = html[html.index('<ul class="cal-legend'):html.index("</ul>")]
        self.assertNotIn('class="flag', legend)
        self.assertIn('<span dir="auto">PT</span>', legend)
        # Since 1.12.15 no legend has flags: the Nomad Life days' tooltips name the country
        # with its flag instead (tests/test_calendar.py, CalendarTooltipTests).

    def test_9_a_click_between_the_tabs_does_not_fold_the_card(self):
        self.assertIn(".fold-tools .cal-tabs { pointer-events: auto; }", read("static", "css", "style.css"))

    def test_10_a_late_failure_keeps_the_choice(self):
        js = read("static", "js", "theme.js")
        load = js[js.index("function load(fresh)", js.index("data-cal-tabs")):]
        block = load[load.index(".catch(function () {"):]
        block = block[:block.index("});")]
        self.assertIn('if (googleTab.getAttribute("aria-current") === "page") {', block)
        self.assertIn("loaded = false;", block)


class FoundInUseTests(TabsCase):
    def test_allow_events_on_a_working_connection(self):
        # A connection from before the events permission showed "Connect again", as if it were
        # broken; Google's answer then went to Settings instead of back to the calendar.
        self.google.scope = appmod.GOOGLE_SCOPE
        self.connect()
        self.choose()
        html = self.client.get("/year/2026/calendar/google").get_data(as_text=True)
        self.assertIn(">Allow events</button>", html)
        self.assertNotIn("Connect again", html)
        self.google.scope = BOTH
        resp = self.client.post("/settings/calendar/google/connect",
                                data={"back": "/year/2026?cal=google#calendar"})
        state = resp.headers["Location"].split("state=")[1].split("&")[0]
        resp = self.client.get(f"/settings/calendar/google/callback?code=abc&state={state}")
        self.assertEqual(resp.headers["Location"], "/year/2026?cal=google#calendar")
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn("Nomad Life now shows the events of Work.", html)
        self.assertIn('<span dir="auto">Lisbon</span>', html)

    def test_back_is_only_a_dashboard_path_of_this_site(self):
        for back in ("https://evil.example/year/1", "//evil.example/year", "/settings", "/year/\n1"):
            resp = self.client.post("/settings/calendar/google/connect", data={"back": back})
            state = resp.headers["Location"].split("state=")[1].split("&")[0]
            self.choose()
            resp = self.client.get(f"/settings/calendar/google/callback?code=abc&state={state}")
            self.assertEqual(resp.headers["Location"], "/settings", back)

    def test_refresh_list_only_while_choosing(self):
        self.connect()
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn(">Refresh list</button>", html)  # no calendar chosen yet
        self.choose()
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertNotIn(">Refresh list</button>", html)
        self.assertIn("Disconnect Google account</button>", html)


class IntegrationReviewTests(TabsCase):
    """A review of the whole Google Calendar integration and how events show (8 bugs), and
    the account chooser found in use."""

    def setUp(self):
        super().setUp()
        self.connect()
        self.choose()

    def events_calls(self):
        return [c for c in self.google.calls if "/events" in c[1]]

    def fragment(self):
        return self.client.get("/year/2026/calendar/google").get_data(as_text=True)

    def test_1_compact_answers_in_smaller_pages(self):
        # Google's indented JSON for 2500 events passed google_api's 1 MB read, so a busy
        # calendar always said "Google did not answer".
        self.fragment()
        url = self.events_calls()[-1][1]
        self.assertIn("prettyPrint=false", url)
        self.assertIn("maxResults=1000", url)
        self.assertIn("prettyPrint=false", [c for c in self.google.calls
                                            if c[1].startswith(appmod.GOOGLE_CALENDAR_LIST_URL)][-1][1])

    def test_2_titles_differing_by_a_mark_stay_apart(self):
        # The country fold drops combining marks, so "visa" (ビザ) and "pizza" (ピザ) were one.
        self.google.events = [ev("ビザ", "2026-03-02", "2026-03-03"), ev("ピザ", "2026-03-04", "2026-03-05")]
        html = self.fragment()
        self.assertIn('<span dir="auto">ビザ</span> <span class="muted">1 day</span>', html)
        self.assertIn('<span dir="auto">ピザ</span> <span class="muted">1 day</span>', html)

    def test_3_a_deleted_calendar_says_so(self):
        def gone(method, url, data=None, token=None):
            if "/events" in url:
                appmod.g.google_status, appmod.g.google_reason = 404, "notFound"
                return None
            return self.google(method, url, data, token)
        with mock.patch.object(appmod, "google_api", side_effect=gone):
            html = self.fragment()
        self.assertIn("is no longer available to this Google account", html)
        self.assertIn("Choose another one in Settings", html)

    def test_3_a_rate_limit_is_an_outage_and_a_lost_permission_asks_again(self):
        def answer(status, reason):
            def fake(method, url, data=None, token=None):
                if "/events" in url:
                    appmod.g.google_status, appmod.g.google_reason = status, reason
                    return None
                return self.google(method, url, data, token)
            return fake
        with mock.patch.object(appmod, "google_api", side_effect=answer(403, "rateLimitExceeded")):
            self.assertIn("Google did not answer", self.fragment())
        appmod._events_failures.clear()  # past the 10 seconds a failure is remembered
        with mock.patch.object(appmod, "google_api", side_effect=answer(403, "insufficientPermissions")):
            self.assertIn(">Allow events</button>", self.fragment())

    def test_4_the_cache_stays_bounded(self):
        with mock.patch.object(appmod, "GOOGLE_EVENTS_CACHE_EVENTS", 3):
            appmod.remember_events(("a",), {"stays": [1, 2], "truncated": False})
            appmod.remember_events(("b",), {"stays": [1, 2], "truncated": False})
            self.assertEqual(list(appmod._events_cache), [("b",)])  # 4 events would not fit
            appmod.remember_events(("c",), {"stays": [1, 2, 3, 4], "truncated": False})
            self.assertNotIn(("c",), appmod._events_cache)  # too big to keep at all
        appmod._events_cache[("old",)] = (0, {"stays": [1], "truncated": False})
        appmod.remember_events(("d",), {"stays": [1], "truncated": False})
        self.assertNotIn(("old",), appmod._events_cache)  # expired entries are dropped

    def test_5_the_calendar_list_cannot_loop(self):
        calls = []

        def endless(method, url, data=None, token=None):
            calls.append(url)
            return {"items": [], "nextPageToken": "again"}
        with self.app.test_request_context():
            with mock.patch.object(appmod, "google_api", side_effect=endless):
                self.assertEqual(appmod.google_calendars("t"), [])
        self.assertEqual(len(calls), 2)  # the token came back the same: stop
        calls.clear()
        tokens = iter(range(100))

        def pages(method, url, data=None, token=None):
            calls.append(url)
            return {"items": [], "nextPageToken": f"p{next(tokens)}"}
        with self.app.test_request_context():
            with mock.patch.object(appmod, "google_api", side_effect=pages):
                appmod.google_calendars("t")
        self.assertEqual(len(calls), appmod.GOOGLE_LIST_PAGES)

    def test_6_the_primary_and_the_chosen_survive_the_cut(self):
        items = [{"id": f"c{i:03d}@group", "summary": f"C {i:03d}"} for i in range(300)]
        items.append({"id": "ada@gmail.com", "summary": "Ada", "primary": True})
        with self.app.test_request_context():
            with mock.patch.object(appmod, "google_api", return_value={"items": items}):
                kept = appmod.google_calendars("t", keep="c299@group")
        self.assertEqual(kept[0]["id"], "ada@gmail.com")
        self.assertIn("c299@group", [c["id"] for c in kept])
        self.assertEqual(len(kept), appmod.GOOGLE_CALENDARS_MAX + 1)

    def test_6_an_unknown_account_never_revokes(self):
        # Without the primary calendar the account read as "", unlike the stored one, so the
        # old token, the same grant as the new one, was revoked.
        self.google.items = [{"id": "work@group.calendar.google.com", "summary": "Work"}]
        self.google.refresh = "refresh-2"
        self.connect()
        self.assertEqual(self.google.revoked(), [])
        conn = __import__("tests.test_fixes_158", fromlist=["x"]).other_connection(self)
        self.assertEqual(conn.execute("SELECT account, calendar_id FROM calendar_links").fetchone(),
                         ("ada@gmail.com", "work@group.calendar.google.com"))
        conn.close()

    def test_7_too_many_events_says_so(self):
        tokens = iter(range(100))

        def many(method, url, data=None, token=None):
            if "/events" in url:
                return {"items": [ev("Lisbon", "2026-01-01", "2026-01-02")],
                        "nextPageToken": f"p{next(tokens)}"}
            return self.google(method, url, data, token)
        with mock.patch.object(appmod, "google_api", side_effect=many):
            html = self.fragment()
        self.assertIn("more events in 2026 than Nomad Life can show at once", html)
        # The cache holds the flag: a second view within the minute says so too.
        self.assertIn("more events in 2026 than Nomad Life can show at once", self.fragment())

    def test_8_the_right_reason_when_nothing_shows(self):
        self.client.post("/settings", data={"action": "calendar_unchoose"})
        self.assertIn("No Google calendar is chosen.", self.fragment())
        self.assertNotIn("is not connected", self.fragment())
        self.choose()
        self.app.config.update(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
        self.assertIn("Calendar Integration is turned off on this server for now.", self.fragment())

    def test_9_google_shows_the_account_chooser(self):
        resp = self.client.post("/settings/calendar/google/connect")
        self.assertIn("prompt=consent+select_account", resp.headers["Location"])
