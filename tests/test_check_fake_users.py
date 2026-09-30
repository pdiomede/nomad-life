"""check-fake-users (./checkFakeUsers.sh): which accounts it lists, and how it deletes them."""
import os
import subprocess

from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CheckFakeUsersTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"boss@example.com"})
        with self.db() as conn:
            def user(email, age_hours=48, confirmed_after=30, signed_in=False, verified=True,
                     ip="198.51.100.9"):
                uid = conn.execute(
                    "INSERT INTO users (email, password_hash, created_at, verified_at, "
                    "last_login_at, signup_ip) VALUES (?, 'x', datetime('now', ?), ?, ?, ?)",
                    (email, f"-{age_hours} hours", "2026-01-01" if verified else None,
                     "2026-01-02" if signed_in else None, ip)).lastrowid
                if verified and confirmed_after is not None:
                    conn.execute("INSERT INTO audit_log (user_id, email, event, ip, created_at) "
                                 "VALUES (?, ?, 'account_confirmed', '72.145.83.93', "
                                 "datetime('now', ?, ?))",
                                 (uid, email, f"-{age_hours} hours", f"+{confirmed_after} seconds"))
                return uid
            self.fake1 = user("fake1@corp.example", confirmed_after=4)
            self.fake2 = user("fake2@corp.example", confirmed_after=3600)  # slow: no scanner mark
            self.fake3 = user("fake3@corp.example", confirmed_after=None)  # no history entry
            user("real@example.com", signed_in=True)
            user("new@example.com", age_hours=2)
            user("boss@example.com")
            with_year = user("year@example.com")
            conn.execute("INSERT INTO years (user_id, year, base_city, base_country) "
                         "VALUES (?, 2026, 'Rome', 'Italy')", (with_year,))
            with_ticket = user("ticket@example.com")
            conn.execute("INSERT INTO tickets (user_id, subject) VALUES (?, 'Help')", (with_ticket,))

    def run_tool(self, *args, answers=""):
        return self.app.test_cli_runner().invoke(args=["check-fake-users", *args], input=answers)

    def emails(self):
        with self.db() as conn:
            return {r[0] for r in conn.execute("SELECT email FROM users")}

    def test_report_lists_only_fake_looking_accounts(self):
        out = self.run_tool("--dry-run").output
        for email in ("fake1@corp.example", "fake2@corp.example", "fake3@corp.example"):
            self.assertIn(email, out)
        for email in ("real@example.com", "new@example.com", "boss@example.com",
                      "year@example.com", "ticket@example.com"):
            self.assertNotIn(email, out)
        self.assertIn("4 s, scanner", out)
        self.assertIn("1 h", out)
        self.assertNotIn("1 h, scanner", out)
        self.assertIn("not recorded", out)
        self.assertIn("72.145.83.93", out)
        self.assertIn("198.51.100.9", out)
        self.assertIn("1 was confirmed within 2 minutes", out)
        self.assertIn("Dry run: nothing deleted.", out)
        self.assertEqual(len(self.emails()), 8)
        # Younger accounts appear with a smaller minimum age.
        self.assertIn("new@example.com", self.run_tool("--dry-run", "--min-age-hours", "1").output)

    def test_answering_no_or_not_typing_delete_keeps_everything(self):
        for answers in ("n\n", "\n", "a\nno\n", "a\ndelete\n"):
            result = self.run_tool(answers=answers)
            self.assertIn("Nothing deleted.", result.output, answers)
        self.assertEqual(len(self.emails()), 8)

    def test_delete_all(self):
        result = self.run_tool(answers="a\nDELETE\n")
        self.assertIn("Deleted 3 accounts", result.output)
        self.assertEqual(self.emails(), {"real@example.com", "new@example.com", "boss@example.com",
                                         "year@example.com", "ticket@example.com"})
        with self.db() as conn:
            rows = conn.execute("SELECT email, actor, detail FROM audit_log WHERE event = "
                                "'admin_delete' ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows],
                         [(f"fake{i}@corp.example", "check-fake-users", "fake account")
                          for i in (1, 2, 3)])

    def test_delete_some_by_number_and_range(self):
        result = self.run_tool(answers="x\n9\n1,3\nDELETE\n")
        self.assertIn("Please answer a, n, or numbers from 1 to 3.", result.output)
        self.assertIn("Deleted 2 accounts", result.output)
        self.assertIn("fake2@corp.example", self.emails())
        self.assertNotIn("fake1@corp.example", self.emails())

    def test_an_account_used_meanwhile_is_kept(self):
        from app import fake_user_candidates  # noqa: F401 (the tool's own query)
        real_prompt = __import__("click").prompt

        def prompt(text, **kwargs):
            answer = real_prompt(text, **kwargs)
            if text.startswith("Type DELETE"):  # fake2 signs in right before the answer
                with self.db() as conn:
                    conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP "
                                 "WHERE email = 'fake2@corp.example'")
            return answer

        import click
        click.prompt = prompt
        try:
            result = self.run_tool(answers="a\nDELETE\n")
        finally:
            click.prompt = real_prompt
        self.assertIn("Deleted 2 accounts", result.output)
        self.assertIn("meanwhile: fake2@corp.example", result.output)
        self.assertIn("fake2@corp.example", self.emails())

    def test_accounts_an_admin_touched_are_never_listed(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET disabled = 1 WHERE email = 'fake1@corp.example'")
            conn.execute("UPDATE users SET plan = 'pro' WHERE email = 'fake2@corp.example'")
            conn.execute("UPDATE users SET quota_bytes = 1 WHERE email = 'fake3@corp.example'")
        self.assertIn("No accounts look fake", self.run_tool().output)

    def test_nothing_to_report(self):
        with self.db() as conn:
            conn.execute("DELETE FROM users WHERE email LIKE 'fake%'")
        self.assertIn("No accounts look fake", self.run_tool().output)

    def test_the_script_runs_the_command(self):
        result = subprocess.run([os.path.join(ROOT, "checkFakeUsers.sh"), "--help"],
                                capture_output=True, text=True, cwd="/")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dry-run", result.stdout)
