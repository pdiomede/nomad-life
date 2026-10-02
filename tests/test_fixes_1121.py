"""Fixes of 1.12.1, from a review of the Security Activity export and purge menu."""
import contextlib
import os
from datetime import datetime, timedelta, timezone
from unittest import mock

import app as appmod
from tests.test_admin_activity_export import ActivityMenuCase
from tests.test_fixes_158 import other_connection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class PurgeFileTests(ActivityMenuCase):
    def another_admin_first(self, where):
        """db.transaction, but another admin's purge of `where` commits first."""
        real = appmod.db.transaction

        @contextlib.contextmanager
        def transaction():
            with other_connection(self) as conn:
                conn.execute(f"DELETE FROM audit_log WHERE {where}")
            with real() as conn:
                yield conn
        return mock.patch.object(appmod.db, "transaction", transaction)

    def test_the_file_lists_only_what_this_purge_deleted(self):
        with self.another_admin_first("event = 'sign_in'"):
            resp = self.purge("30")
        rows = self.parse(resp)
        self.assertEqual([r[2] for r in rows[1:]], ["password_changed"])
        self.assertIn("Purged 1 event of security activity",
                      self.client.get("/admin").get_data(as_text=True))

    def test_a_purge_done_meanwhile_downloads_nothing(self):
        with self.another_admin_first("created_at >= datetime('now', '-30 days')"):
            resp = self.purge("30")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("already purged meanwhile",
                      self.client.get(resp.headers["Location"]).get_data(as_text=True))

    def test_one_cutoff_for_reading_and_deleting(self):
        # "now" moved on between the read and the delete, so an event at the line could be in
        # the file but stay in the log: the cutoff is now a fixed time.
        where, params = appmod.audit_range_sql("30")
        self.assertNotIn("now", where)
        cutoff = datetime.strptime(params[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        expected = datetime.now(timezone.utc) - timedelta(days=appmod.AUDIT_EXPORT_DAYS)
        self.assertLess(abs((cutoff - expected).total_seconds()), 5)
        with self.db() as conn:  # just inside the line when read
            conn.execute("UPDATE audit_log SET created_at = ? WHERE event = 'password_changed'",
                         (params[0],))
        with mock.patch.object(appmod, "audit_range_sql", return_value=(where, params)):
            rows = self.parse(self.purge("30"))
        self.assertIn("password_changed", [r[2] for r in rows])
        self.assertNotIn("new@example.com password_changed", self.events())


class PageFixTests(ActivityMenuCase):
    def test_export_links_are_plain_links(self):
        # With a download attribute, a link answered by the sign in page (a session that
        # ended) saved that page as the file instead of showing it.
        menu = self.client.get("/admin").get_data(as_text=True).split(
            'class="gear-menu" data-menu', 1)[1].split("</details>", 1)[0]
        self.assertNotIn(" download", menu)
        self.assertEqual(menu.count("data-menu-close"), 2)
        self.assertIn('querySelectorAll("[data-menu-close]")', read("static", "js", "theme.js"))

    def test_the_message_after_a_purge_is_in_view(self):
        # The reload kept the scroll position (the table, far below the message).
        js = read("static", "js", "theme.js")
        self.assertIn('history.replaceState(null, "", location.pathname + location.search)', js)
        self.assertIn('history.scrollRestoration = "manual"', js)

    def test_the_menu_fits_a_phone(self):
        # It opened toward the right edge and widened the page on phones.
        self.assertIn(".gear-panel { left: auto; right: 0; min-width: 230px; "
                      "max-width: calc(100vw - 32px); }", read("static", "css", "style.css"))
