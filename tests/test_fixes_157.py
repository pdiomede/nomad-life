"""Regression tests for the bugs found in the 1.5.7 review of the admin page and the whole app,
and the shorter overlap texts."""
import os
import re
import sqlite3
import threading
import time
from unittest import mock

import itsdangerous.timed

import app as appmod
from tests.helpers import AppTestCase, ref


class AdminTicketGoneTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("admin@example.com")
        with self.db() as conn:
            self.uid = conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                                    "VALUES ('u@example.com', 'x', '2026-01-01')").lastrowid
            conn.execute("INSERT INTO tickets (user_id, subject) VALUES (?, 'Help')",
                         (self.uid,))
        self.url = f"/admin/support/{ref(1)}?status=open"

    def delete_owner(self):
        conn = sqlite3.connect(self.app.config["DATABASE_PATH"])
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM users WHERE id = ?", (self.uid,))
        conn.commit()
        conn.close()

    def test_answer_on_a_ticket_deleted_while_the_page_was_open(self):
        self.client.get(self.url)
        self.delete_owner()
        resp = self.client.post(self.url, data={"action": "reply", "body": "Hello"})
        self.assertEqual(resp.headers["Location"], "/admin/support?status=open")
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn(f"Ticket #{ref(1)} no longer exists (its account was deleted)", html)

    def test_answer_on_a_ticket_deleted_while_it_was_being_sent(self):
        # Deleted between the page lookup and the write lock: this was a server error.
        clean = appmod.clean_ticket_body

        def delete_meanwhile(text):
            self.delete_owner()
            return clean(text)

        with mock.patch.object(appmod, "clean_ticket_body", side_effect=delete_meanwhile):
            resp = self.client.post(self.url, data={"action": "reply", "body": "Hello"})
        self.assertEqual(resp.status_code, 302)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ticket_messages").fetchone()[0], 0)


class OverlapTextTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2027)

    def add(self, city, start, end):
        return self.client.post("/year/2027/movements/new", data={
            "city": city, "country": "Spain", "start_date": start, "end_date": end},
            follow_redirects=True).get_data(as_text=True)

    def test_the_warning_on_save_is_short(self):
        self.add("Tirana", "2027-01-01", "2027-01-10")
        self.add("Kabul", "2027-01-08", "2027-01-14")
        html = self.add("Rome", "2027-01-08", "2027-01-09")
        self.assertIn("This stay shares days with Tirana (2 days) and Kabul (2 days). Each shared "
                      "day counts once, for the stay that started later (starting the same day: "
                      "the shorter one; same dates: the newest). If you were not in both places, "
                      "check the dates.", html)
        html = self.add("Oslo", "2027-01-12", "2027-01-13")
        self.assertIn("This stay shares days with Kabul (2 days). Each shared day counts once, "
                      "for the stay that started later. If you were not in both places", html)

    def test_the_report_is_in_the_page_font(self):
        with open("static/css/style.css", encoding="utf-8") as fh:
            css = fh.read()
        rule = css[css.index(".overlap-report {"):]
        self.assertNotIn("monospace", rule[:rule.index("}")])


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def other_connection(test):
    conn = sqlite3.connect(test.app.config["DATABASE_PATH"])
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class AccountFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.age_emails()

    def change_email(self, new):
        self.client.post("/settings", data={"action": "email", "email": new,
                                            "current_password": "password1"})
        return self.last_link(new, kind="account/email")

    def test_undo_of_an_email_change_ends_the_old_password(self):
        # Whoever changed the address knew the password: it signed straight in again.
        self.client.post(self.change_email("thief@example.com"))
        undo = self.last_link("a@example.com", kind="account/email/undo")
        self.app.test_client().post(undo)
        resp = self.app.test_client().post("/login", data={"email": "a@example.com",
                                                           "password": "password1"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Invalid email or password.", resp.data)
        # The emailed link sets the new one.
        owner = self.app.test_client()
        owner.post(self.last_link("a@example.com", kind="reset"),
                   data={"password": "password2", "confirm": "password2"})
        resp = owner.post("/login", data={"email": "a@example.com", "password": "password2"})
        self.assertEqual(resp.status_code, 302)

    def test_opening_the_email_change_link_changes_nothing(self):
        link = self.change_email("typo@example.org")
        scanner = self.app.test_client()  # a company mail scanner opens every link
        html = scanner.get(link).get_data(as_text=True)
        self.assertIn("Confirm my new email</button>", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT email FROM users").fetchone()[0], "a@example.com")
        scanner.post(link)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT email FROM users").fetchone()[0],
                             "typo@example.org")

    def test_an_old_confirmation_link_opened_again(self):
        link = self.last_link("a@example.com")
        later = time.time() + 30 * 60
        with mock.patch.object(itsdangerous.timed.time, "time", return_value=later):
            html = self.app.test_client().get(link, follow_redirects=True).get_data(as_text=True)
        self.assertIn("This account is already confirmed. Please sign in.", html)
        self.assertNotIn("the account was removed", html)

    def test_a_reset_meanwhile_wins_over_a_password_change(self):
        hash_password = appmod.hash_password

        def reset_meanwhile(password):
            with other_connection(self) as conn:  # the owner's reset by email finishes first
                conn.execute("UPDATE users SET password_hash = ?", (hash_password("owner-pass1"),))
            return hash_password(password)

        with mock.patch.object(appmod, "hash_password", side_effect=reset_meanwhile):
            html = self.client.post("/settings", data={
                "action": "password", "current_password": "password1", "password": "attacker1x",
                "confirm": "attacker1x"}, follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("Your password has been changed", html)
        resp = self.app.test_client().post("/login", data={"email": "a@example.com",
                                                           "password": "owner-pass1"})
        self.assertEqual(resp.status_code, 302)

    def test_two_factor_is_not_turned_on_over_a_reset(self):
        html = self.client.get("/settings").get_data(as_text=True)
        secret = re.search(r'<code class="tfa-key">([\w ]+)</code>', html).group(1).replace(" ", "")
        code = appmod.totp_code(secret, int(time.time() // appmod.TOTP_STEP_SECONDS))
        matches = appmod.password_matches

        def reset_meanwhile(pw_hash, password):
            with other_connection(self) as conn:
                conn.execute("UPDATE users SET password_hash = 'reset-by-owner'")
            return matches(pw_hash, password)

        with mock.patch.object(appmod, "password_matches", side_effect=reset_meanwhile):
            html = self.client.post("/settings", data={
                "action": "2fa_enable", "code": code, "current_password": "password1"},
                follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("Two-factor sign in is on.", html)
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT totp_secret FROM users").fetchone()[0])

    def test_settings_actions_of_an_account_deleted_meanwhile(self):
        hash_password = appmod.hash_password

        def delete_meanwhile(password):
            with other_connection(self) as conn:
                conn.execute("DELETE FROM users")
            return hash_password(password)

        with mock.patch.object(appmod, "hash_password", side_effect=delete_meanwhile):
            resp = self.client.post("/settings", data={
                "action": "password", "current_password": "password1", "password": "password2",
                "confirm": "password2"})
        self.assertEqual(resp.status_code, 302)  # was a 500

    def test_sign_in_of_an_account_deleted_during_the_password_check(self):
        self.client.post("/logout")
        matches = appmod.password_matches

        def delete_meanwhile(pw_hash, password):
            with other_connection(self) as conn:  # the fake account cleanup, for example
                conn.execute("DELETE FROM users")
            return matches(pw_hash, password)

        with mock.patch.object(appmod, "password_matches", side_effect=delete_meanwhile):
            resp = self.client.post("/login", data={"email": "a@example.com",
                                                    "password": "password1"},
                                    follow_redirects=True)
        self.assertEqual(resp.status_code, 200)  # was a 500
        self.assertIn(b"Invalid email or password.", resp.data)

    def test_support_message_of_an_account_deleted_meanwhile(self):
        self.client.post("/support/new", data={"kind": "question", "subject": "Hi",
                                               "body": "Hello"})
        clean = appmod.clean_ticket_body

        def delete_meanwhile(text):
            with other_connection(self) as conn:
                conn.execute("DELETE FROM users")
            return clean(text)

        with mock.patch.object(appmod, "clean_ticket_body", side_effect=delete_meanwhile):
            resp = self.client.post(f"/support/{ref(1)}", data={"action": "reply",
                                                                "body": "More"})
        self.assertEqual(resp.status_code, 302)  # was a 500
        self.assertEqual(resp.headers["Location"], "/login")


class ConfirmRaceTests(AppTestCase):
    def test_confirming_an_account_removed_meanwhile(self):
        self.client.post("/signup", data={"email": "n@example.com", "password": "password1",
                                          "confirm": "password1"})
        link = self.last_link("n@example.com")
        fingerprint = appmod.password_fingerprint

        def purge_meanwhile(pw_hash):
            with other_connection(self) as conn:  # its 20 minutes ran out in another request
                conn.execute("DELETE FROM users")
            return fingerprint(pw_hash)

        with mock.patch.object(appmod, "password_fingerprint", side_effect=purge_meanwhile):
            html = self.client.post(link, follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("Your email is confirmed", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'account_confirmed'").fetchone()[0], 0)


class WorkspaceFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def test_a_year_created_twice_opens_it(self):
        data = {"year": "2027", "base_city": "Lisbon", "base_country": "Portugal"}
        self.client.post("/year/new", data=data)
        resp = self.client.post("/year/new", data=data)
        self.assertEqual(resp.headers["Location"], "/year/2027")
        self.assertIn('data-submit-once', self.client.get("/year/new").get_data(as_text=True))

    def test_base_location_of_a_year_deleted_meanwhile(self):
        self.new_year(2027)
        clean = appmod.clean_place

        def delete_meanwhile(text):
            with other_connection(self) as conn:
                conn.execute("DELETE FROM years")
            return clean(text)

        with mock.patch.object(appmod, "clean_place", side_effect=delete_meanwhile):
            html = self.client.post("/year/2027/base", data={
                "action": "update", "base_city": "Porto", "base_country": "Portugal"},
                follow_redirects=True).get_data(as_text=True)
        self.assertIn("The year 2027 no longer exists, so the changes were not saved.", html)
        self.assertNotIn("Base location updated.", html)

    def test_a_name_ending_in_a_dot(self):
        self.new_year(2027)
        self.upload("/year/2027/base", "lease.pdf", b"%PDF-1.4\n%%EOF\n")
        with self.db() as conn:
            doc = conn.execute("SELECT id FROM documents").fetchone()[0]
        self.client.post(f"/documents/{doc}/edit", data={"name": "Hotel. ", "kind": "other"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT original_name FROM documents").fetchone()[0],
                             "Hotel.pdf")


class AdminTicketCloseGoneTests(AdminTicketGoneTests):
    def test_close_and_reopen_of_a_ticket_deleted_meanwhile(self):
        for action in ("close", "reopen"):
            with mock.patch.object(appmod, "ticket_ref", side_effect=lambda t: (
                    self.delete_owner(), f"{t['ref_year']}-{t['ref_seq']}")[1]):
                resp = self.client.post(self.url, data={"action": action})
            html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
            self.assertIn("no longer exists", html, action)


class StyleFixTests(AppTestCase):
    def test_long_addresses_wrap_and_today_is_readable(self):
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".auth-head p { margin-top: 6px; overflow-wrap: anywhere; }", css)
        self.assertIn("max-width: 560px; overflow-wrap: anywhere; }", css)
        self.assertIn(".tl-today { stroke: var(--error-text);", css)
        self.assertIn(".tl-today-label { fill: var(--error-text);", css)


class ThirdReviewTests(AppTestCase):
    """The third review of 1.5.7."""

    def setUp(self):
        super().setUp()
        self.signup()
        self.age_emails()

    def change_and_undo(self, new="thief@example.com"):
        self.client.post("/settings", data={"action": "email", "email": new,
                                            "current_password": "password1"})
        self.client.post(self.last_link(new, kind="account/email"))
        return self.last_link("a@example.com", kind="account/email/undo")

    def test_sign_up_answers_before_its_email_is_sent(self):
        import threading
        self.app.config["EMAIL_IN_BACKGROUND"] = True
        started, release = threading.Event(), threading.Event()

        def slow_send(to, subject, text, html=None):
            started.set()
            release.wait(5)
            return self._record_email(to, subject, text, html)

        self.send_email.side_effect = slow_send
        resp = self.app.test_client().post("/signup", data={
            "email": "new@example.com", "password": "password1", "confirm": "password1"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(started.wait(5))
        self.assertFalse(release.is_set())  # answered while Gmail was still busy
        release.set()
        for thread in threading.enumerate():
            if thread.name == "email":
                thread.join(5)
        self.assertEqual(self.outbox[-1]["to"], "new@example.com")

    def test_undo_turns_off_two_factor_and_works_once(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = 'JBSWY3DPEHPK3PXP'")  # the intruder's
        undo = self.change_and_undo()
        owner = self.app.test_client()
        owner.post(undo)
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT totp_secret FROM users").fetchone()[0])
        # Pressed again: it says so instead of "no longer valid", and records nothing more.
        html = owner.post(undo, follow_redirects=True).get_data(as_text=True)
        self.assertIn("The account already uses a@example.com again.", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'email_reverted'").fetchone()[0], 1)
        with open(os.path.join(ROOT, "templates", "auth", "revert_email.html"),
                  encoding="utf-8") as fh:
            self.assertIn('<form method="post" class="stack" data-submit-once>', fh.read())

    def test_undo_pressed_twice_at_once_undoes_once(self):
        undo = self.change_and_undo()
        hash_password = appmod.hash_password

        def other_press_first(password):
            with other_connection(self) as conn:  # the other press commits first
                conn.execute("UPDATE users SET email = 'a@example.com'")
            return hash_password(password)

        with mock.patch.object(appmod, "hash_password", side_effect=other_press_first):
            html = self.app.test_client().post(undo, follow_redirects=True).get_data(as_text=True)
        self.assertIn("The account already uses a@example.com again.", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'email_reverted'").fetchone()[0], 0)

    def test_two_factor_alert_gives_advice_that_works(self):
        html = self.client.get("/settings").get_data(as_text=True)
        secret = re.search(r'<code class="tfa-key">([\w ]+)</code>', html).group(1).replace(" ", "")
        code = appmod.totp_code(secret, int(time.time() // appmod.TOTP_STEP_SECONDS))
        self.client.post("/settings", data={"action": "2fa_enable", "code": code,
                                            "current_password": "password1"})
        self.assertIn("write to support@nomadlife.pro from this address", self.outbox[-1]["text"])

    def test_wrong_current_password_on_settings_is_recorded(self):
        self.client.post("/settings", data={"action": "password", "current_password": "nope",
                                            "password": "password2", "confirm": "password2"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'password_check_failed'").fetchone()[0], 1)

    def test_email_change_texts_name_the_button(self):
        html = self.client.post("/settings", data={
            "action": "email", "email": "b@example.com", "current_password": "password1"},
            follow_redirects=True).get_data(as_text=True)
        self.assertIn("press Confirm my new email on the page it opens", html)
        self.assertIn("press Confirm my new email on the page it opens", self.outbox[-1]["text"])
        self.assertIn("Until you confirm it with the link we sent there", html)


class PreviousOwnerTests(AppTestCase):
    def test_an_account_history_leaves_out_a_deleted_previous_owner(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("admin@example.com")
        with self.db() as conn:
            alice = conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                                 "VALUES ('reused@example.com', 'x', '2026-01-01')").lastrowid
            conn.execute("INSERT INTO audit_log (user_id, email, event, detail) VALUES "
                         "(?, 'reused@example.com', 'sign_in', 'alice')", (alice,))
            conn.execute("DELETE FROM users WHERE id = ?", (alice,))
            bob = conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                               "VALUES ('reused@example.com', 'x', '2026-01-02')").lastrowid
            conn.execute("INSERT INTO audit_log (user_id, email, event, detail) VALUES "
                         "(?, 'reused@example.com', 'sign_in', 'bob')", (bob,))
        page = self.client.get(f"/admin/users/{bob}").get_data(as_text=True)
        self.assertIn("(bob)", page)
        self.assertNotIn("(alice)", page)


class LayoutReviewTests(AppTestCase):
    def test_tables_stay_inside_the_page_and_the_picker_drops_old_options(self):
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        # A hidden "Actions" label escaped the scrolling admin table (the page scrolled sideways
        # from 601 to 930 px); the docs tables bled 4 px past the page.
        self.assertIn(".table-wrap { position: relative; overflow-x: auto;", css)
        self.assertIn(".docs-body .table-wrap { margin: 0; }", css)
        with open(os.path.join(ROOT, "static", "js", "theme.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('active = -1;\n      // The old options are gone: never leave the input '
                      'pointing at one of them.\n      input.removeAttribute('
                      '"aria-activedescendant");', js)


class FourthReviewTests(AppTestCase):
    """Found in the fourth review of 1.5.7: writes that did not repeat what they checked,
    links racing the 20 minute line, a stored sign in field, timing, and two pages."""

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


class FourthReviewAdminTests(AppTestCase):
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
