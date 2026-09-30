"""Regression tests for the bugs found in the 1.3.1 review of database calls and support pages."""
import os
import sqlite3
import time

import app as appmod
from tests.helpers import AppTestCase, ref


class DatabaseFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def hold_write_lock(self):
        conn = sqlite3.connect(self.app.config["DATABASE_PATH"], timeout=0)
        conn.execute("BEGIN IMMEDIATE")  # another request in the middle of a write
        self.addCleanup(conn.close)
        return conn

    def test_pages_load_while_another_request_writes(self):
        self.new_year(2026)
        with self.db() as conn:
            conn.execute("UPDATE user_sessions SET last_seen_at = datetime('now', '-2 hours')")
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.hold_write_lock()
        resp = self.client.get("/year/2026")
        self.assertEqual(resp.status_code, 200)

    def test_housekeeping_skips_a_busy_database(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.hold_write_lock()
        real = appmod.PURGE_EVERY_SECONDS
        appmod.PURGE_EVERY_SECONDS = 0  # the purge hook runs on this request
        try:
            resp = self.client.get("/support")
        finally:
            appmod.PURGE_EVERY_SECONDS = real
        self.assertEqual(resp.status_code, 200)

    def test_rename_does_not_hold_the_write_lock_while_rewriting(self):
        self.new_year(2026)
        self.upload("/year/2026/base", "Hotel.pdf", b"%PDF-1.7\n" + os.urandom(5000))
        real = appmod.write_receipt_zip
        free = []

        def check(*args):
            other = sqlite3.connect(self.app.config["DATABASE_PATH"], timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
                other.rollback()
                free.append(True)
            except sqlite3.OperationalError:
                free.append(False)
            finally:
                other.close()
            return real(*args)

        appmod.write_receipt_zip = check
        try:
            self.client.post("/documents/1/edit", data={"name": "Hotel Rome", "kind": "other"})
        finally:
            appmod.write_receipt_zip = real
        self.assertEqual(free, [True])
        with self.db() as conn:
            row = conn.execute("SELECT * FROM documents").fetchone()
        self.assertEqual(row["original_name"], "Hotel Rome.pdf")
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        self.assertEqual(os.listdir(folder), [row["stored_name"]])  # no temporary file left
        with appmod.zipfile.ZipFile(os.path.join(folder, row["stored_name"])) as zf:
            self.assertEqual(zf.namelist(), ["Hotel Rome.pdf"])
            self.assertEqual(row["size"], os.path.getsize(zf.filename))


class RaceFixTests(AppTestCase):
    """Each test runs the competing request at the exact moment between a check and its write."""

    def setUp(self):
        super().setUp()
        self.signup()

    def patch(self, obj, name, make):
        real = getattr(obj, name)
        setattr(obj, name, make(real))
        self.addCleanup(setattr, obj, name, real)

    def test_a_reset_link_works_once_even_when_sent_twice_at_once(self):
        self.age_emails()
        anon = self.app.test_client()
        anon.post("/forgot", data={"email": "a@example.com"})
        link = self.last_link("a@example.com", kind="reset")
        other = self.app.test_client()
        done = []

        def make(real):
            def hash_first(password):
                if not done:  # the other tab submits while this one hashes
                    done.append(None)
                    done[0] = other.post(link, data={"password": "secondpass2",
                                                     "confirm": "secondpass2"})
                return real(password)
            return hash_first

        self.patch(appmod, "hash_password", make)
        self.outbox.clear()
        resp = anon.post(link, data={"password": "firstpass1", "confirm": "firstpass1"})
        self.assertEqual(done[0].headers["Location"], "/login")
        self.assertEqual(resp.headers["Location"], "/forgot")
        self.assertEqual(len([m for m in self.outbox if "reset" in m["text"]]), 1)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'password_reset'").fetchone()[0], 1)

    def after_count(self, table, insert):
        """Insert a row right after the route counted the rows of table (the old code path)."""
        fired = []

        def make(real):
            def query(sql, *args, **kwargs):
                rows = real(sql, *args, **kwargs)
                if sql.startswith("SELECT COUNT(*)") and f"FROM {table} " in sql and not fired:
                    fired.append(True)
                    with self.db() as conn:
                        conn.execute(insert)
                return rows
            return query
        self.patch(appmod.db, "query", make)

    def test_the_movement_limit_holds_under_parallel_posts(self):
        self.new_year(2026)
        with self.db() as conn:
            conn.executemany("INSERT INTO movements (year_id, city, country, start_date, "
                             "end_date) VALUES (1, 'Rome', 'Italy', '2026-01-01', '2026-01-02')",
                             [()] * (appmod.MOVEMENTS_MAX - 1))
        self.after_count("movements", "INSERT INTO movements (year_id, city, country, "
                         "start_date, end_date) VALUES (1, 'Oslo', 'Norway', '2026-02-01', "
                         "'2026-02-02')")
        self.client.post("/year/2026/movements/new", data={
            "city": "Paris", "country": "France", "start_date": "2026-03-01",
            "end_date": "2026-03-02"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0],
                             appmod.MOVEMENTS_MAX)

    def test_the_open_ticket_cap_holds_under_parallel_posts(self):
        with self.db() as conn:
            conn.executemany("INSERT INTO tickets (user_id, subject) VALUES (1, 'x')",
                             [()] * (appmod.OPEN_TICKETS_MAX - 1))
        self.after_count("tickets", "INSERT INTO tickets (user_id, subject) VALUES (1, 'y')")
        resp = self.client.post("/support/new", data={"kind": "bug", "subject": "Help",
                                                      "body": "text"})
        self.assertEqual(resp.status_code, 400)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tickets WHERE status = 'open'")
                             .fetchone()[0], appmod.OPEN_TICKETS_MAX)

    def test_deleting_a_receipt_during_zip_receipts_leaves_no_file(self):
        self.new_year(2026)
        folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "old.pdf"), "wb") as fh:
            fh.write(b"%PDF-1.4\n" + b"x" * 500)
        with self.db() as conn:
            conn.execute("INSERT INTO documents (user_id, year_id, kind, original_name, "
                         "stored_name, mime, size, format, file_size) VALUES (1, 1, 'other', "
                         "'Rent.pdf', 'old.pdf', 'application/pdf', 509, 'pdf', 509)")

        def make(real):
            def convert_first():
                self.app.test_cli_runner().invoke(args=["zip-receipts"])
                return real()
            return convert_first

        self.patch(appmod, "delete_confirmed", make)
        self.client.post("/documents/1/delete", data={"confirm_delete": "2"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        self.assertEqual(os.listdir(folder), [])

    def test_an_email_change_link_opened_twice_at_once_applies_once(self):
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "new@example.com",
                                            "current_password": "password1"})
        link = self.last_link("new@example.com", kind="account/email")
        scanner = self.app.test_client()
        opened = []

        def make(real):
            def open_first():
                if not opened:  # a mail scanner opens the same link at the same moment
                    opened.append(None)
                    opened[0] = scanner.get(link)
                return real()
            return open_first

        self.patch(appmod.db, "transaction", make)
        self.outbox.clear()
        self.client.get(link)
        self.assertEqual(len([m for m in self.outbox if m["to"] == "a@example.com"]), 1)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'email_changed'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT email FROM users").fetchone()[0],
                             "new@example.com")


