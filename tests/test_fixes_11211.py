"""Regression tests for the review of 1.12.11 (the whole app and its interface)."""
import os
from datetime import date

import app as appmod
import db
import package
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


class PackageSoFarTests(AppTestCase):
    def test_the_total_so_far_adds_up_the_rows(self):
        # Past days without a stay count nowhere now, so the total is not the elapsed days.
        today = date.today()
        if (today - date(today.year, 1, 1)).days < 11:
            self.skipTest("needs the first ten days of the year behind us")
        y = today.year
        self.signup()
        self.set_plan("pro")
        self.new_year(y, "Lisbon", "Portugal")
        self.add_movement(y, "Madrid", "Spain", f"{y}-01-01", f"{y}-01-10")
        with self.app.test_request_context():
            row = db.query("SELECT * FROM years WHERE year = ?", (y,), one=True)
            data = appmod.build_package_data(row, False)
            text = package.summary_text(data)
        self.assertIn("Total |  | 10 | 2.7% | 10", text)
        unlogged = data.elapsed - 10
        self.assertIn(f"Days without a stay so far (not counted): {unlogged}", text)


class StaleTextTests(AppTestCase):
    def test_the_texts_say_what_the_app_does(self):
        docs = read("templates", "docs.html")
        self.assertNotIn("is a stay away from your base", docs)
        self.assertIn("or your time at the base (only logged days count)", docs)
        self.assertNotIn("see its date, country and city", docs)  # the calendar names the country
        self.assertIn("01_<start date>_[<city>_]<country>", read("package.py"))


class PickerFixTests(AppTestCase):
    """The country picker, the date calendar and the receipt type list."""

    def test_a_star_or_a_tab_keeps_the_whole_list(self):
        # On a page whose field holds a country, starring Japan left only that country listed.
        js = read("static", "js", "theme.js")
        self.assertIn("filtered = !!filter && !current;", js)
        self.assertIn("var items = listFor(filtered);", js)

    def test_the_calendar_keeps_the_focus(self):
        js = read("static", "js", "theme.js")
        # A disabled month button dropped the focus to the page at January and December.
        self.assertIn('prev.setAttribute("aria-disabled", !!min && new Date(y, m, 0) < min ? "true" : "false");', js)
        self.assertNotIn("prev.disabled =", js)
        # A click on the title or the padding moved the focus out of the calendar.
        self.assertIn('pop.addEventListener("mousedown", function (e) { if (!e.target.closest("button")) e.preventDefault(); });', js)
        self.assertIn('.date-nav[aria-disabled="true"] {', read("static", "css", "style.css"))

    def test_escape_closes_the_calendar_from_the_date_button(self):
        js = read("static", "js", "theme.js")
        self.assertIn('wrap.addEventListener("keydown", function (e) {  // also from the date button', js)

    def test_the_receipt_type_list_closes_on_a_click_outside(self):
        js = read("static", "js", "theme.js")
        self.assertIn('document.addEventListener("mousedown", function (e) { if (!list.hidden && !wrap.contains(e.target)) close(); });', js)


class NewYearDefaultTests(AppTestCase):
    def test_the_year_offered_is_one_the_account_does_not_have(self):
        self.signup()
        y = date.today().year
        html = self.client.get("/year/new").get_data(as_text=True)
        self.assertIn(f'value="{y}" required', html)
        self.new_year(y)
        self.new_year(y + 1)
        html = self.client.get(f"/year/new?back={y}").get_data(as_text=True)
        self.assertIn(f'value="{y + 2}" required', html)


class InterfaceReviewTests(AppTestCase):
    def test_the_docs_match_the_app(self):
        docs = read("templates", "docs.html")
        self.assertNotIn("empty future days count nowhere yet", docs)  # past ones count nowhere too
        self.assertNotIn("290 of 365, At least 183 days", docs)  # the marker starts off
        self.assertIn("the Upload button, below two saved receipts", docs)

    def test_the_screenshots_have_focus(self):
        # Without it focus and blur never fire headless, and the year setup shot kept a list open.
        self.assertIn('await page.send("Emulation.setFocusEmulationEnabled", { enabled: true });',
                      read("scripts", "docs_screenshots.mjs"))

    def test_the_busy_download_says_so_to_screen_readers(self):
        # The aria-label hid "Preparing package..." from screen readers.
        js = read("static", "js", "theme.js")
        self.assertIn('if (name) btn.setAttribute("aria-label", "Preparing package...");', js)
        self.assertIn('if (name) btn.setAttribute("aria-label", name);', js)

    def test_outlined_buttons_keep_3_to_1_on_todays_row(self):
        css = read("static", "css", "style.css")
        self.assertEqual(css.count("--btn-border: #8088A3;"), 2)  # both dark blocks
