"""Regression tests for the bugs found in the 1.3.2 review of the admin page and the tickets."""
import re

from tests.helpers import AppTestCase, ref


class FixTestCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("a@example.com")  # self.client, user 1
        self.admin = self.app.test_client()
        self.signup("admin@example.com", client=self.admin)

    def count(self, sql):
        with self.db() as conn:
            return conn.execute(sql).fetchone()[0]


class DisabledAdminTests(FixTestCase):
    def test_a_disabled_account_made_admin_can_be_enabled(self):
        self.admin.post("/admin/users/1", data={"action": "disable"})
        # The address is added to ADMIN_EMAILS afterwards.
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com", "a@example.com"})
        html = self.admin.get("/admin/users/1").get_data(as_text=True)
        self.assertIn('aria-label="Enable a@example.com"', html)
        self.assertNotIn('aria-label="Disable a@example.com"', html)
        self.assertNotIn('aria-label="Delete a@example.com"', html)
        self.admin.post("/admin/users/1", data={"action": "enable"})
        self.assertEqual(self.count("SELECT disabled FROM users WHERE id = 1"), 0)
        # An admin that is not disabled still cannot be touched.
        html = self.admin.get("/admin/users/1").get_data(as_text=True)
        self.assertNotIn('aria-label="Enable a@example.com"', html)
        self.assertNotIn('aria-label="Disable a@example.com"', html)


class TicketBackLinkTests(FixTestCase):
    def setUp(self):
        super().setUp()
        self.client.post("/support/new", data={"kind": "bug", "subject": "S", "body": "B"})

    def test_admin_back_returns_to_the_same_filters_sort_and_search(self):
        query = "status=open&kind=bug&q=a@example&sort=id&dir=asc"
        html = self.admin.get("/admin/support?" + query).get_data(as_text=True)
        link = re.search(r'href="(/admin/support/\d{4}-1[^"]*)" dir="auto"', html)[1]
        self.assertIn("kind=bug", link)
        page = self.admin.get(link.replace("&amp;", "&")).get_data(as_text=True)
        back = re.search(r'<a class="back" href="([^"]+)"', page)[1].replace("&amp;", "&")
        self.assertEqual(back, "/admin/support?" + query)
        # After an action the ticket page keeps the way back.
        resp = self.admin.post(link.replace("&amp;", "&"), data={"action": "close"})
        self.assertIn("kind=bug", resp.headers["Location"])
        self.assertTrue(resp.headers["Location"].startswith(f"/admin/support/{ref(1)}?"))

    def test_user_back_returns_to_the_same_tab(self):
        html = self.client.get("/support?status=open").get_data(as_text=True)
        self.assertIn(f'href="/support/{ref(1)}?status=open"', html)
        page = self.client.get(f"/support/{ref(1)}?status=open").get_data(as_text=True)
        self.assertIn('<a class="back" href="/support?status=open">', page)
        resp = self.client.post(f"/support/{ref(1)}?status=open", data={"action": "close"})
        self.assertEqual(resp.headers["Location"], f"/support/{ref(1)}?status=open")
        # Nothing to keep: the plain addresses as before.
        page = self.client.get(f"/support/{ref(1)}").get_data(as_text=True)
        self.assertIn('<a class="back" href="/support">', page)


class DoubleSubmitTests(FixTestCase):
    def test_a_ticket_sent_twice_is_opened_once(self):
        data = {"kind": "bug", "subject": "Map", "body": "Empty map"}
        first = self.client.post("/support/new", data=data)
        mails = len(self.outbox)
        second = self.client.post("/support/new", data=data)
        self.assertEqual(second.headers["Location"], first.headers["Location"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM tickets"), 1)
        self.assertEqual(len(self.outbox), mails)  # the admins are not told twice
        # A different ticket, or the same one sent again later, is a new ticket.
        self.client.post("/support/new", data=dict(data, body="Other"))
        with self.db() as conn:
            conn.execute("UPDATE tickets SET created_at = datetime('now', '-2 minutes')")
        self.client.post("/support/new", data=data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM tickets"), 3)

    def test_a_message_sent_twice_is_saved_once(self):
        self.client.post("/support/new", data={"kind": "bug", "subject": "S", "body": "B"})
        for _ in range(2):
            self.client.post(f"/support/{ref(1)}", data={"action": "reply", "body": "More"})
        mails = len(self.outbox)
        for _ in range(2):
            self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reply", "body": "Fixed"})
        self.assertEqual(self.count("SELECT COUNT(*) FROM ticket_messages WHERE body = 'More'"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM ticket_messages WHERE body = 'Fixed'"), 1)
        self.assertEqual(len(self.outbox), mails + 1)  # one answer email to the user
        # Saying the same thing again later is a new message.
        with self.db() as conn:
            conn.execute("UPDATE ticket_messages SET created_at = datetime('now', '-2 minutes')")
        self.client.post(f"/support/{ref(1)}", data={"action": "reply", "body": "More"})
        self.assertEqual(self.count("SELECT COUNT(*) FROM ticket_messages WHERE body = 'More'"), 2)

    def test_the_forms_ask_the_browser_to_send_once(self):
        self.assertIn('data-submit-once', self.client.get("/support/new").get_data(as_text=True))
        self.client.post("/support/new", data={"kind": "bug", "subject": "S", "body": "B"})
        self.assertIn('data-submit-once', self.client.get(f"/support/{ref(1)}").get_data(as_text=True))
        self.assertIn('data-submit-once',
                      self.admin.get(f"/admin/support/{ref(1)}").get_data(as_text=True))
