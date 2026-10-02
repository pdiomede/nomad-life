"""Fixes of 1.12.5: a review of the Stripe payment flow, and the Upgrade buttons' color."""
import os
from unittest import mock

from flask import g

import app as appmod
from tests.test_billing import BillingCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def refusing(message, answers=None):
    """A stripe_api stand in: answers from `answers` by path, else refuses with `message`."""
    calls = []

    def fake(method, path, data=None, idempotency_key=None):
        calls.append((method, path, dict(data) if data else data))  # as sent
        for prefix, answer in (answers or {}).items():
            if path.startswith(prefix):
                return answer(data) if callable(answer) else answer
        g.stripe_error = message
        return None
    return fake, calls


class CancelAtTests(BillingCase):
    def test_a_cancellation_by_date_shows_as_cancelled(self):
        # Flexible billing (the default for new subscriptions) cancels from the portal with
        # cancel_at, leaving cancel_at_period_end false.
        self.event("customer.subscription.updated",
                   self.sub(cancel_at=1_820_000_000, cancel_at_period_end=False))
        with self.db() as conn:
            row = conn.execute("SELECT * FROM subscriptions").fetchone()
        self.assertEqual(row["cancel_at_period_end"], 1)
        self.assertEqual(row["current_period_end"], "2027-09-03 19:33:20")
        html = self.client.get("/plan").get_data(as_text=True)
        self.assertIn("Cancelled: ends on 2027-09-0", html)
        self.assertNotIn("Renews on", html)
        self.assertEqual(self.user()["plan"], "pro")  # paid for until then

    def test_taking_it_back_renews_again(self):
        self.event("customer.subscription.updated", self.sub(cancel_at=1_820_000_000))
        self.event("customer.subscription.updated", self.sub(cancel_at=None))
        self.assertIn("Renews on 2027-12-2", self.client.get("/plan").get_data(as_text=True))


class PaidPageTests(BillingCase):
    def test_no_waiting_once_the_webhook_was_quicker(self):
        self.event("customer.subscription.created", self.sub())
        html = self.client.get("/plan?paid=1").get_data(as_text=True)
        self.assertIn("Thank you, your plan is active.", html)
        self.assertNotIn("data-billing-wait", html)
        self.assertNotIn("js/billing.js", html)
        self.assertEqual(self.client.get("/billing/status").get_json(),
                         {"plan": "pro", "paying": True})

    def test_the_page_script_stops_on_a_stored_subscription(self):
        with open(os.path.join(ROOT, "static", "js", "billing.js"), encoding="utf-8") as fh:
            self.assertIn("|| data.paying", fh.read())


class StaleCustomerTests(BillingCase):
    def setUp(self):
        super().setUp()
        with self.db() as conn:
            conn.execute("UPDATE users SET stripe_customer_id = 'cus_test'")

    def test_checkout_makes_a_new_customer(self):
        def session(data):
            if data["customer"] == "cus_test":
                g.stripe_error = "No such customer: 'cus_test'"
                return None
            return {"url": "https://checkout.stripe.com/x"}
        fake, calls = refusing("", {"customers": {"id": "cus_live"},
                                    "subscriptions": {"data": []},
                                    "checkout/sessions": session})
        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            resp = self.client.post("/billing/checkout", data={"plan": "pro"})
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(self.user()["stripe_customer_id"], "cus_live")
        sessions = [d for _, p, d in calls if p == "checkout/sessions"]
        self.assertEqual([d["customer"] for d in sessions], ["cus_test", "cus_live"])

    def test_portal_forgets_it(self):
        fake, _ = refusing("No such customer: 'cus_test'; a similar object exists in test mode")
        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            html = self.client.post("/billing/portal", follow_redirects=True).get_data(as_text=True)
        self.assertIn("There is no billing account to manage yet.", html)
        self.assertIsNone(self.user()["stripe_customer_id"])

    def test_other_refusals_keep_it(self):
        fake, _ = refusing("Your account cannot currently make live charges.")
        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            self.client.post("/billing/checkout", data={"plan": "pro"})
            self.client.post("/billing/portal")
        self.assertEqual(self.user()["stripe_customer_id"], "cus_test")


