"""Dashboard cards in a row share one height: the title stays at the top and the content sits at
the bottom (the four top cards and the three cards under the Map)."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def css():
    with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as f:
        return f.read()


class CardAlignmentTests(unittest.TestCase):
    def test_top_cards_put_their_content_at_the_bottom(self):
        # Scoped to the dashboard: the admin page's .stat cards keep their layout.
        self.assertIn(".year-stats .stat > .stat-label + *, .year-stats .stat > .stat-head + * "
                      "{ margin-top: auto; }", css())

    def test_cards_under_the_map_put_their_content_at_the_bottom(self):
        text = css()
        self.assertIn(".side > .card { display: flex; flex-direction: column; }", text)
        self.assertIn(".side > .card > .card-head + * { margin-top: auto; }", text)


class YearActionsTests(unittest.TestCase):
    def test_the_year_tabs_and_add_movement_share_one_size(self):
        text = css()
        self.assertIn(".year-actions { align-items: stretch; }", text)
        self.assertIn(".year-tab { display: inline-flex; align-items: center; justify-content: center; "
                      "min-width: 68px;", text)
        with open(os.path.join(ROOT, "templates", "dashboard.html"), encoding="utf-8") as f:
            self.assertIn('<div class="row wrap gap-sm year-actions">', f.read())


class PackageButtonTests(unittest.TestCase):
    def test_the_button_says_download_with_a_tooltip(self):
        with open(os.path.join(ROOT, "templates", "dashboard.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn('<button class="btn btn-primary" type="submit" data-tip="Download Accountant Package" '
                      'aria-label="Download Accountant Package"><span class="btn-icon icon-download" '
                      'aria-hidden="true"></span>Download</button>', html)
        with open(os.path.join(ROOT, "static", "js", "theme.js"), encoding="utf-8") as f:
            js = f.read()
        # The icon comes back after "Preparing package...".
        self.assertIn('var label = btn ? btn.innerHTML : "";', js)
        self.assertIn("btn.innerHTML = label;", js)
        self.assertIn(".icon-btn[data-tip]::after, .btn[data-tip]::after {", css())
        self.assertIn(".btn[data-tip]::after { left: 0; transform: translate(0, -2px); }", css())


class OutlinedButtonTests(unittest.TestCase):
    def test_outlined_buttons_have_a_visible_border(self):
        # --border was 1.2:1 on a card: Choose file and Cancel had almost no edge.
        text = css()
        self.assertIn(".btn-ghost { background: transparent; color: var(--text); "
                      "border-color: var(--btn-border); }", text)
        light = text[:text.index('[data-theme="dark"] {')]
        dark = text[text.index(':root[data-theme="dark"] {'):text.index("@media (prefers-color-scheme: dark)")]
        system = text[text.index("@media (prefers-color-scheme: dark)"):]
        self.assertIn("--btn-border: #80869A;", light)
        self.assertIn("--btn-border: #767D98;", dark)
        self.assertIn("--btn-border: #767D98;", system[:system.index("\n}\n")])


if __name__ == "__main__":
    unittest.main()
