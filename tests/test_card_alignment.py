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


if __name__ == "__main__":
    unittest.main()
