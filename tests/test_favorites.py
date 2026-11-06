"""Favorite countries in the country picker (the star), saved with the account; the base country
first in the list; and the styled receipt type list."""
import os

import app as appmod
from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


class FavoriteCountryTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def star(self, country, action="add", client=None):
        return (client or self.client).post("/favorites/countries",
                                            data={"country": country, "action": action})

    def stored(self, email="a@example.com"):
        with self.db() as conn:
            return [r[0] for r in conn.execute(
                "SELECT country FROM favorite_countries f JOIN users u ON u.id = f.user_id "
                "WHERE u.email = ? ORDER BY country", (email,))]

    def test_add_and_remove(self):
        resp = self.star("Italy")
        self.assertEqual(resp.get_json(), {"favorites": ["Italy"]})
        self.assertEqual(self.star("italy").get_json(), {"favorites": ["Italy"]})  # once only
        self.star("Japan")
        self.assertEqual(self.stored(), ["Italy", "Japan"])
        self.assertEqual(self.star("Italy", "remove").get_json(), {"favorites": ["Japan"]})
        self.assertEqual(self.stored(), ["Japan"])

    def test_only_countries_of_the_list(self):
        for country, action in (("Atlantis", "add"), ("Italy", "pin"), ("", "add")):
            self.assertEqual(self.star(country, action).status_code, 400)
        self.assertEqual(self.stored(), [])

    def test_at_most_the_cap(self):
        names = appmod.COUNTRIES[:appmod.FAVORITES_MAX]
        with self.db() as conn:
            conn.executemany("INSERT INTO favorite_countries (user_id, country) VALUES (1, ?)",
                             [(c,) for c in names])
        resp = self.star(appmod.COUNTRIES[appmod.FAVORITES_MAX])
        self.assertEqual(resp.status_code, 409)
        self.assertIn("up to 50 favorite countries", resp.get_json()["error"])
        self.assertEqual(len(self.stored()), appmod.FAVORITES_MAX)
        self.assertEqual(self.star(names[0]).status_code, 200)  # already a favorite: fine

    def test_each_account_has_its_own_and_they_go_with_it(self):
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.star("Italy")
        self.star("Peru", client=other)
        self.assertEqual(self.stored(), ["Italy"])
        self.assertEqual(self.stored("b@example.com"), ["Peru"])
        other.post("/settings", data={"action": "delete", "confirm_email": "b@example.com",
                                      "current_password": "password1", "confirm_delete": "2"})
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM favorite_countries").fetchone()[0], 1)

    def test_needs_sign_in_and_the_csrf_token(self):
        self.assertEqual(self.app.test_client().post(
            "/favorites/countries", data={"country": "Italy", "action": "add"}).status_code, 302)
        self.app.config["WTF_CSRF_ENABLED"] = True
        try:
            self.assertNotEqual(self.star("Italy").status_code, 200)  # refused (the CSRF handler)
        finally:
            self.app.config["WTF_CSRF_ENABLED"] = False
        self.assertEqual(self.stored(), [])

    def test_the_pages_carry_the_favorites_and_the_base(self):
        self.new_year(2026, "Sofia", "Bulgaria")
        self.star("Japan")
        self.star("Italy")
        html = self.client.get("/year/2026/movements/new").get_data(as_text=True)
        self.assertIn('id="country-favorites" data-url="/favorites/countries" data-csrf="', html)
        self.assertIn('>["Italy", "Japan"]</script>', html)
        self.assertIn('data-country-combo data-base-country="Bulgaria"', html)
        base = self.client.get("/year/2026/base").get_data(as_text=True)
        self.assertIn('data-base-country="Bulgaria"', base)


class PickerScriptTests(AppTestCase):
    def test_tabs_star_base_and_keys(self):
        js = read("static", "js", "theme.js")
        self.assertIn('tabAll.textContent = "All countries";', js)
        self.assertIn('tabFav.textContent = "Favorites (" + favCount() + ")";', js)
        self.assertIn('star.className = "combo-star";', js)
        self.assertIn('home.className = "combo-home";', js)
        self.assertIn('if (e.key === "Enter" && e.shiftKey)', js)  # Shift+Enter stars
        self.assertIn('e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")', js)
        css = read("static", "css", "style.css")
        self.assertIn('.combo-star[aria-pressed="true"] { opacity: 1; color: var(--star);', css)
        self.assertIn(".combo-home { color: var(--home);", css)
        for block in (css[:css.index('[data-theme="dark"] {')],
                      css[css.index(':root[data-theme="dark"] {'):css.index("@media (prefers-color-scheme: dark)")],
                      css[css.index("@media (prefers-color-scheme: dark)"):]):
            self.assertIn("--star:", block)
            self.assertIn("--home:", block)


class PickerFixTests(AppTestCase):
    """Found in the review of the new pickers."""

    def test_the_star_is_a_big_enough_target(self):
        # 18 px inside a clickable row: a near miss chose the country instead.
        css = read("static", "css", "style.css")
        self.assertIn(".combo-star { width: 30px; height: 30px; margin: -6px -6px -6px auto; "
                      "-webkit-mask-size: 18px; mask-size: 18px;", css)

    def test_the_keyboard_ways_are_described(self):
        js = read("static", "js", "theme.js")
        self.assertIn("Shift+Enter adds the highlighted country to your favorites or removes it; "
                      "Alt+Right shows your favorites, Alt+Left all countries.", js)
        self.assertIn('input.setAttribute("aria-describedby"', js)

    def test_an_older_answer_does_not_undo_a_newer_star(self):
        js = read("static", "js", "theme.js")
        self.assertIn("var adding = !favorites[e.name], mine = ++saving;", js)
        self.assertEqual(js.count("if (mine !== saving) return;"), 2)  # the answer and the undo


class ReceiptTypeListTests(AppTestCase):
    def test_the_selects_become_a_styled_list(self):
        self.signup()
        self.new_year(2026)
        html = self.client.get("/year/2026/movements/new").get_data(as_text=True)
        self.assertIn('<select id="kind-new" name="kind" data-select-menu>', html)
        base = self.client.get("/year/2026/base").get_data(as_text=True)
        self.assertIn('name="kind" data-select-menu>', base)
        js = read("static", "js", "theme.js")
        self.assertIn('document.querySelectorAll("select[data-select-menu]")', js)
        self.assertIn('oi.className = "kind-icon kind-" + o.value;', js)
        self.assertIn("select.hidden = true;", js)  # still in the form: the value is posted
        css = read("static", "css", "style.css")
        for kind in appmod.DOCUMENT_KINDS:
            self.assertIn(f".kind-{kind} {{ --kind-icon:", css)
