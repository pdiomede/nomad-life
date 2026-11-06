"""The From and To fields of a movement: a month calendar in the app's look (theme.js) on top of
the real date inputs, which stay in the form so the posted value is unchanged."""
import os

from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


class DatePickerTests(AppTestCase):
    def test_the_movement_dates_are_marked_and_keep_their_limits(self):
        self.signup()
        self.new_year(2026)
        html = self.client.get("/year/2026/movements/new").get_data(as_text=True)
        for name in ("start_date", "end_date"):
            self.assertIn(f'<input id="{name}" name="{name}" type="date" data-date-picker '
                          f'min="2026-01-01" max="2026-12-31" value="" required>', html)

    def test_the_calendar(self):
        js = read("static", "js", "theme.js")
        block = js[js.index('document.querySelectorAll("input[data-date-picker]")'):]
        block = block[:block.index("  // Downloads (accountant package)")]
        for text in ('btn.id = input.id + "-picker";', 'grid.setAttribute("role", "grid");',
                     'pop.setAttribute("aria-label", "Choose a date");',
                     'e.key === "PageUp" || e.key === "PageDown"', 'day.setAttribute("aria-current", "date");',
                     'day.setAttribute("aria-disabled", "true");', 'error.textContent = "Please choose a date.";',
                     # A hidden required input would stop the form with no message.
                     "input.required = false;",
                     # Both empty: the focus goes to the first one.
                     "if (input.form.querySelector('.date-btn[aria-invalid=\"true\"]') === btn) btn.focus();",
                     # An empty To opens on the month of From.
                     'input.id === "end_date"'):
            self.assertIn(text, block)
        css = read("static", "css", "style.css")
        for rule in (".date-btn {", ".date-pop {", ".date-day.is-chosen {", ".date-day.is-today {",
                     ".date-day.is-off {"):
            self.assertIn(rule, css)

    def test_the_posted_dates_are_unchanged(self):
        self.signup()
        self.new_year(2026)
        self.add_movement(2026, "Rome", "Italy", "2026-03-01", "2026-03-05")
        with self.db() as conn:
            row = conn.execute("SELECT start_date, end_date FROM movements").fetchone()
        self.assertEqual(tuple(row), ("2026-03-01", "2026-03-05"))
