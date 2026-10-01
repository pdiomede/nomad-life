"""Regression tests for the bugs found in the 1.5.5 review of the dashboard, db.py, package.py,
mailer.py, the country data, app.py, checkFakeUsers.sh and the templates."""
import io
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

import app as appmod
import mailer
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MailerTests(unittest.TestCase):
    def send(self, smtp):
        app = appmod.create_app({"TESTING": True, "SECRET_KEY": "x", "PWNED_CHECK": False,
                                 "DATABASE_PATH": os.path.join(tempfile.mkdtemp(), "t.db"),
                                 "GMAIL_USER": "me@gmail.com", "GMAIL_APP_PASSWORD": "pw"})
        with app.app_context(), mock.patch.object(mailer.smtplib, "SMTP", return_value=smtp):
            return mailer.send_email("you@example.com", "Hi", "plain", "<p>html</p>")

    def fake_smtp(self):
        smtp = mock.MagicMock()
        smtp.__enter__.return_value = smtp
        smtp.__exit__.return_value = False
        return smtp

    def test_starttls_checks_the_certificate_and_host_name(self):
        smtp = self.fake_smtp()
        self.assertTrue(self.send(smtp))
        context = smtp.starttls.call_args.kwargs["context"]
        # smtplib's default context checked neither, so the App Password could be read.
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_a_sent_email_is_not_reported_as_failed_when_quit_fails(self):
        smtp = self.fake_smtp()
        smtp.__exit__.side_effect = OSError("connection reset")
        self.assertTrue(self.send(smtp))
        smtp = self.fake_smtp()
        smtp.login.side_effect = OSError("network down")
        self.assertFalse(self.send(smtp))


