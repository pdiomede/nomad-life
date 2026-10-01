"""Regression tests for the bugs found in the review that became 1.5.8: writes that did not
repeat what they checked, links racing the 20 minute line, a stored sign in field, timing,
numbered email changes for the undo link, and layout."""
import os
import re
import sqlite3
import threading
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def other_connection(test):
    """A second connection to the test database, as another request or process would use."""
    conn = sqlite3.connect(test.app.config["DATABASE_PATH"])
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class ReviewFixTests(AppTestCase):
    """Writes that did not repeat what they checked, links racing the 20 minute line, a stored
    sign in field, timing, and a page too wide for phones."""

    def setUp(self):
        super().setUp()
        self.signup()
        self.age_emails()

    def flashes(self, resp):
        return re.findall(r'flash-text">(.*?)<', self.client.get(resp.headers["Location"])
                          .get_data(as_text=True)) if resp.status_code == 302 else []

    def meanwhile(self, sql, *args):
        """A side effect for a patched helper: run sql through a second connection first."""
        def run(*call_args, **_kwargs):
            with other_connection(self) as conn:
                conn.execute(sql, args)
        return run

    def test_deleting_the_account_does_not_win_over_a_reset_meanwhile(self):
        matches = appmod.password_matches
        reset = self.meanwhile("UPDATE users SET password_hash = 'reset-by-owner'")
        form = {"action": "delete", "confirm_delete": "2", "confirm_email": "a@example.com",
                "current_password": "password1"}
        with mock.patch.object(appmod, "password_matches",
                               side_effect=lambda h, p: (reset(), matches(h, p))[1]):
            html = self.client.post("/settings", data=form,
                                    follow_redirects=True).get_data(as_text=True)
        self.assertIn("so the account was not deleted", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        # Deleted meanwhile by an admin: nothing more to delete, and nothing recorded as if.
        owner = self.signup("b@example.com", client=self.app.test_client())
        gone = self.meanwhile("DELETE FROM users WHERE email = 'b@example.com'")
        with mock.patch.object(appmod, "password_matches",
                               side_effect=lambda h, p: (gone(), matches(h, p))[1]):
            resp = owner.post("/settings", data=dict(form, confirm_email="b@example.com"))
        self.assertEqual(resp.headers["Location"], "/login")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'account_deleted'").fetchone()[0], 0)

    def test_a_reset_link_does_not_apply_once_the_email_changed(self):
        self.client.post("/logout")
        self.client.post("/forgot", data={"email": "a@example.com"})
        link = self.last_link("a@example.com", kind="reset")
        hash_password = appmod.hash_password
        change = self.meanwhile("UPDATE users SET email = 'thief@example.com'")
        with mock.patch.object(appmod, "hash_password",
                               side_effect=lambda p: (change(), hash_password(p))[1]):
            html = self.client.post(link, data={"password": "newpass123", "confirm": "newpass123"},
                                    follow_redirects=True).get_data(as_text=True)
        self.assertIn("This reset link is no longer valid. Please request a new one.", html)
        with self.db() as conn:
            stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertTrue(appmod.check_password_hash(stored, "password1"))
        # A sign up removed at its 20 minutes while the password was hashed: the link is no
        # longer valid, it was never "already used".
        self.client.post("/signup", data={"email": "n@example.com", "password": "password1",
                                          "confirm": "password1"})
        self.age_emails()
        self.client.post("/forgot", data={"email": "n@example.com"})
        link = self.last_link("n@example.com", kind="reset")
        purge = self.meanwhile("DELETE FROM users WHERE email = 'n@example.com'")
        with mock.patch.object(appmod, "hash_password",
                               side_effect=lambda p: (purge(), hash_password(p))[1]):
            html = self.client.post(link, data={"password": "newpass123", "confirm": "newpass123"},
                                    follow_redirects=True).get_data(as_text=True)
        self.assertIn("This reset link is no longer valid.", html)
        self.assertNotIn("already been used", html)

    def test_a_huge_email_at_sign_in_is_not_stored(self):
        self.client.post("/logout")
        self.client.post("/login", data={"email": "x" * 400_000 + "@example.com",
                                         "password": "password1"})
        with self.db() as conn:
            longest = conn.execute("SELECT MAX(length(key)) FROM auth_events").fetchone()[0]
        self.assertLessEqual(longest, 300)  # was 400,018 characters per failed attempt

    def test_an_email_change_is_not_started_over_a_reset_meanwhile(self):
        matches = appmod.password_matches
        reset = self.meanwhile("UPDATE users SET password_hash = 'reset-by-owner', "
                               "pending_email = NULL")
        with mock.patch.object(appmod, "password_matches",
                               side_effect=lambda h, p: (reset(), matches(h, p))[1]):
            html = self.client.post("/settings", data={
                "action": "email", "email": "thief@example.com", "current_password": "password1"},
                follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("We sent a confirmation link", html)
        self.assertNotIn("thief@example.com", [m["to"] for m in self.outbox])
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT pending_email FROM users").fetchone()[0])

    def test_forgot_password_does_nothing_more_for_a_registered_address_until_answered(self):
        self.client.post("/logout")
        self.app.config["EMAIL_IN_BACKGROUND"] = True
        sent = len(self.outbox)
        with self.db() as conn:
            before = conn.execute("SELECT email_sent_at FROM users").fetchone()[0]
        resp = self.client.post("/forgot", data={"email": "a@example.com"})
        # Answered with the account untouched: no lookup, cooldown or email yet, as for an
        # unknown address (claiming and rendering it made the answer slower).
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT email_sent_at FROM users").fetchone()[0], before)
        self.assertEqual(len(self.outbox), sent)
        resp.close()  # the server has sent the answer
        for thread in threading.enumerate():
            if thread.name == "email":
                thread.join(5)
        self.assertIn("/reset/", self.outbox[-1]["text"])

    def test_signing_up_again_does_not_sign_out_an_account_confirmed_meanwhile(self):
        self.app.test_client().post("/signup", data={"email": "n@example.com", "password": "password1",
                                          "confirm": "password1"})
        link = self.last_link("n@example.com")
        self.age_emails()
        hash_password = appmod.hash_password
        owner = self.app.test_client()
        with mock.patch.object(appmod, "hash_password",
                               side_effect=lambda p: (owner.post(link), hash_password(p))[1]):
            self.app.test_client().post("/signup", data={
                "email": "n@example.com", "password": "password2", "confirm": "password2"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT session_version FROM users WHERE email = "
                                          "'n@example.com'").fetchone()[0], 0)
        self.assertNotIn("Finish setting up your Nomad Life account",
                         [m["subject"] for m in self.outbox])

    def test_undo_after_changing_back_promises_no_email(self):
        self.client.post("/settings", data={"action": "email", "email": "b@example.com",
                                            "current_password": "password1"})
        self.client.post(self.last_link("b@example.com", kind="account/email"))
        undo = self.last_link("a@example.com", kind="account/email/undo")
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "a@example.com",
                                            "current_password": "password1"})
        self.client.post(self.last_link("a@example.com", kind="account/email"))
        sent = len(self.outbox)
        html = self.app.test_client().post(undo, follow_redirects=True).get_data(as_text=True)
        self.assertIn("The account already uses a@example.com again.", html)
        self.assertNotIn("Use the link we emailed", html)  # none was: nothing was undone
        self.assertEqual(len(self.outbox), sent)

    def test_a_receipt_cannot_be_renamed_to_invisible_characters(self):
        self.new_year(2027)
        self.upload("/year/2027/base", "lease.pdf", b"%PDF-1.4\n%%EOF\n")
        with self.db() as conn:
            doc = conn.execute("SELECT id FROM documents").fetchone()[0]
        for typed in ("ㅤ", "​​", "⠀ ⠀", "ㅤ.pdf"):
            html = self.client.post(f"/documents/{doc}/edit", data={"name": typed, "kind": "other"},
                                    follow_redirects=True).get_data(as_text=True)
            self.assertIn("Please enter a name for the document.", html, repr(typed))
            with self.db() as conn:
                self.assertEqual(conn.execute("SELECT original_name FROM documents")
                                 .fetchone()[0], "lease.pdf", repr(typed))

    def test_the_email_change_pages_wrap_long_addresses(self):
        # Both addresses are in a paragraph of the form, which did not wrap: a 41 character
        # address made the page scroll sideways at 320 px.
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as fh:
            self.assertIn(".auth-card form p { overflow-wrap: anywhere; }", fh.read())
        for name in ("confirm_email.html", "revert_email.html"):
            with open(os.path.join(ROOT, "templates", "auth", name), encoding="utf-8") as fh:
                self.assertRegex(fh.read(), r'<form method="post"[^>]*>\s*<input[^>]*>\s*<p ')


