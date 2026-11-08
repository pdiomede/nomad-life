"""A city of your own on AUTO movements (2.1.1): when the Google event names only a country
("Italy"), the movement's city can be typed in Nomad Life and every sync keeps it; when the
event names a city ("Rome, Italy"), Google's city wins and the field is locked like the dates."""
import os
import sqlite3
from unittest import mock

import app as appmod
import db
from tests.helpers import AppTestCase
from tests.test_calendar_sync import SyncCase, gev
from tests.test_fixes_158 import other_connection


class SyncCityCase(SyncCase):
    def flag(self, mid):
        with self.db() as conn:
            return conn.execute("SELECT city_from_event FROM movements WHERE id = ?",
                                (mid,)).fetchone()[0]

    def page(self, mid):
        return self.client.get(f"/movements/{mid}").get_data(as_text=True)

    def save(self, mid, **data):
        return self.client.post(f"/movements/{mid}", data=dict({"action": "update", "notes": ""}, **data),
                                follow_redirects=True).get_data(as_text=True)


class CountryEventTests(SyncCityCase):
    def setUp(self):
        super().setUp()
        self.events(gev("g1", "Italy", "2026-06-21", "2026-07-01"))
        self.turn_auto()
        self.dashboard()  # shows the "1 added" message
        [self.mid] = [r["id"] for r in self.rows()]

    def test_the_city_is_yours_and_stays(self):
        html = self.page(self.mid)
        self.assertIn('placeholder="Bali">', html)  # enabled
        self.assertIn('placeholder="Indonesia" required disabled>', html)  # the country is Google's
        self.assertIn("The city, notes and receipts are yours", html)
        html = self.save(self.mid, city="Bali", notes="Coworking")
        self.assertIn("Movement updated.", html)
        row = self.rows()[0]
        self.assertEqual((row["city"], row["country"], row["notes"]), ("Bali", "Italy", "Coworking"))
        self.assertEqual(self.flag(self.mid), 0)
        self.events(gev("g1", "Italy", "2026-06-21", "2026-07-01"))  # a new read of Google
        html = self.dashboard()
        self.assertNotIn("Synced with", html)
        self.assertEqual(self.rows()[0]["city"], "Bali")
        self.assertIn("Bali", html)

    def test_new_dates_keep_the_city(self):
        self.save(self.mid, city="Bali")
        self.events(gev("g1", "Italy", "2026-06-22", "2026-07-03"))
        self.assertIn("Synced with Google Calendar: 1 changed.", self.dashboard())
        row = self.rows()[0]
        self.assertEqual((row["city"], row["start_date"], row["end_date"]), ("Bali", "2026-06-22", "2026-07-02"))

    def test_another_country_clears_the_city(self):
        self.save(self.mid, city="Bali")
        self.events(gev("g1", "Spain", "2026-06-21", "2026-07-01"))
        self.dashboard()
        row = self.rows()[0]
        self.assertEqual((row["city"], row["country"]), ("", "Spain"))
        self.assertEqual(self.flag(self.mid), 0)

    def test_an_event_city_wins_and_locks(self):
        self.save(self.mid, city="Bali")
        self.events(gev("g1", "Milan, Italy", "2026-06-21", "2026-07-01"))
        self.assertIn("Synced with Google Calendar: 1 changed.", self.dashboard())
        self.assertEqual(self.rows()[0]["city"], "Milan")
        self.assertEqual(self.flag(self.mid), 1)
        self.assertIn('placeholder="Bali" disabled>', self.page(self.mid))

    def test_the_event_naming_the_typed_city_locks_it_quietly(self):
        self.save(self.mid, city="Rome")
        self.events(gev("g1", "Rome, Italy", "2026-06-21", "2026-07-01"))
        self.assertNotIn("Synced with", self.dashboard())
        self.assertEqual((self.rows()[0]["city"], self.flag(self.mid)), ("Rome", 1))

    def test_an_empty_city_is_fine(self):
        self.save(self.mid, city="Bali")
        self.save(self.mid, city="")
        self.assertEqual(self.rows()[0]["city"], "")

    def test_a_sync_meanwhile_wins(self):
        real = appmod.clean_place

        def sync_first(text):
            with other_connection(self) as conn:
                conn.execute("UPDATE movements SET city = 'Milan', city_from_event = 1 WHERE id = ?",
                             (self.mid,))
            return real(text)

        with mock.patch.object(appmod, "clean_place", side_effect=sync_first):
            html = self.save(self.mid, city="Bali", notes="Kept")
        self.assertIn("so the city was not saved", html)
        row = self.rows()[0]
        self.assertEqual((row["city"], row["notes"]), ("Milan", "Kept"))

    def test_a_country_changed_meanwhile_wins(self):
        real = appmod.clean_place

        def sync_first(text):
            with other_connection(self) as conn:
                conn.execute("UPDATE movements SET country = 'Spain', city = '' WHERE id = ?", (self.mid,))
            return real(text)

        with mock.patch.object(appmod, "clean_place", side_effect=sync_first):
            self.save(self.mid, city="Bali")
        self.assertEqual((self.rows()[0]["city"], self.rows()[0]["country"]), ("", "Spain"))

    def test_back_in_manual_everything_is_editable(self):
        self.save(self.mid, city="Bali")
        self.client.post("/year/2026/sync-mode", data={"mode": "manual"})
        html = self.page(self.mid)
        self.assertIn('placeholder="Indonesia" required>', html)
        self.save(self.mid, city="Ubud", country="Indonesia", start_date="2026-06-21", end_date="2026-06-30")
        row = self.rows()[0]
        self.assertEqual((row["city"], row["country"]), ("Ubud", "Indonesia"))


