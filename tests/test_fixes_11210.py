"""Regression tests for 1.12.10: the year dashboard lays Movements and Map out over the full
width, with Days per Country, Receipt Storage and Accountant Package in one row below, and the
fixes of the review of storing data and of the interface."""
import os
import re
import unittest
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase
from tests.test_billing import BillingCase
from tests.test_fixes_1125 import refusing
from tests.test_fixes_158 import other_connection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = b"%PDF-1.4\n" + b"receipt " * 64
DELETE = {"action": "delete", "confirm_email": "a@example.com",
          "current_password": "password1", "confirm_delete": "2"}


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


def css():
    return read("static", "css", "style.css")


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

    def test_storage_and_package_share_a_row_and_days_spans_two(self):
        text = css()
        self.assertIn("grid-template-rows: auto 1fr;", rule(text, ".side"))
        self.assertIn("row-gap: 0;", rule(text, ".side"))
        self.assertIn("grid-row: 1 / span 2;", rule(text, ".side > .days-card"))
        self.assertIn("grid-row: 1;", rule(text, ".side > .storage-card"))
        self.assertIn("grid-row: 1;", rule(text, ".side > .package-card"))
        self.assertIn("grid-column: 3;", rule(text, ".side > .package-card"))

    def test_side_cards_stack_on_narrow_screens(self):
        text = css()
        narrow = text[text.index("@media (max-width: 960px)"):text.index("@media (max-width: 600px)")]
        self.assertIn("grid-template-columns: minmax(0, 1fr); grid-template-rows: none;", narrow)
        self.assertIn(".side > .days-card, .side > .storage-card, .side > .package-card { grid-column: auto; grid-row: auto; }", narrow)


class ReceiptNameTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def stored(self):
        with self.db() as conn:
            return conn.execute("SELECT original_name, kind FROM documents").fetchone()

    def test_zero_width_joiners_are_kept(self):
        # Persian needs the non joiner (U+200C) and emoji families the joiner (U+200D), as in
        # names and places; dropping them misspells the word or splits the emoji.
        persian = "می‌شود"
        self.upload("/year/2026/base", persian + ".pdf", PDF)
        self.assertEqual(self.stored()["original_name"], persian + ".pdf")
        family = "Family \U0001f468‍\U0001f469‍\U0001f467 trip"
        self.client.post("/documents/1/edit", data={"name": family, "kind": "other"})
        self.assertEqual(self.stored()["original_name"], family + ".pdf")
        self.assertEqual(appmod.display_name("a‮b​c.pdf"), "abc.pdf")  # others go

    def test_the_limit_counts_the_extension(self):
        self.upload("/year/2026/base", "a.pdf", PDF)
        self.client.post("/documents/1/edit", data={"name": "A" * 150, "kind": "other"})
        name = self.stored()["original_name"]
        self.assertEqual(len(name), 150)
        self.assertTrue(name.endswith(".pdf"))
        # Saving the form again with only the type changed keeps the name as it is.
        self.client.post("/documents/1/edit", data={"name": name, "kind": "flight"})
        self.assertEqual(tuple(self.stored()), (name, "flight"))


class SignInFormTests(AppTestCase):
    def test_remember_me_stays_ticked_after_a_failed_sign_in(self):
        self.signup()
        client = self.app.test_client()
        html = client.post("/login", data={"email": "a@example.com", "password": "wrong-one",
                                           "remember": "1"}).get_data(as_text=True)
        self.assertIn('name="remember" value="1" checked', html)
        html = client.post("/login", data={"email": "a@example.com",
                                           "password": "wrong-one"}).get_data(as_text=True)
        self.assertNotIn('name="remember" value="1" checked', html)


