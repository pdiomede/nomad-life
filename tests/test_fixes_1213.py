"""Regression tests for the fixes in 1.2.13."""
import re

from tests.helpers import AppTestCase


class FooterTests(AppTestCase):
    def test_footer_name_links_home_in_the_same_window(self):
        for signed_in in (False, True):
            with self.subTest(signed_in=signed_in):
                if signed_in:
                    self.signup()
                html = self.client.get("/settings" if signed_in else "/").get_data(as_text=True)
                footer = html.split('<footer class="footer">', 1)[1].split("</footer>", 1)[0]
                self.assertIn('<a href="/">Nomad Life</a> v', footer)


class TwoFactorCardTests(AppTestCase):
    def settings_html(self, secret=None):
        self.signup()
        if secret:
            with self.db() as conn:
                conn.execute("UPDATE users SET totp_secret = ?", (secret,))
        return self.client.get("/settings").get_data(as_text=True)

    def test_turn_off_button_is_red(self):
        html = self.settings_html("JBSWY3DPEHPK3PXP")
        self.assertIn('<button class="btn btn-danger" type="submit">Turn off two-factor sign in</button>',
                      html)

    def assert_intro_spaced(self, html):
        card = html.split('id="two-factor"', 1)[1].split("</section>", 1)[0]
        self.assertIn('<p class="muted small card-intro">', card)
        with open(self.app.static_folder + "/css/style.css", encoding="utf-8") as fh:
            self.assertTrue(re.search(r"\.card-intro \{ margin-bottom: 16px; \}", fh.read()))

    def test_intro_text_has_space_before_the_form_when_on(self):
        self.assert_intro_spaced(self.settings_html("JBSWY3DPEHPK3PXP"))

    def test_intro_text_has_space_before_the_setup_when_off(self):
        self.assert_intro_spaced(self.settings_html())
