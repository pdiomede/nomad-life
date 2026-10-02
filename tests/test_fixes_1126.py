"""Fixes of 1.12.6: Upgrade and Manage billing did nothing, since the page's policy kept forms
from going on to Stripe."""
import app as appmod
from tests.test_billing import BillingCase


class StripeFormActionTests(BillingCase):
    def form_action(self, path):
        csp = self.client.get(path).headers["Content-Security-Policy"]
        return next(p.strip() for p in csp.split(";") if p.strip().startswith("form-action"))

    def test_the_plan_page_may_send_its_forms_on_to_stripe(self):
        # Browsers apply form-action to the redirect that answers a form, so the 303 from
        # /billing/checkout to checkout.stripe.com was blocked: nothing happened on Upgrade.
        self.assertEqual(self.form_action("/plan"), "form-action 'self' "
                         "https://checkout.stripe.com https://billing.stripe.com")

    def test_every_other_page_keeps_its_forms_here(self):
        for path in ("/settings", "/support", "/year/new"):
            self.assertEqual(self.form_action(path), "form-action 'self'", path)
        self.assertNotIn("stripe.com", appmod.CSP)
