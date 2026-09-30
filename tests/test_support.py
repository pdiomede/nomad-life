"""1.3.0: support tickets for users and admins."""
import re

import app as appmod
from tests.helpers import AppTestCase, ref


class SupportBase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup()
        self.admin = self.signup("admin@example.com", client=self.app.test_client())

    def open_ticket(self, subject="Map is empty", body="The map shows nothing.\nAfter login.",
                    kind="bug", client=None):
        resp = (client or self.client).post("/support/new", data={
            "kind": kind, "subject": subject, "body": body})
        return resp

    def ticket(self, ticket_id=1):
        with self.db() as conn:
            return conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()

    def messages(self, ticket_id=1):
        with self.db() as conn:
            return conn.execute("SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id",
                                (ticket_id,)).fetchall()


class NavigationTests(SupportBase):
    def test_icon_and_menu_only_when_signed_in(self):
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('title="Contact support"', html)
        self.assertIn('aria-label="Contact support"', html)
        header = html.split("</header>", 1)[0]
        self.assertLess(header.index("support-btn"), header.index('id="theme-toggle"'))
        self.assertIn('<a class="user-menu-item menu-support" href="/support">Support</a>', html)
        self.assertNotIn("/admin/support", html)
        admin_html = self.admin.get("/settings").get_data(as_text=True)
        self.assertIn('href="/admin/support">Support tickets</a>', admin_html)
        anon = self.app.test_client().get("/login").get_data(as_text=True)
        self.assertNotIn("Contact support", anon)
        self.assertEqual(self.app.test_client().get("/support").status_code, 302)

    def test_footer_links_to_x(self):
        html = self.app.test_client().get("/login").get_data(as_text=True)
        self.assertIn('href="https://x.com/pdiomede"', html)
        self.assertNotIn("pdiomede.com", html)
        self.assertIn("Disallow: /support", self.client.get("/robots.txt").get_data(as_text=True))


class UserTicketTests(SupportBase):
    def test_submit_a_ticket(self):
        page = self.client.get("/support/new").get_data(as_text=True)
        for label in ("Report a bug", "Feature request", "General question"):
            self.assertIn(label, page)
        resp = self.open_ticket(subject="  Map‮ is empty ", body="Line 1\r\nLine 2\x00 ")
        self.assertEqual(resp.headers["Location"], f"/support/{ref(1)}")
        t = self.ticket()
        self.assertEqual((t["kind"], t["subject"], t["status"]), ("bug", "Map is empty", "open"))
        self.assertIsNone(t["closed_at"])
        self.assertEqual(self.messages()[0]["body"], "Line 1\nLine 2")
        html = self.client.get("/support").get_data(as_text=True)
        self.assertIn(f"#{ref(1)} <bdi>Map is empty</bdi>", html)
        self.assertIn("Report a bug", html)
        self.assertIn(">Open<", html)
        ticket_page = self.client.get(f"/support/{ref(1)}").get_data(as_text=True)
        self.assertIn('<div class="message-body" dir="auto">Line 1\nLine 2</div>', ticket_page)

    def test_validation(self):
        cases = [({"kind": "spam", "subject": "a", "body": "b"}, "choose what the ticket"),
                 ({"kind": "bug", "subject": " ", "body": "b"}, "write a subject"),
                 ({"kind": "bug", "subject": "a", "body": "​"}, "write a subject"),
                 ({"kind": "bug", "subject": "x" * 151, "body": "b"}, "at most 150"),
                 ({"kind": "bug", "subject": "a", "body": "x" * 5001}, "at most 5000")]
        for data, message in cases:
            resp = self.client.post("/support/new", data=data)
            self.assertEqual(resp.status_code, 400)
            self.assertIn(message, resp.get_data(as_text=True))
        self.assertIsNone(self.ticket())
        resp = self.client.post("/support/new", data={"kind": "bug", "subject": "Kept",
                                                      "body": ""})
        self.assertIn('value="Kept"', resp.get_data(as_text=True))  # typed values stay

    def test_open_ticket_cap_and_rate_limit(self):
        for i in range(5):
            self.assertEqual(self.open_ticket(subject=f"T{i}").status_code, 302)
        resp = self.open_ticket(subject="T5")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Too many new tickets", resp.get_data(as_text=True))
        with self.db() as conn:
            conn.execute("DELETE FROM auth_events")
            for i in range(15):
                conn.execute("INSERT INTO tickets (user_id, subject) VALUES (1, 'x')")
        resp = self.open_ticket(subject="T21")
        self.assertIn("You already have 20 open tickets", resp.get_data(as_text=True))

    def test_other_users_tickets_are_not_found(self):
        self.open_ticket()
        other = self.signup("b@example.com", client=self.app.test_client())
        self.assertEqual(other.get(f"/support/{ref(1)}").status_code, 404)
        self.assertEqual(other.post(f"/support/{ref(1)}", data={"action": "close"}).status_code, 404)
        self.assertEqual(other.post(f"/support/{ref(1)}", data={"action": "reply", "body": "hi"})
                         .status_code, 404)
        self.assertNotIn("Map is empty", other.get("/support").get_data(as_text=True))
        self.assertEqual(self.client.get("/admin/support").status_code, 404)  # not an admin
        self.assertEqual(self.client.get(f"/admin/support/{ref(1)}").status_code, 404)
        self.assertEqual(len(self.messages()), 1)

    def test_messages_are_escaped(self):
        self.open_ticket(subject="<b>bold</b>", body="<script>alert(1)</script>")
        for client, path in ((self.client, f"/support/{ref(1)}"), (self.admin, f"/admin/support/{ref(1)}"),
                             (self.admin, "/admin/support"), (self.client, "/support")):
            html = client.get(path).get_data(as_text=True)
            self.assertNotIn("<script>alert(1)", html)
            self.assertNotIn("<b>bold</b>", html)


