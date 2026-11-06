"""Regression tests for 1.12.10: the year dashboard lays Movements and Map out over the full
width, with Days per country, Receipt storage and Accountant package in one row below."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def css():
    with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as f:
        return f.read()


def rule(text, selector):
    match = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", text)
    return match.group(1) if match else ""


class DashboardLayoutTests(unittest.TestCase):
    def test_main_grid_is_one_column(self):
        self.assertIn("grid-template-columns: minmax(0, 1fr);", rule(css(), ".grid-main"))

    def test_side_cards_share_one_row(self):
        body = rule(css(), ".side")
        self.assertIn("display: grid;", body)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr));", body)

    def test_side_cards_stack_on_narrow_screens(self):
        text = css()
        narrow = text[text.index("@media (max-width: 960px)"):text.index("@media (max-width: 600px)")]
        self.assertIn(".side { grid-template-columns: minmax(0, 1fr); }", narrow)


if __name__ == "__main__":
    unittest.main()
