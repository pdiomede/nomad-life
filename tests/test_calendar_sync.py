"""AUTO movements (2.0.0): a year's movements synced from the connected Google calendar, the
AUTO / MANUAL switch next to the year, and the review before a year turns AUTO."""
import io
import re
import time

import app as appmod
from tests.test_calendar_tabs import TabsCase, ev


def gev(gid, summary, start, end):
    """A Google all-day event with its id (end exclusive, as Google sends it)."""
    item = ev(summary, start, end)
    item["id"] = gid
    return item


class SyncCase(TabsCase):
    def setUp(self):
        super().setUp()
        self.connect()
        self.choose()
        self.events()

    def events(self, *items):
        self.google.events = list(items)
        appmod._events_cache.clear()  # a new read of Google, as a minute later
        appmod._events_failures.clear()

    def rows(self):
        with self.db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, city, country, start_date, end_date, notes, google_event_id, sync_missing "
                "FROM movements ORDER BY start_date, id")]

    def mode(self):
        with self.db() as conn:
            return conn.execute("SELECT sync_mode FROM years WHERE year = 2026").fetchone()[0]

    def review(self):
        return self.client.get("/year/2026/sync").get_data(as_text=True)

    def apply(self, html=None, untick=()):
        html = html or self.review()
        plan = re.search(r'name="plan" value="([^"]+)"', html).group(1)
        keys = [k for k in re.findall(r'name="change" value="([^"]+)" checked', html) if k not in untick]
        return self.client.post("/year/2026/sync", data={"plan": plan, "change": keys})

    def turn_auto(self, untick=()):
        resp = self.client.post("/year/2026/sync-mode", data={"mode": "auto"})
        self.assertEqual(resp.headers["Location"], "/year/2026/sync")
        return self.apply(untick=untick)

    def add_receipt(self, mid):
        self.client.post(f"/movements/{mid}", data={"action": "upload", "kind": "other",
                                                     "file": (io.BytesIO(b"%PDF-1.4\nx"), "r.pdf")},
                         content_type="multipart/form-data")


class EventPlaceTests(SyncCase):
    def test_titles(self):
        self.assertEqual(appmod.event_place("Italy"), ("", "Italy"))
        self.assertEqual(appmod.event_place("IT"), ("", "Italy"))
        self.assertEqual(appmod.event_place("Rome, Italy"), ("Rome", "Italy"))
        self.assertEqual(appmod.event_place("New York, USA"), ("New York", "United States"))
        self.assertEqual(appmod.event_place("Paris, Texas"), ("Paris", "United States"))  # a US state
        for title in ("Dentist", "No", "in", "Rome", "", "Team call, 10:00"):
            self.assertIsNone(appmod.event_place(title), title)


class SwitchTests(SyncCase):
    def test_a_new_year_starts_manual(self):
        self.assertEqual(self.mode(), "manual")
        html = self.dashboard()
        self.assertIn('<span class="sync-chip is-manual" data-tip="Movements are added manually"', html)
        self.assertIn('role="switch" aria-checked="false"', html)
        self.assertIn("+ Add movement", html)

    def test_without_google_the_switch_is_disabled(self):
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.new_year(2026, client=other)
        html = other.get("/year/2026").get_data(as_text=True)
        self.assertIn('data-tip="Connect Google Calendar in Settings to turn on AUTO" aria-disabled="true"', html)
        resp = other.post("/year/2026/sync-mode", data={"mode": "auto"}, follow_redirects=True)
        self.assertIn("Connect Google Calendar and choose a calendar in Settings", resp.get_data(as_text=True))

    def test_auto_label_and_back_to_manual(self):
        self.turn_auto()
        self.assertEqual(self.mode(), "auto")
        html = self.dashboard()
        self.assertIn('class="sync-chip is-auto" data-tip="Movements are added automatically from the '
                      'connected Google Calendar named &#34;Work&#34;"', html)
        self.assertNotIn("+ Add movement", html)
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        self.assertEqual(self.mode(), "manual")
        self.assertIn("+ Add movement", self.dashboard())