class CityEventTests(SyncCityCase):
    def setUp(self):
        super().setUp()
        self.events(gev("g1", "Rome, Italy", "2026-06-21", "2026-07-01"))
        self.turn_auto()
        self.dashboard()  # shows the "1 added" message
        [self.mid] = [r["id"] for r in self.rows()]

    def test_the_city_is_locked(self):
        self.assertEqual(self.flag(self.mid), 1)
        html = self.page(self.mid)
        self.assertIn('placeholder="Bali" disabled>', html)
        self.assertIn("its place and dates follow the event there", html)
        html = self.save(self.mid, city="Milan", notes="Note")
        self.assertIn("Only the notes were saved", html)
        self.assertEqual((self.rows()[0]["city"], self.rows()[0]["notes"]), ("Rome", "Note"))

    def test_the_event_dropping_its_city_unlocks_it(self):
        self.events(gev("g1", "Italy", "2026-06-21", "2026-07-01"))
        self.assertNotIn("Synced with", self.dashboard())
        self.assertEqual((self.rows()[0]["city"], self.flag(self.mid)), ("Rome", 0))
        self.assertIn('placeholder="Bali">', self.page(self.mid))


class ReviewCityTests(SyncCityCase):
    def test_a_typed_city_links_to_a_country_event(self):
        mid = self.add_movement(2026, "Rome", "Italy", "2026-02-01", "2026-02-05")
        self.events(gev("g1", "Italy", "2026-02-01", "2026-02-06"))
        html = self.review()
        self.assertIn(f'value="link:{mid}:g1" checked', html)
        self.apply(html)
        row = self.rows()[0]
        self.assertEqual((row["city"], row["google_event_id"]), ("Rome", "g1"))
        self.assertEqual(self.flag(mid), 0)

    def test_an_edit_shows_the_city_kept(self):
        mid = self.add_movement(2026, "Rome", "Italy", "2026-02-01", "2026-02-05")
        self.events(gev("g1", "Italy", "2026-02-02", "2026-02-08"))
        html = self.review()
        self.assertIn(f'value="edit:{mid}:g1" checked', html)
        after = html[html.index("becomes"):]
        self.assertIn("Rome", after[:after.index("</li>") if "</li>" in after else 400])
        self.apply(html)
        row = self.rows()[0]
        self.assertEqual((row["city"], row["start_date"]), ("Rome", "2026-02-02"))

    def test_an_event_city_replaces_the_typed_one(self):
        mid = self.add_movement(2026, "Rome", "Italy", "2026-02-01", "2026-02-05")
        self.events(gev("g1", "Milan, Italy", "2026-02-01", "2026-02-06"))
        self.apply()
        self.assertEqual((self.rows()[0]["city"], self.flag(mid)), ("Milan", 1))

    def test_a_country_event_adds_without_a_city(self):
        self.events(gev("g1", "Italy", "2026-02-01", "2026-02-06"), gev("g2", "Rome, Italy", "2026-03-01", "2026-03-06"))
        self.turn_auto()
        with self.db() as conn:
            flags = dict(conn.execute("SELECT google_event_id, city_from_event FROM movements"))
        self.assertEqual(flags, {"g1": 0, "g2": 1})


class MigrationTests(AppTestCase):
    def test_synced_cities_of_an_old_database_stay_google_s(self):
        path = os.path.join(self.tmp, "old.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE movements (id INTEGER PRIMARY KEY AUTOINCREMENT, year_id INTEGER NOT NULL, "
                     "city TEXT NOT NULL, country TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, "
                     "notes TEXT NOT NULL DEFAULT '', google_event_id TEXT, "
                     "sync_missing INTEGER NOT NULL DEFAULT 0)")
        conn.executemany("INSERT INTO movements (year_id, city, country, start_date, end_date, google_event_id) "
                         "VALUES (1, ?, ?, '2026-01-01', '2026-01-02', ?)",
                         [("Rome", "Italy", "g1"), ("", "Spain", "g2"), ("Paris", "France", None)])
        conn.commit()
        conn.close()
        db.init_db(path)
        conn = sqlite3.connect(path)
        self.assertEqual([r[0] for r in conn.execute("SELECT city_from_event FROM movements ORDER BY id")],
                         [1, 0, 0])
        conn.close()
