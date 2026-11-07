"""1.12.14 fixes: a review of the Google Calendar widget with its new Refresh Calendar and open
in Google Calendar icons (6 bugs)."""
from unittest import mock

import app as appmod
from tests.test_calendar_integration import read
from tests.test_calendar_tabs import TabsCase


def js_block():
    js = read("static", "js", "theme.js")
    block = js[js.index('document.querySelectorAll("[data-cal-tabs]")'):]
    return block[:block.index("  // Map: zoom")]


class WidgetReviewTests(TabsCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.choose()
        self.clock = [1000.0]
        patcher = mock.patch.object(appmod.time, "monotonic", side_effect=lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def google_down(self):
        def down(method, url, data=None, token=None):
            if "/events" in url:
                self.google.calls.append((method, url, data, token))
                appmod.g.google_status, appmod.g.google_reason = 429, "rateLimitExceeded"
                return None
            return self.google(method, url, data, token)
        return mock.patch.object(appmod, "google_api", side_effect=down)

    def reads(self):
        return sum("/events" in c[1] for c in self.google.calls)

    def test_1_a_failed_refresh_keeps_the_calendar(self):
        # A refresh Google did not answer replaced the calendar on screen with "Google did not
        # answer"; the page treated that answer as a success.
        self.client.get("/year/2026/calendar/google")
        self.clock[0] += 30
        with self.google_down():
            resp = self.client.get("/year/2026/calendar/google?refresh=1")
        html = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('<span dir="auto">Lisbon</span>', html)  # the minute's read, kept
        self.assertIn("data-cal-refresh-failed>Google did not answer, so this is the calendar as it was read", html)

    def test_1_with_nothing_to_keep_the_page_keeps_its_view(self):
        with self.google_down():
            resp = self.client.get("/year/2026/calendar/google?refresh=1")
        self.assertEqual(resp.status_code, 503)  # theme.js keeps what it shows
        with self.google_down():
            self.clock[0] += 30
            self.assertEqual(self.client.get("/year/2026/calendar/google").status_code, 200)  # not a refresh

    def test_5_a_failure_is_not_retried_for_ten_seconds(self):
        # Every refresh or Try again called Google twice while it asked the app to slow down.
        with self.google_down():
            self.client.get("/year/2026/calendar/google?refresh=1")
            reads = self.reads()
            self.clock[0] += appmod.GOOGLE_REFRESH_MIN_SECONDS - 1
            self.client.get("/year/2026/calendar/google?refresh=1")
            self.client.get("/year/2026/calendar/google")
            self.assertEqual(self.reads(), reads)
            self.clock[0] += 2
            self.client.get("/year/2026/calendar/google?refresh=1")
            self.assertEqual(self.reads(), reads + 1)

    def test_2_the_tabs_fit_small_phones(self):
        css = read("static", "css", "style.css")
        rule = css[css.index("@media (max-width: 360px) {\n  .cal-tab"):]
        rule = rule[:rule.index("}\n}")]
        self.assertIn(".cal-tab { padding: 4px 8px; }", rule)
        self.assertIn(".cal-icon { width: 24px; height: 24px; }", rule)

    def test_3_refresh_opens_a_folded_card(self):
        block = js_block()
        handler = block[block.index('if (refresh && e.target.closest("[data-cal-refresh]")) {'):]
        handler = handler[:handler.index("return;\n      }")]
        self.assertIn("card.open = true;", handler)

    def test_4_only_the_newest_answer_changes_the_view(self):
        block = js_block()
        self.assertIn("var latest = 0;", block)
        self.assertEqual(block.count("var mine = ++latest;"), 2)  # load and reload
        self.assertGreaterEqual(block.count("if (mine !== latest) return;"), 5)

    def test_6_refresh_is_a_button_with_a_live_status(self):
        html = self.dashboard()
        self.assertIn('data-cal-refresh role="button"', html)
        self.assertIn('<p class="visually-hidden" aria-live="polite" data-cal-status></p>', html)
        block = js_block()
        self.assertIn('if (e.key === " " && e.target.closest("[data-cal-refresh]")) {', block)
        self.assertIn('"Calendar refreshed."', block)
        self.assertIn('"Could not refresh the calendar."', block)
        self.assertNotIn('note.setAttribute("role", "status")', block)
