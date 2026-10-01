"""1.5.6 speed work: the database in write-ahead log mode (and backups of it), the index behind
the confirmation time of each account, the Security activity table paged before its join with
cached sort keys, and photos stored without a second compression in the accountant package."""
import datetime
import io
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import app as appmod
import db
import package
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import backup  # noqa: E402


class WalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "nomad.db")
        db.init_db(self.path)

    def test_the_database_runs_in_wal_mode_and_its_files_stay_private(self):
        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        conn.execute("INSERT INTO users (email, password_hash) VALUES ('a@x.com', 'x')")
        conn.commit()
        for name in os.listdir(self.tmp):
            self.assertEqual(os.stat(os.path.join(self.tmp, name)).st_mode & 0o777, 0o600, name)
        conn.close()

    def test_request_connections_skip_the_fsync_of_every_commit(self):
        app = appmod.create_app({"TESTING": True, "SECRET_KEY": "k", "DATABASE_PATH": self.path,
                                 "PWNED_CHECK": False})
        with app.app_context():
            self.assertEqual(db.get_db().execute("PRAGMA synchronous").fetchone()[0], 1)  # NORMAL

    def test_a_busy_database_does_not_stop_the_app_from_starting(self):
        conn = mock.Mock()
        conn.execute.side_effect = sqlite3.OperationalError("database is locked")
        with self.assertLogs("db", "WARNING"):
            self.assertIsNone(db.use_wal(conn))

    def test_backups_hold_what_is_still_in_the_wal(self):
        app_conn = sqlite3.connect(self.path)  # the app, running
        app_conn.execute("PRAGMA wal_autocheckpoint = 0")  # keep the rows in nomad.db-wal
        for i in range(30):
            app_conn.execute("INSERT INTO users (email, password_hash) VALUES (?, 'x')",
                             (f"u{i}@x.com",))
        app_conn.commit()
        copy = os.path.join(self.tmp, "copy.db")
        backup.copy_database(self.path, copy)
        app_conn.close()
        with sqlite3.connect(copy) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 30)
            # One self-contained file: restoring it needs no -wal.
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_backup_of_a_stopped_app(self):
        # With the app stopped there is no nomad.db-shm, which a read only connection cannot
        # create: the nightly backup failed with "unable to open database file".
        for name in ("nomad.db-wal", "nomad.db-shm"):
            if os.path.exists(os.path.join(self.tmp, name)):
                os.remove(os.path.join(self.tmp, name))
        copy = os.path.join(self.tmp, "copy.db")
        backup.copy_database(self.path, copy)
        with sqlite3.connect(copy) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_restore_steps_remove_the_old_wal(self):
        with open(os.path.join(ROOT, "setup_vps.md"), encoding="utf-8") as fh:
            guide = fh.read()
        self.assertIn("rm -f nomad.db-wal nomad.db-shm", guide)
        self.assertIn("delete `data/nomad.db-wal` and `data/nomad.db-shm`", guide)
        with open(os.path.join(ROOT, "scripts", "backup.py"), encoding="utf-8") as fh:
            self.assertIn("delete nomad.db-wal and nomad.db-shm", fh.read())


class ConfirmedIndexTests(unittest.TestCase):
    def test_confirmation_lookups_use_the_partial_index(self):
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        db.init_db(path)
        conn = sqlite3.connect(path)
        for column in ("created_at", "ip"):  # the admin table and check-fake-users
            plan = " ".join(r[-1] for r in conn.execute(
                f"EXPLAIN QUERY PLAN SELECT u.id, (SELECT a.{column} FROM audit_log a WHERE "
                "a.user_id = u.id AND a.event = 'account_confirmed' ORDER BY a.id LIMIT 1) "
                "FROM users u"))
            self.assertIn("USING INDEX idx_audit_confirmed", plan)
        conn.close()
        with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            source = fh.read()
        # SQLite uses a partial index only when the query names the event as this literal:
        # the admin table, the bulk delete and check-fake-users (twice) all do.
        self.assertGreaterEqual(source.count("a.event = 'account_confirmed' ORDER BY a.id"), 4)
        self.assertNotIn("a.event = ? ORDER BY a.id", source)


class ActivityTableTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("admin@example.com")
        events = ["sign_in", "sign_in_failed", "password_changed", "admin_plan", "2fa_enabled"]
        with self.db() as conn:
            conn.execute("DELETE FROM audit_log")
            for i in range(65):
                # Ties on every column, empty emails and IPs, accents and case.
                email = ["", "Élodie@x.com", "bob@x.com", "alice@x.com"][i % 4]
                ip = ["", "10.0.0.2", "9.1.1.1", "2001:db8::1", "not an ip"][i % 5]
                conn.execute("INSERT INTO audit_log (email, event, detail, ip) VALUES "
                             "(?, ?, ?, ?)", (email, events[i % 5], f"#{i}", ip))

    def shown(self, **args):
        html = self.client.get("/admin", query_string=args).get_data(as_text=True)
        return [int(n) for n in re.findall(r"\(#(\d+)\)", html.split('id="activity"', 1)[1])]

    def expected(self, sort, direction, page):
        """The order of the query before 1.5.6 (users joined before sorting)."""
        fixed, key = appmod.AUDIT_SORTS[sort]
        order = ", ".join(filter(None, [fixed, f"{key} {direction.upper()}", "a.id DESC"]))
        with self.app.app_context():
            rows = db.query("SELECT a.detail FROM audit_log a LEFT JOIN users u ON "
                            f"u.id = a.user_id ORDER BY {order} LIMIT 20 OFFSET ?",
                            ((page - 1) * 20,))
        return [int(r["detail"][1:]) for r in rows]

    def test_every_sort_and_page_shows_what_it_showed_before(self):
        for sort in appmod.AUDIT_SORTS:
            for direction in ("asc", "desc"):
                for page in (1, 2, 4):
                    self.assertEqual(self.shown(asort=sort, adir=direction, apage=page),
                                     self.expected(sort, direction, page),
                                     (sort, direction, page))

    def test_sort_keys_are_cached(self):
        db.sort_fold.cache_clear()
        self.shown(asort="account")
        self.shown(asort="account", apage=2)
        info = db.sort_fold.cache_info()
        self.assertGreater(info.hits, info.misses)


class DayMathTests(AppTestCase):
    def test_dates_are_read_once_with_the_same_answers(self):
        for value, expected in (("2027-03-01", datetime.date(2027, 3, 1)),
                                ("2027-3-1", datetime.date(2027, 3, 1)),  # as typed before
                                ("2027-02-30", None), ("2027-13-01", None), ("", None),
                                (None, None), ("x" * 10, None)):
            self.assertEqual(appmod.parse_date(value), expected, value)
        appmod._cached_date.cache_clear()
        for _ in range(3):
            appmod.parse_date("2027-03-01")
        self.assertEqual(appmod._cached_date.cache_info().hits, 2)
        # A huge form field is read, not kept: the cache would hold up to 4,096 of them.
        appmod.parse_date("2027-03-01" + "x" * 1_000_000)
        self.assertEqual(appmod._cached_date.cache_info().currsize, 1)

    def test_the_dashboard_works_out_who_gets_each_day_once(self):
        self.signup()
        self.new_year(2027)
        self.add_movement(2027, "Vienna", "Austria", "2027-01-01", "2027-01-10")
        self.add_movement(2027, "Sidney", "Australia", "2027-01-08", "2027-01-10")
        with mock.patch.object(appmod, "assign_days", wraps=appmod.assign_days) as spy:
            html = self.client.get("/year/2027").get_data(as_text=True)
        self.assertEqual(spy.call_count, 1)  # compute_stats; the overlap alert reuses it
        self.assertIn("They count for Sidney, Australia, so Vienna, Austria counts 7 of its "
                      "10 days.", html)

    def test_day_owner_is_the_assignment(self):
        ms = [{"id": 1, "city": "A", "country": "Spain", "start_date": "2027-02-01",
               "end_date": "2027-02-20"},
              {"id": 2, "city": "B", "country": "Italy", "start_date": "2027-02-10",
               "end_date": "2027-02-12"}]
        stats = appmod.compute_stats({"year": 2027, "base_country": "Portugal"}, ms)
        self.assertEqual(stats["day_owner"], appmod.assign_days(ms))
        self.assertEqual(appmod.movement_overlaps(ms, stats["day_owner"]),
                         appmod.movement_overlaps(ms))


class PackageCompressionTests(AppTestCase):
    def test_photos_are_not_deflated_again(self):
        self.assertEqual([package.compress_level(e) for e in ("jpg", "JPEG", "png", "heic",
                                                             "webp", "pdf")], [0, 0, 0, 0, 0, 1])
        self.signup()
        self.set_plan("pro")
        year = self.new_year(2025)
        photo = b"\xff\xd8\xff\xe0" + b"\x00" * 200000  # compresses well, but is a photo
        pdf = b"%PDF-1.4\n" + b"0" * 200000
        self.upload("/year/2025/base", "photo.jpg", photo)
        self.upload("/year/2025/base", "lease.pdf", pdf)
        _resp, zf = self.package(2025)
        for info in zf.infolist():
            self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED, info.filename)
        photo_info = next(i for i in zf.infolist() if i.filename.endswith(".jpg"))
        pdf_info = next(i for i in zf.infolist() if i.filename.endswith(".pdf")
                        and "/receipts/" in i.filename)
        self.assertGreaterEqual(photo_info.compress_size, photo_info.file_size)  # stored blocks
        self.assertLess(pdf_info.compress_size, pdf_info.file_size // 10)
        self.assertEqual(zf.read(photo_info), photo)
        self.assertIsNone(zf.testzip())


if __name__ == "__main__":
    unittest.main()
