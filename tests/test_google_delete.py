"""Deleting a movement's event in Google Calendar (2.1.1): always in AUTO, a choice in MANUAL
for a movement that came from an event. The permission to change the calendar
(calendar.events) is asked once, only when a delete needs it."""
import re
from urllib.parse import parse_qs, urlsplit

import app as appmod
from tests.test_calendar_integration import ITEMS, read
from tests.test_calendar_sync import SyncCase, gev
from tests.test_calendar_tabs import BOTH

WRITE = appmod.GOOGLE_WRITE_SCOPE
CAL = "work@group.calendar.google.com"


class DeleteCase(SyncCase):
    def setUp(self):
        super().setUp()
        self.deletes, self.reads, self.delete_answer = [], [], "ok"
        events_call = self.google.__class__.__call__

        def call(fake, method, url, data=None, token=None):
            single = re.match(r"https://www\.googleapis\.com/calendar/v3/calendars/[^/]+/events/([^/?]+)", url)
            if method == "GET" and single:  # the event read before it is deleted
                self.reads.append(url)
                found = [e for e in fake.events if e.get("id") == single.group(1)]
                if self.delete_answer == "none":
                    appmod.g.google_status = 0
                    return None
                if self.delete_answer == "404" or not found:
                    appmod.g.google_status = 404
                    return None
                return dict(found[0])
            if method == "DELETE":
                self.deletes.append(url)
                if self.delete_answer == "ok":
                    gid = url.rsplit("/", 1)[1]
                    fake.events = [e for e in fake.events if e.get("id") != gid]
                    return {}
                appmod.g.google_status = {"404": 404, "403": 403}.get(self.delete_answer, 0)
                return None
            return events_call(fake, method, url, data, token)

        self.google.__class__ = type("DeleteGoogle", (self.google.__class__,), {"__call__": call})
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"))
        self.turn_auto()
        self.dashboard()  # the "1 added" message
        [self.mid] = [r["id"] for r in self.rows()]

    def allow_write(self, items=None):
        """Delete once without the permission: Google's page, then its answer."""
        resp = self.delete()
        self.assertEqual(resp.status_code, 303)
        location = resp.headers["Location"]
        state = parse_qs(urlsplit(location).query)["state"][0]
        self.google.scope = f"{BOTH} {WRITE}"
        if items is not None:
            self.google.items = items
        return location, self.client.get(f"/settings/calendar/google/callback?code=c&state={state}",
                                         follow_redirects=True)

    def delete(self, mid=None, **extra):
        return self.client.post(f"/movements/{mid or self.mid}",
                                data=dict({"action": "delete", "confirm_delete": "2"}, **extra))

    def scopes(self):
        with self.db() as conn:
            return conn.execute("SELECT scopes FROM calendar_links").fetchone()[0].split()

    def grant(self):
        with self.db() as conn:
            conn.execute("UPDATE calendar_links SET scopes = scopes || ' ' || ?", (WRITE,))


