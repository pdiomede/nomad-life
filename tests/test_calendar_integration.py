"""Calendar Integration: the gear on the dashboard Calendar card and the Google Calendar
connection in Settings (connect, choose one calendar, refresh, disconnect)."""
import base64
import hashlib
import json
import os
import re
import time
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import app as appmod
from tests.helpers import AppTestCase
from tests.test_fixes_158 import other_connection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


ITEMS = [
    {"id": "work@group.calendar.google.com", "summary": "Work", "backgroundColor": "#0B8043"},
    {"id": "ada@gmail.com", "summary": "Ada Lovelace", "primary": True,
     "backgroundColor": "#9fe1e7"},
    {"id": "trips@group.calendar.google.com", "summary": "Old name", "summaryOverride": "Trips",
     "backgroundColor": "red; background-image: url(x)"},
]


class FakeGoogle:
    """Answers google_api like Google would; records every call."""
    def __init__(self, items=None, scope=appmod.GOOGLE_SCOPE, refresh="refresh-1"):
        self.items, self.scope, self.refresh = list(ITEMS if items is None else items), scope, refresh
        self.calls, self.refresh_error = [], ""

    def __call__(self, method, url, data=None, token=None):
        self.calls.append((method, url, data, token))
        if url == appmod.GOOGLE_TOKEN_URL and data["grant_type"] == "authorization_code":
            return {"access_token": "access-1", "refresh_token": self.refresh,
                    "scope": self.scope, "expires_in": 3599}
        if url == appmod.GOOGLE_TOKEN_URL:
            if self.refresh_error:
                appmod.g.google_error = self.refresh_error
                return None
            return {"access_token": "access-2"}
        if url.startswith(appmod.GOOGLE_CALENDAR_LIST_URL):
            return {"items": self.items}
        if url == appmod.GOOGLE_REVOKE_URL:
            return {}
        raise AssertionError(url)

    def revoked(self):
        return [c[2]["token"] for c in self.calls if c[1] == appmod.GOOGLE_REVOKE_URL]


class CalendarCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config.update(GOOGLE_CLIENT_ID="client-id.apps.googleusercontent.com",
                               GOOGLE_CLIENT_SECRET="client-secret",
                               APP_BASE_URL="https://nomad.example")
        self.signup()
        self.google = FakeGoogle()
        patcher = mock.patch.object(appmod, "google_api", side_effect=self.google)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, client=None):
        resp = (client or self.client).post("/settings/calendar/google/connect")
        self.assertEqual(resp.status_code, 303)
        return parse_qs(urlsplit(resp.headers["Location"]).query)

    def connect(self, client=None):
        params = self.start(client)
        return (client or self.client).get("/settings/calendar/google/callback?code=abc&state="
                                           + params["state"][0], follow_redirects=True)

    def link(self):
        return appmod_query(self, "SELECT * FROM calendar_links")

    def settings_html(self):
        return self.client.get("/settings").get_data(as_text=True)


def appmod_query(test, sql, args=()):
    conn = other_connection(test)
    conn.row_factory = __import__("sqlite3").Row
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


class GearTests(AppTestCase):
    def test_the_gear_sits_after_the_title_outside_the_summary(self):
        self.signup()
        self.new_year(2026)
        html = self.client.get("/year/2026").get_data(as_text=True)
        summary = html[html.index('<details class="card fold-card cal-card">'):]
        summary = summary[:summary.index("</summary>")]
        self.assertNotIn("fold-gear\"", summary)
        self.assertIn('<span class="fold-gear-space" aria-hidden="true"></span>', summary)
        tools = html[html.index('<div class="fold-tools">'):]
        # Before the card in the page (laid over its head), so the gear comes before the grid.
        self.assertLess(html.index('<div class="fold-tools">'), html.index('<details class="card fold-card cal-card">'))
        self.assertIn('<span class="fold-ghost title-icon icon-calendar" aria-hidden="true">Calendar (2026)</span>', tools)
        self.assertIn('<a class="fold-gear" href="/settings#calendar" data-tip="Calendar Integration" '
                      'aria-label="Calendar Integration"></a>', tools)

    def test_the_gear_styles(self):
        css = read("static", "css", "style.css")
        self.assertIn(".fold-tools { position: absolute; top: 21px; left: 21px;", css)
        self.assertIn("pointer-events: none; }", css)
        self.assertIn(".fold-gear { pointer-events: auto;", css)
        # The copy of the title is as wide as the title (h2: 17px, 600).
        self.assertIn(".fold-ghost { visibility: hidden; font-size: 17px; font-weight: 600; letter-spacing: -0.01em;", css)
        self.assertIn(".fold-gear[data-tip]:focus-visible::after { opacity: 1;", css)
        narrow = css[css.index("@media (max-width: 600px) {"):]
        self.assertLess(narrow.index("  .card { padding: 16px; }"), narrow.index("  .fold-tools { top: 17px; left: 17px; }"))


