"""The public calendar link (2.1.5): Settings > Calendar turns on a secret /c/<token> page with
the calendar only (countries, their days, each day's country), read only, for anyone."""

import re

from tests.test_delegates import DelegateCase


class PublicCalendarCase(DelegateCase):
    def setUp(self):
        super().setUp()
        with self.db() as conn:
            conn.execute("UPDATE users SET first_name = 'Paolo', last_name = 'Diomede' "
                         "WHERE email = 'a@example.com'")

    def switch(self, value, client=None):
        return (client or self.client).post("/settings", data={
            "action": "public_calendar", "value": value}, follow_redirects=True)

    def token(self):
        with self.db() as conn:
            row = conn.execute("SELECT token FROM public_calendars").fetchone()
            return row["token"] if row else None

    def page(self, path=None):
        return self.app.test_client().get(path or f"/c/{self.token()}")


class SwitchTests(PublicCalendarCase):
    def test_off_by_default(self):
        html = self.text(self.client.get("/settings"))
        self.assertIn("Generate public calendar URL", html)
        self.assertIn('role="switch" aria-checked="false"', html)
        self.assertIsNone(self.token())
        self.assertNotIn("fold-copy", self.text(self.client.get("/year/2026")))

    def test_on_makes_a_link_off_kills_it_on_again_makes_another(self):
        html = self.text(self.switch("on"))
        first = self.token()
        self.assertRegex(first, r"^[A-Za-z0-9_-]{32}$")
        self.assertIn("Your public calendar link is ready.", html)
        self.assertIn(f'value="http://localhost:5050/c/{first}"', html)
        self.assertIn('role="switch" aria-checked="true"', html)
        self.assertEqual(self.page().status_code, 200)
        self.switch("off")
        self.assertIsNone(self.token())
        self.assertEqual(self.page(f"/c/{first}").status_code, 404)
        self.switch("on")
        self.assertNotEqual(self.token(), first)
        self.assertEqual(self.page(f"/c/{first}").status_code, 404)

    def test_on_twice_keeps_one_link(self):
        self.switch("on")
        first = self.token()
        self.assertIn("already on", self.text(self.switch("on")))
        self.assertEqual(self.token(), first)

    def test_switching_is_audited(self):
        self.switch("on")
        self.switch("off")
        with self.db() as conn:
            events = [r[0] for r in conn.execute(
                "SELECT event FROM audit_log WHERE event LIKE 'public_calendar%' ORDER BY id")]
        self.assertEqual(events, ["public_calendar_on", "public_calendar_off"])

    def test_signed_out_cannot_switch(self):
        resp = self.app.test_client().post("/settings", data={"action": "public_calendar",
                                                              "value": "on"})
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(self.token())