class ConversationTests(SupportBase):
    def setUp(self):
        super().setUp()
        self.open_ticket()
        self.outbox.clear()

    def test_admin_and_user_talk_and_emails_carry_no_text(self):
        self.assertIn("New", self.admin.get("/admin/support").get_data(as_text=True))
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reply", "body": "Secret fix steps"})
        mail = self.outbox[-1]
        self.assertEqual(mail["to"], "a@example.com")
        self.assertIn(f"/support/{ref(1)}", mail["text"])
        self.assertNotIn("Secret fix steps", mail["text"] + mail["html"])
        self.assertNotIn("Map is empty", mail["text"] + mail["html"])
        html = self.client.get("/app", follow_redirects=True).get_data(as_text=True)
        self.assertIn("support-dot", html)
        self.assertIn("1 ticket with a new reply", html)
        self.assertIn("New reply", self.client.get("/support").get_data(as_text=True))
        page = self.client.get(f"/support/{ref(1)}").get_data(as_text=True)
        self.assertIn("Nomad Life support", page)
        self.assertIn("Secret fix steps", page)
        self.assertNotIn("support-dot", self.client.get("/support").get_data(as_text=True))
        self.client.post(f"/support/{ref(1)}", data={"action": "reply", "body": "Thanks, still empty"})
        admin_mail = self.outbox[-1]
        self.assertEqual(admin_mail["to"], "admin@example.com")
        self.assertIn(f"/admin/support/{ref(1)}", admin_mail["text"])
        self.assertNotIn("still empty", admin_mail["text"] + admin_mail["html"])
        self.assertEqual([m["from_admin"] for m in self.messages()], [0, 1, 0])

    def test_new_ticket_emails_the_admins(self):
        self.outbox.clear()
        self.open_ticket(subject="Second")
        self.assertEqual([m["to"] for m in self.outbox], ["admin@example.com"])
        self.assertIn(f"opened support ticket #{ref(2)}", self.outbox[-1]["text"])

    def test_closing_reopening_and_dates(self):
        self.client.post(f"/support/{ref(1)}", data={"action": "close"})
        t = self.ticket()
        self.assertEqual((t["status"], t["closed_by"]), ("closed", "user"))
        self.assertIsNotNone(t["closed_at"])
        self.assertEqual(t["updated_at"], t["closed_at"])
        self.client.post(f"/support/{ref(1)}", data={"action": "reply", "body": "Back again"})
        t = self.ticket()
        self.assertEqual(t["status"], "open")
        self.assertIsNone(t["closed_at"])
        self.assertIn("opened it again", self.outbox[-1]["text"])
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "close"})
        t = self.ticket()
        self.assertEqual((t["status"], t["closed_by"]), ("closed", "admin"))
        self.assertIn("closed your ticket", self.outbox[-1]["text"])
        resp = self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reply", "body": "late"},
                               follow_redirects=True)
        self.assertIn("Reopen it to answer", resp.get_data(as_text=True))
        self.assertEqual(len(self.messages()), 2)
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reopen"})
        self.assertEqual(self.ticket()["status"], "open")
        self.assertIsNone(self.ticket()["closed_at"])

    def test_closing_twice_sends_one_email(self):
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "close"})
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "close"})
        self.assertEqual(len([m for m in self.outbox if "closed" in m["subject"]]), 1)

    def test_empty_reply_keeps_the_draft_page(self):
        resp = self.client.post(f"/support/{ref(1)}", data={"action": "reply", "body": "  "})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Please write a message.", resp.get_data(as_text=True))

    def test_deleting_the_account_deletes_its_tickets(self):
        self.client.post("/settings", data={"action": "delete", "confirm_delete": "2",
                                            "confirm_email": "a@example.com",
                                            "current_password": "password1"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ticket_messages").fetchone()[0], 0)

    def test_ticket_events_stay_out_of_the_security_list(self):
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertNotIn("Support ticket opened", html)
        admin_html = self.admin.get("/admin").get_data(as_text=True)
        self.assertIn("Support ticket opened", admin_html)


class AdminTableTests(SupportBase):
    def setUp(self):
        super().setUp()
        self.client.post("/settings", data={"action": "profile", "first_name": "Ada",
                                            "last_name": "Lovelace"})
        self.open_ticket(subject="Alpha bug", kind="bug")
        self.open_ticket(subject="beta idea", kind="feature")
        bob = self.signup("bob@example.com", client=self.app.test_client())
        self.open_ticket(subject="Gamma question", kind="question", client=bob)
        self.client.post(f"/support/{ref(2)}", data={"action": "close"})

    def rows(self, query=""):
        html = self.admin.get("/admin/support" + query).get_data(as_text=True)
        return re.findall(r'<a href="/admin/support/\d{4}-(\d+)" dir="auto">', html)

    def test_filters_and_search(self):
        self.assertEqual(sorted(self.rows()), ["1", "2", "3"])
        self.assertEqual(sorted(self.rows("?status=open")), ["1", "3"])
        self.assertEqual(self.rows("?status=closed"), ["2"])
        self.assertEqual(self.rows("?kind=feature"), ["2"])
        self.assertEqual(self.rows("?kind=question&status=open"), ["3"])
        self.assertEqual(self.rows("?q=bob"), ["3"])
        self.assertEqual(sorted(self.rows("?q=Lovelace")), ["1", "2"])
        self.assertEqual(self.rows(f"?q={ref(3)}"), ["3"])
        self.assertEqual(self.rows("?q=100%25"), [])
        self.assertEqual(sorted(self.rows("?status=bogus&kind=bogus&sort=bogus")),
                         ["1", "2", "3"])

    def test_every_column_sorts_both_ways(self):
        # Type sorts by its shown name: Feature request, General question, Report a bug.
        expected = {"id": ["1", "2", "3"], "kind": ["2", "3", "1"],
                    "subject": ["1", "2", "3"], "user": ["1", "2", "3"]}
        for key, order in expected.items():
            self.assertEqual(self.rows(f"?sort={key}&dir=asc"), order, key)
            self.assertEqual(self.rows(f"?sort={key}&dir=desc"), order[::-1], key)
        for key in appmod.TICKET_SORTS:
            for direction in ("asc", "desc"):
                self.assertEqual(sorted(self.rows(f"?sort={key}&dir={direction}")),
                                 ["1", "2", "3"])
        self.assertEqual(self.rows("?sort=closed&dir=desc")[0], "2")  # open tickets last
        self.assertEqual(self.rows("?sort=closed&dir=asc")[0], "2")
        html = self.admin.get("/admin/support?sort=subject&dir=asc").get_data(as_text=True)
        self.assertIn('aria-sort="ascending"', html)
        self.assertIn("sort=subject&amp;dir=desc", html)

    def test_table_shows_dates_and_user(self):
        html = self.admin.get("/admin/support").get_data(as_text=True)
        for header in ("Opened", "Last updated", "Closed", "Type", "User", "Status"):
            self.assertIn(header, html)
        self.assertIn("Ada Lovelace", html)
        self.assertIn("bob@example.com", html)
        self.assertIn("Feature request", html)

    def test_paging_keeps_filters(self):
        with self.db() as conn:
            for i in range(30):
                conn.execute("INSERT INTO tickets (user_id, subject, kind) VALUES (1, ?, 'bug')",
                             (f"Bulk {i}",))
        html = self.admin.get("/admin/support?kind=bug&sort=id&dir=asc").get_data(as_text=True)
        self.assertIn("kind=bug", html.split('class="pager"', 1)[1])
        self.assertIn("sort=id", html.split('class="pager"', 1)[1])
        self.assertEqual(len(self.rows("?kind=bug&sort=id&dir=asc&page=2")), 6)

    def test_admin_page_needs_two_factor(self):
        self.app.config["ADMIN_REQUIRE_2FA"] = True
        self.assertEqual(self.admin.get("/admin/support").headers["Location"], "/settings")
        self.assertEqual(self.admin.post(f"/admin/support/{ref(1)}", data={"action": "close"})
                         .status_code, 302)
        self.assertEqual(self.ticket()["status"], "open")
