"""Regression tests for the bugs found in the 1.5.7 review of the admin page and the whole app,
and the shorter overlap texts."""
import os
import re
import sqlite3
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
        self.assertIn("a@example.com is already confirmed. Please sign in.", html)
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
