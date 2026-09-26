"""Regression tests for the v0.0.11 fixes."""
import io
import os
import re
import unittest
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


class WordingTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2025)

    def test_one_day_is_singular(self):
        self.add_movement(2025, "Madrid", "Spain", "2025-03-01", "2025-03-01")
        html = self.client.get("/year/2025").data.decode()
        self.assertIn("1 day (1 so far)", html)
        self.assertNotIn("1 days", html)

    def test_rule_text_explains_same_start_day(self):
        self.add_movement(2025, "Madrid", "Spain", "2025-01-01", "2025-01-31")
        resp = self.client.post("/year/2025/movements/new", data={
            "city": "Paris", "country": "France", "start_date": "2025-01-01",
            "end_date": "2025-01-05"}, follow_redirects=True)
        html = resp.data.decode()
        self.assertIn("on the same start day, the shorter stay", html)
        dash = self.client.get("/year/2025").data.decode()
        self.assertIn('title="A day shared with another stay counts toward the stay that started '
                      'later (on the same start day, the shorter stay', dash)


class FormTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2025)

    def test_base_form_keeps_typed_values_after_an_error(self):
        html = self.client.post("/year/2025/base", data={
            "action": "update", "base_city": "   ", "base_country": "Spain"}).data.decode()
        self.assertIn("Base city and country are required.", html)
        self.assertIn('value="Spain"', html)

    def test_cancel_returns_to_the_movement_page_of_the_list(self):
        ids = [self.add_movement(2025, f"C{i:02d}", "Spain", f"2025-{1 + i // 4:02d}-{1 + (i % 4) * 7:02d}",
                                 f"2025-{1 + i // 4:02d}-{3 + (i % 4) * 7:02d}") for i in range(12)]
        html = self.client.get(f"/movements/{ids[11]}").data.decode()
        self.assertIn('href="/year/2025?page=2#movements">Cancel</a>', html)

    def test_notes_are_limited(self):
        resp = self.client.post("/year/2025/movements/new", data={
            "city": "Rome", "country": "Italy", "start_date": "2025-03-01",
            "end_date": "2025-03-02", "notes": "x" * (appmod.NOTES_MAX + 1)})
        self.assertIn(b"Notes can be at most 5000 characters.", resp.data)
        self.assertIn(b'maxlength="5000"', self.client.get("/year/2025/movements/new").data)

    def test_too_large_form_does_not_claim_a_file(self):
        resp = self.client.post("/year/2025/movements/new", data={
            "city": "Rome", "country": "Italy", "start_date": "2025-03-01",
            "end_date": "2025-03-02", "notes": "x" * 600_000},
            content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"This form was too large to save.", resp.data)
        self.assertNotIn(b"The file is larger", resp.data)


class RaceTests(AppTestCase):
    def test_upload_to_a_stay_deleted_meanwhile_is_not_an_error(self):
        self.signup()
        self.new_year(2025)
        mid = self.add_movement(2025, "Rome", "Italy", "2025-03-01", "2025-03-05")
        other = self.app.test_client()
        other.post("/login", data={"email": "a@example.com", "password": "password1"})
        real_used = appmod.storage_used

        def delete_first(uid):  # the other tab deletes the stay after the checks passed
            other.post(f"/movements/{mid}", data={"action": "delete", "confirm_delete": "2"})
            return real_used(uid)

        with mock.patch.object(appmod, "storage_used", delete_first):
            resp = self.upload(f"/movements/{mid}", "r.pdf", b"%PDF-1 receipt")
        self.assertEqual(resp.status_code, 302)
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        self.assertEqual(os.listdir(folder) if os.path.isdir(folder) else [], [])
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)


class ConfigTests(unittest.TestCase):
    def test_home_folder_is_expanded(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/tester"}):
            self.assertEqual(appmod._abs("~/NomadReceipts"), "/home/tester/NomadReceipts")
        self.assertEqual(appmod._abs("uploads"), os.path.join(appmod.BASE_DIR, "uploads"))

    def test_config_env_wins_over_shell_variables(self):
        source = open(os.path.join(appmod.BASE_DIR, "app.py"), encoding="utf-8").read()
        self.assertRegex(source, r'load_dotenv\([^)]*config\.env"\), override=True\)')


if __name__ == "__main__":
    unittest.main()