class EmailSyncTests(BillingCase):
    def change_email(self, new):
        self.age_emails()
        self.client.post("/settings", data={"action": "email", "email": new,
                                            "current_password": "password1"})
        return self.last_link(new, kind="account/email")

    def test_stripe_hears_of_an_email_change_and_its_undo(self):
        self.event("customer.subscription.created", self.sub())
        link = self.change_email("b@example.com")
        with mock.patch.object(appmod, "stripe_api", return_value={"id": "cus_1"}) as api:
            self.client.post(link)
        api.assert_called_once_with("POST", "customers/cus_1", {"email": "b@example.com"})
        undo = self.last_link("a@example.com", kind="account/email/undo")
        with mock.patch.object(appmod, "stripe_api", return_value={"id": "cus_1"}) as api:
            self.app.test_client().post(undo)
        api.assert_called_once_with("POST", "customers/cus_1", {"email": "a@example.com"})

    def test_no_call_without_a_customer(self):
        link = self.change_email("b@example.com")
        with mock.patch.object(appmod, "stripe_api") as api:
            self.client.post(link)
        api.assert_not_called()


class DowngradeEmailTests(BillingCase):
    def test_moving_down_is_no_welcome(self):
        self.event("customer.subscription.created", self.sub(price="price_plus"))
        self.event("customer.subscription.updated", self.sub(price="price_pro"))
        self.assertEqual(self.user()["plan"], "pro")
        self.assertEqual(self.outbox[-1]["subject"], "Your Nomad Life plan is now Pro")
        self.assertNotIn("Thank you", self.outbox[-1]["text"])


class ForgetTestBillingTests(BillingCase):
    def run_cli(self, *args, answer="y\n"):
        return self.app.test_cli_runner().invoke(args=["forget-test-billing", *args],
                                                 input=answer)

    def test_needs_the_live_keys(self):
        result = self.run_cli()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("live Stripe keys", result.output)

    def test_forgets_only_what_live_mode_does_not_know(self):
        self.event("customer.subscription.created", self.sub())  # bought with the test keys
        self.signup("b@example.com", client=self.app.test_client())
        with self.db() as conn:
            conn.execute("UPDATE users SET stripe_customer_id = 'cus_live' WHERE id = 2")
        self.app.config.update(STRIPE_SECRET_KEY="rk_live_x")
        fake, _ = refusing("No such subscription: 'sub_1'",
                           {"customers/cus_live": {"id": "cus_live"},
                            "customers/cus_1": lambda d: setattr(
                                g, "stripe_error", "No such customer: 'cus_1'")})
        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            self.assertIn("Dry run", self.run_cli("--dry-run").output)
            self.assertEqual(self.user()["plan"], "pro")
            result = self.run_cli()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Forgot 1 subscriptions and 1 customers", result.output)
        self.assertEqual(self.user()["plan"], "free")
        self.assertIsNone(self.user()["stripe_customer_id"])
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT stripe_customer_id FROM users WHERE id = 2")
                             .fetchone()[0], "cus_live")
            self.assertEqual(conn.execute("SELECT detail FROM audit_log WHERE event = "
                                          "'plan_downgraded'").fetchone()[0],
                             "Pro to Free (test mode purchase forgotten)")

    def test_an_unreachable_stripe_changes_nothing(self):
        self.event("customer.subscription.created", self.sub())
        self.app.config.update(STRIPE_SECRET_KEY="sk_live_x")
        fake, _ = refusing("")
        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            result = self.run_cli()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("nothing was changed", result.output)
        self.assertEqual(self.user()["plan"], "pro")


class UpgradeButtonTests(BillingCase):
    def test_upgrade_buttons_wear_the_map_pin_color(self):
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".btn-upgrade { background: var(--map-pin); color: var(--map-pin-text); "
                      "border-color: var(--map-pin); }", css)
        self.new_year(2026)
        html = self.client.get("/year/2026").get_data(as_text=True)
        self.assertEqual(html.count('class="btn btn-upgrade btn-sm"'), 2)
