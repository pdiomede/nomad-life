"""Settings page (1.2.11): names, where they show, the old /account address, 2FA setup key."""
import io
import os
import re
import sqlite3
import time
import zipfile

import app as appmod
from tests.helpers import AppTestCase


class SettingsPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def save(self, first, last, client=None):
        return (client or self.client).post("/settings", data={
            "action": "profile", "first_name": first, "last_name": last}, follow_redirects=True)

    def names(self):
        with self.db() as conn:
            return tuple(conn.execute("SELECT first_name, last_name FROM users").fetchone())

    def test_menu_and_footer_link_to_settings(self):
        html = self.client.get("/app", follow_redirects=True).get_data(as_text=True)
        self.assertIn('<a class="user-menu-item menu-settings" href="/settings">Settings</a>', html)
        self.assertIn('<a href="/settings">Settings</a>', html)
        page = self.client.get("/settings").get_data(as_text=True)
        for anchor in ("profile", "password", "two-factor", "email", "devices", "activity",
                       "delete"):
            self.assertIn(f'href="#{anchor}"', page)
            self.assertIn(f'id="{anchor}"', page)
        self.assertIn("<title>Settings | Nomad Life</title>", page)

    def test_old_account_address_still_works(self):
        resp = self.client.get("/account")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/settings")
        self.client.post("/account", data={"action": "profile", "first_name": "Ada",
                                           "last_name": "Lovelace"})
        self.assertEqual(self.names(), ("Ada", "Lovelace"))
        self.assertIn("Disallow: /settings", self.client.get("/robots.txt").get_data(as_text=True))

    def test_saving_and_clearing_the_name(self):
        html = self.save("  Ada ", "Love‮lace  King").get_data(as_text=True)
        self.assertIn("Your name was saved.", html)
        self.assertEqual(self.names(), ("Ada", "Lovelace King"))
        self.assertIn('value="Ada"', html)
        self.assertIn("<h1>Ada Lovelace King</h1>", html)
        self.assertIn("Nothing changed.", self.save("Ada", "Lovelace King").get_data(as_text=True))
        self.assertIn("Your name was removed.", self.save("", "").get_data(as_text=True))
        self.assertEqual(self.names(), ("", ""))
        with self.db() as conn:
            events = [r[0] for r in conn.execute("SELECT event FROM audit_log")]
        self.assertEqual(events.count("profile_changed"), 2)

    def test_long_names_are_refused(self):
        html = self.save("x" * (appmod.NAME_MAX + 1), "Ok").get_data(as_text=True)
        self.assertIn("at most 60 characters each", html)
        self.assertEqual(self.names(), ("", ""))
        self.save("x" * appmod.NAME_MAX, "Ok")
        self.assertEqual(self.names(), ("x" * appmod.NAME_MAX, "Ok"))

    def test_name_is_shown_in_the_header_menu_and_escaped(self):
        self.save("<b>Ada</b>", "Lovelace")
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('<span class="topnav-email" dir="auto">&lt;b&gt;Ada&lt;/b&gt; Lovelace</span>', html)
        self.assertIn('title="a@example.com"', html)
        self.assertNotIn("<b>Ada</b>", html)

    def test_without_a_name_the_email_is_shown(self):
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('<span class="topnav-email" dir="auto">a@example.com</span>', html)
        self.assertIn("<h1>a@example.com</h1>", html)


class NameUsageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.client.post("/settings", data={"action": "profile", "first_name": "Ada",
                                            "last_name": "Lovelace"})
        self.age_emails()

    def test_emails_greet_by_first_name(self):
        anon = self.app.test_client()
        anon.post("/forgot", data={"email": "a@example.com"})
        mail = self.outbox[-1]
        self.assertTrue(mail["text"].startswith("Hi Ada,"))
        self.assertIn("Hi Ada,</p>", mail["html"])

    def test_emails_without_a_name_say_hi(self):
        anon = self.app.test_client()
        anon.post("/signup", data={"email": "b@example.com", "password": "password1",
                                   "confirm": "password1"})
        mail = self.outbox[-1]
        self.assertTrue(mail["text"].startswith("Hi,"))
        self.assertNotIn("Hi ,", mail["text"])
        self.assertNotIn(">Hi", mail["html"])

    def test_mails_to_other_addresses_and_alerts_never_carry_the_name(self):
        # The name is text the account holder typed: it must not reach an address typed in
        # the email form, nor a security alert (a thief could write "ignore this" into it).
        self.client.post("/settings", data={"action": "profile", "first_name": "Call support",
                                            "last_name": "now"})
        self.client.post("/settings", data={"action": "email", "email": "new@example.com",
                                            "current_password": "password1"})
        mail = self.outbox[-1]
        self.assertEqual(mail["to"], "new@example.com")
        self.assertTrue(mail["text"].startswith("Hi,"))
        self.assertNotIn("Call support", mail["text"] + mail["html"])
        self.client.post(self.last_link("new@example.com", kind="account/email"))
        alert = self.outbox[-1]
        self.assertEqual(alert["to"], "a@example.com")
        self.assertIn("Security alert", alert["subject"])
        self.assertNotIn("Call support", alert["text"] + alert["html"])

    def test_admin_list_shows_the_name(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        admin = self.signup("admin@example.com", client=self.app.test_client())
        html = admin.get("/admin").get_data(as_text=True)
        self.assertIn('<p class="admin-name" dir="auto">Ada Lovelace</p>', html)

    def test_accountant_package_names_the_holder(self):
        self.set_plan("pro")
        self.new_year(2025)
        data = self.client.get("/year/2025/package").data
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            readme = zf.read("NomadLife-2025-accountant-package/README.txt").decode()
        self.assertIn("Prepared for: Ada Lovelace (a@example.com)", readme)
        with self.app.test_request_context():
            year_row = appmod.db.query("SELECT * FROM years", one=True)
            text = appmod.package.summary_text(appmod.build_package_data(year_row, False))
        self.assertIn("Prepared for: Ada Lovelace (a@example.com)", text)  # the summary PDF

    def test_package_without_a_name_uses_the_email(self):
        self.client.post("/settings", data={"action": "profile", "first_name": "",
                                            "last_name": ""})
        self.set_plan("pro")
        self.new_year(2025)
        with zipfile.ZipFile(io.BytesIO(self.client.get("/year/2025/package").data)) as zf:
            readme = zf.read("NomadLife-2025-accountant-package/README.txt").decode()
        self.assertIn("Prepared for: a@example.com", readme)


class TwoFactorKeyTests(AppTestCase):
    def test_grouped_key_enables_two_factor(self):
        self.signup()
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("Google Authenticator", html)
        self.assertIn('src="data:image/svg+xml', html)
        grouped = re.search(r'<code class="tfa-key">([\w ]+)</code>', html).group(1)
        self.assertRegex(grouped, r"^([A-Z2-7]{4} )+[A-Z2-7]{1,4}$")
        secret = grouped.replace(" ", "")
        code = appmod.totp_code(secret, int(time.time() // 30))
        html = self.client.post("/settings", data={
            "action": "2fa_enable", "code": code, "current_password": "password1"},
            follow_redirects=True).get_data(as_text=True)
        self.assertIn("Two-factor sign in is on.", html)
        self.assertIn("Turn off two-factor sign in", html)


class MigrationTests(AppTestCase):
    def test_an_older_database_gets_the_name_columns(self):
        path = os.path.join(self.tmp, "old.db")
        conn = sqlite3.connect(path)
        conn.executescript("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO users (email, password_hash) VALUES ('old@example.com', 'x');""")
        conn.commit()
        conn.close()
        appmod.create_app({"TESTING": True, "SECRET_KEY": "k", "DATABASE_PATH": path,
                           "UPLOAD_DIR": os.path.join(self.tmp, "up-old"), "PWNED_CHECK": False})
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT first_name, last_name FROM users").fetchone()
        conn.close()
        self.assertEqual(row, ("", ""))
