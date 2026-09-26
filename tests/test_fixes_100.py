"""Regression tests for the 1.0.0 bug hunt."""
import os
import unittest
from unittest import mock

import app as appmod
import package
from tests.helpers import AppTestCase


class NotesTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def post_new(self, notes):
        return self.client.post("/year/2026/movements/new", data={
            "city": "Rome", "country": "Italy", "start_date": "2026-03-01",
            "end_date": "2026-03-02", "notes": notes})

    def test_line_breaks_count_as_one_character(self):
        notes = ("x" * 99 + "\r\n") * 49 + "x" * 99   # 4999 characters in the browser
        self.assertEqual(self.post_new(notes).status_code, 302)
        with self.db() as conn:
            saved = conn.execute("SELECT notes FROM movements").fetchone()[0]
        self.assertNotIn("\r", saved)
        self.assertEqual(len(saved), 4999)

    def test_old_long_notes_do_not_block_other_edits(self):
        mid = self.add_movement(2026, "Rome", "Italy", "2026-03-01", "2026-03-02")
        with self.db() as conn:
            conn.execute("UPDATE movements SET notes = ?", ("y" * 6000,))
        self.client.post(f"/movements/{mid}", data={
            "action": "update", "city": "Milan", "country": "Italy", "start_date": "2026-03-01",
            "end_date": "2026-03-02", "notes": "y" * 6000})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT city FROM movements").fetchone()[0], "Milan")


class UploadGoneTests(AppTestCase):
    def test_upload_to_a_deleted_stay_lands_on_a_real_page(self):
        self.signup()
        self.new_year(2026)
        mid = self.add_movement(2026, "Rome", "Italy", "2026-03-01", "2026-03-05")
        other = self.app.test_client()
        other.post("/login", data={"email": "a@example.com", "password": "password1"})
        real_used = appmod.storage_used

        def delete_first(uid):
            other.post(f"/movements/{mid}", data={"action": "delete", "confirm_delete": "2"})
            return real_used(uid)

        with mock.patch.object(appmod, "storage_used", delete_first):
            resp = self.upload(f"/movements/{mid}", "r.pdf", b"%PDF-1 receipt")
        self.assertEqual(resp.headers["Location"], "/app")
        page = self.client.get("/year/2026")
        self.assertEqual(page.status_code, 200)
        self.assertIn(appmod.UPLOAD_GONE.encode(), page.data)


class SizeAndTextTests(unittest.TestCase):
    def test_no_1024_kb(self):
        for size in (1048300, 1048575):
            self.assertEqual(appmod.format_size(size), "1 MB")
            self.assertEqual(appmod.format_size(size, "up"), "1 MB")
            self.assertEqual(package._fmt_size(size), "1.0 MB")
        self.assertEqual(appmod.format_size(1048000, "down"), "1023 KB")

    def test_package_rules_cover_identical_dates(self):
        self.assertTrue(any("identical dates" in rule for rule in package.RULES))

    def test_404_page_announces_404(self):
        path = os.path.join(appmod.BASE_DIR, "static", "404.html")
        self.assertIn('role="img" aria-label="404"', open(path, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