class DeleteAsksStripeTests(BillingCase):
    """A payment whose webhook is late is not stored yet: deleting the account then would
    leave Stripe charging it every year, with no account left to cancel it from."""

    def setUp(self):
        super().setUp()
        with self.db() as conn:
            conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")

    def delete(self, fake, client=None, url="/settings", data=DELETE):
        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            return (client or self.client).post(url, data=data, follow_redirects=True
                                                 ).get_data(as_text=True)

    def test_a_late_webhook_does_not_let_the_account_go(self):
        fake, calls = refusing("", {"subscriptions?": {"data": [self.sub()]}})
        html = self.delete(fake)
        self.assertIn("Your plan is still paid for", html)
        self.assertIsNotNone(self.user())
        self.assertEqual([p for _, p, _ in calls],
                         ["subscriptions?customer=cus_1&status=all&limit=20"])

    def test_nothing_is_deleted_when_stripe_cannot_answer(self):
        fake, _ = refusing("Something went wrong on Stripe's end.")
        html = self.delete(fake)
        self.assertIn("could not check your plan", html)
        self.assertIsNotNone(self.user())

    def test_a_customer_stripe_no_longer_knows_does_not_block(self):
        fake, _ = refusing("No such customer: 'cus_1'")
        self.delete(fake)
        self.assertIsNone(self.user())

    def test_a_plan_cancelled_by_date_does_not_block(self):
        fake, _ = refusing("", {"subscriptions?": {"data": [
            self.sub(cancel_at=1_820_000_000), self.sub(sub_id="sub_0", status="canceled")]}})
        self.delete(fake)
        self.assertIsNone(self.user())

    def test_admin_delete_asks_stripe_too(self):
        self.app.config.update(ADMIN_EMAILS=frozenset({"admin@example.com"}),
                               ADMIN_REQUIRE_2FA=False)
        admin = self.app.test_client()
        self.signup("admin@example.com", client=admin)
        data = {"action": "delete", "confirm_email": "a@example.com", "confirm_delete": "2"}
        fake, _ = refusing("", {"subscriptions?": {"data": [self.sub(status="past_due")]}})
        html = self.delete(fake, admin, "/admin/users/1", data)
        self.assertIn("still pays for a plan", html)
        self.assertIsNotNone(self.user())
        fake, _ = refusing("Something went wrong on Stripe's end.")
        self.assertIn("Stripe could not be asked", self.delete(fake, admin, "/admin/users/1", data))
        self.assertIsNotNone(self.user())

    def test_a_webhook_stored_during_the_delete_stops_it(self):
        # The check passes, then the subscription is stored before the DELETE runs: the DELETE
        # repeats the rule, so the account stays.
        def webhook_meanwhile(row):
            conn = other_connection(self)
            conn.execute("INSERT INTO subscriptions (user_id, subscription_id, customer_id, "
                         "plan, status) VALUES (1, 'sub_1', 'cus_1', 'pro', 'active')")
            conn.commit()
            conn.close()
            return False
        with mock.patch.object(appmod, "stripe_charging", side_effect=webhook_meanwhile):
            html = self.client.post("/settings", data=DELETE,
                                    follow_redirects=True).get_data(as_text=True)
        self.assertIn("Your plan is still paid for", html)
        self.assertNotIn("password of this account was changed", html)
        self.assertIsNotNone(self.user())


class FoldingMapTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)
        self.html = self.client.get("/year/2026").get_data(as_text=True)

    def test_the_map_starts_folded_and_opens_from_its_head(self):
        self.assertIn('<details class="card map-card">', self.html)  # no "open": folded
        card = self.html[self.html.index('<details class="card map-card">'):]
        card = card[:card.index("</details>")]
        summary = card[card.index('<summary class="card-head map-summary">'):card.index("</summary>")]
        self.assertIn('<h2 id="map-title" class="title-icon icon-globe">Map</h2>', summary)
        # The zoom buttons are outside the summary, where a click would fold the card.
        self.assertNotIn("map-zoom", summary)
        self.assertLess(card.index("</summary>"), card.index('<div class="map-zoom" hidden>'))

    def test_the_pins_are_placed_when_the_card_opens(self):
        js = read("static", "js", "theme.js")
        block = js[js.index('card.addEventListener("toggle"'):]
        self.assertIn("if (!card.open) return;", block[:200])
        self.assertIn("render();", block[:400])

    def test_movements_and_map_titles_have_their_icons(self):
        self.assertIn('<h2 id="movements-title" class="title-icon icon-plane">Movements</h2>',
                      self.html)
        text = css()
        self.assertIn("mask: var(--title-icon)", rule(text, ".title-icon::before")
                      or text[text.index(".title-icon::before {"):][:400])
        self.assertIn("<circle cx='12' cy='12' r='9'/>", rule(text, ".icon-globe"))
        self.assertIn("<path d='M3 21h18'/>", rule(text, ".icon-plane"))  # the runway
        self.assertIn("transform: rotate(180deg);", rule(text, ".map-card[open] > .map-summary::after"))


class InterfaceTests(unittest.TestCase):
    def test_purge_items_use_the_readable_red(self):
        # --danger is not lightened in the dark theme (2.5:1 on the menu): only for buttons
        # with white text on it.
        self.assertIn("color: var(--error-text);", rule(css(), ".menu-purge"))

    def test_cancelling_a_dialog_opened_from_a_menu_returns_to_the_menu(self):
        js = read("static", "js", "theme.js")
        self.assertIn('var menu = opener.closest("details:not([open])");', js)
        self.assertIn('(menu ? menu.querySelector("summary") : opener).focus();', js)

    def test_the_browser_leaves_the_storage_check_to_the_server(self):
        # Receipts are stored zipped, so a PDF bigger than the space left may still fit.
        js = read("static", "js", "theme.js")
        self.assertIn("} else if (file && left === 0) {", js)
        self.assertNotIn("file.size > left", js)


if __name__ == "__main__":
    unittest.main()
