"""Regression tests for the bugs found in the 1.3.1 review of database calls and support pages."""
import os
import sqlite3

import app as appmod
from tests.helpers import AppTestCase


class DatabaseFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def hold_write_lock(self):
        conn = sqlite3.connect(self.app.config["DATABASE_PATH"], timeout=0)
        conn.execute("BEGIN IMMEDIATE")  # another request in the middle of a write
        self.addCleanup(conn.close)
        return conn

    def test_pages_load_while_another_request_writes(self):
        self.new_year(2026)
        with self.db() as conn:
            conn.execute("UPDATE user_sessions SET last_seen_at = datetime('now', '-2 hours')")
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.hold_write_lock()
        resp = self.client.get("/year/2026")
        self.assertEqual(resp.status_code, 200)

    def test_housekeeping_skips_a_busy_database(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.hold_write_lock()
        real = appmod.PURGE_EVERY_SECONDS
        appmod.PURGE_EVERY_SECONDS = 0  # the purge hook runs on this request
        try:
            resp = self.client.get("/support")
        finally:
            appmod.PURGE_EVERY_SECONDS = real
        self.assertEqual(resp.status_code, 200)

    def test_rename_does_not_hold_the_write_lock_while_rewriting(self):
        self.new_year(2026)
        self.upload("/year/2026/base", "Hotel.pdf", b"%PDF-1.7\n" + os.urandom(5000))
        real = appmod.write_receipt_zip
        free = []

        def check(*args):
            other = sqlite3.connect(self.app.config["DATABASE_PATH"], timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.rollback()
                free.append(True)
            except sqlite3.OperationalError:
                free.append(False)
            finally:
                other.close()
            return real(*args)

        appmod.write_receipt_zip = check
        try:
            self.client.post("/documents/1/edit", data={"name": "Hotel Rome", "kind": "other"})
        finally:
            appmod.write_receipt_zip = real
        self.assertEqual(free, [True])
        with self.db() as conn:
            row = conn.execute("SELECT * FROM documents").fetchone()
        self.assertEqual(row["original_name"], "Hotel Rome.pdf")
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        self.assertEqual(os.listdir(folder), [row["stored_name"]])  # no temporary file left
        with appmod.zipfile.ZipFile(os.path.join(folder, row["stored_name"])) as zf:
            self.assertEqual(zf.namelist(), ["Hotel Rome.pdf"])
            self.assertEqual(row["size"], os.path.getsize(zf.filename))
