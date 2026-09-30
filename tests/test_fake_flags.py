"""Admin accounts table: the "Likely fake" chip, the All / Suspected fake / Hide suspected filter,
and the same rule as check-fake-users."""
import re
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


class FakeFlagTests(AppTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(appmod, "ADMIN_USERS_PER_PAGE", 50)  # one page of rows
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")
        with self.db() as conn:
            def user(email, age_hours=48, confirmed_after=30, signed_in=False, **extra):
                uid = conn.execute(
                    "INSERT INTO users (email, password_hash, created_at, verified_at, "
                    "last_login_at) VALUES (?, 'x', datetime('now', ?), '2026-01-01', ?)",
                    (email, f"-{age_hours} hours", "2026-01-02" if signed_in else None)).lastrowid
                for column, value in extra.items():
                    conn.execute(f"UPDATE users SET {column} = ? WHERE id = ?", (value, uid))
                if confirmed_after is not None:
                    conn.execute("INSERT INTO audit_log (user_id, email, event, created_at) "
                                 "VALUES (?, ?, 'account_confirmed', datetime('now', ?, ?))",
                                 (uid, email, f"-{age_hours} hours", f"+{confirmed_after} seconds"))
                return uid
            self.scanner = user("scanner@corp.example", confirmed_after=4)
            self.slow = user("slow@corp.example", confirmed_after=3600)
            user("real@example.com", signed_in=True)
            user("new@example.com", age_hours=2)
            user("disabled@example.com", disabled=1)
            user("paid@example.com", plan="pro")
            user("quota@example.com", quota_bytes=1024)
            with_year = user("year@example.com")
            conn.execute("INSERT INTO years (user_id, year, base_city, base_country) "
                         "VALUES (?, 2026, 'Rome', 'Italy')", (with_year,))
            with_ticket = user("ticket@example.com")
            conn.execute("INSERT INTO tickets (user_id, subject) VALUES (?, 'Help')", (with_ticket,))
            # An admin that never signed in and holds nothing is still never flagged.
            user("other-admin@example.com")
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com",
                                                     "other-admin@example.com"})

    def rows(self, query=""):
        html = self.client.get("/admin" + query).get_data(as_text=True)
        section = html.split('id="accounts"', 1)[1].split('id="activity"', 1)[0]
        out = {}
        for row in re.findall(r"<tr>(.*?)</tr>", section, re.S):
            email = re.search(r'class="admin-email"><a [^>]*>([^<]+)</a>', row)
            if email:
                out[email[1]] = "chip-fake" in row
        return section, out

    def flagged(self, query=""):
        return {e for e, fake in self.rows(query)[1].items() if fake}

    def test_chip_on_exactly_the_accounts_the_tool_lists(self):
        self.assertEqual(self.flagged(),
                         {"scanner@corp.example", "slow@corp.example"})
        with self.app.app_context():
            tool = {r["email"] for r in appmod.fake_user_candidates(
                appmod.FAKE_MIN_AGE_HOURS, self.app.config["ADMIN_EMAILS"])}
        self.assertEqual(tool, {"scanner@corp.example", "slow@corp.example"})

    def test_reason(self):
        section, _ = self.rows()
        self.assertIn('data-tip="Confirmed 4 s after sign up, likely by a mail scanner; never '
                      'signed in, no data after 24 hours."', section)
        self.assertIn('data-tip="Confirmed 1 h after sign up; never signed in, no data after 24 '
                      'hours."', section)
        self.assertIn('<span class="visually-hidden">: Confirmed 4 s after', section)
        self.assertIn('tabindex="0"', section)
        page = self.client.get(f"/admin/users/{self.scanner}").get_data(as_text=True)
        self.assertIn('<span class="pill pill-error chip-fake">Likely fake</span>', page)
        self.assertIn("./checkFakeUsers.sh", page)
        page = self.client.get("/admin/users/1").get_data(as_text=True)
        self.assertNotIn("chip-fake", page)

    def test_filter(self):
        section, rows = self.rows("?fake=only")
        self.assertEqual(set(rows), {"scanner@corp.example", "slow@corp.example"})
        self.assertIn("Showing 1 to 2 of 2 suspected accounts", section)
        self.assertIn('aria-current="true" class="admin-filter-fake">Suspected fake '
                      '<span class="admin-filter-count">2</span>', section)
        _, rows = self.rows("?fake=hide")
        self.assertNotIn("scanner@corp.example", rows)
        self.assertIn("real@example.com", rows)
        _, rows = self.rows("?fake=bogus")  # unknown values show every account
        self.assertIn("scanner@corp.example", rows)
        # The count ignores the search; the list follows both.
        section, rows = self.rows("?fake=only&search=scanner")
        self.assertEqual(set(rows), {"scanner@corp.example"})
        self.assertIn('<span class="admin-filter-count">2</span>', section)
        # Nothing left to flag.
        with self.db() as conn:
            conn.execute("UPDATE users SET last_login_at = '2026-01-02'")
        section, rows = self.rows("?fake=only")
        self.assertEqual(rows, {})
        self.assertIn("No accounts look fake.", section)

    def test_filter_is_kept_by_links_forms_and_the_account_page(self):
        section, _ = self.rows("?fake=only&sort=email&dir=asc")
        self.assertIn('href="/admin?sort=email&amp;fake=only#accounts"', section)  # sort header
        self.assertIn('<input type="hidden" name="fake" value="only">', section)  # search form
        self.assertIn(f'href="/admin/users/{self.scanner}?sort=email&amp;dir=asc&amp;fake=only"',
                      section)
        page = self.client.get(f"/admin/users/{self.scanner}?fake=only").get_data(as_text=True)
        self.assertIn('href="/admin?fake=only#accounts"', page)  # Back to accounts
        resp = self.client.post(f"/admin/users/{self.scanner}", data={
            "action": "delete", "confirm_delete": "2", "confirm_email": "scanner@corp.example",
            "fake": "only"})
        self.assertEqual(resp.headers["Location"], "/admin?fake=only")
        # The other buttons of the filter keep the rest of the state.
        html = self.client.get("/admin?search=corp&fake=only&q=x@example.com").get_data(as_text=True)
        self.assertIn('href="/admin?search=corp&amp;q=x@example.com#accounts">All accounts', html)
        self.assertIn('href="/admin?search=corp&amp;fake=hide&amp;q=x@example.com#accounts">'
                      'Hide suspected', html)

    def test_years_column_is_gone(self):
        section, _ = self.rows()
        self.assertNotIn(">Years<", section)
        self.assertNotIn("sort=years", section)
        self.assertEqual(self.rows("?sort=years")[1], self.rows()[1])  # falls back to Joined
        self.assertNotIn("years", appmod.ADMIN_USER_SORTS)
