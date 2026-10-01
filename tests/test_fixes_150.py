"""Regression tests for the bugs found in the 1.5.0 review of the Security activity table and the
documentation page."""
import os
import re

from tests.helpers import AppTestCase


class ActivityFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")

    def accounts_in(self, html):
        section = html.split('id="activity"', 1)[1]
        return [re.sub(r"<[^>]+>", "", m).strip() for m in
                re.findall(r'activity-account hide-sm">(.*?)</td>', section, re.S)]

    def test_accented_emails_sort_with_their_letter(self):
        with self.db() as conn:
            conn.execute("DELETE FROM audit_log")
            conn.executemany("INSERT INTO audit_log (event, email) VALUES ('sign_in', ?)",
                             [("zoe@example.com",), ("élodie@example.com",), ("Eva@example.com",),
                              ("fred@example.com",)])
        html = self.client.get("/admin?asort=account&adir=asc").get_data(as_text=True)
        self.assertEqual(self.accounts_in(html), ["élodie@example.com", "Eva@example.com",
                                                  "fred@example.com", "zoe@example.com"])
        # The accounts table and the tickets table use the same order.
        with self.db() as conn:
            conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                         "VALUES ('élodie@example.com', 'x', '2026-01-01')")
            conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                         "VALUES ('zoe@example.com', 'x', '2026-01-01')")
        html = self.client.get("/admin?sort=email&dir=asc").get_data(as_text=True)
        accounts = re.findall(r'class="admin-email"><a [^>]*>([^<]+)</a>', html)
        self.assertEqual(accounts, ["admin@example.com", "élodie@example.com", "zoe@example.com"])

    def test_an_email_from_before_a_change_links_to_the_whole_history(self):
        user = self.app.test_client()
        self.signup("old@example.com", client=user)
        self.age_emails()
        user.post("/settings", data={"action": "email", "email": "new@example.com",
                                     "current_password": "password1"})
        user.post(self.last_link("new@example.com", kind="account/email"))
        html = self.client.get("/admin").get_data(as_text=True)
        section = html.split('id="activity"', 1)[1]
        # "Account confirmed" was recorded under old@, and its link filters by the account now.
        self.assertNotIn('href="/admin?q=old@example.com#activity"', section)
        self.assertIn('href="/admin?q=new@example.com#activity" dir="auto">old@example.com</a>',
                      section)


class DocsFixTests(AppTestCase):
    def page(self):
        return self.app.test_client().get("/docs").get_data(as_text=True)

    def test_the_text_matches_the_app(self):
        html = self.page()
        # The sign in page links to "Create an account"; there is no "Sign up" button.
        self.assertIn("then <strong>Create an account</strong>", html)
        self.assertNotIn("<strong>Sign up</strong>", html)
        # A reset signs out every device, this one too, and does not sign in.
        self.assertIn("signs out every device; then sign in with it", html)
        # HEIC photos, small ones and huge ones keep their metadata.
        self.assertIn("Big JPG, PNG and WebP photos", html)
        self.assertIn("HEIC files, smaller photos and photos over 24 megapixels are stored as they "
                      "are, metadata included", html)
        # The worked example counts 14 days, not the whole dashboard.
        self.assertIn("Result for these 14 days: Portugal 4", html)
        self.assertIn("Close the results; press again to clear the search", html)
        self.assertIn("add your notes to <code>timeline.csv</code>", html)
        self.assertIn("every other file with its SHA-256 checksum", html)
        self.assertIn("Signing in with your email and password sends a new link", html)
        # Words people search for are on the page (the overlap alert in the app).
        self.assertIn("(an overlap)", html)

    def test_step_text_is_one_inline_block(self):
        # Each numbered step's text is one span, so bold words do not become flex items.
        html = self.page()
        steps = re.findall(r'<li><span class="doc-flow-n" aria-hidden="true">\d</span> (.*?)</li>', html)
        self.assertEqual(len(steps), 4)
        for step in steps:
            self.assertTrue(step.startswith("<span>") and step.endswith("</span>"), step)

    def test_phone_header_keeps_room_for_the_name(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "static", "css", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".topnav .docs-btn-app { display: none; }", css)
        # Documentation stays reachable from the menu there.
        self.signup()
        self.assertIn('class="user-menu-item menu-docs" href="/docs"',
                      self.client.get("/settings").get_data(as_text=True))
