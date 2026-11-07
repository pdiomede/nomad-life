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
        rule = css[css.index(".cal-tab[data-tip]::after {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("display: none;", rule)
        self.assertIn("max-width: min(300px, calc(100vw - 48px));", rule)
        self.assertIn("white-space: normal;", rule)
        self.assertIn(".cal-tab[data-tip]:last-child::after { left: auto; right: 0; }", css)
        self.assertIn(".cal-tab[data-tip]:focus-visible::after { display: block; }", css)

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
        self.assertIn('<details class="card fold-card cal-card">', self.dashboard())  # folded
        self.assertIn('<details class="card fold-card cal-card">', self.dashboard("?cal=other"))

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
        self.add_movement(2026, "Lisbon", "Portugal", "2026-02-01", "2026-02-05")
        local = self.dashboard()
        local = local[local.index('data-cal-panel="local"'):]
        self.assertIn('class="flag', local[:local.index("</ul>")])  # Nomad Life keeps its flags

    def test_9_a_click_between_the_tabs_does_not_fold_the_card(self):
        self.assertIn(".fold-tools .cal-tabs { pointer-events: auto; }", read("static", "css", "style.css"))

    def test_10_a_late_failure_keeps_the_choice(self):
        js = read("static", "js", "theme.js")
        block = js[js.index(".catch(function () {", js.index("data-cal-tabs")):]
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
