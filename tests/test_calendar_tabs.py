"""The dashboard Calendar card's Nomad Life and Google Calendar tabs (1.12.13): the tabs show
only with a working Google connection, and the Google tab draws the chosen calendar's events
on the same 12-month grid, one color per title."""
import os
import sqlite3
from datetime import date
from unittest import mock

import app as appmod
import db
from tests.helpers import AppTestCase
from tests.test_calendar_integration import ITEMS, FakeGoogle, read
from tests.test_fixes_158 import other_connection

BOTH = f"{appmod.GOOGLE_SCOPE} {appmod.GOOGLE_EVENTS_SCOPE}"


def ev(summary, start, end, timed=False, status="confirmed"):
    if timed:
        return {"summary": summary, "status": status, "start": {"dateTime": start},
                "end": {"dateTime": end}}
    return {"summary": summary, "status": status, "start": {"date": start}, "end": {"date": end}}


class EventsGoogle(FakeGoogle):
    def __init__(self, events=None, **kw):
        super().__init__(scope=BOTH, **kw)
        self.events, self.events_error = list(events or []), False

    def __call__(self, method, url, data=None, token=None):
        if url.startswith("https://www.googleapis.com/calendar/v3/calendars/"):
            self.calls.append((method, url, data, token))
            return None if self.events_error else {"items": self.events}
        return super().__call__(method, url, data, token)


class TabsCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config.update(GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="secret",
                               APP_BASE_URL="https://nomad.example")
        self.signup()
        self.new_year(2026)
        self.google = EventsGoogle([ev("Lisbon", "2026-03-02", "2026-03-05")])
        patcher = mock.patch.object(appmod, "google_api", side_effect=self.google)
        patcher.start()
        self.addCleanup(patcher.stop)

    def connect(self):
        resp = self.client.post("/settings/calendar/google/connect")
        state = resp.headers["Location"].split("state=")[1].split("&")[0]
        self.client.get(f"/settings/calendar/google/callback?code=abc&state={state}")

    def choose(self, calendar_id="work@group.calendar.google.com"):
        self.client.post("/settings", data={"action": "calendar_choose", "calendar_id": calendar_id})

    def dashboard(self, query=""):
        return self.client.get(f"/year/2026{query}").get_data(as_text=True)