class PermissionTests(DeleteCase):
    def test_the_first_delete_asks_google_once(self):
        location, resp = self.allow_write()
        query = parse_qs(urlsplit(location).query)
        self.assertEqual(query["scope"], [WRITE])
        self.assertEqual(query["login_hint"], ["ada@gmail.com"])
        self.assertEqual(query["prompt"], ["consent"])  # no account chooser
        self.assertEqual(query["include_granted_scopes"], ["true"])
        html = resp.get_data(as_text=True)
        self.assertIn("Press Delete again", html)
        self.assertEqual(len(self.rows()), 1)  # nothing deleted on Google's redirect
        self.assertEqual(self.deletes, [])
        self.assertIn(WRITE, self.scopes())
        self.assertEqual(self.mode(), "auto")  # the year and the choice stay
        self.assertEqual(urlsplit(resp.request.url).path, f"/movements/{self.mid}")
        self.delete()
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.deletes), 1)

    def test_another_google_account_changes_nothing(self):
        other = [{"id": "bob@gmail.com", "summary": "Bob", "primary": True}]
        _location, resp = self.allow_write(items=other)
        self.assertIn("Choose ada@gmail.com on Google", resp.get_data(as_text=True))
        self.assertNotIn(WRITE, self.scopes())
        self.assertEqual(self.mode(), "auto")
        self.assertEqual(len(self.rows()), 1)

    def test_unticked_on_google_s_page(self):
        resp = self.delete()
        state = parse_qs(urlsplit(resp.headers["Location"]).query)["state"][0]
        html = self.client.get(f"/settings/calendar/google/callback?code=c&state={state}",
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn("Google did not allow Nomad Life to change your calendar", html)
        self.assertNotIn(WRITE, self.scopes())

    def test_connect_still_asks_read_only(self):
        resp = self.client.post("/settings/calendar/google/connect")
        query = parse_qs(urlsplit(resp.headers["Location"]).query)
        self.assertNotIn(WRITE, query["scope"][0].split())
        self.assertEqual(query["prompt"], ["consent select_account"])


class AutoDeleteTests(DeleteCase):
    def test_auto_deletes_the_event_and_it_never_comes_back(self):
        self.grant()
        html = self.client.post(f"/movements/{self.mid}", data={"action": "delete", "confirm_delete": "2"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Movement deleted, and its event in Google Calendar.", html)
        self.assertEqual(self.deletes, [f"https://www.googleapis.com/calendar/v3/calendars/"
                                        f"work%40group.calendar.google.com/events/g1"])
        self.assertEqual(self.rows(), [])
        # Another process may still hold the read with the event: it is older, so skipped.
        with self.db() as conn:
            synced = conn.execute("SELECT synced_read_at FROM years").fetchone()[0]
        with self.app.test_request_context():
            year_id = self.db().execute("SELECT id FROM years").fetchone()[0]
            done = appmod.apply_sync(year_id, appmod.sync_events(2026, [
                {"gid": "g1", "title": "Rome, Italy", "start_date": "2026-02-01",
                 "end_date": "2026-02-05"}]), read_at=synced - 5)
        self.assertEqual(done["added"], 0)
        self.assertEqual(self.rows(), [])

    def test_the_row_and_page_say_the_event_goes(self):
        self.grant()
        html = self.dashboard()
        self.assertIn('aria-label="Delete movement to Rome"', html)
        self.assertIn("and its event in your Google calendar “Work”. It cannot be undone.", html)
        self.assertNotIn("data-confirm-option", html)
        page = self.client.get(f"/movements/{self.mid}").get_data(as_text=True)
        self.assertIn("Its event in Google Calendar is deleted too.", page)

    def test_a_year_edge_warns_about_the_other_year(self):
        self.events(gev("g1", "Rome, Italy", "2025-12-28", "2026-01-04"))
        self.dashboard()
        self.assertIn("The event may also cover days of another year", self.dashboard())

    def test_google_not_answering_deletes_nothing(self):
        self.grant()
        self.delete_answer = "none"
        html = self.client.post(f"/movements/{self.mid}", data={"action": "delete", "confirm_delete": "2"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Google Calendar did not answer, so nothing was deleted.", html)
        self.assertEqual(len(self.rows()), 1)

    def test_an_event_already_gone_still_deletes(self):
        self.grant()
        self.delete_answer = "404"
        self.google.events = []  # deleted in Google Calendar meanwhile
        html = self.client.post(f"/movements/{self.mid}", data={"action": "delete", "confirm_delete": "2"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Its event was already gone from Google Calendar.", html)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.deletes, [])

    def test_a_permission_taken_back_asks_again(self):
        self.grant()
        self.delete_answer = "403"
        html = self.client.post(f"/movements/{self.mid}", data={"action": "delete", "confirm_delete": "2"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Press Delete again to allow it.", html)
        self.assertEqual(len(self.rows()), 1)
        self.assertNotIn(WRITE, self.scopes())
        self.assertEqual(self.delete().status_code, 303)  # Google's page again

    def test_a_removed_access_deletes_nothing(self):
        self.grant()
        self.google.refresh_error = "invalid_grant"
        html = self.client.post(f"/movements/{self.mid}", data={"action": "delete", "confirm_delete": "2"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Google Calendar no longer lets Nomad Life in", html)
        self.assertEqual(len(self.rows()), 1)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT broken FROM calendar_links").fetchone()[0], 1)

    def test_not_in_google_calendar_deletes_only_here(self):
        self.grant()
        self.add_receipt(self.mid)
        self.events()
        self.dashboard()
        self.assertEqual(self.rows()[0]["sync_missing"], 1)
        self.delete()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.deletes, [])


class ManualDeleteTests(DeleteCase):
    def setUp(self):
        super().setUp()
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        self.grant()

    def test_the_choice_is_offered_unticked(self):
        html = self.dashboard()
        self.assertIn('data-confirm-option="Also delete its event in Google Calendar" '
                      'data-confirm-option-name="google"', html)
        page = self.client.get(f"/movements/{self.mid}").get_data(as_text=True)
        self.assertIn("You can delete its event in Google Calendar too.", page)
        self.assertIn('data-confirm-option="Also delete its event in Google Calendar"', page)

    def test_without_the_box_only_here(self):
        self.delete()
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.deletes, [])

    def test_with_the_box_both(self):
        self.delete(google="1")
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.deletes), 1)

    def test_a_typed_movement_has_no_choice_and_no_call(self):
        mid = self.add_movement(2026, "", "Spain", "2026-05-01", "2026-05-03")
        html = self.dashboard()
        row = html[html.index(f'/movements/{mid}" data-confirm-delete'):][:400]
        self.assertNotIn("data-confirm-option", row)
        self.delete(mid, google="1")
        self.assertEqual(self.deletes, [])
        self.assertEqual(len(self.rows()), 1)

    def test_a_calendar_no_longer_listed_is_never_called(self):
        self.google.items = [c for c in ITEMS if c["id"] != CAL]
        self.client.post("/settings", data={"action": "calendar_refresh"})
        self.assertNotIn("data-confirm-option", self.dashboard())
        self.delete(google="1")
        self.assertEqual(self.deletes, [])
        self.assertEqual(self.rows(), [])


class DialogScriptTests(DeleteCase):
    def test_the_dialog_option(self):
        js = read("static", "js", "theme.js")
        self.assertIn('if (form.getAttribute("data-confirm-option")) setHidden(form.getAttribute('
                      '"data-confirm-option-name"), option ? "1" : "");', js)
        self.assertIn("dOption.checked = false;", js)
        self.assertIn('confirmAndSubmit(form, typed, label ? window.confirm(label + "?") : false);', js)
        base = read("templates", "base.html")
        self.assertTrue(re.search(r'<label class="check confirm-option" id="confirm-option-field" hidden>', base))


class ManualFixTests(DeleteCase):
    """The bugs found in deleting events from MANUAL years (2.1.1)."""
    def setUp(self):
        super().setUp()
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})

    def test_1_an_event_changed_in_google_is_never_deleted(self):
        # The year left AUTO, then the event became another trip: deleting it would delete
        # that trip, not this movement's.
        self.grant()
        self.google.events = [gev("g1", "Spain", "2026-03-01", "2026-03-10")]
        html = self.client.post(f"/movements/{self.mid}", data={"action": "delete", "confirm_delete": "2",
                                                                 "google": "1"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("is now another place or other dates, so nothing was deleted", html)
        self.assertIn("Delete the movement without its event", html)
        self.assertEqual(self.deletes, [])
        self.assertEqual(len(self.rows()), 1)

    def test_1_new_dates_count_as_changed_too(self):
        self.grant()
        self.google.events = [gev("g1", "Rome, Italy", "2026-02-01", "2026-02-09")]
        self.delete(google="1")
        self.assertEqual((self.deletes, len(self.rows())), ([], 1))

    def test_1_a_country_only_title_still_matches_a_typed_city(self):
        self.grant()
        self.google.events = [gev("g1", "Italy", "2026-02-01", "2026-02-06")]
        self.delete(google="1")
        self.assertEqual((len(self.deletes), self.rows()), (1, []))

    def test_2_after_the_permission_manual_says_to_tick_the_box(self):
        resp = self.delete(google="1")
        state = parse_qs(urlsplit(resp.headers["Location"]).query)["state"][0]
        self.google.scope = f"{BOTH} {WRITE}"
        html = self.client.get(f"/settings/calendar/google/callback?code=c&state={state}",
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn("Press Delete again and tick \u201cAlso delete its event in Google Calendar\u201d", html)

    def test_3_cancelling_on_google_s_page_goes_back_to_the_movement(self):
        resp = self.delete(google="1")
        state = parse_qs(urlsplit(resp.headers["Location"]).query)["state"][0]
        resp = self.client.get(f"/settings/calendar/google/callback?error=access_denied&state={state}")
        self.assertEqual(resp.headers["Location"], f"/movements/{self.mid}")
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn("Google was not allowed to change your calendar, so nothing was deleted.", html)
        self.assertNotIn("Google Calendar was not connected.", html)

    def test_4_google_failing_after_its_page_goes_back_to_the_row(self):
        resp = self.delete(google="1", page="1")
        state = parse_qs(urlsplit(resp.headers["Location"]).query)["state"][0]
        resp = self.client.get(f"/settings/calendar/google/callback?error=server_error&state={state}")
        self.assertTrue(resp.headers["Location"].startswith("/year/2026"))
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn("Google did not answer, so nothing was deleted.", html)

    def test_5_the_other_year_warning_in_manual(self):
        with self.db() as conn:
            conn.execute("UPDATE movements SET start_date = '2026-01-01'")
        self.grant()
        self.assertIn("The event may also cover days of another year: they would be deleted",
                      self.dashboard())

    def test_6_an_event_already_gone_is_not_called_deleted(self):
        self.grant()
        self.google.events = []
        html = self.client.post(f"/movements/{self.mid}", data={"action": "delete", "confirm_delete": "2",
                                                                 "google": "1"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Its event was already gone from Google Calendar.", html)
        self.assertNotIn("and its event in Google Calendar.", html)
        self.assertEqual(self.rows(), [])
