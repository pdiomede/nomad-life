"""Security fixes of 2.1.5, from the audit of the public calendar link."""

from unittest import mock

import app as appmod
from tests.test_fixes_158 import other_connection
from tests.test_public_calendar import PublicCalendarCase


class AlertTests(PublicCalendarCase):
    def test_turning_it_on_alerts_the_mailbox(self):
        before = len(self.outbox)
        self.switch("on")
        alerts = [m for m in self.outbox[before:] if m["subject"].startswith("Security alert")]
        self.assertEqual(len(alerts), 1)
        self.assertIn("A public link to the calendar of your Nomad Life account was turned on",
                      alerts[0]["text"])

    def test_pressing_on_again_sends_nothing_more(self):
        self.switch("on")
        before = len(self.outbox)
        self.switch("on")
        self.assertEqual(len(self.outbox), before)


class RaceTests(PublicCalendarCase):
    def test_a_session_ended_meanwhile_cannot_make_the_link_again(self):
        real = appmod.db.transaction

        def owner_secures_first(*args, **kwargs):
            conn = other_connection(self)  # the password changed and the link ended meanwhile
            conn.execute("UPDATE users SET password_hash = 'x', session_version = "
                         "session_version + 1 WHERE email = 'a@example.com'")
            conn.execute("DELETE FROM public_calendars")
            conn.commit()
            conn.close()
            return real(*args, **kwargs)

        with mock.patch.object(appmod.db, "transaction", owner_secures_first):
            html = self.text(self.client.post("/settings", data={
                "action": "public_calendar", "value": "on"}, follow_redirects=True))
        self.assertIsNone(self.token())
        self.assertNotIn("Your public calendar link is ready", html)


class RemovedDelegateTests(PublicCalendarCase):
    def test_removing_a_delegate_says_the_link_may_be_with_them(self):
        self.invite()
        _resp, sid = self.accept()
        self.switch("on")
        html = self.text(self.client.post("/settings", data={
            "action": "delegate_remove", "delegate_id": sid, "confirm_delete": "2"},
            follow_redirects=True))
        self.assertIn("d@example.com may still have your public calendar link", html)

    def test_without_the_link_nothing_is_said(self):
        self.invite()
        _resp, sid = self.accept()
        html = self.text(self.client.post("/settings", data={
            "action": "delegate_remove", "delegate_id": sid, "confirm_delete": "2"},
            follow_redirects=True))
        self.assertNotIn("public calendar link", html)


class RobotsTests(PublicCalendarCase):
    def test_crawlers_may_fetch_it_to_read_its_noindex(self):
        self.switch("on")
        self.assertNotIn("Disallow: /c/", self.text(self.app.test_client().get("/robots.txt")))
        resp = self.page()
        self.assertEqual(resp.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', self.text(resp))


class FreeTextCountryTests(PublicCalendarCase):
    def test_a_country_that_is_not_one_shows_as_other_place(self):
        self.switch("on")
        self.add_movement(2026, "", "Home with Marco", "2026-05-01", "2026-05-03")
        html = self.text(self.page())
        self.assertNotIn("Marco", html)
        self.assertIn("Other place</span> <span class=\"muted\">3 days</span>", html)
        self.assertIn("Italy</span>", html)  # known countries as they are
        # The owner's own dashboard keeps what was typed.
        self.assertIn("Home with Marco", self.text(self.client.get("/year/2026")))
