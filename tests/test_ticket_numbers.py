"""Ticket numbers: YEAR-N, counted per year by the database, in pages, emails, URLs and search."""
import os
import sqlite3
import tempfile
import threading
import unittest

import db as dbmod
from tests.helpers import AppTestCase, ref


class TicketNumberTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("a@example.com")  # self.client, user 1
        self.admin = self.app.test_client()
        self.signup("admin@example.com", client=self.admin)

    def open_ticket(self, subject="Map is empty"):
        return self.client.post("/support/new", data={"kind": "bug", "subject": subject,
                                                      "body": "Nothing shows."})

    def refs(self):
        with self.db() as conn:
            return [tuple(r) for r in conn.execute(
                "SELECT ref_year, ref_seq FROM tickets ORDER BY id")]

    def test_numbers_count_per_year_and_restart(self):
        with self.db() as conn:
            for created in ("2025-03-01 10:00:00", "2025-12-31 23:59:59", "2026-01-01 00:00:00",
                            "2025-06-01 00:00:00", "2026-02-01 00:00:00"):
                conn.execute("INSERT INTO tickets (user_id, subject, created_at) VALUES (1, 'x', ?)",
                             (created,))
        self.assertEqual(self.refs(), [(2025, 1), (2025, 2), (2026, 1), (2025, 3), (2026, 2)])

    def test_the_database_refuses_a_number_twice(self):
        with self.db() as conn:
            conn.execute("INSERT INTO tickets (user_id, subject, ref_year, ref_seq) "
                         "VALUES (1, 'x', 2026, 5)")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO tickets (user_id, subject, ref_year, ref_seq) "
                             "VALUES (1, 'y', 2026, 5)")
            # The trigger continues after the highest number of the year.
            conn.execute("INSERT INTO tickets (user_id, subject, created_at) "
                         "VALUES (1, 'z', '2026-07-01 00:00:00')")
            self.assertEqual(conn.execute("SELECT ref_seq FROM tickets WHERE subject = 'z'")
                             .fetchone()[0], 6)

    def test_a_deleted_tickets_number_is_never_given_again(self):
        for i in range(3):
            self.open_ticket(f"T{i}")
        with self.db() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM tickets WHERE ref_seq = 3")  # as with a deleted account
        self.open_ticket("After")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT ref_seq FROM tickets WHERE subject = 'After'")
                             .fetchone()[0], 4)

    def test_a_whole_email_finds_only_that_account(self):
        other = self.app.test_client()
        self.signup("aa@example.com", client=other)
        other.post("/support/new", data={"kind": "bug", "subject": "Other", "body": "x"})
        self.open_ticket("Mine")
        html = self.admin.get("/admin/support?q=a@example.com").get_data(as_text=True)
        self.assertIn(">Mine<", html)
        self.assertNotIn(">Other<", html)
        # Part of an address still finds both.
        html = self.admin.get("/admin/support?q=a@example").get_data(as_text=True)
        self.assertIn(">Mine<", html)
        self.assertIn(">Other<", html)

    def test_parallel_inserts_get_different_numbers(self):
        path = self.app.config["DATABASE_PATH"]
        errors = []

        def insert():
            conn = sqlite3.connect(path, timeout=10)
            try:
                for _ in range(10):
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("INSERT INTO tickets (user_id, subject) VALUES (1, 'p')")
                    conn.commit()
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=insert) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        seqs = sorted(seq for _year, seq in self.refs())
        self.assertEqual(seqs, list(range(1, 41)))

    def test_pages_emails_and_flash_show_the_number(self):
        resp = self.open_ticket()
        self.assertEqual(resp.headers["Location"], f"/support/{ref(1)}")
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn(f"Ticket #{ref(1)} was sent.", html)
        self.assertIn(f"<title>Ticket #{ref(1)} | Nomad Life</title>", html)
        mail = self.outbox[-1]
        self.assertEqual(mail["subject"], f"Support ticket #{ref(1)} needs an answer")
        self.assertIn(f"/admin/support/{ref(1)}", mail["text"])
        self.assertIn(f"#{ref(1)} <bdi>Map is empty</bdi>",
                      self.client.get("/support").get_data(as_text=True))
        self.admin.post(f"/admin/support/{ref(1)}", data={"action": "reply", "body": "Fixed"})
        mail = self.outbox[-1]
        self.assertEqual(mail["subject"], f"Support answered your ticket #{ref(1)}")
        self.assertIn(f"/support/{ref(1)}", mail["text"])
        self.assertIn(f"#{ref(1)}", mail["html"])
        with self.db() as conn:
            details = {r[0] for r in conn.execute(
                "SELECT detail FROM audit_log WHERE event LIKE 'ticket%'")}
        self.assertEqual(details, {f"#{ref(1)}"})

    def test_old_links_redirect_and_keep_a_post(self):
        self.open_ticket()
        self.open_ticket("Second")
        resp = self.client.get("/support/2")
        self.assertEqual(resp.status_code, 307)
        self.assertEqual(resp.headers["Location"], f"/support/{ref(2)}")
        resp = self.client.post("/support/2", data={"action": "close"}, follow_redirects=True)
        self.assertIn("The ticket is closed.", resp.get_data(as_text=True))
        resp = self.admin.get("/admin/support/1")
        self.assertEqual((resp.status_code, resp.headers["Location"]),
                         (307, f"/admin/support/{ref(1)}"))
        # Other people's tickets, old or new address, are not found.
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        for path in ("/support/1", f"/support/{ref(1)}", f"/support/{ref(9)}", "/support/99",
                     f"/support/{2 ** 70}-1", f"/support/2026-{2 ** 70}"):
            self.assertEqual(other.get(path).status_code, 404, path)
        self.assertEqual(self.client.get("/admin/support/1").status_code, 404)
        self.assertEqual(self.client.get("/support/new").status_code, 200)

    def test_admin_search_and_sort_by_number(self):
        with self.db() as conn:
            for i in range(11):
                conn.execute("INSERT INTO tickets (user_id, subject) VALUES (1, ?)", (f"S{i}",))

        def rows(query):
            import re
            html = self.admin.get("/admin/support" + query).get_data(as_text=True)
            return re.findall(r'href="/admin/support/\d{4}-(\d+)(?:\?[^"]*)?" dir="auto"', html)

        self.assertEqual(rows(f"?q={ref(2)}"), ["2"])
        self.assertEqual(rows(f"?q=%23{ref(10)}"), ["10"])
        self.assertEqual(rows("?q=2026-999999999999999999999"), [])
        # 2026-9 sorts before 2026-10.
        self.assertEqual(rows("?sort=id&dir=asc")[8:11], ["9", "10", "11"])
        for q in ("#12", "2026-", "-1", "２０２６-1", "#", "2026-1-1"):
            self.assertEqual(self.admin.get("/admin/support", query_string={"q": q}).status_code,
                             200, q)


