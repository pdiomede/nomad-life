"""Fixes of 1.12.4, from a review of the Stripe integration against Stripe's guidance."""
import io
import json
import re
from unittest import mock

from flask import g

import app as appmod
from tests.test_billing import STRIPE, BillingCase


class LatestSubscriptionTests(BillingCase):
    def test_the_subscription_is_read_from_stripe(self):
        # The event carries an older copy (events can arrive out of order, even within the
        # same second); what Stripe answers now is what counts.
        resp = self.event("customer.subscription.created", self.sub(status="incomplete"),
                          latest=self.sub(status="active"))
        self.assertEqual(resp.status_code, 200)
        self.fetched.assert_called_once_with("GET", "subscriptions/sub_1")
        self.assertEqual(self.user()["plan"], "pro")

    def test_stripe_unreachable_asks_for_the_event_again(self):
        with mock.patch.object(appmod, "stripe_api", return_value=None):
            body = json.dumps({"id": "evt_retry", "type": "customer.subscription.created",
                               "created": 1_800_000_000, "data": {"object": self.sub()}}).encode()
            from tests.test_billing import sign
            resp = self.app.test_client().post("/billing/webhook", data=body,
                                               headers={"Stripe-Signature": sign(body)})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(self.user()["plan"], "free")
        # Not recorded as handled: Stripe's next delivery applies it.
        self.assertEqual(self.event("customer.subscription.created", self.sub(),
                                    event_id="evt_retry").status_code, 200)
        self.assertEqual(self.user()["plan"], "pro")

    def test_a_strange_subscription_id_is_refused(self):
        resp = self.event("customer.subscription.updated", self.sub(sub_id="../customers/x"))
        self.assertEqual(resp.status_code, 400)
        self.fetched.assert_not_called()


class ApiVersionTests(BillingCase):
    def test_every_request_names_the_api_version(self):
        answer = io.BytesIO(b'{"id": "cus_1"}')
        with self.app.test_request_context("/"):
            with mock.patch.object(appmod.urllib.request, "urlopen",
                                   return_value=answer) as urlopen:
                appmod.stripe_api("POST", "customers", {"email": "a@example.com"})
        sent = urlopen.call_args[0][0]
        self.assertEqual(sent.get_header("Stripe-version"), appmod.STRIPE_API_VERSION)
        self.assertRegex(appmod.STRIPE_API_VERSION, r"^\d{4}-\d{2}-\d{2}\.[a-z]+$")


class OpenSubscriptionTests(BillingCase):
    def test_unpaid_or_paused_still_blocks_a_second_checkout_and_deleting(self):
        for status, words in (("unpaid", "The last payment did not go through"),
                              ("paused", "Paused. Resume it with Manage billing")):
            with self.db() as conn:
                conn.execute("DELETE FROM subscriptions")
                conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")
            self.event("customer.subscription.updated", self.sub(status=status))
            self.assertEqual(self.user()["plan"], "free")  # not paid for: no plan
            with mock.patch.object(appmod, "stripe_api") as api:
                html = self.client.post("/billing/checkout", data={"plan": "pro"},
                                        follow_redirects=True).get_data(as_text=True)
            api.assert_not_called()
            self.assertIn("You already have a plan with us", html)
            self.assertIn(words, html)
            html = self.client.post("/settings", data={
                "action": "delete", "confirm_email": "a@example.com",
                "current_password": "password1", "confirm_delete": "2"},
                follow_redirects=True).get_data(as_text=True)
            self.assertIn("cancel it with Manage billing", html)
            self.assertIsNotNone(self.user())


class AdminLinkTests(BillingCase):
    def test_restricted_test_keys_link_to_the_test_dashboard(self):
        self.app.config.update(ADMIN_EMAILS=frozenset({"a@example.com"}),
                               STRIPE_SECRET_KEY="rk_test_abc")
        self.event("customer.subscription.created", self.sub())
        html = self.client.get("/admin/users/1").get_data(as_text=True)
        self.assertIn('href="https://dashboard.stripe.com/test/subscriptions/sub_1"', html)
        self.app.config.update(STRIPE_SECRET_KEY="rk_live_abc")
        html = self.client.get("/admin/users/1").get_data(as_text=True)
        self.assertIn('href="https://dashboard.stripe.com/subscriptions/sub_1"', html)


class ConsentTests(BillingCase):
    def checkout(self, refuse_terms=False):
        calls = []

        def fake(method, path, data=None, idempotency_key=None):
            calls.append((path, data))
            if path == "customers":
                return {"id": "cus_1"}
            if refuse_terms and "consent_collection" in data:
                g.stripe_error = ("You cannot collect consent to your terms of service unless "
                                  "a URL is set in the Stripe Dashboard.")
                return None
            return {"url": "https://checkout.stripe.com/x"}

        with mock.patch.object(appmod, "stripe_api", side_effect=fake):
            resp = self.client.post("/billing/checkout", data={"plan": "pro"})
        return resp, [d for p, d in calls if p == "checkout/sessions"]

    def test_terms_are_a_recorded_checkbox(self):
        resp, sessions = self.checkout()
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(sessions[0]["consent_collection"], {"terms_of_service": "required"})
        message = sessions[0]["custom_text"]["terms_of_service_acceptance"]["message"]
        self.assertRegex(message, r"\[Terms\]\(http[^)]+/terms\)")
        self.assertRegex(message, r"\[Refund Policy\]\(http[^)]+/refunds\)")

    def test_without_a_terms_url_in_stripe_it_still_takes_the_payment(self):
        resp, sessions = self.checkout(refuse_terms=True)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(len(sessions), 2)
        self.assertNotIn("consent_collection", sessions[1])
        self.assertIn("agree to the Terms", sessions[1]["custom_text"]["submit"]["message"])