class BusyStampTests(AppTestCase):
    def test_a_skipped_stamp_is_not_retried_on_every_page(self):
        self.signup()
        self.client.get("/plan")  # housekeeping done for this minute
        with self.db() as conn:
            conn.execute("UPDATE user_sessions SET last_seen_at = datetime('now', '-2 hours')")
        conn = sqlite3.connect(self.app.config["DATABASE_PATH"], timeout=0)
        self.addCleanup(conn.close)
        conn.execute("BEGIN IMMEDIATE")
        self.assertEqual(self.client.get("/plan").status_code, 200)  # waits once, then skips
        started = time.monotonic()
        self.assertEqual(self.client.get("/plan").status_code, 200)
        self.assertEqual(self.client.get("/static/css/style.css").status_code, 200)
        self.assertLess(time.monotonic() - started, 2)


class SupportUiFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup()
        self.admin = self.signup("admin@example.com", client=self.app.test_client())

    def open_ticket(self, subject="Help", body="Please help"):
        return self.client.post("/support/new", data={"kind": "bug", "subject": subject,
                                                      "body": body})

    def test_text_made_only_of_invisible_marks_is_refused(self):
        for mark in ("͏", "️", "឴", "\U000e0100", "́́"):
            self.assertEqual(self.open_ticket(subject=mark).status_code, 400, repr(mark))
            self.assertEqual(self.open_ticket(body=mark).status_code, 400, repr(mark))
        self.assertEqual(self.open_ticket(subject="👍️").status_code, 302)  # emoji stay

    def test_names_typed_decomposed_are_found(self):
        self.client.post("/settings", data={"action": "profile", "first_name": "Élodie",
                                            "last_name": "Martin"})
        self.open_ticket()
        for q in ("Élodie", "élodie", "Élodie"):
            html = self.admin.get("/admin/support", query_string={"q": q}).get_data(as_text=True)
            self.assertIn(f"/admin/support/{ref(1)}", html, repr(q))

    def test_closed_ticket_at_the_cap_does_not_promise_to_reopen(self):
        self.open_ticket()
        self.client.post(f"/support/{ref(1)}", data={"action": "close"})
        html = self.client.get(f"/support/{ref(1)}").get_data(as_text=True)
        self.assertIn("Writing a message opens it again.", html)
        with self.db() as conn:
            conn.executemany("INSERT INTO tickets (user_id, subject) VALUES (1, 'x')",
                             [()] * appmod.OPEN_TICKETS_MAX)
        html = self.client.get(f"/support/{ref(1)}").get_data(as_text=True)
        self.assertNotIn("Writing a message opens it again.", html)
        self.assertIn("writing here keeps it closed", html)

    def test_admin_is_told_when_a_close_or_reopen_changed_nothing(self):
        self.open_ticket()
        self.client.post(f"/support/{ref(1)}", data={"action": "close"})
        html = self.admin.post(f"/admin/support/{ref(1)}", data={"action": "close"},
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn("This ticket was already closed.", html)
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reopen"})
        html = self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reopen"},
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn("This ticket is already open.", html)

    def test_long_subjects_and_emails_wrap_in_the_admin_table(self):
        css = open(os.path.join(appmod.BASE_DIR, "static", "css", "style.css"),
                   encoding="utf-8").read()
        self.assertIn(".support-table td:nth-child(3), .support-table td:nth-child(4) "
                      "{ overflow-wrap: anywhere; }", css)


class SessionReviewTests(AppTestCase):
    """Bugs found in the review of what 1.3.1 added (admin accounts, ticket numbers, chat)."""

    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("a@example.com")
        self.admin = self.app.test_client()
        self.signup("admin@example.com", client=self.admin)

    def test_storage_sums_use_an_index(self):
        # Without it the admin accounts table read every receipt once per account (2 s for
        # 2000 accounts and 60000 receipts), blocking every write meanwhile.
        with self.db() as conn:
            plan = " ".join(str(r[-1]) for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT COALESCE(SUM(size), 0) FROM documents WHERE user_id = 1"))
        self.assertIn("idx_documents_user", plan)

    def test_ticket_pages_load_while_another_request_writes(self):
        self.client.post("/support/new", data={"kind": "bug", "subject": "S", "body": "B"})
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reply", "body": "Answer"})
        self.client.post(f"/support/{ref(1)}", data={"action": "reply", "body": "More"})
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        conn = sqlite3.connect(self.app.config["DATABASE_PATH"], timeout=0)
        conn.execute("BEGIN IMMEDIATE")  # another request in the middle of a write
        try:
            self.assertEqual(self.admin.get(f"/admin/support/{ref(1)}").status_code, 200)
        finally:
            conn.rollback()
            conn.close()
        # The new message still counts as unread, and is marked seen on the next view.
        with self.db() as c:
            seen = c.execute("SELECT admin_seen_id FROM tickets").fetchone()[0]
            last = c.execute("SELECT MAX(id) FROM ticket_messages").fetchone()[0]
        self.assertLess(seen, last)
        self.admin.get(f"/admin/support/{ref(1)}")
        with self.db() as c:
            self.assertEqual(c.execute("SELECT admin_seen_id FROM tickets").fetchone()[0], last)

    def test_disabling_is_all_or_nothing(self):
        with self.db() as conn:
            conn.execute("CREATE TRIGGER fail_session_delete BEFORE DELETE ON user_sessions "
                         "BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        resp = self.admin.post("/admin/users/1", data={"action": "disable"})
        self.assertEqual(resp.status_code, 500)
        with self.db() as conn:
            # Before, the account stayed disabled with its sessions left behind.
            self.assertEqual(conn.execute("SELECT disabled FROM users WHERE id = 1").fetchone()[0], 0)
            conn.execute("DROP TRIGGER fail_session_delete")
        self.admin.post("/admin/users/1", data={"action": "disable"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT disabled FROM users WHERE id = 1").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id = 1")
                             .fetchone()[0], 0)

    def test_the_404_page_has_contact_us(self):
        html = self.app.test_client().get("/no-such-page").get_data(as_text=True)
        self.assertIn(f'href="mailto:{appmod.CONTACT_EMAIL}">Contact Us</a>', html)


class LandingFooterTests(AppTestCase):
    def footer(self, path):
        html = self.client.get(path).get_data(as_text=True)
        return html[html.index("<footer"):html.index("</footer>")]

    def test_landing_footer_has_no_account_links_even_when_signed_in(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"a@example.com"})
        self.signup("a@example.com")
        landing = self.footer("/")
        self.assertIn("Contact Us", landing)
        self.assertNotIn('href="/settings"', landing)
        self.assertNotIn('href="/admin"', landing)
        self.assertIn('href="/settings"', self.footer("/plan"))  # the app pages keep them
