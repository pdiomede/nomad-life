"""Paid plans with Stripe: checkout, the customer portal and the signed webhook that is the
only thing setting a plan. Stripe's API is patched; webhooks are signed with a test secret."""
import hashlib
import hmac
import json
import time
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase

STRIPE = {"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_test",
          "STRIPE_PRICE_PRO": "price_pro", "STRIPE_PRICE_PLUS": "price_plus"}


def sign(payload, secret="whsec_test", t=None):
    t = int(t if t is not None else time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


class BillingCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config.update(STRIPE)
        self.signup()
        self.uid = 1
        self.n = 0

    def user(self):
        with self.db() as conn:
            return conn.execute("SELECT * FROM users WHERE id = ?", (self.uid,)).fetchone()

    def event(self, kind, obj, created=None, event_id=None, latest=None):
        """Post a signed event. For subscription events the webhook reads the subscription
        from Stripe first: `latest` is what Stripe answers (by default the event's own copy)."""
        self.n += 1
        body = json.dumps({"id": event_id or f"evt_{self.n}", "type": kind,
                           "created": created or 1_800_000_000 + self.n,
                           "data": {"object": obj}}).encode()
        answer = latest if latest is not None else obj
        with mock.patch.object(appmod, "stripe_api", return_value=answer) as self.fetched:
            return self.app.test_client().post("/billing/webhook", data=body, headers={
                "Stripe-Signature": sign(body), "Content-Type": "application/json"})

    def sub(self, status="active", price="price_pro", user_id=None, sub_id="sub_1",
            customer="cus_1", **extra):
        return dict({"id": sub_id, "object": "subscription", "customer": customer,
                     "status": status, "cancel_at_period_end": False,
                     "current_period_end": 1_830_000_000,
                     "metadata": {"user_id": str(user_id or self.uid)},
                     "items": {"data": [{"price": {"id": price}}]}}, **extra)


class WebhookTests(BillingCase):
    def test_signature_is_required(self):
        body = json.dumps({"id": "evt_x", "type": "customer.subscription.created",
                           "data": {"object": self.sub()}}).encode()
        client = self.app.test_client()
        for header in (None, "t=1,v1=abc", sign(body, "whsec_wrong"),
                       sign(body, t=time.time() - 600)):
            headers = {"Stripe-Signature": header} if header else {}
            self.assertEqual(client.post("/billing/webhook", data=body,
                                         headers=headers).status_code, 400, header)
        self.assertEqual(self.user()["plan"], "free")
        big = b"x" * (appmod.BILLING_WEBHOOK_MAX + 1)
        self.assertEqual(client.post("/billing/webhook", data=big,
                                     headers={"Stripe-Signature": sign(big)}).status_code, 413)

    def test_off_without_keys(self):
        self.app.config.update(STRIPE_SECRET_KEY="")
        self.assertEqual(self.event("customer.subscription.created", self.sub()).status_code, 404)

    def test_paying_sets_the_plan_and_tells_the_owner(self):
        resp = self.event("customer.subscription.created", self.sub())
        self.assertEqual(resp.status_code, 200)
        user = self.user()
        self.assertEqual(user["plan"], "pro")
        self.assertEqual(user["stripe_customer_id"], "cus_1")
        self.assertIn("Welcome to Nomad Life Pro", self.outbox[-1]["subject"])
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT detail FROM audit_log WHERE event = "
                                          "'plan_upgraded'").fetchone()[0], "Free to Pro")
            sub = conn.execute("SELECT * FROM subscriptions").fetchone()
        self.assertEqual((sub["plan"], sub["status"], sub["current_period_end"]),
                         ("pro", "active", "2027-12-28 13:20:00"))

    def test_lifecycle(self):
        self.event("customer.subscription.created", self.sub())
        self.event("customer.subscription.updated", self.sub(price="price_plus"))
        self.assertEqual(self.user()["plan"], "plus")
        self.event("customer.subscription.updated", self.sub(status="past_due", price="price_plus"))
        self.assertEqual(self.user()["plan"], "plus")  # Stripe retries the card meanwhile
        self.event("customer.subscription.deleted", self.sub(status="canceled", price="price_plus"))
        self.assertEqual(self.user()["plan"], "free")
        self.assertIn("has ended", self.outbox[-1]["subject"])

    def test_unpaid_gives_free(self):
        self.event("customer.subscription.created", self.sub())
        self.event("customer.subscription.updated", self.sub(status="unpaid"))
        self.assertEqual(self.user()["plan"], "free")

    def test_an_event_is_applied_once(self):
        self.event("customer.subscription.created", self.sub(), event_id="evt_same")
        self.event("customer.subscription.deleted", self.sub(status="canceled"),
                   event_id="evt_same")  # the same id again: ignored
        self.assertEqual(self.user()["plan"], "pro")

    def test_an_older_event_arriving_late_is_ignored(self):
        self.event("customer.subscription.updated", self.sub(status="canceled"), created=2_000)
        self.event("customer.subscription.created", self.sub(), created=1_000)
        self.assertEqual(self.user()["plan"], "free")

    def test_unknown_price_or_account_changes_nothing(self):
        self.event("customer.subscription.created", self.sub(price="price_other"))
        self.assertEqual(self.user()["plan"], "free")
        self.event("customer.subscription.created",
                   self.sub(sub_id="sub_2", customer="cus_2", user_id=999))
        self.assertEqual(self.user()["plan"], "free")

    def test_a_stored_subscription_never_moves_to_another_account(self):
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.event("customer.subscription.created", self.sub())
        self.event("customer.subscription.updated", self.sub(user_id=2))  # metadata edited
        with self.db() as conn:
            plans = dict(conn.execute("SELECT email, plan FROM users").fetchall())
        self.assertEqual(plans, {"a@example.com": "pro", "b@example.com": "free"})

    def test_metadata_cannot_claim_an_account_with_another_customer(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET stripe_customer_id = 'cus_owner'")
        self.event("customer.subscription.created", self.sub(customer="cus_stranger"))
        self.assertEqual(self.user()["plan"], "free")

    def test_failed_payment_and_renewal(self):
        self.event("customer.subscription.created", self.sub())
        self.event("invoice.payment_failed", {"customer": "cus_1"})
        self.assertIn("did not go through", self.outbox[-1]["subject"])
        self.event("invoice.paid", {"customer": "cus_1", "billing_reason": "subscription_cycle"})
        with self.db() as conn:
            events = [r[0] for r in conn.execute("SELECT event FROM audit_log WHERE event IN "
                                                 "('payment_failed', 'plan_renewed')")]
        self.assertEqual(events, ["payment_failed", "plan_renewed"])

    def test_checkout_completed_links_the_customer(self):
        self.event("checkout.session.completed", {"customer": "cus_9",
                                                  "client_reference_id": str(self.uid)})
        self.assertEqual(self.user()["stripe_customer_id"], "cus_9")

    def test_a_failure_rolls_back_so_stripe_retries(self):
        with mock.patch.object(appmod, "apply_subscription", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.event("customer.subscription.created", self.sub(), event_id="evt_retry")
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM billing_events").fetchone()[0], 0)
        self.event("customer.subscription.created", self.sub(), event_id="evt_retry")
        self.assertEqual(self.user()["plan"], "pro")


class CheckoutTests(BillingCase):
    def stripe(self, answers):
        calls = []

        def fake(method, path, data=None, idempotency_key=None):
            calls.append((method, path, data))
            return answers.get(path)
        return mock.patch.object(appmod, "stripe_api", side_effect=fake), calls

    def test_checkout_redirects_to_stripe_for_this_account(self):
        patcher, calls = self.stripe({"customers": {"id": "cus_new"},
                                      "checkout/sessions": {"url": "https://checkout.stripe.com/x"}})
        with patcher:
            resp = self.client.post("/billing/checkout", data={"plan": "pro", "user_id": "99"})
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["Location"], "https://checkout.stripe.com/x")
        session = calls[1][2]
        self.assertEqual(session["customer"], "cus_new")
        self.assertEqual(session["client_reference_id"], self.uid)
        self.assertEqual(session["subscription_data"]["metadata"]["user_id"], self.uid)
        self.assertEqual(session["line_items"][0]["price"], "price_pro")
        self.assertTrue(session["automatic_tax"]["enabled"])
        self.assertTrue(session["tax_id_collection"]["enabled"])
        self.assertTrue(session["success_url"].endswith("/plan?paid=1"))
        self.assertEqual(self.user()["stripe_customer_id"], "cus_new")

    def test_refusals(self):
        patcher, calls = self.stripe({})
        with patcher:
            self.app.config.update(STRIPE_SECRET_KEY="")
            self.client.post("/billing/checkout", data={"plan": "pro"})
            self.app.config.update(STRIPE)
            self.set_plan("plus")
            self.client.post("/billing/checkout", data={"plan": "pro"})  # already higher
            self.set_plan("free")
            self.event("customer.subscription.created", self.sub(status="past_due"))
            self.set_plan("free")
            html = self.client.post("/billing/checkout", data={"plan": "plus"},
                                    follow_redirects=True).get_data(as_text=True)
            self.assertIn("You already have a plan with us", html)
            self.assertEqual(self.client.post("/billing/checkout",
                                              data={"plan": "free"}).status_code, 400)
        self.assertEqual(calls, [])

    def test_stripe_down(self):
        patcher, _ = self.stripe({})
        with patcher:
            html = self.client.post("/billing/checkout", data={"plan": "pro"},
                                    follow_redirects=True).get_data(as_text=True)
        self.assertIn("could not be opened", html)

    def test_portal(self):
        patcher, calls = self.stripe({"billing_portal/sessions": {"url": "https://billing.stripe.com/p"}})
        with patcher:
            html = self.client.post("/billing/portal", follow_redirects=True).get_data(as_text=True)
            self.assertIn("no billing account", html)
            with self.db() as conn:
                conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")
            resp = self.client.post("/billing/portal")
        self.assertEqual(resp.headers["Location"], "https://billing.stripe.com/p")
        self.assertEqual(calls[0][2]["customer"], "cus_1")

    def test_plan_page(self):
        html = self.client.get("/plan").get_data(as_text=True)
        self.assertIn('action="/billing/checkout"', html)
        self.assertIn("Upgrade to Pro, $9.99 / year", html)
        self.assertNotIn("Online payment is coming soon", html)
        self.event("customer.subscription.created", self.sub())
        html = self.client.get("/plan").get_data(as_text=True)
        self.assertIn("Manage billing", html)
        self.assertIn("Renews on 2027-12-2", html)
        self.assertNotIn('action="/billing/checkout"', html)
        self.assertIn("Switch to Nomad+ with Manage billing", html)
        self.app.config.update(STRIPE_SECRET_KEY="")
        self.assertIn("Online payment is coming soon", self.client.get("/plan").get_data(as_text=True))

    def test_paid_page_waits_for_the_webhook(self):
        html = self.client.get("/plan?paid=1").get_data(as_text=True)
        self.assertIn("data-billing-wait", html)
        self.assertIn("js/billing.js", html)
        self.assertEqual(self.client.get("/billing/status").get_json(), {"plan": "free", "paying": False})

    def test_status_needs_sign_in(self):
        self.assertEqual(self.app.test_client().get("/billing/status").status_code, 302)


class DeleteGuardTests(BillingCase):
    def test_a_paying_account_cannot_be_deleted(self):
        self.event("customer.subscription.created", self.sub())
        html = self.client.post("/settings", data={
            "action": "delete", "confirm_email": "a@example.com",
            "current_password": "password1", "confirm_delete": "2"},
            follow_redirects=True).get_data(as_text=True)
        self.assertIn("cancel it with Manage billing", html)
        self.assertIsNotNone(self.user())
        self.event("customer.subscription.deleted", self.sub(status="canceled"))
        self.client.post("/settings", data={
            "action": "delete", "confirm_email": "a@example.com",
            "current_password": "password1", "confirm_delete": "2"})
        self.assertIsNone(self.user())
        with self.db() as conn:  # the company's billing record stays, without the account
            self.assertIsNone(conn.execute("SELECT user_id FROM subscriptions").fetchone()[0])


class SettingsTests(AppTestCase):
    def test_partial_stripe_settings_stop_the_app(self):
        import os
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "",
                                          "STRIPE_PRICE_PRO": "", "STRIPE_PRICE_PLUS": ""}):
            with self.assertRaises(SystemExit):
                appmod.billing_settings()

    def test_form_encoding(self):
        self.assertEqual(appmod.stripe_form({"a": {"b": 1}, "c": [{"d": True}], "e": None}),
                         [("a[b]", "1"), ("c[0][d]", "true")])