class ReviewTests(SyncCase):
    def test_every_kind_of_change(self):
        same = self.add_movement(2026, "Rome", "Italy", "2026-02-01", "2026-02-05")
        moved = self.add_movement(2026, "", "Spain", "2026-03-01", "2026-03-10")
        gone = self.add_movement(2026, "", "France", "2026-04-01", "2026-04-03")
        kept = self.add_movement(2026, "", "Japan", "2026-05-01", "2026-05-03")
        self.add_receipt(kept)
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"),
                    gev("g2", "Madrid, Spain", "2026-03-02", "2026-03-12"),
                    gev("g3", "Thailand", "2026-06-01", "2026-06-11"),
                    gev("g4", "Dentist", "2026-07-01", "2026-07-02"))
        html = self.review()
        self.assertIn(f'value="link:{same}:g1" checked', html)
        self.assertIn(f'value="edit:{moved}:g2" checked', html)
        self.assertIn(f'value="del:{gone}" checked', html)
        self.assertIn(f'value="del:{kept}">', html)  # it has a receipt: unticked
        self.assertIn('value="add:g3" checked', html)
        self.assertIn("1 receipt deleted too", html)
        self.assertIn("1 other event in 2026 is not named after a place", html)
        self.assertEqual(self.mode(), "manual")  # the review changes nothing
        self.apply(html)
        self.assertEqual(self.mode(), "auto")
        rows = {r["id"]: r for r in self.rows()}
        self.assertEqual(rows[same]["google_event_id"], "g1")
        self.assertEqual((rows[moved]["city"], rows[moved]["start_date"], rows[moved]["end_date"],
                          rows[moved]["google_event_id"]), ("Madrid", "2026-03-02", "2026-03-11", "g2"))
        self.assertNotIn(gone, rows)
        self.assertIsNone(rows[kept]["google_event_id"])  # a typed movement kept as typed
        added = [r for r in rows.values() if r["google_event_id"] == "g3"][0]
        self.assertEqual((added["city"], added["country"], added["start_date"], added["end_date"]),
                         ("", "Thailand", "2026-06-01", "2026-06-10"))

    def test_an_unticked_change_stays_out_of_auto(self):
        self.events(gev("g1", "Italy", "2026-02-01", "2026-02-03"), gev("g2", "Spain", "2026-03-01", "2026-03-03"))
        self.turn_auto(untick=("add:g2",))
        self.assertEqual([r["google_event_id"] for r in self.rows()], ["g1"])
        self.dashboard()  # AUTO's own sync leaves the unticked event out
        self.assertEqual([r["google_event_id"] for r in self.rows()], ["g1"])

    def test_a_plan_changed_meanwhile_is_refused(self):
        self.events(gev("g1", "Italy", "2026-02-01", "2026-02-03"))
        html = self.review()
        self.events(gev("g1", "Italy", "2026-02-01", "2026-02-09"))
        resp = self.apply(html)
        self.assertEqual(resp.headers["Location"], "/year/2026/sync")
        self.assertEqual(self.mode(), "manual")
        self.assertEqual(self.rows(), [])

    def test_cancel_keeps_manual(self):
        self.events(gev("g1", "Italy", "2026-02-01", "2026-02-03"))
        self.assertIn('href="/year/2026">Cancel</a>', self.review())
        self.assertEqual(self.mode(), "manual")