class SettingsSectionTests(AppTestCase):
    def test_off_without_google_settings(self):
        self.signup()
        resp = self.client.get("/settings")
        html = resp.get_data(as_text=True)
        self.assertLess(html.index('id="profile"'), html.index('id="calendar"'))
        self.assertLess(html.index('id="calendar"'), html.index('id="plan"'))
        self.assertIn('<a href="#profile">Profile</a>\n    <a href="#calendar">Calendar</a>', html)
        self.assertIn('<h2 id="calendar-title">Calendar Integration</h2>', html)
        self.assertIn("Calendar Integration is not set up on this server yet.", html)
        self.assertNotIn("Connect Google Calendar", html)
        self.assertEqual(resp.headers["Content-Security-Policy"], appmod.CSP)
        resp = self.client.post("/settings/calendar/google/connect", follow_redirects=True)
        self.assertIn("Calendar Integration is not set up on this server.", resp.get_data(as_text=True))

    def test_one_setting_alone_stops_the_app(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": ""}):
            with self.assertRaises(SystemExit):
                appmod.google_settings()
        with mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": " x ", "GOOGLE_CLIENT_SECRET": "y"}):
            self.assertEqual(appmod.google_settings(),
                             {"GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y"})


class ConnectTests(CalendarCase):
    def test_the_button_and_the_policy(self):
        resp = self.client.get("/settings")
        html = resp.get_data(as_text=True)
        self.assertIn('<form method="post" action="/settings/calendar/google/connect">', html)
        self.assertIn("Connect Google Calendar</button>", html)
        self.assertEqual(resp.headers["Content-Security-Policy"], appmod.CALENDAR_CSP)
        self.assertIn("form-action 'self' https://accounts.google.com;", appmod.CALENDAR_CSP)
        # Only Settings may send forms to Google.
        self.assertEqual(self.client.get("/app").headers.get("Content-Security-Policy", appmod.CSP),
                         appmod.CSP)

    def test_connect_sends_to_google_with_pkce(self):
        params = self.start()
        self.assertEqual(params["client_id"], ["client-id.apps.googleusercontent.com"])
        self.assertEqual(params["redirect_uri"], ["https://nomad.example/settings/calendar/google/callback"])
        # The calendar list, and since 1.12.13 the chosen calendar's events (read only).
        self.assertEqual(params["scope"], [f"{appmod.GOOGLE_SCOPE} {appmod.GOOGLE_EVENTS_SCOPE}"])
        self.assertEqual(params["access_type"], ["offline"])
        # The account chooser too: someone signed in to Google can pick another account.
        self.assertEqual(params["prompt"], ["consent select_account"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        with self.client.session_transaction() as sess:
            pending = sess["gcal"]
        self.assertEqual(pending["state"], params["state"][0])
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(pending["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(params["code_challenge"], [challenge])

    def test_connect_stores_the_writable_calendars_primary_first(self):
        html = self.connect().get_data(as_text=True)
        self.assertIn("Google Calendar is connected. Choose the calendar to use.", html)
        exchange = self.google.calls[0]
        self.assertEqual(exchange[2]["code"], "abc")
        self.assertEqual(exchange[2]["redirect_uri"], "https://nomad.example/settings/calendar/google/callback")
        with self.client.session_transaction() as sess:
            self.assertNotIn("gcal", sess)  # one use
        listing = self.google.calls[1]
        self.assertIn("minAccessRole=writer", listing[1])
        self.assertEqual(listing[3], "access-1")
        link = self.link()
        self.assertEqual(link["refresh_token"], "refresh-1")
        self.assertEqual(link["account"], "ada@gmail.com")
        self.assertIsNone(link["calendar_id"])
        self.assertEqual(json.loads(link["calendars"]), [
            {"id": "ada@gmail.com", "name": "Ada Lovelace", "color": "#9fe1e7", "primary": True},
            {"id": "trips@group.calendar.google.com", "name": "Trips", "color": "", "primary": False},
            {"id": "work@group.calendar.google.com", "name": "Work", "color": "#0b8043", "primary": False}])
        self.assertIn("Connected to <strong dir=\"auto\">ada@gmail.com</strong>.", html)
        self.assertIn('<legend>Choose one calendar</legend>', html)
        self.assertEqual(html.count('type="radio" name="calendar_id"'), 3)
        self.assertIn('style="background: #9fe1e7"', html)
        self.assertNotIn("background-image", html)  # a color Google never sends is dropped
        # Connected to Google, no calendar chosen yet.
        self.assertIn('<span class="pill pill-warn">Choose a calendar</span>', html)
        self.assertIn("Disconnect Google account</button>", html)
        self.assertEqual(appmod_query(self, "SELECT event, detail FROM audit_log WHERE event = 'calendar_connected'")["detail"],
                         "ada@gmail.com")

    def test_a_state_that_differs_is_refused(self):
        self.start()
        html = self.client.get("/settings/calendar/google/callback?code=abc&state=other",
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn("This Google connection expired or was not started here.", html)
        self.assertIsNone(self.link())
        self.assertEqual(self.google.calls, [])

    def test_a_state_is_used_once(self):
        params = self.start()
        url = "/settings/calendar/google/callback?code=abc&state=" + params["state"][0]
        self.client.get(url)
        self.google.calls.clear()
        html = self.client.get(url, follow_redirects=True).get_data(as_text=True)
        self.assertIn("This Google connection expired", html)
        self.assertEqual(self.google.calls, [])

    def test_an_old_state_is_refused(self):
        params = self.start()
        with self.client.session_transaction() as sess:
            pending = sess["gcal"]
            pending["t"] = int(time.time()) - appmod.GOOGLE_STATE_SECONDS - 5
            sess["gcal"] = pending
        html = self.client.get("/settings/calendar/google/callback?code=abc&state=" + params["state"][0],
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn("This Google connection expired", html)
        self.assertIsNone(self.link())

    def test_a_password_changed_meanwhile_is_refused(self):
        params = self.start()
        with self.client.session_transaction() as sess:
            pending = sess["gcal"]
            pending["fp"] = "0" * 16
            sess["gcal"] = pending
        self.client.get("/settings/calendar/google/callback?code=abc&state=" + params["state"][0])
        self.assertIsNone(self.link())

    def test_a_state_with_other_characters_is_refused(self):
        self.start()
        resp = self.client.get("/settings/calendar/google/callback?code=abc&state=%C3%A9")
        self.assertEqual(resp.status_code, 302)  # not a 500 from compare_digest
        self.assertIsNone(self.link())

    def test_denied_on_google(self):
        params = self.start()
        html = self.client.get("/settings/calendar/google/callback?error=access_denied&state="
                               + params["state"][0], follow_redirects=True).get_data(as_text=True)
        self.assertIn("Google Calendar was not connected.", html)
        self.assertIsNone(self.link())

    def test_the_permission_unticked_stores_nothing(self):
        self.google.scope = "openid"
        html = self.connect().get_data(as_text=True)
        self.assertIn("Google was not allowed to show Nomad Life your calendars", html)
        self.assertIsNone(self.link())
        self.assertEqual(self.google.revoked(), [])  # see FailedReconnectTests

    def test_reconnecting_keeps_the_choice(self):
        self.connect()
        self.client.post("/settings", data={"action": "calendar_choose",
                                            "calendar_id": "work@group.calendar.google.com"})
        self.google.refresh = "refresh-2"
        html = self.connect().get_data(as_text=True)
        self.assertIn("Google Calendar is connected again. Nomad Life still uses Work.", html)
        link = self.link()
        self.assertEqual((link["refresh_token"], link["calendar_id"]),
                         ("refresh-2", "work@group.calendar.google.com"))
        # The same Google account: its old token is the same grant, never revoked.
        self.assertEqual(self.google.revoked(), [])

    def test_another_google_account_revokes_the_old_token(self):
        self.connect()
        self.google.refresh = "refresh-2"
        self.google.items = [{"id": "bob@gmail.com", "summary": "Bob", "primary": True}]
        self.connect()
        self.assertEqual(self.link()["account"], "bob@gmail.com")
        self.assertEqual(self.google.revoked(), ["refresh-1"])


class ChooseTests(CalendarCase):
    def setUp(self):
        super().setUp()
        self.connect()

    def choose(self, calendar_id):
        return self.client.post("/settings", data={"action": "calendar_choose", "calendar_id": calendar_id},
                                follow_redirects=True).get_data(as_text=True)

    def test_choose_one(self):
        html = self.choose("trips@group.calendar.google.com")
        self.assertIn("Nomad Life will use the calendar Trips.", html)
        link = self.link()
        self.assertEqual((link["calendar_id"], link["calendar_name"]), ("trips@group.calendar.google.com", "Trips"))
        # Saved: the calendar is shown as connected, with its own Disconnect, not the list.
        self.assertNotIn('name="calendar_id"', html)
        chosen = html[html.index('<div class="cal-chosen">'):]
        chosen = chosen[:chosen.index("</div>")]
        self.assertIn('<span class="cal-chosen-name" dir="auto">Trips</span>', chosen)
        self.assertIn('<span class="pill pill-ok">Connected</span>', chosen)
        self.assertIn('<input type="hidden" name="action" value="calendar_unchoose">', chosen)
        self.assertIn('aria-label="Disconnect the calendar Trips">Disconnect</button>', chosen)
        head = html[html.index('<h2 id="calendar-title">'):]
        self.assertIn('<span class="pill pill-ok">Connected</span>', head[:head.index("</div>")])
        self.assertIn("Nomad Life uses <strong dir=\"auto\">Trips</strong>.", html)
        self.assertIn("Nothing changed.", self.choose("trips@group.calendar.google.com"))

    def test_only_a_listed_calendar(self):
        self.assertIn("Choose one of the calendars in the list.", self.choose("someone-else@gmail.com"))
        self.assertIsNone(self.link()["calendar_id"])

    def test_disconnected_meanwhile(self):
        real = appmod.calendar_link
        calls = []

        def first_disconnect(user_id):
            link = real(user_id)
            if not calls:
                calls.append(1)
                conn = other_connection(self)
                conn.execute("DELETE FROM calendar_links")
                conn.commit()
                conn.close()
            return link

        with mock.patch.object(appmod, "calendar_link", side_effect=first_disconnect):
            html = self.choose("work@group.calendar.google.com")
        self.assertIn("Google Calendar was disconnected in the meantime.", html)
        self.assertIsNone(self.link())


class RefreshAndDisconnectTests(CalendarCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.client.post("/settings", data={"action": "calendar_choose",
                                            "calendar_id": "work@group.calendar.google.com"})

    def post(self, action):
        return self.client.post("/settings", data={"action": action},
                                follow_redirects=True).get_data(as_text=True)

    def test_refresh_reads_the_list_again(self):
        self.google.items = ITEMS + [{"id": "new@group.calendar.google.com", "summary": "New"}]
        self.assertIn("The list of calendars was updated.", self.post("calendar_refresh"))
        self.assertEqual(len(json.loads(self.link()["calendars"])), 4)
        self.assertEqual(self.google.calls[-1][3], "access-2")

    def test_a_vanished_choice_is_cleared(self):
        self.google.items = ITEMS[1:]
        html = self.post("calendar_refresh")
        self.assertIn("The calendar you had chosen, Work, is no longer one you can edit", html)
        self.assertIsNone(self.link()["calendar_id"])

    def test_access_removed_at_google(self):
        self.google.refresh_error = "invalid_grant"
        html = self.post("calendar_refresh")
        self.assertIn("Google no longer lets Nomad Life see your calendars (the access was removed", html)
        self.assertEqual(self.link()["broken"], 1)
        html = self.settings_html()
        self.assertIn('<span class="pill pill-warn">Connect again</span>', html)
        self.assertIn("Connect again</button>", html)
        self.assertNotIn('name="calendar_id"', html)
        # Connecting again repairs it.
        self.google.refresh_error = ""
        self.connect()
        self.assertEqual(self.link()["broken"], 0)

    def test_disconnect(self):
        html = self.post("calendar_disconnect")
        self.assertIn("Google Calendar was disconnected, and Nomad Life no longer has access", html)
        self.assertIsNone(self.link())
        self.assertEqual(self.google.revoked(), ["refresh-1"])
        self.assertIn("Connect Google Calendar", html)
        self.assertIn("Google Calendar was disconnected in the meantime.", self.post("calendar_disconnect"))
        events = [r[0] for r in other_connection(self).execute(
            "SELECT event FROM audit_log WHERE event LIKE 'calendar_%' ORDER BY id")]
        self.assertEqual(events, ["calendar_connected", "calendar_chosen", "calendar_disconnected"])

    def test_deleting_the_account_revokes_the_token(self):
        html = self.client.post("/settings", data={"action": "delete", "current_password": "password1",
                                                   "confirm_delete": "2", "confirm_email": "a@example.com"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Your account and all its data have been deleted.", html)
        self.assertIsNone(self.link())
        self.assertEqual(self.google.revoked(), ["refresh-1"])


class ReviewFixTests(CalendarCase):
    """Found in a review of the Google integration before its release."""

    def test_settings_are_blanked_in_tests(self):
        # config.env may hold a real OAuth client: tests must not depend on it.
        from tests.helpers import AppTestCase as Base
        case = Base("run")
        case.setUp()
        try:
            self.assertEqual(case.app.config["GOOGLE_CLIENT_ID"], "")
            self.assertEqual(case.app.config["GOOGLE_CLIENT_SECRET"], "")
        finally:
            case.tearDown()

    def test_a_failed_reconnect_keeps_the_working_connection(self):
        # The new token is the same grant as the stored one: revoking it after a failure cut the
        # connection that still worked.
        self.connect()
        self.google.refresh, self.google.scope = "refresh-2", "openid"
        self.connect()
        self.google.scope = appmod.GOOGLE_SCOPE
        self.google.items = None  # Google answers nothing for the list
        with mock.patch.object(appmod, "google_calendars", return_value=None):
            html = self.connect().get_data(as_text=True)
        self.assertIn("Google did not send the list of your calendars", html)
        self.assertEqual(self.google.revoked(), [])
        self.assertEqual(self.link()["refresh_token"], "refresh-1")

    def test_a_google_account_shared_by_two_accounts_keeps_working(self):
        # One Google account connected to two Nomad Life accounts is one grant: disconnecting
        # one revoked it, and the other connection stopped working.
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.connect(other)
        self.connect()
        html = self.client.post("/settings", data={"action": "calendar_disconnect"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Google Calendar was disconnected from this account.", html)
        self.assertEqual(self.google.revoked(), [])
        # The last one to go revokes it.
        html = other.post("/settings", data={"action": "calendar_disconnect"},
                          follow_redirects=True).get_data(as_text=True)
        self.assertIn("Nomad Life no longer has access to your Google account.", html)
        self.assertEqual(self.google.revoked(), ["refresh-1"])

    def test_deleting_an_account_keeps_a_shared_grant(self):
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.connect(other)
        self.connect()
        self.client.post("/settings", data={"action": "delete", "current_password": "password1",
                                            "confirm_delete": "2", "confirm_email": "a@example.com"})
        self.assertEqual(self.google.revoked(), [])
        self.assertIsNotNone(self.link())  # b's

    def test_a_refresh_keeps_a_choice_made_meanwhile(self):
        # Refresh read the row, then wrote the old choice back over one saved in another tab.
        self.connect()
        self.client.post("/settings", data={"action": "calendar_choose",
                                            "calendar_id": "work@group.calendar.google.com"})
        real = appmod.google_calendars

        def choose_meanwhile(token, keep=None):
            conn = other_connection(self)
            conn.execute("UPDATE calendar_links SET calendar_id = 'trips@group.calendar.google.com', "
                         "calendar_name = 'Trips'")
            conn.commit()
            conn.close()
            return real(token, keep=keep)

        with mock.patch.object(appmod, "google_calendars", side_effect=choose_meanwhile):
            html = self.client.post("/settings", data={"action": "calendar_refresh"},
                                    follow_redirects=True).get_data(as_text=True)
        self.assertIn("The list of calendars was updated.", html)
        self.assertEqual(self.link()["calendar_id"], "trips@group.calendar.google.com")

    def test_choosing_after_a_refresh_in_another_tab(self):
        # A page made before a refresh elsewhere was refused ("the list changed") although its
        # calendar was still listed.
        self.connect()
        self.google.items = ITEMS + [{"id": "new@group.calendar.google.com", "summary": "New"}]
        self.client.post("/settings", data={"action": "calendar_refresh"})
        html = self.client.post("/settings", data={"action": "calendar_choose",
                                                   "calendar_id": "work@group.calendar.google.com"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Nomad Life will use the calendar Work.", html)

    def test_undoing_an_email_change_removes_the_calendar(self):
        # Whoever changed the email knew the password and may have connected their own calendar,
        # where the owner's stays would go.
        self.connect()
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "new@example.com",
                                            "current_password": "password1"})
        self.client.post(self.last_link("new@example.com", kind="account/email"))
        undo = self.last_link("a@example.com", kind="account/email/undo")
        self.app.test_client().post(undo)
        self.assertIsNone(self.link())
        self.assertEqual(self.google.revoked(), ["refresh-1"])

    def test_calendar_names_read_as_text(self):
        # Inside a <label>, the names took the muted label color, and "Choose one calendar"
        # touched the line above it.
        css = read("static", "css", "style.css")
        rule = css[css.index(".cal-choice { "):]
        self.assertIn("font-size: 14px; color: var(--text); }", rule[:rule.index("\n")])
        self.connect()
        self.assertIn('<p class="small card-intro">Connected to', self.settings_html())


class ChangeCalendarTests(CalendarCase):
    """After saving, the chosen calendar shows as Connected with a Disconnect of its own, which
    brings the list back to choose another one; the Google account stays connected."""

    def test_disconnect_the_calendar_to_choose_another(self):
        self.connect()
        self.client.post("/settings", data={"action": "calendar_choose",
                                            "calendar_id": "work@group.calendar.google.com"})
        html = self.client.post("/settings", data={"action": "calendar_unchoose"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Nomad Life no longer uses the calendar Work. Choose another one in the list.", html)
        link = self.link()
        self.assertEqual((link["calendar_id"], link["calendar_name"]), (None, ""))
        self.assertEqual(link["refresh_token"], "refresh-1")  # still connected to Google
        self.assertEqual(self.google.revoked(), [])
        self.assertEqual(html.count('type="radio" name="calendar_id"'), 3)
        self.assertNotIn('class="cal-chosen"', html)
        html = self.client.post("/settings", data={"action": "calendar_choose",
                                                   "calendar_id": "trips@group.calendar.google.com"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Nomad Life will use the calendar Trips.", html)
        events = [r[0] for r in other_connection(self).execute(
            "SELECT event FROM audit_log WHERE event LIKE 'calendar_%' ORDER BY id")]
        self.assertEqual(events, ["calendar_connected", "calendar_chosen", "calendar_unchosen",
                                  "calendar_chosen"])

    def test_nothing_chosen_or_gone(self):
        self.connect()
        html = self.client.post("/settings", data={"action": "calendar_unchoose"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("No calendar is chosen. Choose one in the list.", html)
        self.client.post("/settings", data={"action": "calendar_disconnect"})
        html = self.client.post("/settings", data={"action": "calendar_unchoose"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Google Calendar was disconnected in the meantime.", html)

    def test_the_chosen_row_styles(self):
        css = read("static", "css", "style.css")
        self.assertIn(".cal-chosen { display: flex; flex-wrap: wrap; align-items: center;", css)
        self.assertIn(".cal-chosen-action { margin: 0 0 0 auto; }", css)
