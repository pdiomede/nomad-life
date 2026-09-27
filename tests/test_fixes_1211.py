"""Regression tests for the bugs found in the 1.2.11 review of the Settings page."""
from unittest import mock

import fpdf

import app as appmod
import package
from tests.helpers import AppTestCase


class SettingsFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.age_emails()

    def save(self, first, last):
        return self.client.post("/settings", data={"action": "profile", "first_name": first,
                                                   "last_name": last})

    def test_redirects_with_a_message_keep_it_in_view(self):
        self.assertEqual(self.save("Ada", "Lovelace").headers["Location"], "/settings")
        self.app.config["ADMIN_EMAILS"] = frozenset({"a@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = True
        self.assertEqual(self.client.get("/admin").headers["Location"], "/settings")

    def test_name_changes_do_not_push_sign_ins_off_the_activity_list(self):
        for i in range(12):
            self.save(f"Name{i}", "X")
        html = self.client.get("/settings").get_data(as_text=True)
        activity = html.split('id="activity"', 1)[1].split("</section>", 1)[0]
        self.assertIn("<span>Signed in</span>", activity)
        self.assertNotIn("Name changed", activity)
        with self.db() as conn:  # still recorded for the admin page
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'profile_changed'").fetchone()[0], 12)

    def test_real_names_keep_their_joiners_and_blank_names_are_empty(self):
        sinhala = "ශ්‍රී"  # needs the zero width joiner
        persian = "می‌خواهم"  # zero width non joiner
        self.assertEqual(appmod.clean_name(sinhala), sinhala)
        self.assertEqual(appmod.clean_name(persian), persian)
        self.assertEqual(appmod.clean_name("Ada‮ Love"), "Ada Love")
        for blank in ("ㅤ", "⠀", "ᅟᅠ", "ﾠ ㅤ", "‍", " ‌ "):
            self.assertEqual(appmod.clean_name(blank), "", repr(blank))
        self.save("ㅤ", "⠀")
        with self.db() as conn:
            self.assertEqual(tuple(conn.execute("SELECT first_name, last_name FROM users")
                                   .fetchone()), ("", ""))
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('<span class="topnav-email" dir="auto">a@example.com</span>', html)

    def test_long_first_name_wraps_in_html_emails(self):
        self.save("A" * 60, "B")
        self.app.test_client().post("/forgot", data={"email": "a@example.com"})
        html = self.outbox[-1]["html"]
        greeting = html[html.rindex("<p", 0, html.index("Hi " + "A" * 60)):]
        self.assertIn("overflow-wrap:anywhere", greeting.split(">", 1)[0])

    def test_wording_says_settings_page(self):
        self.assertIn("Settings page", appmod.AUDIT_LABELS["code_failed"])
        secret = appmod.new_totp_secret()
        with self.db() as conn:
            conn.execute("UPDATE users SET totp_secret = ?", (secret,))
        result = self.app.test_cli_runner().invoke(args=["reset-2fa", "a@example.com"])
        self.assertIn("on the Settings page", result.output)
        self.assertIn("on the Settings page", self.outbox[-1]["text"])
        self.assertNotIn("account page", self.outbox[-1]["text"] + result.output)


class RightToLeftPdfTests(AppTestCase):
    def test_right_to_left_names_turn_on_text_shaping(self):
        self.assertTrue(package._needs_shaping("محمد"))  # Arabic
        self.assertTrue(package._needs_shaping("דוד"))  # Hebrew
        self.assertFalse(package._needs_shaping("Ada Lovelace 山田"))
        self.signup()
        self.client.post("/settings", data={"action": "profile",
                                            "first_name": "محمد",
                                            "last_name": "عبد"})
        self.set_plan("pro")
        self.new_year(2025)
        calls = []
        real = fpdf.FPDF.set_text_shaping

        def spy(pdf, *args, **kwargs):
            calls.append(args or kwargs)
            return real(pdf, *args, **kwargs)

        with mock.patch.object(fpdf.FPDF, "set_text_shaping", spy):
            resp = self.client.get("/year/2025/package")
            body = resp.data
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(body.startswith(b"PK"))
        self.assertEqual(calls, [(True,)])

    def test_left_to_right_packages_are_not_shaped(self):
        self.signup()
        self.set_plan("pro")
        self.new_year(2025)
        with mock.patch.object(fpdf.FPDF, "set_text_shaping") as shaping:
            self.client.get("/year/2025/package").data
        shaping.assert_not_called()
