"""Flash messages can be dismissed."""
import unittest

from tests.helpers import AppTestCase


class DismissFlashTests(AppTestCase):
    def test_flash_has_dismiss_button(self):
        self.signup()
        html = self.client.post("/logout", follow_redirects=True).get_data(as_text=True)
        self.assertIn("You have been signed out.", html)
        self.assertIn('class="flash-close" aria-label="Dismiss message" data-dismiss-flash hidden', html)

    def test_no_button_without_flash(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertNotIn("data-dismiss-flash", html)


if __name__ == "__main__":
    unittest.main()
