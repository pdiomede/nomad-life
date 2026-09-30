"""Regression tests for the bugs found in the 1.3.0 review of support tickets."""
import os
import re
import threading

import app as appmod
from tests.helpers import AppTestCase


class Base(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com", "boss@example.com"})
        self.signup()
        self.admin = self.signup("admin@example.com", client=self.app.test_client())

    def open_ticket(self, subject="Help", body="Please help", kind="question", client=None):
        return (client or self.client).post("/support/new", data={
            "kind": kind, "subject": subject, "body": body})

    def ticket(self, ticket_id=1):
        with self.db() as conn:
            return conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()

    def messages(self, ticket_id=1):
        with self.db() as conn:
            return conn.execute("SELECT from_admin, body FROM ticket_messages WHERE ticket_id = ? "
                                "ORDER BY id", (ticket_id,)).fetchall()


class AdminFixTests(Base):
    def test_odd_ticket_numbers_never_crash_the_search(self):
        self.open_ticket()
        for q in ("#²", "#99999999999999999999", "#١", "#", "##1"):
            resp = self.admin.get("/admin/support", query_string={"q": q})
            self.assertEqual(resp.status_code, 200, q)
        html = self.admin.get("/admin/support", query_string={"q": "#1"}).get_data(as_text=True)
        self.assertIn("/admin/support/1", html)

    def test_answer_is_not_saved_on_a_ticket_closed_meanwhile(self):
        self.open_ticket()
        boss = self.signup("boss@example.com", client=self.app.test_client())
        real = appmod.db.transaction
        closed = []

        def close_first():
            if not closed:
                closed.append(True)
                boss.post("/admin/support/1", data={"action": "close"})
            return real()

        self.outbox.clear()
        appmod.db.transaction = close_first
        try:
            resp = self.admin.post("/admin/support/1", data={"action": "reply",
                                                             "body": "Here is the fix"})
        finally:
            appmod.db.transaction = real
        self.assertEqual(resp.status_code, 400)
        html = resp.get_data(as_text=True)
        self.assertIn("closed meanwhile", html)
        self.assertIn(">Here is the fix</textarea>", html)  # the draft is not lost
        self.assertEqual(self.ticket()["status"], "closed")
        self.assertEqual([m["body"] for m in self.messages()], ["Please help"])
        self.assertEqual([m["subject"] for m in self.outbox if m["to"] == "a@example.com"],
                         ["Your support ticket #1 was closed"])

    def test_reopen_and_send_from_a_closed_ticket(self):
        self.open_ticket()
        self.admin.post("/admin/support/1", data={"action": "close"})
        html = self.admin.get("/admin/support/1").get_data(as_text=True)
        self.assertIn("Reopen and send", html)
        self.admin.post("/admin/support/1", data={"action": "reopen_reply", "body": "Answer"})
        self.assertEqual(self.ticket()["status"], "open")
        self.assertEqual(self.messages()[-1]["body"], "Answer")

    def test_accented_names_are_found(self):
        self.client.post("/settings", data={"action": "profile", "first_name": "Élodie",
                                            "last_name": "Ångström"})
        self.open_ticket()
        for q in ("Élodie", "élodie", "ÉLODIE", "ångström"):
            html = self.admin.get("/admin/support", query_string={"q": q}).get_data(as_text=True)
            self.assertIn("/admin/support/1", html, q)

    def test_searching_for_the_word_all_is_kept_in_links(self):
        self.client.post("/settings", data={"action": "profile", "first_name": "Allan",
                                            "last_name": "Ball"})
        self.open_ticket()
        html = self.admin.get("/admin/support?q=all").get_data(as_text=True)
        self.assertIn("q=all&amp;sort=id", html)
        self.assertIn('value="all"', html)

    def test_times_are_utc_and_labelled(self):
        self.open_ticket()
        with self.db() as conn:
            conn.execute("UPDATE tickets SET created_at = '2026-09-30 12:43:00'")
        for client, path in ((self.admin, "/admin/support"), (self.client, "/support"),
                             (self.client, "/support/1")):
            self.assertIn("2026-09-30 12:43 UTC", client.get(path).get_data(as_text=True), path)

    def test_user_messages_have_their_own_audit_label(self):
        self.open_ticket()
        self.client.post("/support/1", data={"action": "reply", "body": "More details"})
        html = self.admin.get("/admin").get_data(as_text=True)
        self.assertIn("Support ticket message from the user", html)
        self.assertNotIn("Support ticket answered", html)

    def test_type_sorts_by_name_and_messages_sort(self):
        self.open_ticket(kind="bug")
        self.open_ticket(kind="feature")
        self.client.post("/support/1", data={"action": "reply", "body": "x"})
        rows = lambda q: re.findall(r'href="/admin/support/(\d+)" dir="auto"',
                                    self.admin.get("/admin/support" + q).get_data(as_text=True))
        self.assertEqual(rows("?sort=kind&dir=asc"), ["2", "1"])
        self.assertEqual(rows("?sort=messages&dir=desc"), ["1", "2"])
        self.assertIn("sort=messages", self.admin.get("/admin/support").get_data(as_text=True))


class UserFixTests(Base):
    def test_writing_on_a_closed_ticket_respects_the_open_limit(self):
        self.open_ticket()
        self.client.post("/support/1", data={"action": "close"})
        with self.db() as conn:
            for _ in range(appmod.OPEN_TICKETS_MAX):
                conn.execute("INSERT INTO tickets (user_id, subject) VALUES (1, 'x')")
        html = self.client.post("/support/1", data={"action": "reply", "body": "again"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("The ticket stays closed", html)
        self.assertEqual(self.ticket()["status"], "closed")
        self.assertEqual(self.messages()[-1]["body"], "again")  # the message is kept

    def test_rate_limit_follows_the_account_not_the_email(self):
        for i in range(5):
            self.open_ticket(subject=f"T{i}")
        with self.db() as conn:
            conn.execute("UPDATE users SET email = 'renamed@example.com' WHERE id = 1")
        self.assertEqual(self.open_ticket(subject="T6").status_code, 400)

    def test_closing_twice_says_so(self):
        self.open_ticket()
        self.client.post("/support/1", data={"action": "close"})
        html = self.client.post("/support/1", data={"action": "close"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("This ticket was already closed.", html)

    def test_header_label_counts_tickets(self):
        self.open_ticket()
        for i in range(3):
            self.admin.post("/admin/support/1", data={"action": "reply", "body": f"r{i}"})
        html = self.client.get("/support").get_data(as_text=True)
        self.assertIn('aria-label="Contact support (1 ticket with a new reply)"', html)

    def test_invisible_subjects_and_bodies_are_refused(self):
        for subject, body in (("ㅤㅤ", "ok"), ("ok", "ㅤ ⠀"), ("ok", "‍")):
            self.assertEqual(self.open_ticket(subject=subject, body=body).status_code, 400)
        self.assertIsNone(self.ticket())

    def test_rtl_subject_keeps_the_number_first(self):
        self.open_ticket(subject="مرحبا hello")
        html = self.client.get("/support").get_data(as_text=True)
        self.assertIn("#1 <bdi>مرحبا hello</bdi></a>", html)


class LayoutRuleTests(AppTestCase):
    def test_css_rules_for_phones(self):
        css = open(os.path.join(appmod.BASE_DIR, "static", "css", "style.css"),
                   encoding="utf-8").read()
        self.assertIn(".icon-btn { flex: none; }", css)
        self.assertIn(".ticket-close .btn { white-space: normal;", css)
        self.assertIn(".page-head > div { min-width: 0; }", css)
        self.assertIn(".support-table td { vertical-align: top; overflow-wrap: break-word; }", css)
        self.assertIn(".ticket-from { overflow-wrap: anywhere; }", css)