class PageTests(PublicCalendarCase):
    def setUp(self):
        super().setUp()
        self.switch("on")

    def test_the_page_shows_the_calendar_without_signing_in(self):
        resp = self.page()
        html = self.text(resp)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("<h1>Calendar (2026)</h1>", html)
        self.assertIn("Italy</span> <span class=\"muted\">10 days</span>", html)
        self.assertIn("January", html)
        self.assertIn('id="theme-toggle"', html)

    def test_years_have_tabs_and_their_own_address(self):
        self.new_year(2027)
        html = self.text(self.page())
        self.assertIn(f'href="/c/{self.token()}/2027"', html)
        self.assertIn("<h1>Calendar (2027)</h1>", self.text(self.page(f"/c/{self.token()}/2027")))
        self.assertEqual(self.page(f"/c/{self.token()}/2031").status_code, 404)

    def test_bad_links_are_all_the_same_404(self):
        for path in ("/c/" + "A" * 32, "/c/short", "/c/" + "A" * 33, "/c/" + "a" * 5000,
                     "/c/" + "%3Cscript%3E" + "A" * 20, f"/c/{self.token()}x"):
            self.assertEqual(self.page(path).status_code, 404, path)

    def test_a_disabled_or_unconfirmed_owner_hides_it(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET disabled = 1 WHERE email = 'a@example.com'")
        self.assertEqual(self.page().status_code, 404)
        with self.db() as conn:
            conn.execute("UPDATE users SET disabled = 0, verified_at = NULL "
                         "WHERE email = 'a@example.com'")
        self.assertEqual(self.page().status_code, 404)

    def test_nothing_private_is_on_the_page(self):
        with self.db() as conn:
            conn.execute("UPDATE movements SET notes = 'secret note'")
        html = self.text(self.page())
        for private in ("a@example.com", "Paolo", "Diomede", "Lisbon", "Rome", "Coworking",
                        "secret note", "hotel.pdf", "lease.pdf", "csrf_token", "<form",
                        "application/json", "user-menu", "Settings"):
            self.assertNotIn(private, html, private)

    def test_headers_keep_the_link_secret(self):
        resp = self.page()
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(resp.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(resp.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("script-src 'self'", resp.headers["Content-Security-Policy"])
        # Not in robots.txt: a crawler kept out never reads the noindex (test_security_215.py).
        self.assertNotIn("/c/", self.text(self.app.test_client().get("/robots.txt")))

    def test_an_owner_without_years_gets_an_empty_page(self):
        with self.db() as conn:
            conn.execute("DELETE FROM years")
        resp = self.page()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("No calendar yet.", self.text(resp))


class CopyButtonTests(PublicCalendarCase):
    def test_owner_has_it_in_settings_and_next_to_the_calendar_title(self):
        self.switch("on")
        url = f"http://localhost:5050/c/{self.token()}"
        html = self.text(self.client.get("/settings"))
        self.assertIn(f'data-copy-link="{url}" data-tip="Copy the public calendar link"', html)
        html = self.text(self.client.get("/year/2026"))
        self.assertIn(f'class="fold-gear fold-copy" data-copy-link="{url}" data-copy-icon '
                      'data-tip="Copy the public calendar link"', html)

    def test_delegate_has_it_on_the_shared_view_while_the_owner_has_it_on(self):
        self.invite()
        _resp, sid = self.accept()
        self.assertNotIn("fold-copy", self.text(self.d.get(f"/shared/{sid}/year/2026")))
        self.switch("on")
        html = self.text(self.d.get(f"/shared/{sid}/year/2026"))
        self.assertIn(f'data-copy-link="http://localhost:5050/c/{self.token()}"', html)
        self.assertNotIn("Calendar Integration", html)  # the gear stays the owner's

    def test_the_public_page_has_no_copy_button(self):
        self.switch("on")
        self.assertNotIn("data-copy-link", self.text(self.page()))


class SecuringEndsItTests(PublicCalendarCase):
    def setUp(self):
        super().setUp()
        self.switch("on")

    def test_password_change(self):
        html = self.text(self.client.post("/settings", data={
            "action": "password", "current_password": "password1",
            "password": "new-password-2", "confirm": "new-password-2"}, follow_redirects=True))
        self.assertIsNone(self.token())
        self.assertIn("The public calendar link was turned off too", html)

    def test_sign_out_everywhere_else(self):
        self.client.post("/settings", data={"action": "sessions"})
        self.assertIsNone(self.token())

    def test_password_reset(self):
        self.age_emails()
        self.app.test_client().post("/forgot", data={"email": "a@example.com"})
        self.app.test_client().post(self.last_link("a@example.com", kind="reset"),
                                    data={"password": "reset-password-3",
                                          "confirm": "reset-password-3"})
        self.assertIsNone(self.token())

    def test_account_delete(self):
        self.client.post("/settings", data={"action": "delete", "current_password": "password1",
                                            "confirm_email": "a@example.com",
                                            "confirm_delete": "2"})
        self.assertIsNone(self.token())

    def test_email_undo(self):
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "b@example.com",
                                            "current_password": "password1"})
        self.client.post(self.last_link("b@example.com", kind="account/email"))
        self.app.test_client().post(self.last_link("a@example.com", kind="account/email/undo"))
        self.assertIsNone(self.token())
