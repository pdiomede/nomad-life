"""1.12.18 fixes: three small inconsistencies noted by the 1.12.17 reviews."""
import os
from unittest import mock

import app as appmod
import package
from tests.test_support import SupportBase


class LeftoverTests(SupportBase):
    """Three small inconsistencies the 1.12.17 reviews noted."""

    def test_admin_dates_are_utc_like_the_activity(self):
        with self.db() as conn:  # 23:30 UTC: already the next day east of UTC
            conn.execute("UPDATE users SET created_at = '2026-03-01 23:30:00', "
                         "last_login_at = '2026-03-02 23:45:00' WHERE email = 'a@example.com'")
        with mock.patch.dict(os.environ, {"TZ": "Asia/Tokyo"}):
            import time
            time.tzset()
            try:
                table = self.admin.get("/admin").get_data(as_text=True)
                page = self.admin.get("/admin/users/1").get_data(as_text=True)
            finally:
                os.environ.pop("TZ", None)
                time.tzset()
        self.assertIn(">Joined (UTC)<", table)
        self.assertIn('<td class="small nowrap hide-sm">2026-03-01</td>', table)
        self.assertIn('<td class="small nowrap">2026-03-02</td>', table)
        self.assertIn("<dt>Joined</dt><dd>2026-03-01</dd>", page)
        self.assertIn("<dt>Last sign in</dt><dd>2026-03-02</dd>", page)
        self.assertIn("Dates in UTC", page)

    def test_choosing_a_calendar_is_not_security_activity(self):
        with self.db() as conn:
            for event in ("calendar_connected", "calendar_chosen", "calendar_unchosen"):
                conn.execute("INSERT INTO audit_log (user_id, email, event) VALUES (1, 'a@example.com', ?)", (event,))
        html = self.client.get("/settings").get_data(as_text=True)
        activity = html[html.index('id="activity"'):]
        self.assertIn(appmod.AUDIT_LABELS["calendar_connected"], activity)
        self.assertNotIn(appmod.AUDIT_LABELS["calendar_chosen"], activity)
        self.assertNotIn(appmod.AUDIT_LABELS["calendar_unchosen"], activity)

    def test_pdf_sizes_read_like_the_receipt_list(self):
        for size in (0, 1023, 1024, 1536, 1048300, 2 * 1024 * 1024, 2_621_440, 10 * 1024 * 1024 - 1,
                     1024 * 1024 * 1024 + 1):
            self.assertEqual(package._fmt_size(size), appmod.format_size(size), size)