class TabsShownTests(TabsCase):
    def test_no_tabs_without_a_working_connection(self):
        self.assertNotIn("data-cal-tabs", self.dashboard())  # not connected
        self.connect()
        self.assertNotIn("data-cal-tabs", self.dashboard())  # no calendar chosen
        self.choose()
        self.assertIn("data-cal-tabs", self.dashboard())
        conn = other_connection(self)
        conn.execute("UPDATE calendar_links SET broken = 1")
        conn.commit()
        conn.close()
        self.assertNotIn("data-cal-tabs", self.dashboard())  # broken
        conn = other_connection(self)
        conn.execute("UPDATE calendar_links SET broken = 0")
        conn.commit()
        conn.close()
        self.app.config.update(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
        self.assertNotIn("data-cal-tabs", self.dashboard())  # off on the server

    def test_the_card_without_tabs_is_unchanged(self):
        html = self.dashboard()
        self.assertIn('<details class="card fold-card cal-card">', html)
        self.assertIn('<summary class="card-head fold-summary">', html)
        self.assertNotIn("fold-head-grid", html)
        self.assertNotIn("cal-panel", html)

    def test_the_tabs(self):
        self.connect()
        self.choose()
        html = self.dashboard()
        summary = html[html.index('<details class="card fold-card cal-card">'):]
        summary = summary[:summary.index("</summary>")]
        self.assertNotIn("<a ", summary)  # the real tabs lie outside the summary
        self.assertIn('<span class="cal-tabs cal-tabs-ghost" aria-hidden="true"><span class="cal-tab">Nomad Life</span>'
                      '<span class="cal-tab">Google Calendar</span><span class="cal-refresh cal-icon"></span>'
                      '<span class="cal-open cal-icon"></span></span>', summary)
        self.assertIn('<summary class="card-head fold-summary fold-head-grid">', summary)
        tools = html[html.index('<div class="fold-tools fold-head-grid">'):]
        tools = tools[:tools.index("</nav>")]
        self.assertIn('<a class="cal-tab" href="/year/2026?cal=local#calendar" data-cal-tab="local" '
                      'data-tip="Nomad Life local calendar" aria-current="page">Nomad Life</a>', tools)
        self.assertIn('href="/year/2026?cal=google#calendar" data-cal-tab="google" '
                      'data-src="/year/2026/calendar/google" '
                      'data-tip="This is the Google calendar &quot;Work&quot;" '
                      'aria-label="Google Calendar: Work">Google Calendar</a>', tools)
        self.assertIn('class="fold-gear"', tools)
        # Nomad Life's view is shown, the Google one waits to be opened.
        self.assertIn('<div class="cal-panel" data-cal-panel="local">', html)
        self.assertIn('<div class="cal-panel" data-cal-panel="google" hidden>', html)
        self.assertEqual(self.google.calls[-1][1], appmod.GOOGLE_CALENDAR_LIST_URL + "?" +
                         self.google.calls[-1][1].split("?", 1)[1])  # no events read for it
        # Its Connect again form goes on to Google.
        self.assertEqual(self.client.get("/year/2026").headers["Content-Security-Policy"],
                         appmod.CALENDAR_CSP)

    def test_a_name_is_escaped_in_the_tooltip(self):
        self.google.items = [{"id": "x@group", "summary": 'Trips "2026" <b>'}]
        self.connect()
        self.choose("x@group")
        html = self.dashboard()
        self.assertIn('data-tip="This is the Google calendar &quot;Trips &#34;2026&#34; &lt;b&gt;&quot;"', html)

    def test_cal_google_shows_the_google_view_with_the_card_open(self):
        self.connect()
        self.choose()
        html = self.dashboard("?cal=google")
        self.assertIn('<details class="card fold-card cal-card" open>', html)
        self.assertIn('<div class="cal-panel" data-cal-panel="local" hidden>', html)
        self.assertIn('data-cal-tab="google" data-src', html)
        google = html[html.index('data-cal-panel="google"'):]
        self.assertNotIn("Events of your Google calendar", google)  # the line is gone (1.12.15)
        self.assertIn('<span dir="auto">Lisbon</span> <span class="muted">3 days</span>', google)
        self.assertIn("No event <span class=\"muted\">362 days</span>", google)
        self.assertIn('aria-label="Google Calendar: Work" aria-current="page"', html)


class EventDaysTests(AppTestCase):
    def days(self, events, year=2026):
        with self.app.test_request_context():
            with mock.patch.object(appmod, "google_api", return_value={"items": events}):
                stays = appmod.google_events("t", "cal@x", year)["stays"]
        return [(s["title"], s["start_date"], s["end_date"]) for s in stays]

    def test_all_day_ends_are_exclusive(self):
        self.assertEqual(self.days([ev("Rome", "2026-04-01", "2026-04-04")]),
                         [("Rome", "2026-04-01", "2026-04-03")])

    def test_timed_events(self):
        self.assertEqual(self.days([
            ev("Flight", "2026-05-01T22:00:00+02:00", "2026-05-02T06:00:00+02:00", timed=True),
            ev("Dinner", "2026-05-03T19:00:00Z", "2026-05-04T00:00:00Z", timed=True)]),
            [("Flight", "2026-05-01", "2026-05-02"), ("Dinner", "2026-05-03", "2026-05-03")])

    def test_clipped_to_the_year_and_cancelled_skipped(self):
        self.assertEqual(self.days([
            ev("New year", "2025-12-30", "2026-01-03"),
            ev("Winter", "2026-12-30", "2027-01-05"),
            ev("Before", "2025-12-01", "2025-12-05"),
            ev("Gone", "2026-06-01", "2026-06-02", status="cancelled"),
            {"summary": "Broken", "start": {}, "end": {}},
            ev("", "2026-07-01", "2026-07-02")]),
            # The real dates (google_year_calendar clips them): the order of a shared day
            # depends on when each event started, also before the year.
            [("New year", "2025-12-30", "2026-01-02"), ("Winter", "2026-12-30", "2027-01-04"),
             ("(No title)", "2026-07-01", "2026-07-01")])

    def test_the_request(self):
        calls = []
        with self.app.test_request_context():
            with mock.patch.object(appmod, "google_api",
                                   side_effect=lambda *a, **k: calls.append((a, k)) or {"items": []}):
                appmod.google_events("token", "a b/c@group.calendar.google.com", 2026)
        url = calls[0][0][1]
        self.assertTrue(url.startswith("https://www.googleapis.com/calendar/v3/calendars/"
                                       "a%20b%2Fc%40group.calendar.google.com/events?"))
        self.assertIn("singleEvents=true", url)
        self.assertIn("timeMin=2025-12-31T00%3A00%3A00Z", url)
        self.assertIn("timeMax=2027-01-02T00%3A00%3A00Z", url)
        self.assertEqual(calls[0][1], {"token": "token"})

    def test_google_down(self):
        with self.app.test_request_context():
            with mock.patch.object(appmod, "google_api", return_value=None):
                self.assertIsNone(appmod.google_events("t", "c", 2026))


class GoogleGridTests(AppTestCase):
    def grid(self, stays):
        with self.app.app_context():
            stays = [dict(s, id=i + 1) for i, s in enumerate(stays)]
            return appmod.google_year_calendar(2026, stays)

    def day(self, cal, iso):
        d = date.fromisoformat(iso)
        return cal["months"][d.month - 1]["days"][d.day - 1]

    def test_same_title_same_color_and_later_start_wins(self):
        cal = self.grid([
            {"title": "Lisbon", "start_date": "2026-01-01", "end_date": "2026-01-10"},
            {"title": "Porto", "start_date": "2026-01-05", "end_date": "2026-01-06"},
            {"title": "LISBON", "start_date": "2026-02-01", "end_date": "2026-02-01"}])
        self.assertEqual(self.day(cal, "2026-01-05")["where"], "Porto")  # inside Lisbon
        self.assertEqual(self.day(cal, "2026-01-07")["where"], "Lisbon")
        self.assertEqual(self.day(cal, "2026-02-01")["color"], self.day(cal, "2026-01-01")["color"])
        self.assertNotEqual(self.day(cal, "2026-01-05")["color"], self.day(cal, "2026-01-01")["color"])
        self.assertEqual([(c["country"], c["days"]) for c in cal["legend"]],
                         [("Lisbon", 9), ("Porto", 2)])
        self.assertEqual(self.day(cal, "2026-03-01")["color"], "cal-none")
        self.assertEqual(self.day(cal, "2026-03-01")["where"], "no event")
        self.assertEqual(cal["not_logged"], 365 - 11)

    def test_colors_repeat_after_fourteen(self):
        stays = [{"title": f"Place {i:02d}", "start_date": f"2026-03-{i + 1:02d}",
                  "end_date": f"2026-03-{i + 1:02d}"} for i in range(15)]
        cal = self.grid(stays)
        self.assertEqual([c["color"] for c in cal["legend"]],
                         [f"cal-{i % 14 + 1}" for i in range(15)])


class FragmentTests(TabsCase):
    def fragment(self, client=None):
        return (client or self.client).get("/year/2026/calendar/google")

    def test_the_events(self):
        self.connect()
        self.choose()
        resp = self.fragment()
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("<html", html)  # a fragment
        self.assertNotIn("<script", html)
        self.assertIn('<span dir="auto">Lisbon</span>', html)
        self.assertIn("work%40group.calendar.google.com/events", self.google.calls[-1][1])

    def test_another_users_year_is_a_404(self):
        self.connect()
        self.choose()
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.assertEqual(self.fragment(other).status_code, 404)
        self.assertEqual(self.client.get("/year/2031/calendar/google").status_code, 404)

    def test_without_the_events_permission(self):
        self.google.scope = appmod.GOOGLE_SCOPE  # the events permission unticked
        self.connect()
        self.choose()
        html = self.fragment().get_data(as_text=True)
        self.assertIn("Your Google Calendar <strong dir=\"auto\">Work</strong> is connected. To show its "
                      "events here, Google needs one more permission", html)
        self.assertIn('<input type="hidden" name="back" value="/year/2026?cal=google#calendar">', html)
        self.assertIn(">Allow events</button>", html)
        self.assertNotIn("Connect again", html)
        self.assertIn('<form method="post" action="/settings/calendar/google/connect">', html)
        self.assertFalse(any("/events" in c[1] for c in self.google.calls))
        # Connecting again with it keeps the chosen calendar and shows the events.
        self.google.scope = BOTH
        self.connect()
        self.assertEqual(appmod_link(self)["calendar_id"], "work@group.calendar.google.com")
        self.assertIn('<span dir="auto">Lisbon</span>', self.fragment().get_data(as_text=True))

    def test_access_removed_at_google(self):
        self.connect()
        self.choose()
        self.google.refresh_error = "invalid_grant"
        html = self.fragment().get_data(as_text=True)
        self.assertIn("Google no longer lets Nomad Life see your calendar.", html)
        self.assertEqual(appmod_link(self)["broken"], 1)
        self.assertNotIn("data-cal-tabs", self.dashboard())

    def test_google_down(self):
        self.connect()
        self.choose()
        self.google.events_error = True
        html = self.fragment().get_data(as_text=True)
        self.assertIn("Google did not answer, so the calendar could not be shown.", html)
        self.assertIn('href="/year/2026?cal=google#calendar" data-cal-retry>Try again</a>', html)
        self.assertEqual(appmod_link(self)["broken"], 0)

    def test_no_events(self):
        self.connect()
        self.choose()
        self.google.events = []
        html = self.fragment().get_data(as_text=True)
        self.assertIn("No events in 2026 in this calendar.", html)
        self.assertIn("No event <span class=\"muted\">365 days</span>", html)

    def test_not_connected(self):
        html = self.fragment().get_data(as_text=True)
        self.assertIn("Google Calendar is not connected.", html)

    def test_the_granted_scopes_are_stored(self):
        self.connect()
        self.assertEqual(appmod_link(self)["scopes"], BOTH)


def appmod_link(test):
    conn = other_connection(test)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM calendar_links").fetchone()
    finally:
        conn.close()


class MigrationTests(AppTestCase):
    def test_an_old_database_gets_the_scopes_column(self):
        path = os.path.join(self.tmp, "old.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE calendar_links (user_id INTEGER PRIMARY KEY, provider TEXT NOT NULL "
                     "DEFAULT 'google', account TEXT NOT NULL DEFAULT '', refresh_token TEXT NOT NULL, "
                     "calendars TEXT NOT NULL DEFAULT '[]', calendar_id TEXT, calendar_name TEXT NOT NULL "
                     "DEFAULT '', broken INTEGER NOT NULL DEFAULT 0, connected_at TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO calendar_links (user_id, refresh_token) VALUES (1, 'r')")
        conn.commit()
        conn.close()
        db.init_db(path)
        conn = sqlite3.connect(path)
        self.assertEqual(conn.execute("SELECT scopes FROM calendar_links").fetchone(), ("",))
        conn.close()


class AssetTests(AppTestCase):
    def test_the_head_grid_and_phone_layout(self):
        css = read("static", "css", "style.css")
        self.assertIn(".fold-head-grid { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);", css)
        self.assertIn(".fold-summary.fold-head-grid::after { grid-column: 3; grid-row: 1; justify-self: end; }", css)
        self.assertIn(".cal-tab[aria-current=\"page\"] { background: var(--primary); color: var(--primary-text); }", css)
        phone = css[css.index("@media (max-width: 820px) {\n  .fold-head-grid"):]
        self.assertIn(".fold-head-grid > .cal-tabs { grid-column: 1 / -1; grid-row: 2; justify-self: center; }", phone)

    def test_the_tabs_script(self):
        js = read("static", "js", "theme.js")
        block = js[js.index('document.querySelectorAll("[data-cal-tabs]")'):]
        block = block[:block.index("  // Map: zoom")]
        self.assertIn("card.open = true;", block)
        self.assertIn('fetch(googleTab.getAttribute("data-src")', block)
        self.assertIn("if (!r.ok || r.redirected)", block)
        self.assertIn("window.location.href = googleTab.href;", block)
        self.assertIn("history.replaceState(", block)
        self.assertNotIn("innerHTML = '", block)  # only the server's fragment goes in as HTML


class RefreshTests(TabsCase):
    """Refresh Calendar: the icon next to the Google Calendar tab reads the calendar again."""

    def setUp(self):
        super().setUp()
        self.connect()
        self.choose()

    def event_reads(self):
        return sum("/events" in c[1] for c in self.google.calls)

    def test_the_icon(self):
        html = self.dashboard()
        nav = html[html.index("data-cal-tabs>"):]
        nav = nav[:nav.index("</nav>")]
        self.assertLess(nav.index('data-cal-tab="google"'), nav.index("data-cal-refresh"))
        self.assertIn('<a class="cal-refresh cal-icon" href="/year/2026?cal=google&amp;refresh=1#calendar" '
                      'data-cal-refresh role="button" data-tip="Refresh Calendar" aria-label="Refresh Calendar"></a>', nav)
        # Its place is kept in the head's invisible copy, so the tabs stay centred.
        self.assertIn('<span class="cal-refresh cal-icon"></span><span class="cal-open cal-icon"></span></span>', html)

    def test_only_with_a_connected_calendar(self):
        self.client.post("/settings", data={"action": "calendar_unchoose"})
        self.assertNotIn("cal-refresh", self.dashboard())
        self.client.post("/settings", data={"action": "calendar_disconnect"})
        self.assertNotIn("cal-refresh", self.dashboard())

    def test_refresh_reads_google_again(self):
        clock = [1000.0]
        with mock.patch.object(appmod.time, "monotonic", side_effect=lambda: clock[0]):
            self.client.get("/year/2026/calendar/google")
            reads = self.event_reads()
            clock[0] += 30
            self.client.get("/year/2026/calendar/google")
            self.assertEqual(self.event_reads(), reads)  # a plain view: the minute's copy
            self.google.events = [ev("Madrid", "2026-05-04", "2026-05-06")]
            html = self.client.get("/year/2026/calendar/google?refresh=1").get_data(as_text=True)
        self.assertEqual(self.event_reads(), reads + 1)
        self.assertIn('<span dir="auto">Madrid</span>', html)
        self.assertNotIn('<span dir="auto">Lisbon</span>', html)

    def test_repeated_refreshes_reuse_a_fresh_read(self):
        clock = [1000.0]
        with mock.patch.object(appmod.time, "monotonic", side_effect=lambda: clock[0]):
            self.client.get("/year/2026/calendar/google?refresh=1")
            reads = self.event_reads()
            clock[0] += appmod.GOOGLE_REFRESH_MIN_SECONDS - 1
            self.client.get("/year/2026/calendar/google?refresh=1")
            self.assertEqual(self.event_reads(), reads)
            clock[0] += 2
            self.client.get("/year/2026/calendar/google?refresh=1")
            self.assertEqual(self.event_reads(), reads + 1)

    def test_without_javascript(self):
        clock = [1000.0]
        with mock.patch.object(appmod.time, "monotonic", side_effect=lambda: clock[0]):
            self.dashboard("?cal=google")
            reads = self.event_reads()
            clock[0] += 30
            html = self.dashboard("?cal=google&refresh=1")
        self.assertEqual(self.event_reads(), reads + 1)
        self.assertIn('<details class="card fold-card cal-card" open>', html)

    def test_the_script_and_styles(self):
        js = read("static", "js", "theme.js")
        block = js[js.index('document.querySelectorAll("[data-cal-tabs]")'):]
        block = block[:block.index("  // Map: zoom")]
        self.assertIn('googleTab.getAttribute("data-src") + (fresh ? "?refresh=1" : "")', block)
        self.assertIn("if (refreshing) return;", block)
        self.assertIn("Could not refresh the calendar. Try again in a moment.", block)
        self.assertIn('if (loaded) reload(); else load(true);', block)
        reload = block[block.index("function reload()"):block.index("function load(fresh)")]
        self.assertNotIn("location.href", reload)  # a failed refresh keeps the view on screen
        css = read("static", "css", "style.css")
        self.assertIn(".cal-refresh.is-spinning::before { animation: cal-spin 0.8s linear infinite; }", css)
        self.assertIn("@media (prefers-reduced-motion: reduce) { .cal-refresh.is-spinning::before { animation: none;", css)


class GoogleIconsTests(TabsCase):
    """The Google tab's icons (Refresh Calendar, open in Google Calendar) show only with it."""

    def setUp(self):
        super().setUp()
        self.connect()
        self.choose()

    def test_open_in_google_calendar(self):
        html = self.dashboard()
        nav = html[html.index("data-cal-tabs>"):]
        nav = nav[:nav.index("</nav>")]
        self.assertLess(nav.index("data-cal-refresh"), nav.index('class="cal-open cal-icon"'))
        self.assertIn('<a class="cal-open cal-icon" href="https://calendar.google.com/calendar/r/year/2026/1/1?'
                      'cid=work%40group.calendar.google.com&amp;authuser=ada%40gmail.com" target="_blank" '
                      'rel="noopener noreferrer" data-tip="Open the Google calendar &quot;Work&quot;" '
                      'aria-label="Open the Google calendar Work (opens in a new tab)"></a>', nav)

    def test_the_icons_show_only_on_the_google_tab(self):
        self.assertIn('<div class="fold-wrap has-cal-tabs" id="calendar">', self.dashboard())
        self.assertIn('<div class="fold-wrap has-cal-tabs" id="calendar">', self.dashboard("?cal=local"))
        self.assertIn('<div class="fold-wrap has-cal-tabs cal-on-google" id="calendar">', self.dashboard("?cal=google"))
        css = read("static", "css", "style.css")
        self.assertIn(".cal-icon { position: relative; flex: none; display: none;", css)
        self.assertIn(".cal-on-google .cal-icon { display: inline-grid; }", css)
        js = read("static", "js", "theme.js")
        self.assertIn('wrap.classList.toggle("cal-on-google", which === "google");', js)
        self.assertIn('if (e.target.closest(".cal-open")) return;', js)


class LandingShotTests(AppTestCase):
    def test_the_landing_page_shows_the_calendar(self):
        html = self.client.get("/").get_data(as_text=True)
        section = html[html.index('<section class="landing-shot"'):]
        section = section[:section.index("</section>")]
        self.assertIn('<h2 id="calendar-shot-title" class="section-title">Your whole year at a glance</h2>', section)
        self.assertIn('class="doc-shot doc-shot-light" src="/static/img/docs/calendar-light.webp', section)
        self.assertIn('class="doc-shot doc-shot-dark" src="/static/img/docs/calendar-dark.webp', section)
        import json
        size = json.loads(read("static", "img", "docs", "shots.json"))["calendar"]
        self.assertIn(f'width="{size[0]}" height="{size[1]}"', section)  # the screenshot's own size
        self.assertLess(html.index('class="landing-shot"'), html.index('id="how-it-works"'))

    def test_the_docs_show_the_google_tab(self):
        html = self.client.get("/docs").get_data(as_text=True)
        self.assertIn("img/docs/calendar-google-light.webp", html)
        self.assertIn("img/docs/calendar-google-dark.webp", html)
