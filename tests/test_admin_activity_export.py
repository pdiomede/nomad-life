"""Security Activity menu on the admin page: export the last 30 days or everything as CSV, and
purge either after the two step dialog with PURGE typed, downloading what was deleted."""
import csv
import io
import os
import re
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase
from tests.test_fixes_158 import other_connection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ActivityMenuCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("admin@example.com")
        with self.db() as conn:
            conn.execute("DELETE FROM audit_log")
            conn.executemany(
                "INSERT INTO audit_log (created_at, event, email, detail, ip, user_id) "
                "VALUES (datetime('now', ?), ?, ?, ?, ?, ?)",
                [("-90 days", "sign_in", "old@example.com", "", "203.0.113.1", None),
                 ("-40 days", "sign_in_failed", "old@example.com", "", "203.0.113.2", None),
                 ("-5 days", "sign_in", "new@example.com", "=HYPERLINK(\"x\")", "203.0.113.3", 1),
                 ("-1 hours", "password_changed", "new@example.com", "", "203.0.113.4", 1)])

    def events(self):
        with self.db() as conn:
            return [r[0] for r in conn.execute("SELECT email || ' ' || event FROM audit_log "
                                               "ORDER BY id")]

    def parse(self, resp):
        text = resp.get_data().decode("utf-8")
        self.assertTrue(text.startswith("﻿"))  # Excel reads it as UTF-8
        return list(csv.reader(io.StringIO(text[1:])))

    def purge(self, range_key, word="PURGE", confirm="2", client=None):
        return (client or self.client).post("/admin/activity/purge", data={
            "range": range_key, "confirm_delete": confirm, "confirm_word": word})


class ExportTests(ActivityMenuCase):
    def test_export_last_30_days(self):
        resp = self.client.get("/admin/activity/export?range=30")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "text/csv")
        self.assertRegex(resp.headers["Content-Disposition"],
                         r'attachment; filename="nomadlife-security-activity-last-30-days-'
                         r'\d{4}-\d{2}-\d{2}\.csv"')
        self.assertEqual(resp.headers["Cache-Control"], "private, no-store")
        rows = self.parse(resp)
        self.assertEqual(rows[0], appmod.AUDIT_CSV_HEADER)
        self.assertEqual([(r[3], r[2]) for r in rows[1:]],
                         [("new@example.com", "sign_in"), ("new@example.com", "password_changed")])
        self.assertEqual(rows[1][1], "Signed in")
        self.assertEqual(rows[1][5], "'=HYPERLINK(\"x\")")  # never a formula in a spreadsheet

    def test_export_all_in_chunks(self):
        with mock.patch.object(appmod, "AUDIT_EXPORT_CHUNK", 1):
            rows = self.parse(self.client.get("/admin/activity/export?range=all"))
        self.assertEqual(len(rows) - 1, 4)
        self.assertEqual(rows[1][3], "old@example.com")  # oldest first
        self.assertEqual(len(self.events()), 4)  # an export deletes nothing

    def test_bad_range_and_access(self):
        self.assertEqual(self.client.get("/admin/activity/export?range=7").status_code, 404)
        stranger = self.app.test_client()
        self.signup("b@example.com", client=stranger)
        self.assertEqual(stranger.get("/admin/activity/export?range=all").status_code, 404)
        self.assertEqual(self.purge("all", client=stranger).status_code, 404)
        self.assertEqual(self.app.test_client().get("/admin/activity/export?range=all")
                         .status_code, 302)
        # The stranger's own sign up and sign in were added; nothing was purged.
        self.assertEqual(len([e for e in self.events() if "@example.com" in e
                              and not e.startswith("b@")]), 4)

    def test_download_cookie(self):
        resp = self.client.get("/admin/activity/export?range=all&dl=abc12345")
        self.assertIn("nl_download=abc12345", resp.headers.get("Set-Cookie", ""))


class PurgeTests(ActivityMenuCase):
    def test_needs_both_confirmations_and_the_word(self):
        self.purge("all", confirm="")
        self.purge("all", word="purge it")
        self.purge("all", word="")
        self.assertEqual(len(self.events()), 4)
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Type PURGE to confirm.", html)

    def test_purge_last_30_days(self):
        resp = self.purge("30", word=" purge ")
        self.assertEqual(resp.status_code, 200)
        self.assertRegex(resp.headers["Content-Disposition"], r"last-30-days")
        rows = self.parse(resp)
        self.assertEqual([r[2] for r in rows[1:]], ["sign_in", "password_changed"])
        self.assertEqual(self.events(), ["old@example.com sign_in",
                                         "old@example.com sign_in_failed"])
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("Purged 2 events of security activity of the last 30 days.", html)

    def test_purge_all(self):
        rows = self.parse(self.purge("all"))
        self.assertEqual(len(rows) - 1, 4)
        self.assertEqual(self.events(), [])

    def test_an_event_written_meanwhile_stays(self):
        original = appmod.audit_csv_lines

        def sign_in_meanwhile(rows, header=False):
            with other_connection(self) as conn:
                conn.execute("INSERT INTO audit_log (event, email) VALUES "
                             "('sign_in', 'late@example.com')")
            return original(rows, header)

        with mock.patch.object(appmod, "audit_csv_lines", side_effect=sign_in_meanwhile):
            rows = self.parse(self.purge("all"))
        self.assertNotIn("late@example.com", [r[3] for r in rows])
        self.assertEqual(self.events(), ["late@example.com sign_in"])

    def test_nothing_to_purge(self):
        with self.db() as conn:
            conn.execute("DELETE FROM audit_log")
        resp = self.purge("30")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("There was no security activity to purge.",
                      self.client.get(resp.headers["Location"]).get_data(as_text=True))


class PageTests(ActivityMenuCase):
    def test_menu_and_titles(self):
        html = self.client.get("/admin").get_data(as_text=True)
        for title in ("Plans &amp; Prices", "Registered Users", "Security Activity"):
            self.assertIn(f'class="admin-section-title">{title}</h2>', html)
        menu = html.split('class="gear-menu" data-menu', 1)[1].split("</details>", 1)[0]
        self.assertIn('aria-label="Security Activity options"', menu)
        self.assertEqual(menu.count('class="user-menu-item menu-export"'), 2)
        self.assertEqual(menu.count('class="user-menu-item menu-purge"'), 2)
        self.assertEqual(menu.count('data-confirm-type="PURGE"'), 2)
        self.assertIn("Export last 30 days", menu)
        self.assertIn("Purge all", menu)
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".admin-section-title { font-family: \"Outfit\"", css)
        self.assertIn(".menu-purge { color: var(--danger);", css)
        with open(os.path.join(ROOT, "static", "js", "theme.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('querySelectorAll("[data-user-menu], [data-menu]")', js)
        self.assertIn('form.hasAttribute("data-download-reload")', js)
