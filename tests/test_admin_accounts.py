"""Admin accounts table (search, sort, pages of 10) and the account page with its actions."""
import re

from tests.helpers import AppTestCase


class AdminAccountsTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")  # self.client is the admin, id 1
        with self.db() as conn:
            conn.execute("UPDATE users SET created_at = '2026-01-01 00:00:00'")
            for i in range(12):
                conn.execute(
                    "INSERT INTO users (email, password_hash, verified_at, created_at, "
                    "first_name, last_name, last_login_at) VALUES (?, 'x', '2026-01-01', ?, ?, ?, ?)",
                    (f"user{i:02d}@example.com", f"2026-02-{i + 1:02d} 00:00:00",
                     "Élodie" if i == 5 else "", "Martin" if i == 5 else "",
                     None if i % 2 else f"2026-03-{i + 1:02d} 00:00:00"))
            conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                         "VALUES ('odd%_name@example.com', 'x', '2026-01-01')")

    def uid(self, email):
        with self.db() as conn:
            return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0]

    def listed(self, url):
        html = self.client.get(url).get_data(as_text=True)
        table = html.split('class="table admin-users-table"', 1)[-1].split("</table>", 1)[0]
        return html, re.findall(r'aria-label="View ([^"]+)"', table)

    def test_ten_accounts_a_page_newest_first(self):
        html, emails = self.listed("/admin")
        self.assertEqual(len(emails), 10)
        self.assertEqual(emails[0], "odd%_name@example.com")  # joined last
        self.assertIn("Showing 1 to 10 of 14 accounts", html)
        self.assertIn('href="/admin?page=2#accounts"', html)
        html, emails = self.listed("/admin?page=2")
        self.assertEqual(emails[-1], "admin@example.com")
        self.assertIn("Showing 11 to 14 of 14 accounts", html)
        # Out of range and invalid pages are clamped.
        self.assertIn("Showing 11 to 14", self.client.get("/admin?page=99").get_data(as_text=True))
        self.assertIn("Showing 1 to 10", self.client.get("/admin?page=abc").get_data(as_text=True))

    def test_search_by_email_and_name(self):
        _, emails = self.listed("/admin?search=USER03")
        self.assertEqual(emails, ["user03@example.com"])
        # Case is ignored in every script, and so is how the accent was typed (E plus U+0301).
        _, emails = self.listed("/admin?search=%C3%89LODIE%20mart")
        self.assertEqual(emails, ["user05@example.com"])
        _, emails = self.listed("/admin?search=E%CC%81lodie")
        self.assertEqual(emails, ["user05@example.com"])
        # % and _ are matched as typed, not as wildcards.
        _, emails = self.listed("/admin?search=%25_")
        self.assertEqual(emails, ["odd%_name@example.com"])
        html = self.client.get("/admin?search=nobody").get_data(as_text=True)
        self.assertIn("No accounts match.", html)

    def test_totals_ignore_the_search(self):
        html = self.client.get("/admin?search=user03").get_data(as_text=True)
        self.assertRegex(html, r'Accounts</p>\s*<p class="stat-value">14<')

    def test_sorting(self):
        _, emails = self.listed("/admin?sort=email&dir=asc")
        self.assertEqual(emails[:2], ["admin@example.com", "odd%_name@example.com"])
        html, emails = self.listed("/admin?sort=last&dir=desc")
        self.assertEqual(emails[0], "admin@example.com")  # signed in today
        self.assertIn('aria-sort="descending"', html)
        # Never signed in goes last in both directions.
        _, emails = self.listed("/admin?sort=last&dir=asc&page=2")
        self.assertIn("odd%_name@example.com", emails)
        html, emails = self.listed("/admin?sort=bogus&dir=sideways")
        self.assertEqual(emails[0], "odd%_name@example.com")
        # The header links keep the search and flip the direction of the active column.
        html = self.client.get("/admin?search=user&sort=email&dir=asc").get_data(as_text=True)
        self.assertIn('href="/admin?search=user&amp;sort=email#accounts"', html)
        self.assertIn('href="/admin?search=user&amp;sort=used#accounts"', html)

    def test_search_and_activity_filter_keep_each_other(self):
        html = self.client.get("/admin?search=user&q=admin@example.com").get_data(as_text=True)
        self.assertIn('<input type="hidden" name="q" value="admin@example.com">', html)
        self.assertIn('<input type="hidden" name="search" value="user">', html)

    def test_account_page(self):
        uid = self.uid("user05@example.com")
        html = self.client.get(f"/admin/users/{uid}?search=mart&page=1").get_data(as_text=True)
        self.assertIn("Élodie Martin", html)
        self.assertIn('href="/admin?search=mart#accounts"', html)  # back to the same list
        self.assertIn(f'id="plan-{uid}"', html)
        self.assertIn(f'id="quota-{uid}"', html)
        self.assertIn('aria-label="Disable user05@example.com"', html)
        self.assertIn('aria-label="Delete user05@example.com"', html)
        self.assertIn('<input type="hidden" name="search" value="mart">', html)
        self.assertEqual(self.client.get("/admin/users/9999").status_code, 404)
        self.assertEqual(self.client.get(f"/admin/users/{2 ** 70}").status_code, 404)
        own = self.client.get("/admin/users/1").get_data(as_text=True)
        self.assertIn("This is you.", own)
        self.assertNotIn('aria-label="Disable admin@example.com"', own)

    def test_account_page_is_admin_only(self):
        user = self.app.test_client()
        self.signup("someone@example.com", client=user)
        self.assertEqual(user.get("/admin/users/1").status_code, 404)
        self.assertIn("/login", self.app.test_client().get("/admin/users/1").headers["Location"])

    def test_actions_return_to_the_account_page_with_the_list_state(self):
        uid = self.uid("user03@example.com")
        state = {"search": "user", "sort": "email", "dir": "asc", "page": "2"}
        resp = self.client.post(f"/admin/users/{uid}", data={"action": "plan", "plan": "pro", **state})
        self.assertEqual(resp.headers["Location"],
                         f"/admin/users/{uid}?search=user&sort=email&dir=asc&page=2")
        resp = self.client.post(f"/admin/users/{uid}", data={"action": "disable", **state},
                                follow_redirects=True)
        self.assertIn("user03@example.com is disabled", resp.get_data(as_text=True))
        # A refused delete stays on the account page, a done one goes back to the list.
        resp = self.client.post(f"/admin/users/{uid}", data={"action": "delete", **state})
        self.assertEqual(resp.headers["Location"],
                         f"/admin/users/{uid}?search=user&sort=email&dir=asc&page=2")
        resp = self.client.post(f"/admin/users/{uid}", data={
            "action": "delete", "confirm_delete": "2", "confirm_email": "user03@example.com",
            **state})
        self.assertEqual(resp.headers["Location"], "/admin?search=user&sort=email&dir=asc&page=2")
        self.assertEqual(self.client.get(f"/admin/users/{uid}").status_code, 404)

    def test_account_page_lists_its_activity(self):
        uid = self.uid("user03@example.com")
        self.client.post(f"/admin/users/{uid}", data={"action": "plan", "plan": "plus"})
        html = self.client.get(f"/admin/users/{uid}").get_data(as_text=True)
        activity = html.split('id="activity-title"', 1)[1]
        self.assertIn("Nomad+", activity)
        self.assertIn('href="/admin?q=user03@example.com#activity"', html)