class TicketNumberMigrationTests(unittest.TestCase):
    def test_the_first_trigger_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dev.db")
            dbmod.init_db(path)
            conn = sqlite3.connect(path)
            conn.execute("CREATE TRIGGER tickets_ref AFTER INSERT ON tickets WHEN 0 BEGIN "
                         "SELECT 1; END")
            conn.commit()
            conn.close()
            dbmod.init_db(path)
            conn = sqlite3.connect(path)
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            conn.close()
            self.assertNotIn("tickets_ref", names)
            self.assertIn("tickets_number", names)

    def test_old_tickets_are_numbered_by_year_and_history_follows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.executescript(dbmod.SCHEMA.replace(
                ",\n    ref_year INTEGER NOT NULL DEFAULT 0,\n    ref_seq INTEGER NOT NULL DEFAULT 0", ""))
            conn.execute("INSERT INTO users (email, password_hash) VALUES ('old@x.com', 'h')")
            for created in ("2025-11-01 00:00:00", "2026-01-02 00:00:00", "2025-12-01 00:00:00"):
                conn.execute("INSERT INTO tickets (user_id, subject, created_at) VALUES (1, 's', ?)",
                             (created,))
            conn.executemany("INSERT INTO audit_log (event, detail) VALUES (?, ?)",
                             [("ticket_opened", "#3"), ("ticket_closed", "#2"),
                              ("ticket_reply", "#77"), ("sign_in", "#1")])
            conn.commit()
            self.assertNotIn("ref_seq", [r[1] for r in conn.execute("PRAGMA table_info(tickets)")])
            conn.close()
            dbmod.init_db(path)
            dbmod.init_db(path)  # repeatable
            conn = sqlite3.connect(path)
            self.assertEqual(conn.execute("SELECT ref_year, ref_seq FROM tickets ORDER BY id")
                             .fetchall(), [(2025, 1), (2026, 1), (2025, 2)])
            self.assertEqual([r[0] for r in conn.execute("SELECT detail FROM audit_log ORDER BY id")],
                             ["#2025-2", "#2026-1", "#77", "#1"])
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            self.assertLessEqual({"idx_tickets_ref", "tickets_number", "ticket_counters"}, names)
            conn.execute("INSERT INTO tickets (user_id, subject, created_at) "
                         "VALUES (1, 'new', '2025-12-31 00:00:00')")
            self.assertEqual(conn.execute("SELECT ref_seq FROM tickets WHERE subject = 'new'")
                             .fetchone()[0], 3)
            conn.close()


if __name__ == "__main__":
    unittest.main()
