"""2.1.0: the bugs found by the review of Delegates before release."""

import os
from unittest import mock

import app as appmod
from tests.test_delegates import DelegateCase
from tests.test_fixes_158 import other_connection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


class ServerFixTests(DelegateCase):
    def test_1_an_invite_racing_an_email_undo_invites_nobody(self):
        # The password checked at the start must still be the account's under the write lock:
        # an undo meanwhile removed the delegates and locked the password.
        real = appmod.take_attempt

        def meanwhile(kind, *args, **kwargs):
            if kind == "delegate_to":
                conn = other_connection(self)
                with conn:
                    conn.execute("UPDATE users SET password_hash = 'locked' WHERE email = "
                                 "'a@example.com'")
                conn.close()
            return real(kind, *args, **kwargs)

        with mock.patch.object(appmod, "take_attempt", side_effect=meanwhile):
            page = self.text(self.invite("intruder@example.com"))
        self.assertIn("changed elsewhere in the meantime", page)
        self.assertEqual(self.rows(), [])

    def test_2_a_waiting_invitation_does_not_trap_a_new_account(self):
        self.invite()
        self.assertIn("/year/new", self.d.get("/app").headers["Location"])
        self.accept()
        self.assertTrue(self.d.get("/app").headers["Location"].endswith("/shared"))
        page = self.text(self.d.get("/shared"))
        self.assertIn('href="/year/new"', page)
        self.assertIn("Start your own year", page)
        self.assertNotIn("Back to my account", page)
        banner = self.text(self.d.get(f"/shared/{self.share_id()}/year/2026"))
        self.assertIn('<a class="btn btn-ghost btn-sm tip tip-end share-btn" href="/year/new" data-tip="Track your own days in your own account"><span class="btn-icon icon-plus" aria-hidden="true"></span>Start your own year</a>', banner)
        # With a year of their own, Back goes home.
        self.new_year(2026, client=self.d)
        self.assertIn("Back to my account", self.text(self.d.get("/shared")))

    def test_3_one_access_per_account_and_owner(self):
        self.invite("d@example.com")
        self.invite("d2@example.com")
        with self.db() as conn:
            first, second = [r["id"] for r in conn.execute("SELECT id FROM delegates ORDER BY id")]
        self.d.post(f"/shared/{first}/accept")
        with self.db() as conn:
            conn.execute("UPDATE users SET email = 'd2@example.com' WHERE email = 'd@example.com'")
        self.d.post("/login", data={"email": "d2@example.com", "password": "password1"})
        page = self.text(self.d.post(f"/shared/{second}/accept", follow_redirects=True))
        self.assertIn("You can already view", page)
        self.assertEqual([r["id"] for r in self.rows()], [first])
        # Remove now ends the access for good.
        self.client.post("/settings", data={"action": "delegate_remove", "delegate_id": first,
                                            "confirm_delete": "2"})
        self.assertEqual(self.d.get(f"/shared/{first}/year/2026").status_code, 404)
        self.assertEqual(self.d.get(f"/shared/{second}/year/2026").status_code, 404)

    def test_4_moving_to_an_invited_address_drops_that_invitation(self):
        self.invite("x@example.com")
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "x@example.com",
                                            "current_password": "password1"})
        self.client.post(self.last_link("x@example.com", kind="account/email"))
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT email FROM users WHERE email = "
                                          "'x@example.com'").fetchone()[0], "x@example.com")
        self.assertEqual(self.rows(), [])

    def test_5_a_waiting_invitation_shows_the_email_only(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET first_name = 'Nomad Life Security', last_name = "
                         "'call 555' WHERE email = 'a@example.com'")
        self.invite()
        page = self.text(self.d.get("/shared"))
        self.assertIn("a@example.com", page)
        self.assertNotIn("Nomad Life Security", page)
        resp, _sid = self.accept()
        self.assertIn("You can now view the account of a@example.com.", self.text(resp))
        # Once accepted, the owner is named in full.
        self.assertIn("Nomad Life Security call 555 (a@example.com)", self.text(self.d.get("/shared")))

    def test_6_one_address_gets_a_few_invitations_at_most(self):
        limit = appmod.LIMITS[("delegate_to", "email")]
        for _ in range(limit):
            self.invite()
            self.client.post("/settings", data={"action": "delegate_remove",
                                                "delegate_id": self.share_id(),
                                                "confirm_delete": "2"})
        sent = len(self.outbox)
        page = self.text(self.invite())
        self.assertIn("was sent several invitations", page)
        self.assertEqual(len(self.outbox), sent)
        self.assertEqual(self.rows(), [])
        # Another address is not held back.
        self.assertIn("p@example.com was invited", self.text(self.invite("p@example.com")))

    def test_13_an_invitee_without_an_account_is_sent_to_sign_up(self):
        self.invite("new@example.com")
        mail = self.outbox[-1]
        self.assertIn("Create your free account", mail["html"])
        self.assertIn('href="http://localhost:5050/signup"', mail["html"].split("Create your free account")[0][-400:])
        self.assertIn("/signup", mail["text"])
        self.assertIn("/shared", mail["text"])
        self.invite("d@example.com")
        mail = self.outbox[-1]
        self.assertIn("Open Shared with you", mail["html"])
        self.assertNotIn("/signup", mail["text"])


