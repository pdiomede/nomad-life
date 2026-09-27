"""Header user menu: the email opens a panel with the plan, Admin (admins only) and Sign out."""
import unittest

from tests.helpers import AppTestCase


class UserMenuTests(AppTestCase):
    def menu(self, client=None):
        html = (client or self.client).get("/plan").get_data(as_text=True)
        start = html.index('<details class="user-menu"')
        return html[start:html.index("</details>", start)]

    def test_email_opens_a_menu_with_plan_and_sign_out(self):
        self.signup("user@example.com")
        menu = self.menu()
        self.assertIn('aria-label="Account menu for user@example.com"', menu)
        self.assertIn('<span class="topnav-email">user@example.com</span>', menu)
        panel = menu[menu.index("user-menu-panel"):]
        self.assertLess(panel.index("plan-chip"), panel.index("Sign out"))
        self.assertIn('action="/logout"', panel)
        self.assertIn('name="csrf_token"', panel)
        self.assertNotIn("/admin", menu)
        self.assertNotIn("/account", menu)

    def test_admin_item_only_for_admins(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.signup("admin@example.com")
        panel = self.menu()
        self.assertLess(panel.index("plan-chip"), panel.index('href="/admin">Admin</a>'))
        self.assertLess(panel.index('href="/admin">Admin</a>'), panel.index("Sign out"))

    def test_no_nav_items_outside_the_menu(self):
        self.signup("user@example.com")
        html = self.client.get("/plan").get_data(as_text=True)
        nav = html[html.index('<nav class="topnav">'):html.index("</nav>")]
        outside = nav.replace(nav[nav.index("<details"):nav.index("</details>")], "")
        self.assertNotIn("plan-chip", outside)
        self.assertNotIn("Sign out", outside)

    def test_no_menu_when_signed_out(self):
        for url in ("/", "/login"):
            self.assertNotIn("user-menu", self.client.get(url).get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
