"""Regression tests for the four bugs left from the 1.5.3 review: pages left open after their
account, year or movement was deleted, the endless blinking, and the price form on phones."""
import os

from tests.helpers import AppTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class StaleAdminPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")
        with self.db() as conn:
            self.uid = conn.execute("INSERT INTO users (email, password_hash, verified_at) "
                                    "VALUES ('gone@example.com', 'x', '2026-01-01')").lastrowid

    def test_actions_on_an_account_deleted_meanwhile(self):
        with self.db() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (self.uid,))
        for data in ({"action": "plan", "plan": "pro"}, {"action": "quota", "quota_mb": "5"},
                     {"action": "disable"}, {"action": "enable"}):
            resp = self.client.post(f"/admin/users/{self.uid}", data=dict(data, search="gone"),
                                    follow_redirects=False)
            self.assertEqual(resp.status_code, 302, data)  # not a bare 404
            self.assertEqual(resp.headers["Location"], "/admin?search=gone")
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("This account no longer exists.", html)

    def test_two_admins_deleting_at_once_report_it_once(self):
        import app as appmod
        with self.app.test_request_context():
            self.assertTrue(appmod.delete_account(self.uid))
            self.assertFalse(appmod.delete_account(self.uid))  # was: success again
        with self.db() as conn:
            conn.execute("INSERT INTO users (id, email, password_hash, verified_at) "
                         "VALUES (?, 'gone@example.com', 'x', '2026-01-01')", (self.uid,))
        # The second admin's request reads the row, then finds it deleted at the write.
        real = appmod.delete_account
        appmod.delete_account = lambda uid: False
        try:
            html = self.client.post(f"/admin/users/{self.uid}", data={
                "action": "delete", "confirm_delete": "2", "confirm_email": "gone@example.com"},
                follow_redirects=True).get_data(as_text=True)
        finally:
            appmod.delete_account = real
        self.assertIn("The account gone@example.com was already deleted.", html)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = "
                                          "'admin_delete'").fetchone()[0], 0)


class StaleMovementPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.client.post("/year/new", data={"year": "2027", "base_city": "Lisbon",
                                            "base_country": "Portugal"})
        self.client.post("/year/2027/movements/new", data={
            "city": "Rome", "country": "Italy", "start_date": "2027-03-01",
            "end_date": "2027-03-05"})

    def delete_everything(self):
        with self.db() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM years")

    def test_forms_left_open_after_a_delete_explain_it(self):
        self.delete_everything()
        cases = [("/year/2027/movements/new", {"city": "Paris", "country": "France",
                                                "start_date": "2027-04-01",
                                                "end_date": "2027-04-02"},
                  "The year 2027 no longer exists, so the movement was not saved."),
                 ("/movements/1", {"action": "update", "city": "Rome", "country": "Italy",
                                   "start_date": "2027-03-01", "end_date": "2027-03-05"},
                  "This movement no longer exists, so the changes were not saved."),
                 ("/movements/1", {"action": "delete", "confirm_delete": "2"},
                  "This movement was already deleted."),
                 ("/movements/1", {"action": "upload"},
                  "This stay or year no longer exists, so the file was not saved.")]
        for path, data, message in cases:
            resp = self.client.post(path, data=data)
            self.assertEqual(resp.status_code, 302, path)
            html = self.client.get(resp.headers["Location"], follow_redirects=True).get_data(
                as_text=True)
            self.assertIn(message, html)
        # Opening such a page is still "not found".
        self.assertEqual(self.client.get("/movements/1").status_code, 404)

    def test_another_users_movement_answers_like_a_missing_one(self):
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        mine = other.post("/movements/1", data={"action": "delete", "confirm_delete": "2"})
        missing = other.post("/movements/999", data={"action": "delete", "confirm_delete": "2"})
        self.assertEqual((mine.status_code, mine.headers["Location"]),
                         (missing.status_code, missing.headers["Location"]))
        with self.db() as conn:  # and nothing of the owner's was touched
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM movements").fetchone()[0], 1)
        self.assertEqual(other.get("/movements/1").status_code, 404)


class BlinkAndPriceFormTests(AppTestCase):
    def test_triangle_stops_blinking_once_its_report_was_opened(self):
        self.signup()
        self.client.post("/year/new", data={"year": "2027", "base_city": "Lisbon",
                                            "base_country": "Portugal"})
        for city, start in (("Vienna", "2027-01-01"), ("Sidney", "2027-01-08")):
            self.client.post("/year/2027/movements/new", data={
                "city": city, "country": "Austria", "start_date": start,
                "end_date": "2027-01-10"})
        html = self.client.get("/year/2027").get_data(as_text=True)
        self.assertRegex(html, r'data-overlap-key="2027:[0-9a-f]{16}"')
        js = read("static", "js", "theme.js")
        self.assertIn('overlapOpen.classList.add("is-seen");', js)
        self.assertIn('var overlapKey = "nl_overlap_seen_" + seenKey.split(":")[0];', js)
        css = read("static", "css", "style.css")
        self.assertIn(".overlap-alert.is-seen svg { animation: none; }", css)
        # Only the icon blinks: the tooltip, part of the button, stays fully visible.
        self.assertIn(".overlap-alert svg { animation: overlap-blink", css)
        import re
        button_rule = re.search(r"\n\.overlap-alert \{([^}]*)\}", css)[1]
        self.assertNotIn("animation", button_rule)

    def test_price_labels_stay_with_their_fields(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertEqual(html.count('<span class="price-field">'), 4)
        self.assertIn(".admin-price .price-field { display: inline-flex; align-items: center; "
                      "gap: 6px; white-space: nowrap; }", read("static", "css", "style.css"))
