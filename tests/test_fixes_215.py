"""Fixes of 2.1.5, found by the bug hunt over the public calendar link and the 2.1.4 notice."""

import os

from tests.test_public_calendar import PublicCalendarCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SwitchMessageTests(PublicCalendarCase):
    def test_the_message_after_the_switch_stays_in_view(self):
        # It went to /settings#calendar, below the message at the top of the page.
        resp = self.client.post("/settings", data={"action": "public_calendar", "value": "on"})
        self.assertTrue(resp.headers["Location"].endswith("/settings"))


class CopyFallbackTests(PublicCalendarCase):
    def test_a_refused_clipboard_falls_back_to_the_old_copy(self):
        # writeText is refused when the page is not focused or permission is missing: it said
        # "Copy failed" without trying the selection copy that works there.
        with open(os.path.join(ROOT, "static", "js", "theme.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("navigator.clipboard.writeText(url).then(function () { done(true); }, "
                      "bySelection);", js)

    def test_an_icon_button_keeps_its_icon_and_says_it_in_its_tooltip(self):
        with open(os.path.join(ROOT, "static", "js", "theme.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('icon = btn.hasAttribute("data-copy-icon")', js)
        self.assertIn('if (tip !== null) btn.setAttribute("data-tip", say);', js)


class MessageOrderTests(PublicCalendarCase):
    def test_the_password_message_comes_before_what_it_ended(self):
        self.switch("on")
        html = self.text(self.client.post("/settings", data={
            "action": "password", "current_password": "password1",
            "password": "new-password-2", "confirm": "new-password-2"}, follow_redirects=True))
        self.assertLess(html.index("Your password has been changed"),
                        html.index("The public calendar link was turned off too"))


class CopyAnnouncedTests(PublicCalendarCase):
    def test_link_copied_is_said_in_a_live_region(self):
        with open(os.path.join(ROOT, "static", "js", "theme.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('live.setAttribute("aria-live", "polite");', js)
        self.assertIn('live.setAttribute("data-copy-status", "")', js)
