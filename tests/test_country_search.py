"""The country picker's search (static/js/theme.js), run in Node on the app's own country list:
it matches the start of a name, of a word or of an alias, never the middle of a word, and a
country found through an alias names that alias."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import app as appmod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class CountrySearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "static", "js", "theme.js"), encoding="utf-8") as f:
            js = f.read()
        code = js[js.index("  var fold = function (s) {"):
                  js.index("  // Favorite countries (the star in the list)")]
        data = [[c[0], c[1], list(c[2]) if len(c) > 2 else []] for c in appmod.COUNTRY_DATA]
        script = ("var countries = %s;\n%s\nvar out = {};\n"
                  "JSON.parse(process.argv[2]).forEach(function (q) { var r = search(q);\n"
                  "  out[q] = r.map(function (e, i) { return [e.name, r.hints[i] || \"\"]; }); });\n"
                  "console.log(JSON.stringify(out));\n" % (json.dumps(data), code))
        cls.script = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
        cls.script.write(script)
        cls.script.close()

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.script.name)

    def search(self, *queries):
        out = subprocess.run(["node", self.script.name, json.dumps(list(queries))],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def test_letters_inside_a_word_do_not_match(self):
        found = self.search("ita", "it", "land", "stan")
        # "ita" found Mauritania, and United Kingdom through the alias "Great Britain".
        self.assertEqual(found["ita"], [["Italy", ""]])
        self.assertEqual(found["it"], [["Italy", ""]])
        self.assertEqual(found["land"], [])
        self.assertEqual(found["stan"], [])

    def test_names_words_and_codes_still_match(self):
        found = self.search("new ", "zealand", "gb", "guinea bissau", "united")
        self.assertEqual([n for n, _ in found["new "]], ["New Caledonia", "New Zealand", "Papua New Guinea"])
        self.assertEqual(found["zealand"], [["New Zealand", ""]])
        self.assertEqual(found["gb"][0], ["United Kingdom", ""])
        self.assertEqual(found["guinea bissau"][0], ["Guinea-Bissau", ""])
        self.assertIn(["United Kingdom", ""], found["united"])

    def test_a_country_found_through_an_alias_names_it(self):
        found = self.search("eng", "holland", "st lucia")
        self.assertEqual(found["eng"], [["United Kingdom", "England"]])
        self.assertEqual(found["holland"], [["Netherlands", "Holland"]])
        self.assertEqual(found["st lucia"][0], ["Saint Lucia", "St Lucia"])

    def test_the_hint_is_shown_in_the_list(self):
        with open(os.path.join(ROOT, "static", "js", "theme.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('via.className = "combo-hint";', js)
        self.assertIn('via.textContent = "(" + hint + ")";', js)


if __name__ == "__main__":
    unittest.main()