class AdminEnableTests(AppTestCase):
    def test_enabling_a_blocked_sign_up_goes_back_to_the_list(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("admin@example.com")
        with self.db() as conn:
            uid = conn.execute("INSERT INTO users (email, password_hash, disabled, created_at) "
                               "VALUES ('bot@example.com', 'x', 1, datetime('now', '-2 hours'))"
                               ).lastrowid
        resp = self.client.post(f"/admin/users/{uid}", data={"action": "enable", "q": "bot"})
        # Never confirmed: enabled, its 20 minutes are long over, so it goes (and its page).
        self.assertEqual(resp.headers["Location"], "/admin?q=bot")
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn("bot@example.com is no longer blocked. The account was never confirmed, "
                      "so it was removed: the address can sign up again.", html)
        self.assertNotIn("can sign in again", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users WHERE id = ?",
                                          (uid,)).fetchone()[0], 0)


class UndoChainTests(AppTestCase):
    """Email changes are numbered, so the owner's undo link survives the intruder changing the
    address again, and the intruder's own undo links cannot take the account back after it."""

    def setUp(self):
        super().setUp()
        self.signup()
        self.address = "a@example.com"
        self.intruder = self.client  # signed in with the password the intruder learned

    def change(self, new, password="password1"):
        """Change the address from the intruder's browser; returns the undo link the address
        it left received."""
        self.age_emails()
        self.intruder.post("/settings", data={"action": "email", "email": new,
                                              "current_password": password})
        self.intruder.post(self.last_link(new, kind="account/email"))
        left, self.address = self.address, new
        return self.last_link(left, kind="account/email/undo")

    def email(self):
        with self.db() as conn:
            return conn.execute("SELECT email FROM users").fetchone()[0]

    def test_the_owners_undo_works_after_the_address_changed_again(self):
        owner_undo = self.change("b@evil.example")
        intruder_undo = self.change("c@evil.example")  # this was enough to keep the owner out
        html = self.app.test_client().post(owner_undo, follow_redirects=True).get_data(as_text=True)
        self.assertIn("The account uses a@example.com again", html)
        self.assertEqual(self.email(), "a@example.com")
        self.assertEqual(self.intruder.get("/settings").status_code, 302)  # signed out
        # The undo of the later change is void: it cannot take the account from the owner.
        html = self.app.test_client().post(intruder_undo, follow_redirects=True).get_data(as_text=True)
        self.assertIn("This link is no longer valid.", html)
        self.assertEqual(self.email(), "a@example.com")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT detail FROM audit_log WHERE event = "
                                          "'email_reverted'").fetchall()[0][0],
                             "c@evil.example to a@example.com")

    def test_the_intruder_undoing_first_does_not_take_the_owners_way_back(self):
        owner_undo = self.change("b@evil.example")
        later_undo = self.change("c@evil.example")
        self.app.test_client().post(later_undo)  # back to b@evil.example, password locked
        self.assertEqual(self.email(), "b@evil.example")
        self.address = "b@evil.example"
        self.intruder.post(self.last_link("b@evil.example", kind="reset"),
                           data={"password": "intruder1", "confirm": "intruder1"})
        self.intruder.post("/login", data={"email": "b@evil.example", "password": "intruder1"})
        third_undo = self.change("d@evil.example", password="intruder1")
        self.assertEqual(self.email(), "d@evil.example")
        self.app.test_client().post(owner_undo)
        self.assertEqual(self.email(), "a@example.com")
        html = self.app.test_client().post(third_undo, follow_redirects=True).get_data(as_text=True)
        self.assertIn("This link is no longer valid.", html)
        self.assertEqual(self.email(), "a@example.com")
        # Pressed again, the owner's link says it is done, and that the password link went out.
        html = self.app.test_client().post(owner_undo, follow_redirects=True).get_data(as_text=True)
        self.assertIn("The account already uses a@example.com again. Use the link we emailed "
                      "there to choose a new password.", html)

    def test_the_undo_page_does_not_name_the_address_it_moved_on_to(self):
        owner_undo = self.change("b@evil.example")
        self.change("c@evil.example")
        html = self.app.test_client().get(owner_undo).get_data(as_text=True)
        self.assertIn("instead of the address it uses now (it was changed again since).", html)
        self.assertNotIn("c@evil.example", html)
        self.assertEqual(self.email(), "c@evil.example")  # a GET changes nothing
        self.assertIn("also if the address is changed again meanwhile",
                      [m for m in self.outbox if m["to"] == "a@example.com"][-1]["text"])

    def test_undo_links_from_before_the_numbering(self):
        self.change("b@example.com")
        with self.app.test_request_context():
            token = appmod.serializer("email-revert").dumps(
                {"uid": 1, "old": "a@example.com", "new": "b@example.com"})
        link = f"/account/email/undo/{token}"
        self.assertIn(b"Undo the change", self.app.test_client().get(link).data)
        self.change("c@example.com")  # as before: valid only while the account uses b@
        html = self.app.test_client().post(link, follow_redirects=True).get_data(as_text=True)
        self.assertIn("This link is no longer valid.", html)
        self.assertEqual(self.email(), "c@example.com")

    def test_numbered_changes_are_pruned_once_their_links_expired(self):
        self.change("b@example.com")
        with self.db() as conn:
            conn.execute("UPDATE email_changes SET created_at = datetime('now', '-9 days')")
            conn.execute("INSERT INTO email_changes (user_id, old_email, new_email, created_at) "
                         "VALUES (1, 'x@example.com', 'b@example.com', datetime('now', '-2 days'))")
        with mock.patch.object(appmod, "PURGE_EVERY_SECONDS", 0):
            self.app.test_client().get("/login")
        with self.db() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT old_email FROM email_changes")],
                             ["x@example.com"])


class StaleCancelAndLongNameTests(AppTestCase):
    def test_cancel_on_a_page_left_open_after_the_change_was_confirmed(self):
        self.signup()
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "b@example.com",
                                            "current_password": "password1"})
        self.client.get("/settings")  # the page with "Cancel this change", left open
        self.app.test_client().post(self.last_link("b@example.com", kind="account/email"))
        html = self.client.post("/settings", data={"action": "cancel_email"},
                                follow_redirects=True).get_data(as_text=True)
        # It said nothing at all, on a page that now showed another address.
        self.assertIn("No email change is waiting any more. Your email address is "
                      "b@example.com.", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT email FROM users").fetchone()[0],
                             "b@example.com")

    def test_a_long_name_wraps_in_the_ticket_chat(self):
        # A first name of one 60 letter word made the admin ticket page 453 px wide at 320.
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".chat-key { display: inline-flex; align-items: center; gap: 6px; "
                      "min-width: 0; overflow-wrap: anywhere; }", css)
        self.assertIn('.chat-key::before { content: ""; flex: none;', css)
        self.assertIn(".bubble-meta { margin: 4px 6px 0; color: var(--muted); "
                      "overflow-wrap: anywhere; }", css)
