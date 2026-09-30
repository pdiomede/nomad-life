"""Regression tests for the bugs found in the 1.5.1 review of the admin accounts table, the
documentation and the header, and for the flash message that made the page jump."""
import os
import re

from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class FakeChipLayoutTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")
        with self.db() as conn:
            conn.execute("INSERT INTO users (email, password_hash, created_at, verified_at) "
                         "VALUES ('fake@corp.example', 'x', datetime('now', '-3 days'), "
                         "'2026-01-01')")

    def test_on_phones_the_chip_and_its_reason_sit_under_the_email(self):
        html = self.client.get("/admin").get_data(as_text=True)
        # The Check column is hidden on phones, where it squeezed emails mid-word.
        self.assertIn('<th scope="col" class="admin-check hide-sm">', html)
        self.assertIn('<td class="admin-check hide-sm"><span class="pill pill-error chip-fake"', html)
        # There the reason is plain text (a tooltip is hard to reach with a finger).
        self.assertRegex(html, r'<p class="admin-fake-sm show-sm"><span class="pill pill-error '
                               r'chip-fake">Likely fake</span> <span class="muted small">'
                               r'Confirmed; never signed in')

    def test_hidden_tooltip_takes_no_room(self):
        css = read("static", "css", "style.css")
        rule = re.search(r"\.chip-fake\[data-tip\]::after \{(.*?)\}", css, re.S)[1]
        self.assertIn("display: none;", rule)  # a transparent box still widened the table
        self.assertNotIn("opacity: 0", rule)

    def test_filter_is_three_equal_buttons_on_phones(self):
        css = read("static", "css", "style.css")
        self.assertIn(".admin-filter { display: grid; grid-template-columns: repeat(3, "
                      "minmax(0, 1fr)); width: 100%; }", css)


class HeaderAndFlashTests(AppTestCase):
    def test_landing_header_fits_phones(self):
        css = read("static", "css", "style.css")
        block = css[css.index("@media (max-width: 420px) {\n  /* The landing header"):]
        block = block[:block.index("\n}\n")]
        self.assertIn(".btn-launch { padding: 6px 8px; font-size: 13px; margin-left: 0; }", block)
        self.assertIn(".topnav:has(.btn-launch) { gap: 4px; }", block)

    def test_dismissing_a_message_does_not_scroll_the_page(self):
        js = read("static", "js", "theme.js")
        self.assertNotIn("main.focus();", js)
        self.assertIn('main.focus({ preventScroll: true });', js)
        self.assertIn('.focus({ preventScroll: true });\n          return;', js)
        self.assertIn('box.classList.add("is-collapsing");', js)
        css = read("static", "css", "style.css")
        self.assertIn("main:focus { outline: none; }", css)
        self.assertIn(".flash.is-collapsing, .flashes.is-collapsing {", css)
