"""Fixes of 1.12.7: a second review of the payment flow, with the plan on the Settings page."""
from unittest import mock

from flask import g

import app as appmod
from tests.test_billing import BillingCase
from tests.test_fixes_1125 import refusing

DELETE = {"action": "delete", "confirm_email": "a@example.com",
          "current_password": "password1", "confirm_delete": "2"}


class CancelledPlanDeleteTests(BillingCase):
    def test_a_plan_that_no_longer_renews_does_not_block_deleting(self):
        # Cancelled in the portal, it runs to the end of the paid year and Stripe never
        # charges it again: the owner should not wait up to a year to delete the account.
        self.event("customer.subscription.updated", self.sub(cancel_at_period_end=True))
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertNotIn("still open in Stripe", html)
        self.client.post("/settings", data=DELETE)
        self.assertIsNone(self.user())
        # Its end later is no surprise (no account, nothing logged as an error).
        with mock.patch.object(self.app.logger, "error") as logged:
            self.event("customer.subscription.deleted", self.sub(status="canceled"))
        logged.assert_not_called()

    def test_a_cancelled_plan_whose_payment_failed_still_blocks(self):
        self.event("customer.subscription.updated",
                   self.sub(status="past_due", cancel_at_period_end=True))
        self.client.post("/settings", data=DELETE)
        self.assertIsNotNone(self.user())

    def test_admin_may_delete_it_too(self):
        self.app.config.update(ADMIN_EMAILS=frozenset({"admin@example.com"}),
                               ADMIN_REQUIRE_2FA=False)
        self.event("customer.subscription.updated", self.sub(cancel_at_period_end=True))
        admin = self.app.test_client()
        self.signup("admin@example.com", client=admin)
        admin.post("/admin/users/1", data={"action": "delete", "confirm_email": "a@example.com",
                                           "confirm_delete": "2"})
        self.assertIsNone(self.user())


class AskStripeBeforeCheckoutTests(BillingCase):
    def setUp(self):
        super().setUp()
        with self.db() as conn:
            conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")

    def checkout(self, fake):
        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            return self.client.post("/billing/checkout", data={"plan": "pro"},
                                    follow_redirects=True).get_data(as_text=True)

    def test_a_payment_whose_webhook_is_late_is_not_paid_twice(self):
        fake, calls = refusing("", {"subscriptions?": {"data": [{"status": "active"}]}})
        html = self.checkout(fake)
        self.assertIn("If you just paid, it shows here within a minute", html)
        self.assertEqual([p for _, p, _ in calls],
                         ["subscriptions?customer=cus_1&status=all&limit=20"])

    def test_ended_subscriptions_do_not_count(self):
        fake, calls = refusing("", {"subscriptions?": {"data": [{"status": "canceled"}]},
                                    "checkout/sessions": {"url": "https://checkout.stripe.com/x"}})
        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            resp = self.client.post("/billing/checkout", data={"plan": "pro"})
        self.assertEqual(resp.status_code, 303)

    def test_stripe_unreachable_opens_nothing(self):
        fake, calls = refusing("")
        self.assertIn("could not be opened", self.checkout(fake))
        self.assertEqual(len(calls), 1)

    def test_an_unknown_customer_is_made_again(self):
        def listing(data):
            g.stripe_error = "No such customer: 'cus_1'"
        fake, calls = refusing("", {"subscriptions?": listing, "customers": {"id": "cus_2"},
                                    "checkout/sessions": {"url": "https://checkout.stripe.com/x"}})
        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            resp = self.client.post("/billing/checkout", data={"plan": "pro"})
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(self.user()["stripe_customer_id"], "cus_2")


class SettingsPlanTests(BillingCase):
    def test_settings_show_the_plan(self):
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('<a href="#plan">Plan</a>', html)
        self.assertIn("You are on <strong>Free</strong>", html)
        self.assertIn('href="/plan">Manage my plan</a>', html)
        self.event("customer.subscription.created", self.sub())
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("You are on <strong>Pro</strong>", html)
        self.assertIn("Renews on 2027-12-2", html)
        self.assertIn("Your paid plan is still open in Stripe", html)  # by the delete button


class WebhookAuditIpTests(BillingCase):
    def test_stripe_events_record_no_address(self):
        # The webhook request comes from Stripe's servers: its address is not the owner's,
        # and would show as an unknown IP in their security activity.
        self.event("customer.subscription.created", self.sub())
        self.event("invoice.payment_failed", {"customer": "cus_1"})
        with self.db() as conn:
            rows = conn.execute("SELECT event, ip FROM audit_log WHERE actor = 'Stripe'").fetchall()
        self.assertEqual(sorted(r["event"] for r in rows), ["payment_failed", "plan_upgraded"])
        self.assertEqual({r["ip"] for r in rows}, {""})


class UnpaidAdviceTests(BillingCase):
    def test_an_unpaid_plan_is_paid_not_switched(self):
        self.event("customer.subscription.updated", self.sub(status="unpaid"))
        html = self.client.get("/plan").get_data(as_text=True)
        self.assertIn("Your Pro plan is waiting: pay the open invoice with Manage billing", html)
        self.assertNotIn("Switch to Pro", html)
        self.event("customer.subscription.updated", self.sub(status="paused"))
        self.assertIn("resume it with Manage billing",
                      self.client.get("/plan").get_data(as_text=True))