class AutoSyncTests(SyncCase):
    def setUp(self):
        super().setUp()
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"))
        self.turn_auto()
        [self.mid] = [r["id"] for r in self.rows()]

    def test_google_wins_and_notes_stay(self):
        self.client.post(f"/movements/{self.mid}", data={"action": "update", "notes": "Coworking",
                                                         "city": "Milan", "country": "France",
                                                         "start_date": "2026-01-01", "end_date": "2026-01-02"})
        row = self.rows()[0]
        self.assertEqual((row["city"], row["country"], row["notes"]), ("Rome", "Italy", "Coworking"))
        self.events(gev("g1", "Florence, Italy", "2026-02-02", "2026-02-08"),
                    gev("g2", "PT", "2026-08-01", "2026-08-03"))
        html = self.dashboard()
        self.assertIn("Synced with Google Calendar: 1 added, 1 changed.", html)
        rows = self.rows()
        self.assertEqual((rows[0]["city"], rows[0]["start_date"], rows[0]["notes"]), ("Florence", "2026-02-02", "Coworking"))
        self.assertEqual(rows[1]["country"], "Portugal")
        self.events(gev("g2", "PT", "2026-08-01", "2026-08-03"))
        self.assertIn("Synced with Google Calendar: 1 removed.", self.dashboard())
        self.assertEqual([r["google_event_id"] for r in self.rows()], ["g2"])
        self.assertNotIn("Synced with", self.dashboard())  # nothing new: no message

    def test_a_movement_with_receipts_is_kept_and_flagged(self):
        self.add_receipt(self.mid)
        self.events()
        html = self.dashboard()
        self.assertEqual(self.rows()[0]["sync_missing"], 1)
        self.assertIn("Not in Google Calendar", html)
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"))
        self.dashboard()
        self.assertEqual(self.rows()[0]["sync_missing"], 0)

    def test_events_are_clipped_to_the_year(self):
        self.events(gev("g1", "Rome, Italy", "2025-12-28", "2026-01-04"))
        self.dashboard()
        self.assertEqual((self.rows()[0]["start_date"], self.rows()[0]["end_date"]), ("2026-01-01", "2026-01-03"))

    def test_auto_rules_on_movements(self):
        self.assertEqual(self.client.get("/year/2026/movements/new").headers["Location"], "/year/2026")
        html = self.client.get(f"/movements/{self.mid}").get_data(as_text=True)
        self.assertIn("This movement comes from your Google calendar", html)
        self.assertIn('placeholder="Indonesia" required disabled>', html)
        resp = self.client.post(f"/movements/{self.mid}", data={"action": "delete", "confirm_delete": "2"},
                                follow_redirects=True)
        self.assertIn("delete the event there", resp.get_data(as_text=True))
        self.assertEqual(len(self.rows()), 1)

    def test_a_broken_connection_reads_manual_and_touches_nothing(self):
        with self.db() as conn:
            conn.execute("UPDATE calendar_links SET broken = 1")
        self.events()
        html = self.dashboard()
        self.assertEqual(len(self.rows()), 1)
        self.assertIn("is-manual", html)
        self.assertIn("2026 is back to MANUAL: Google no longer lets Nomad Life see your calendars", html)
        self.assertEqual(self.mode(), "manual")

    def test_google_not_answering_keeps_the_movements(self):
        self.events()
        self.google.events_error = True
        html = self.dashboard()
        self.assertEqual(len(self.rows()), 1)
        self.assertIn("Google Calendar did not answer", html)

    def test_the_cap(self):
        with self.db() as conn:
            year_id = conn.execute("SELECT id FROM years").fetchone()[0]
            conn.executemany("INSERT INTO movements (year_id, city, country, start_date, end_date) "
                             "VALUES (?, '', 'Spain', '2026-09-01', '2026-09-01')",
                             [(year_id,)] * (appmod.MOVEMENTS_MAX - 1))
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"),
                    gev("g2", "Spain", "2026-10-01", "2026-10-02"))
        self.assertIn("1 event of your Google calendar is left out: a year holds at most 1,000 movements.",
                      self.dashboard())

    def test_two_syncs_at_once_add_once(self):
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"), gev("g2", "Spain", "2026-03-01", "2026-03-02"))
        events = appmod.sync_events(2026, [dict(s, gid=s["gid"]) for s in [
            {"title": "Spain", "start_date": "2026-03-01", "end_date": "2026-03-01", "gid": "g2"},
            {"title": "Rome, Italy", "start_date": "2026-02-01", "end_date": "2026-02-05", "gid": "g1"}]])
        with self.app.test_request_context():
            year_id = appmod.db.query("SELECT id FROM years", one=True)["id"]
            now = time.time()
            appmod.apply_sync(year_id, events, read_at=now)
            appmod.apply_sync(year_id, events, read_at=now)
        self.assertEqual(sorted(r["google_event_id"] for r in self.rows()), ["g1", "g2"])

    def test_refresh_reloads_the_page_when_movements_changed(self):
        self.events(gev("g1", "Rome, Italy", "2026-02-01", "2026-02-06"), gev("g2", "Spain", "2026-03-01", "2026-03-02"))
        resp = self.client.get("/year/2026/calendar/google?refresh=1")
        self.assertEqual(resp.headers.get("X-Nomad-Synced"), "1")
        resp = self.client.get("/year/2026/calendar/google?refresh=1")
        self.assertIsNone(resp.headers.get("X-Nomad-Synced"))


class AssetsTests(SyncCase):
    def test_switch_and_review_scripts(self):
        js = open("static/js/theme.js", encoding="utf-8").read()
        self.assertIn('if (btn.getAttribute("aria-disabled") === "true") e.preventDefault();', js)
        self.assertIn('if (r.headers.get("X-Nomad-Synced") && !r.redirected) {', js)
        self.assertIn("loadPage(googleTab.href);", js)
        self.assertIn('form.querySelectorAll(\'input[name="change"]\').forEach(function (box) { box.checked = on; });', js)
        css = open("static/css/style.css", encoding="utf-8").read()
        self.assertIn(".sync-chip.is-auto {", css)
        self.assertIn('.sync-switch[aria-disabled="true"] { cursor: not-allowed; opacity: 0.55; }', css)