class TextFixTests(DelegateCase):
    def test_7_privacy_landing_and_docs_mention_delegates(self):
        privacy = self.text(self.app.test_client().get("/privacy"))
        self.assertIn("<strong>Delegates:</strong>", privacy)
        landing = self.text(self.app.test_client().get("/"))
        self.assertNotIn("Only you can see your stays", landing)
        self.assertIn("the people you choose to invite", landing)
        docs = self.text(self.app.test_client().get("/docs"))
        self.assertNotIn("and only you see them", docs)

    def test_8_remove_and_stop_viewing_are_not_called_a_permanent_delete(self):
        self.invite()
        page = self.text(self.client.get("/settings"))
        self.assertIn('data-confirm-verb="Remove"', page)
        self.assertIn('data-confirm-final="The invitation to d@example.com stops working.', page)
        _resp, sid = self.accept()
        self.assertIn('data-confirm-verb="Stop viewing"', self.text(self.d.get("/shared")))
        js = read("static/js/theme.js")
        self.assertIn('pending.getAttribute("data-confirm-verb")', js)
        self.assertIn('"Yes, " + verb.toLowerCase()', js)

    def test_9_and_12_overlaps_on_a_shared_view(self):
        self.add_movement(2026, "Madrid", "Spain", "2026-03-05", "2026-03-12")
        self.invite()
        _resp, sid = self.accept()
        own = self.text(self.client.get("/year/2026"))
        shared = self.text(self.d.get(f"/shared/{sid}/year/2026"))
        self.assertIn('data-overlap-key="2026:', own)
        self.assertIn(f'data-overlap-key="s{sid}-2026:', shared)
        self.assertIn("fix the dates", own)
        self.assertNotIn("fix the dates", shared)

    def test_12_not_in_google_calendar_does_not_ask_a_delegate_to_delete(self):
        self.invite()
        _resp, sid = self.accept()
        with self.db() as conn:
            conn.execute("UPDATE years SET sync_mode = 'auto'")
            conn.execute("UPDATE movements SET google_event_id = 'g1', sync_missing = 1")
        page = self.text(self.d.get(f"/shared/{sid}/year/2026"))
        self.assertIn("Not in Google Calendar", page)
        self.assertNotIn("delete it when you no longer need it", page)

    def test_10_the_can_view_tag_has_its_color(self):
        css = read("static/css/style.css")
        self.assertIn(".tag.tag-ok { background: var(--success-bg); color: var(--success-text);", css)

    def test_11_the_alert_footer_says_what_it_covers(self):
        self.invite()
        self.accept()
        alert = self.outbox[-1]
        self.assertIn("when someone accepts your invitation to view your account", alert["text"])
        self.assertNotIn("or delegates.", alert["text"])
