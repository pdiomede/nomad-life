"""Admin "Delete these N accounts": deleting the suspected fake accounts the Users table lists,
its confirmations, and the Security activity it records (and finds by event)."""
import os
import re
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


class DeleteSuspectsTests(AppTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(appmod, "ADMIN_USERS_PER_PAGE", 50)  # one page of rows
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("admin@example.com")
        with self.db() as conn:
            def user(email, age_hours=48, confirmed_after=30, signed_in=False, **extra):
                uid = conn.execute(
                    "INSERT INTO users (email, password_hash, created_at, verified_at, "
                    "last_login_at, signup_ip) VALUES (?, 'x', datetime('now', ?), "
                    "'2026-01-01', ?, '203.0.113.7')",
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
            self.other = user("bot@elsewhere.example")
            user("real@example.com", signed_in=True)
            user("new@example.com", age_hours=2)
            user("disabled@example.com", disabled=1)
            user("paid@example.com", plan="pro")
            user("quota@example.com", quota_bytes=1024)
            with_year = user("year@example.com")
            conn.execute("INSERT INTO years (user_id, year, base_city, base_country) "
                         "VALUES (?, 2026, 'Rome', 'Italy')", (with_year,))
            user("other-admin@example.com")
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com",
                                                     "other-admin@example.com"})
        self.suspects = {"scanner@corp.example", "slow@corp.example", "bot@elsewhere.example"}

    def emails(self):
        with self.db() as conn:
            return {r[0] for r in conn.execute("SELECT email FROM users")}

    def asof(self, query="?fake=only"):
        """The moment the Suspected fake list was made, as its form sends it back."""
        html = self.client.get("/admin" + query).get_data(as_text=True)
        return re.search(r'name="asof" value="([^"]+)"', html).group(1)

    def delete(self, count, follow=True, **state):
        data = {"fake": "only", "confirm_delete": "2", "confirm_count": str(count)}
        data.update(state)
        if "asof" not in data:
            data["asof"] = self.asof()
        return self.client.post("/admin/suspects/delete", data=data, follow_redirects=follow)

    def events(self, event):
        with self.db() as conn:
            return [tuple(r) for r in conn.execute(
                "SELECT email, actor, detail, ip, user_id IS NOT NULL FROM audit_log "
                "WHERE event = ? ORDER BY id", (event,))]

    def test_the_button_is_on_the_list_of_suspects_only(self):
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertNotIn("admin-bulk", html)
        html = self.client.get("/admin?fake=only").get_data(as_text=True)
        self.assertIn("3 accounts look fake.", html)
        form = html[html.index('action="/admin/suspects/delete"') - 30:]
        form = form[:form.index("</form>")]
        self.assertIn('data-confirm-delete="the 3 suspected fake accounts listed"', form)
        self.assertIn('data-confirm-type="3" data-confirm-field="confirm_count"', form)
        self.assertIn('<input type="hidden" name="fake" value="only">', form)
        self.assertIn('>Delete these 3 accounts</button>', form)
        # With a search, the bar names it and counts only what the table lists.
        html = self.client.get("/admin?fake=only&search=corp.example").get_data(as_text=True)
        self.assertIn("2 accounts look fake matching &ldquo;corp.example&rdquo;.", html)
        self.assertIn('data-confirm-delete="the 2 suspected fake accounts listed matching '
                      '&#34;corp.example&#34;"', html)
        self.assertIn('<input type="hidden" name="search" value="corp.example">', html)
        # Nothing to delete: no bar.
        html = self.client.get("/admin?fake=only&search=nobody").get_data(as_text=True)
        self.assertNotIn("admin-bulk", html)

    def test_deletes_exactly_the_listed_suspects(self):
        html = self.delete(2, search="corp.example").get_data(as_text=True)
        self.assertIn("2 suspected fake accounts matching &#34;corp.example&#34; were deleted.",
                      html)
        self.assertNotIn("scanner@corp.example", self.emails())
        self.assertIn("bot@elsewhere.example", self.emails())  # not in that search
        html = self.delete(1).get_data(as_text=True)
        self.assertIn("1 suspected fake account was deleted.", html)
        # Everyone else stays: real, new, disabled, paid, custom quota, with data, admins.
        self.assertEqual(self.emails(), {
            "admin@example.com", "real@example.com", "new@example.com", "disabled@example.com",
            "paid@example.com", "quota@example.com", "year@example.com",
            "other-admin@example.com"})

    def test_refusals_delete_nothing(self):
        before = self.emails()
        for data, message in (
                ({"confirm_delete": ""}, "Please confirm the deletion twice."),
                ({"confirm_count": ""}, "Type the number of accounts to confirm the deletion."),
                ({"confirm_count": "²"}, "Type the number of accounts to confirm the deletion."),
                ({"confirm_count": "2"}, "The list changed: 3 accounts are suspected now. "
                                         "Nothing was deleted"),
                ({"fake": ""}, "Show the suspected fake accounts first"),
                ({"confirm_count": "3" * 5000}, "Type the number of accounts"),  # was a 500
                ({"asof": ""}, "This list is out of date. Reload the page"),
                ({"asof": "yesterday"}, "This list is out of date. Reload the page")):
            resp = self.client.post("/admin/suspects/delete", data=dict(
                {"fake": "only", "confirm_delete": "2", "confirm_count": "3",
                 "asof": self.asof()}, **data), follow_redirects=True)
            self.assertIn(message, resp.get_data(as_text=True), data)
            self.assertEqual(self.emails(), before, data)
        self.assertEqual(self.events("admin_fake_cleanup"), [])

    def test_a_list_that_changed_after_the_page_loaded_is_refused(self):
        self.client.get("/admin?fake=only")  # the admin sees 3
        with self.db() as conn:  # one of them signs in meanwhile
            conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (self.slow,))
        html = self.delete(3).get_data(as_text=True)
        self.assertIn("The list changed: 2 accounts are suspected now.", html)
        self.assertTrue(self.suspects <= self.emails())

    def test_accounts_passing_the_24_hour_line_after_the_page_are_kept(self):
        # The admin sees and types 3. Meanwhile one listed account signs in and another one
        # passes 24 hours: the count still matched, so an account never shown was deleted
        # (and with the line passed between the count and the DELETE, without a record).
        with self.db() as conn:  # the page was loaded an hour ago
            asof = conn.execute("SELECT datetime('now', '-1 hour')").fetchone()[0]
            conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (self.slow,))
            # Over 24 hours old now, but not yet when the list was made.
            conn.execute("UPDATE users SET created_at = datetime('now', '-24 hours', "
                         "'-1 minute') WHERE email = 'new@example.com'")
        html = self.delete(3, asof=asof).get_data(as_text=True)
        self.assertIn("The list changed: 2 accounts are suspected now.", html)
        self.assertIn("new@example.com", self.emails())
        self.delete(2, asof=asof)
        self.assertIn("new@example.com", self.emails())  # not on the list that was shown
        self.assertEqual({r[0] for r in self.events("admin_delete_fake")},
                         {"scanner@corp.example", "bot@elsewhere.example"})
        # A moment in the future (a crafted form) is never later than now.
        with self.db() as conn:
            conn.execute("UPDATE users SET created_at = datetime('now', '-23 hours') "
                         "WHERE email = 'new@example.com'")
        html = self.delete(1, asof="2099-01-01 00:00:00").get_data(as_text=True)
        self.assertIn("No suspected fake accounts to delete.", html)
        self.assertIn("new@example.com", self.emails())

    def test_bulk_deletes_do_not_scan_every_ticket_message(self):
        with self.app.app_context():
            plan = " ".join(r[-1] for r in appmod.db.query(
                "EXPLAIN QUERY PLAN UPDATE ticket_messages SET author_id = NULL "
                "WHERE author_id = 1"))
        self.assertIn("idx_ticket_messages_author", plan)

    def test_only_admins(self):
        other = self.signup("someone@example.com", client=self.app.test_client())
        resp = other.post("/admin/suspects/delete", data={
            "fake": "only", "confirm_delete": "2", "confirm_count": "3"})
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(self.suspects <= self.emails())

    def test_every_deletion_is_in_the_security_activity(self):
        os.makedirs(os.path.join(self.app.config["UPLOAD_DIR"], str(self.scanner)))
        self.delete(3)
        self.assertFalse(os.path.exists(os.path.join(self.app.config["UPLOAD_DIR"],
                                                     str(self.scanner))))
        rows = self.events("admin_delete_fake")
        self.assertEqual({r[0] for r in rows}, self.suspects)
        self.assertTrue(all(r[1] == "admin@example.com" and r[3] == "127.0.0.1" and r[4]
                            for r in rows))
        self.assertIn(("scanner@corp.example", "admin@example.com",
                       "Confirmed 4 s after sign up, likely by a mail scanner; never signed in, "
                       "no data after 24 hours.", "127.0.0.1", 1), rows)
        self.assertEqual(self.events("admin_fake_cleanup"),
                         [("", "admin@example.com", "3 accounts", "127.0.0.1", 0)])
        # Found in the table by the event, a deleted address, and the admin who did it.
        for q in ("fake", "bulk", "Suspected FAKE", "scanner@corp", "admin@example.com"):
            section = self.client.get("/admin", query_string={"q": q}).get_data(
                as_text=True).split('id="activity"', 1)[1]
            self.assertIn("Suspected fake account", section, q)
        section = self.client.get("/admin?q=bulk").get_data(as_text=True).split(
            'id="activity"', 1)[1]
        self.assertIn("Suspected fake accounts deleted (bulk)", section)
        self.assertIn("(3 accounts)", section)

    def test_activity_search_finds_any_event_by_its_name_or_detail(self):
        with self.db() as conn:
            conn.execute("INSERT INTO audit_log (email, event) VALUES ('x@example.com', "
                         "'password_changed')")
            conn.execute("INSERT INTO audit_log (email, event, detail, actor) VALUES "
                         "('y@example.com', 'admin_plan', 'Nomad+', 'admin@example.com')")
        section = self.client.get("/admin?q=password+changed").get_data(as_text=True).split(
            'id="activity"', 1)[1]
        self.assertIn("x@example.com", section)
        self.assertNotIn("y@example.com", section)
        section = self.client.get("/admin?q=nomad%2B").get_data(as_text=True).split(
            'id="activity"', 1)[1]
        self.assertIn("y@example.com", section)
        # Internal keys are not searched by parts: "_" matched every event.
        section = self.client.get("/admin?q=_").get_data(as_text=True).split(
            'id="activity"', 1)[1]
        self.assertIn('No events for &ldquo;_&rdquo;.', section)
        self.assertIn('placeholder="Email, IP or event"', self.client.get("/admin").get_data(
            as_text=True))
        # An account's whole email still means its history, not a text search.
        section = self.client.get("/admin?q=real@example.com").get_data(as_text=True).split(
            'id="activity"', 1)[1]
        self.assertNotIn("x@example.com", section)


if __name__ == "__main__":
    import unittest
    unittest.main()
