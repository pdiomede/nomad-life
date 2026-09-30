"""Ticket conversation as a chat: sides, colors by party, groups, days and screen reader text."""
import re
import unittest

import app as appmod
from tests.helpers import AppTestCase, ref


def msg(i, from_admin, at, body="x"):
    return {"id": i, "from_admin": from_admin, "created_at": at, "body": body}


class ChatRowsTests(unittest.TestCase):
    def test_groups_by_side_gap_and_day(self):
        rows = appmod.chat_rows([
            msg(1, 0, "2026-09-29 09:00:00"), msg(2, 0, "2026-09-29 09:05:00"),
            msg(3, 0, "2026-09-29 09:30:00"),  # over 10 minutes: a new group
            msg(4, 1, "2026-09-29 09:31:00"),  # other side: a new group
            msg(5, 1, "2026-09-30 00:01:00"),  # next day
        ], admin_view=False)
        self.assertEqual([(r["group_start"], r["group_end"]) for r in rows],
                         [(True, False), (False, True), (True, True), (True, True), (True, True)])
        self.assertEqual([r["new_day"] for r in rows], [True, False, False, False, True])
        self.assertEqual(rows[4]["day_label"], "30 Sep 2026")
        self.assertEqual(rows[0]["time"], "09:00")

    def test_sides_and_senders_follow_the_viewer(self):
        messages = [msg(1, 0, "2026-09-29 09:00:00"), msg(2, 1, "2026-09-29 09:01:00")]
        user = appmod.chat_rows(messages, admin_view=False)
        self.assertEqual([(r["mine"], r["party"], r["sender"]) for r in user],
                         [(True, "user", "You"), (False, "support", "Nomad Life support")])
        admin = appmod.chat_rows(messages, admin_view=True, owner_name="Ada Lovelace")
        self.assertEqual([(r["mine"], r["party"], r["sender"]) for r in admin],
                         [(False, "user", "Ada Lovelace"), (True, "support", "Nomad Life support")])
        self.assertEqual(appmod.chat_rows(messages, admin_view=True)[0]["sender"], "User")


class ChatPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("a@example.com")
        self.admin = self.app.test_client()
        self.signup("admin@example.com", client=self.admin)
        self.client.post("/support/new", data={"kind": "bug", "subject": "Map", "body": "مرحبا hello"})
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reply", "body": "On it"})

    def rows(self, html):
        return re.findall(r'<li class="chat-row (from-\w+) (is-\w+)', html)

    def test_user_view_puts_the_user_on_the_right(self):
        html = self.client.get(f"/support/{ref(1)}").get_data(as_text=True)
        self.assertEqual(self.rows(html), [("from-user", "is-mine"), ("from-support", "is-theirs")])
        self.assertIn('<span class="chat-key from-user">You</span>', html)

    def test_admin_view_puts_support_on_the_right(self):
        html = self.admin.get(f"/admin/support/{ref(1)}").get_data(as_text=True)
        self.assertEqual(self.rows(html), [("from-user", "is-theirs"), ("from-support", "is-mine")])
        self.assertIn("Nomad Life support (you)", html)

    def test_screen_reader_text_stays_outside_the_bubble(self):
        # Inside the bubble it would decide the direction of a right to left message.
        html = self.client.get(f"/support/{ref(1)}").get_data(as_text=True)
        self.assertIn('<div class="bubble" dir="auto">مرحبا hello</div>', html)
        self.assertRegex(html, r'<span class="visually-hidden">You, \d{4}-\d\d-\d\d \d\d:\d\d UTC:</span>')
        self.assertIn('class="chat-day" aria-hidden="true"', html)


if __name__ == "__main__":
    unittest.main()
