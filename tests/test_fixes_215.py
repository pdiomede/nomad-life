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


class PublicLinkHuntTests(PublicCalendarCase):
    """2.1.6 bug hunt over the public calendar link."""

    def test_the_copy_tooltip_never_widens_a_phone(self):
        # Hidden only with opacity, it stuck 21 px out of a 320 px screen.
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".fold-copy[data-tip]::after { display: none; left: auto; right: 0; "
                      "transform: none; transition: none; }", css)

    def test_two_free_text_places_keep_two_names_in_the_legend(self):
        self.switch("on")
        self.add_movement(2026, "", "Home with Marco", "2026-05-01", "2026-05-03")
        self.add_movement(2026, "", "Grandma's farm", "2026-06-01", "2026-06-02")
        html = self.text(self.page())
        self.assertIn("Other place</span>", html)
        self.assertIn("Other place 2</span>", html)
        self.assertNotIn("Marco", html)
        self.assertNotIn("Grandma", html)

    def test_an_email_undo_records_that_it_ended_the_link(self):
        self.switch("on")
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "b@example.com",
                                            "current_password": "password1"})
        self.client.post(self.last_link("b@example.com", kind="account/email"))
        self.app.test_client().post(self.last_link("a@example.com", kind="account/email/undo"))
        with self.db() as conn:
            details = [r[0] for r in conn.execute("SELECT detail FROM audit_log WHERE event = "
                                                  "'public_calendar_off'")]
        self.assertEqual(details, ["email change undone"])

    def test_the_preview_image_address_is_absolute_without_app_base_url(self):
        self.switch("on")
        self.app.config["APP_BASE_URL"] = ""
        html = self.text(self.page())
        self.assertIn('<meta property="og:image" content="http://localhost/static/img/'
                      'og-calendar.png">', html)