class ScriptTests(unittest.TestCase):
    def test_another_projects_environment_is_skipped(self):
        # An active virtual environment without the app's packages (another project) was picked
        # and failed with "No such command 'check-fake-users'".
        with tempfile.TemporaryDirectory() as env:
            os.makedirs(os.path.join(env, "bin"))
            fake = os.path.join(env, "bin", "python")
            with open(fake, "w") as fh:
                fh.write("#!/bin/sh\nexit 1\n")
            os.chmod(fake, 0o755)
            bin_dir = os.path.join(env, "path")
            os.makedirs(bin_dir)
            os.symlink(sys.executable, os.path.join(bin_dir, "python3"))
            environ = {k: v for k, v in os.environ.items() if k != "PYTHON"}
            environ.update(VIRTUAL_ENV=env, PATH=bin_dir + os.pathsep + os.environ["PATH"])
            result = subprocess.run([os.path.join(ROOT, "checkFakeUsers.sh"), "--help"],
                                    capture_output=True, text=True, cwd="/", env=environ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dry-run", result.stdout)

    def test_an_explicit_python_is_used_as_given(self):
        result = subprocess.run([os.path.join(ROOT, "checkFakeUsers.sh"), "--help"],
                                capture_output=True, text=True, cwd="/",
                                env=dict(os.environ, PYTHON="/nonexistent/python"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("($PYTHON is /nonexistent/python)", result.stderr)


class AuthTemplateTests(AppTestCase):
    def test_password_links_of_unconfirmed_accounts_give_the_real_deadline(self):
        self.client.post("/signup", data={"email": "new@example.com", "password": "password1",
                                          "confirm": "password1"})
        with self.db() as conn:  # signed up 15 minutes ago: 5 minutes left
            conn.execute("UPDATE users SET created_at = datetime('now', '-15 minutes')")
        self.age_emails()
        self.client.post("/signup", data={"email": "new@example.com", "password": "password2",
                                          "confirm": "password2"})
        mail = self.outbox[-1]
        self.assertEqual(mail["subject"], "Finish setting up your Nomad Life account")
        self.assertIn("It expires in 5 minutes.", mail["text"])
        self.assertIn("<strong>5 minutes</strong>", mail["html"])
        self.assertNotIn("1 hour", mail["text"])
        # "Forgot password" for the same account says the same.
        self.age_emails()
        self.client.post("/forgot", data={"email": "new@example.com"})
        self.assertIn("It expires in 5 minutes and works only once.", self.outbox[-1]["text"])
        # A confirmed account keeps the hour.
        self.signup("old@example.com")
        self.age_emails()
        self.client.post("/forgot", data={"email": "old@example.com"})
        self.assertIn("It expires in 1 hour and works only once.", self.outbox[-1]["text"])

    def test_confirm_page_says_one_minute(self):
        self.client.post("/signup", data={"email": "new@example.com", "password": "password1",
                                          "confirm": "password1"})
        link = self.last_link("new@example.com")
        with self.db() as conn:
            conn.execute("UPDATE users SET created_at = datetime('now', '-19 minutes', "
                         "'-30 seconds')")
        html = self.client.get(link).get_data(as_text=True)
        self.assertIn("removed within 1 minute.", html)

    def test_lost_phone_points_to_support(self):
        self.signup()
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = 'JBSWY3DPEHPK3PXP'")
        self.client.post("/logout")
        self.client.post("/login", data={"email": "a@example.com", "password": "password1"})
        html = self.client.get("/login/code").get_data(as_text=True)
        self.assertIn('Lost your phone? Write to <a href="mailto:support@nomadlife.pro">'
                      'support@nomadlife.pro</a>', html)

    def test_password_hints_are_read_with_their_fields(self):
        html = self.client.get("/signup").get_data(as_text=True)
        self.assertIn('aria-describedby="password-hint"', html)
        self.assertIn('<span class="hint" id="password-hint">', html)

    def test_text_security_alert_says_why_it_was_sent(self):
        self.signup()
        self.client.post("/settings", data={"action": "password", "current_password": "password1",
                                            "password": "password2", "confirm": "password2"})
        mail = self.outbox[-1]
        self.assertEqual(mail["subject"], "Security alert for your Nomad Life account")
        self.assertIn("You get this email for every change to your password", mail["text"])


class PendingEmailTests(AppTestCase):
    def test_a_new_password_ends_a_pending_email_change(self):
        self.signup()
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": "b@example.com",
                                            "current_password": "password1"})
        self.assertIn("Waiting for confirmation", self.client.get("/settings").get_data(as_text=True))
        link = self.last_link("b@example.com", kind="account/email")
        self.client.post("/settings", data={"action": "password", "current_password": "password1",
                                            "password": "password2", "confirm": "password2"})
        # The link carries the old password, so it cannot work: Settings no longer waits for it.
        self.assertNotIn("Waiting for confirmation",
                         self.client.get("/settings").get_data(as_text=True))
        html = self.client.post(link, follow_redirects=True).get_data(as_text=True)
        self.assertIn("This link is no longer valid", html)


class SignInFixTests(AppTestCase):
    def test_a_password_reset_lifts_the_lock_on_this_device(self):
        self.signup()
        self.client.post("/logout")
        for _ in range(5):
            self.client.post("/login", data={"email": "a@example.com", "password": "wrong"})
        resp = self.client.post("/login", data={"email": "a@example.com", "password": "password1"})
        self.assertEqual(resp.status_code, 429)
        self.assertIn(b"You can also reset your password.", resp.data)
        self.age_emails()
        self.client.post("/forgot", data={"email": "a@example.com"})
        self.client.post(self.last_link("a@example.com", kind="reset"),
                         data={"password": "password2", "confirm": "password2"})
        # The reset was the way out the message offered; the device was still locked after it.
        resp = self.client.post("/login", data={"email": "a@example.com", "password": "password2"})
        self.assertEqual(resp.status_code, 302)

    def test_forgot_password_answers_before_the_email_is_sent(self):
        self.signup()
        self.client.post("/logout")
        self.app.config["EMAIL_IN_BACKGROUND"] = True
        started, release = threading.Event(), threading.Event()

        def slow_send(to, subject, text, html=None):
            started.set()
            release.wait(5)  # Gmail takes seconds; an unknown address sends nothing
            return self._record_email(to, subject, text, html)

        self.send_email.side_effect = slow_send
        self.age_emails()
        resp = self.client.post("/forgot", data={"email": "a@example.com"})
        self.assertEqual(resp.status_code, 302)
        # Since 1.5.7 the email is made once the answer is sent, which the server marks by
        # closing the response (the test client leaves that to the caller).
        self.assertFalse(started.wait(0.2))
        resp.close()
        self.assertTrue(started.wait(5))
        self.assertFalse(release.is_set())  # answered while the email was still being sent
        release.set()
        self.join_email_threads()
        self.assertIn("/reset/", self.outbox[-1]["text"])
        # A failed send still lets the user ask again at once.
        self.send_email.side_effect = lambda *a, **k: False
        self.age_emails()
        self.client.post("/forgot", data={"email": "a@example.com"}).close()
        self.join_email_threads()
        with self.db() as conn:
            self.assertIsNone(conn.execute("SELECT email_sent_at FROM users").fetchone()[0])

    @staticmethod
    def join_email_threads():
        for thread in threading.enumerate():
            if thread.name == "email":
                thread.join(5)


class EmailChangeFixTests(AppTestCase):
    def change_email(self, new):
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": new,
                                            "current_password": "password1"})
        return self.last_link(new, kind="account/email")

    def test_a_disabled_pending_account_keeps_its_address(self):
        self.signup()
        with self.db() as conn:  # blocked by an admin before it was confirmed
            conn.execute("INSERT INTO users (email, password_hash, disabled) "
                         "VALUES ('bad@example.com', 'x', 1)")
        html = self.client.post(self.change_email("bad@example.com"),
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn("Another account started using this email address", html)
        with self.db() as conn:
            self.assertEqual([tuple(r) for r in conn.execute(
                "SELECT email, disabled FROM users ORDER BY id")],
                [("a@example.com", 0), ("bad@example.com", 1)])

    def test_undo_link_leaves_another_signed_in_account_alone(self):
        self.signup()
        self.client.post(self.change_email("thief@example.com"))
        undo = self.last_link("a@example.com", kind="account/email/undo")
        other = self.signup("b@example.com", client=self.app.test_client())
        html = other.post(undo, follow_redirects=True).get_data(as_text=True)
        self.assertIn("The account uses a@example.com again", html)
        self.assertEqual(other.get("/settings").status_code, 200)  # b@ is still signed in

    def test_undo_says_when_the_password_link_could_not_be_sent(self):
        self.signup()
        self.client.post(self.change_email("thief@example.com"))
        undo = self.last_link("a@example.com", kind="account/email/undo")
        self.send_email.side_effect = lambda *a, **k: False
        html = self.app.test_client().post(undo, follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("We sent you a link", html)
        self.assertIn("We could not send the link to choose a new password", html)


class AdminSupportFixTests(AppTestCase):
    def test_a_search_for_1_is_kept_in_the_links(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("admin@example.com")
        with self.db() as conn:
            uid = conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                               "VALUES ('user1@example.com', 'x', '2026-01-01')").lastrowid
            conn.execute("INSERT INTO tickets (user_id, subject) VALUES (?, 'Help')", (uid,))
        html = self.client.get("/admin/support?q=1").get_data(as_text=True)
        self.assertRegex(html, r'href="/admin/support/\d+-1\?q=1&amp;')


class DashboardFixTests(AppTestCase):
    PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"

    def setUp(self):
        super().setUp()
        self.signup()
        self.client.post("/year/new", data={"year": "2027", "base_city": "Lisbon",
                                            "base_country": "Portugal"})

    def add(self, **extra):
        data = {"city": "Rome", "country": "Italy", "start_date": "2027-03-01",
                "end_date": "2027-03-05"}
        data.update(extra)
        return self.client.post("/year/2027/movements/new", data=data,
                                content_type="multipart/form-data", follow_redirects=True)

    def test_a_duplicate_movement_says_its_receipt_was_not_added(self):
        self.add()
        html = self.add(kind="other", file=(io.BytesIO(self.PDF), "ticket.pdf")).get_data(
            as_text=True)
        self.assertIn("This movement to Rome is already saved.", html)
        self.assertIn("The receipt you chose was not added: add it on this page.", html)
        html = self.add(notes="Flat near the station").get_data(as_text=True)
        self.assertIn("Your notes were not added: add them on this page.", html)
        # The same form sent twice (its receipt already saved) adds no warning.
        self.add(city="Paris", country="France", file=(io.BytesIO(self.PDF), "hotel.pdf"))
        html = self.add(city="Paris", country="France",
                        file=(io.BytesIO(self.PDF), "hotel.pdf")).get_data(as_text=True)
        self.assertIn("This movement to Paris is already saved.", html)
        self.assertNotIn("not added", html)

    def test_base_page_and_receipts_left_open_after_a_delete(self):
        self.upload("/year/2027/base", "lease.pdf", self.PDF)
        with self.db() as conn:
            doc = conn.execute("SELECT id FROM documents").fetchone()[0]
        self.client.post(f"/documents/{doc}/delete", data={"confirm_delete": "2"})
        resp = self.client.post(f"/documents/{doc}/delete", data={"confirm_delete": "2"},
                                follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("This document was already deleted.", resp.get_data(as_text=True))
        html = self.client.post(f"/documents/{doc}/edit", data={"name": "x", "kind": "other"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("This document no longer exists, so the changes were not saved.", html)
        self.client.post("/year/2027/base", data={"action": "delete_year", "confirm_delete": "2",
                                                  "confirm_year": "2027"})
        for action, message in (("update", "The year 2027 no longer exists, so the changes "
                                           "were not saved."),
                                ("delete_year", "Year 2027 was already deleted."),
                                ("upload", appmod.UPLOAD_GONE)):
            resp = self.client.post("/year/2027/base", data={
                "action": action, "base_city": "Porto", "base_country": "Portugal",
                "confirm_delete": "2", "confirm_year": "2027"}, follow_redirects=True)
            self.assertEqual(resp.status_code, 200, action)
            self.assertIn(message, resp.get_data(as_text=True))

    def test_renaming_a_jpeg_to_jpg_keeps_one_extension(self):
        self.upload("/year/2027/base", "scan.jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 64)
        with self.db() as conn:
            doc = conn.execute("SELECT id FROM documents").fetchone()[0]
        self.client.post(f"/documents/{doc}/edit", data={"name": "Hotel Rome.jpg",
                                                         "kind": "accommodation"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT original_name FROM documents").fetchone()[0],
                             "Hotel Rome.jpeg")

    def test_damaged_receipt_header_is_listed_missing(self):
        self.set_plan("pro")
        self.upload("/year/2027/base", "lease.pdf", self.PDF)
        with self.db() as conn:
            stored = conn.execute("SELECT stored_name, user_id FROM documents").fetchone()
        path = os.path.join(self.app.config["UPLOAD_DIR"], str(stored[1]), stored[0])
        with open(path, "rb") as fh:
            raw = bytearray(fh.read())
        central = raw.index(b"PK\x01\x02")
        raw[central + 8] |= 1  # one changed bit: the receipt now asks for a password
        with open(path, "wb") as fh:
            fh.write(raw)
        _resp, zf = self.package(2027)  # was a server error for the whole year
        manifest = self.read_csv(zf, "manifest.csv")
        self.assertEqual([(r["path"].rsplit("/", 1)[-1], r["status"]) for r in manifest
                          if r["stay"] == "Base"], [("other_lease.pdf", "missing")])

    def test_overlap_triangle_and_long_off_map_places(self):
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        rule = css[css.index(".overlap-alert {"):]
        # --danger is not lightened in the dark theme: 2.5:1 on the card, too faint.
        self.assertIn("color: var(--error-text);", rule[:rule.index("}")])
        self.assertIn(".map-off { overflow-wrap: anywhere; }", css)
        self.add(city="Tiraspol", country="Transnistria" * 8)
        html = self.client.get("/year/2027").get_data(as_text=True)
        self.assertIn('<p class="muted small map-off">Not on the map: ', html)


class CountryFixTests(unittest.TestCase):
    def test_hyphens_ampersands_and_short_names(self):
        for typed, name in (("Guinea Bissau", "Guinea-Bissau"), ("timor leste", "Timor-Leste"),
                            ("Bosnia & Herzegovina", "Bosnia and Herzegovina"),
                            ("Trinidad & Tobago", "Trinidad and Tobago"),
                            ("Brunei", "Brunei Darussalam"), ("Macau", "Macao"),
                            ("St Lucia", "Saint Lucia"), ("The Gambia", "Gambia"),
                            ("Falkland Islands", "Falkland Islands (Malvinas)")):
            self.assertEqual(appmod.normalize_country(typed), name)
        stats = appmod.compute_stats({"year": 2025, "base_country": "Portugal"}, [
            {"id": 1, "city": "Bissau", "country": "Guinea-Bissau", "start_date": "2025-01-01",
             "end_date": "2025-01-10"},
            {"id": 2, "city": "Bafata", "country": "Guinea Bissau", "start_date": "2025-02-01",
             "end_date": "2025-02-10"}])
        self.assertEqual([r["days"] for r in stats["rows"] if r["country"] != "Portugal"], [20])

    def test_the_picker_folds_the_same_way_and_an_emptied_field_highlights_nothing(self):
        with open(os.path.join(ROOT, "static", "js", "theme.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('.replace(/[-\\u2010-\\u2015]/g, " ").replace(/&/g, " and ")', js)
        # Clearing the field highlighted Afghanistan, so Enter filled it in.
        self.assertIn("render(search(input.value), fold(input.value) ? 0 : -1);", js)


if __name__ == "__main__":
    unittest.main()
