# Setting up Stripe for Nomad Life

How to open and configure the Stripe account that takes payments for Nomad Life's yearly plans, for **Nemax Tech LLC** (Sofia, Bulgaria, UIC 207405380, VAT BG207405380). Do everything in **test mode** first, check it end to end, then repeat the few live mode steps at the end.

What the app needs at the end: four values for `config.env` on the server.

```
STRIPE_SECRET_KEY=        # step 9
STRIPE_WEBHOOK_SECRET=    # step 8
STRIPE_PRICE_PRO=         # step 3
STRIPE_PRICE_PLUS=        # step 3
```

Until all four are set, the site keeps saying "Online payment is coming soon". Nothing is charged before then.

Menu names below are Stripe's at the time of writing; Stripe moves things around now and then, and the search box at the top of the dashboard finds any setting by name.

## 1. Create the account

1. Sign up at https://dashboard.stripe.com/register with an email you will keep (for example a company address).
2. **Activate payments** (the dashboard asks for it):
   - **Business type:** Company. **Legal name:** Nemax Tech LLC. **Country:** Bulgaria.
   - **Registration number (UIC / EIK):** 207405380. **VAT number:** BG207405380.
   - **Representative:** you, with an ID document. Stripe checks it, which can take from minutes to a couple of days.
   - **Business website:** `https://nomadlife.pro`. **Product description:** "Online service for digital nomads: counts the days spent in each country and stores travel receipts. Yearly subscription plans."
   - **Bank account for payouts:** the company's IBAN (EUR).
   - **Statement descriptor** (what appears on card statements): `NOMADLIFE.PRO`.
3. Turn on two-step authentication for your Stripe login (Settings -> Personal details -> Two-step authentication).

You can do steps 2 to 10 in test mode while the activation is being reviewed.

## 2. Public business details

**Settings -> Business -> Public details:**

- **Public business name:** Nomad Life
- **Support email:** support@nomadlife.pro
- **Support URL / website:** `https://nomadlife.pro`
- **Privacy policy:** `https://nomadlife.pro/privacy`
- **Terms of service:** `https://nomadlife.pro/terms`
- **Refund policy** (if asked): `https://nomadlife.pro/refunds`

Customers see these on the checkout page, receipts and the billing portal. The **Terms of service** URL also turns on the checkout's "I agree to the Terms" checkbox: until it is set, the app falls back to a line of text under the Pay button (and logs an error saying so).

Also in **Settings -> Payments -> Checkout**: turn on **Limit customers to one subscription**, a second guard (the app already sends anyone with a plan to Manage billing).

## 3. The two products (test mode)

**Done in the Nemax Tech test account** (created through the Stripe connection on 2026-10-02):

- Nomad Life Pro, `prod_VMr8x3HPt8sbbd`: yearly $9.99, tax exclusive -> `STRIPE_PRICE_PRO=price_1UM7PpRKOf8syB1PdlM1ccpi`
- Nomad Life Nomad+, `prod_VMr8hlGKpT7hoy`: yearly $19.99, tax exclusive -> `STRIPE_PRICE_PLUS=price_1UM7PpRKOf8syB1POd4AcacE`
- Both with tax code `txcd_10103000` (Software as a Service, personal use): confirm it with your accountant before going live.

For live mode (or to redo them), turn on **Test mode** off/on as needed, then **Product catalog -> Add product**, twice:

| Product name | Price | Billing period | Currency |
| --- | --- | --- | --- |
| Nomad Life Pro | 9.99 | Yearly (recurring) | USD |
| Nomad Life Nomad+ | 19.99 | Yearly (recurring) | USD |

For each one:

- **Pricing model:** Standard pricing, **Recurring**, **Yearly**.
- **Include tax in price:** **No** (tax is added on top where it applies; the site says prices are before tax).
- **Tax code:** "Software as a service (SaaS) - personal use" (or set it once as the default in step 4).

Open each product and copy its **price ID** (`price_...`, not the product ID `prod_...`):

- Nomad Life Pro -> `STRIPE_PRICE_PRO`
- Nomad Life Nomad+ -> `STRIPE_PRICE_PLUS`

The prices must stay equal to the ones on the admin page's **Plans & Prices**. To change a price later, add a new price to the product, put its id in `config.env`, change the admin page, and restart.

## 4. Stripe Tax (VAT and sales tax)

**Settings -> Tax** (or **Tax** in the menu) -> **Get started**:

1. **Origin address:** the company's address in Sofia, Bulgaria.
2. **Default tax code:** "Software as a service (SaaS) - personal use". **Default tax behavior:** exclusive.
3. **Registrations -> Add registration:**
   - **Bulgaria (VAT)**: always, since the company is VAT registered there.
   - **EU, One Stop Shop (OSS):** if Nemax Tech is registered for the Union OSS scheme in Bulgaria. EU consumers then pay the VAT rate of their own country, which the company declares in one quarterly OSS return. Below the EU threshold of EUR 10,000 a year of cross-border sales to consumers, a company may instead charge Bulgarian VAT to every EU consumer.
   - Elsewhere (UK, US states, others): Stripe's **Monitoring** tab shows when sales get close to a country's threshold; add a registration once the company has registered there.

Stripe Tax has its own fee per transaction where it calculates tax (see Stripe's pricing page). **Ask the company's accountant** which registrations Nemax Tech needs; Stripe only calculates and collects, the company still files the returns (Stripe's tax reports help with that).

Business customers who enter their VAT number at checkout are charged with the reverse charge where it applies; Stripe checks the number.

## 5. Invoices and receipts

**Settings -> Billing -> Invoices** (invoice template):

- Company name, address and **Account tax IDs:** add `BG207405380` (type: EU VAT).
- **Invoice footer:** `Nemax Tech LLC, Sofia, Bulgaria. UIC 207405380, VAT BG207405380.`
- Invoice numbering prefix: for example `NL`.

**Settings -> Customer emails** (or Billing -> Subscriptions and emails):

- **Successful payments:** on (the customer gets the receipt and invoice).
- **Refunds:** on.
- **Upcoming renewals:** on, 7 days before for yearly plans. Stripe sends a reminder before charging the next year.

## 6. Failed payments

**Settings -> Billing -> Subscriptions and emails -> Manage failed payments:**

- **Smart Retries:** on, for up to 2 or 3 weeks.
- **Send emails about failed payments:** on, with a link to update the card.
- **If all retries fail:** "Cancel the subscription". The app moves the account to Free when that happens; "mark the subscription as unpaid" also works.

While Stripe retries, the account keeps its plan (status `past_due`).

## 7. Customer portal

**Done in the test account:** configuration `bpc_1UM7QARKOf8syB1PngMk3gcJ` (the default), with the settings below. Live mode needs the same once (Stripe keeps test and live settings apart).

**Settings -> Billing -> Customer portal** -> activate it, then:

- **Invoices:** show invoice history, on.
- **Customer information:** customers can update their billing address and tax IDs.
- **Payment methods:** customers can update them, on.
- **Cancellations:** on, **at the end of the billing period** (the paid year stays usable). Asking for a cancellation reason is optional.
- **Subscriptions -> Customers can switch plans:** on. Add both products and their yearly prices.
  - **Proration:** prorate charges and credits, **invoice immediately** (moving up to Nomad+ charges only the difference for the rest of the year, as the Refund Policy says).
  - **Downgrades:** schedule them **at the end of the billing period**, if Stripe offers the option.
- **Business information:** headline "Nomad Life billing", links to the privacy policy and terms (from step 2).
- **Default redirect link:** `https://nomadlife.pro/plan`.

## 8. The webhook

The app learns about payments only from this webhook; without it nobody gets their plan.

**Developers -> Webhooks -> Add endpoint** (in test mode):

- **Endpoint URL:** `https://nomadlife.pro/billing/webhook`
- **Events to send:**
  - `checkout.session.completed`
  - `customer.subscription.created`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.paid`
  - `invoice.payment_failed`
- **API version:** `2026-08-26.dahlia`, the version the app sends with every request (`STRIPE_API_VERSION` in `app.py`), so events and API answers have the same shape.

Open the new endpoint and **reveal the signing secret** (`whsec_...`) -> `STRIPE_WEBHOOK_SECRET`.

**Cloudflare:** Stripe's requests come from Stripe's servers, not a browser. If the endpoint's attempts fail with 403 in Stripe's dashboard, turn off Bot Fight Mode, or add a WAF rule that skips security checks for the path `/billing/webhook` (Security -> WAF -> Custom rules -> Skip). The app checks every request's signature itself.

## 9. The API key

**Developers -> API keys:**

- Simplest: the **Secret key** (`sk_test_...`) -> `STRIPE_SECRET_KEY`.
- Safer, and what Stripe recommends: **Create restricted key**, named "Nomad Life server", with only these permissions (everything else None):
  - **Customers:** Write
  - **Checkout Sessions:** Write
  - **Customer portal:** Write
  - **Subscriptions:** Read (the webhook reads each subscription as it is now, since Stripe may deliver events out of order)
  
  Its value (`rk_test_...`) works as `STRIPE_SECRET_KEY`. A leaked restricted key cannot refund, pay out or read other data.

Never put the key in the repository or send it by email: only in `config.env` on the server, which is git-ignored.

## 10. Put the keys on the server and test

On the server:

```bash
nano /var/www/nomad-life/config.env
```

Add the four lines with the test values, save, then restart and check:

```bash
sudo systemctl restart nomad-life && sleep 3 && systemctl is-active nomad-life
```

If the app does not start, the log says which `STRIPE_*` value is missing (all four or none):

```bash
sudo journalctl -u nomad-life --since "-2 min" --no-pager | tail -20
```

Then, on https://nomadlife.pro with a test account:

1. **Your plan -> Upgrade to Pro.** Stripe's test checkout opens. Pay with the card `4242 4242 4242 4242`, any future date, any CVC, any postcode.
2. Back on the plan page: "Payment received", then within seconds the plan shows **Pro**, with "Renews on ...". The account gets a "Welcome to Nomad Life Pro" email; Stripe sends the test receipt.
3. In Stripe, **Developers -> Webhooks -> your endpoint:** every attempt shows **200**. A 400 means the signing secret in `config.env` is wrong; a 403 means Cloudflare blocked it (step 8).
4. **Manage billing** opens the portal: switch to Nomad+ (the plan page shows Nomad+), then cancel (the plan page shows "Cancelled: ends on ...").
5. A card that is declined at renewal: `4000 0000 0000 0341`. A card that asks for 3D Secure: `4000 0027 6000 3184`.
6. **Admin -> the account:** the Subscription row shows the status and links to it in Stripe; Security Activity shows "Plan bought", "Plan switched".

To end a test subscription at once: in Stripe, **Subscriptions -> the subscription -> Cancel -> immediately**. The webhook moves the account to Free.

## 11. Go live

When the account is activated (step 1) and the test run works:

1. Switch the dashboard to **live mode** (turn Test mode off).
2. Products: open each test product and use **Copy to live mode** (or create them again, step 3). Copy the two **live** price ids.
3. Repeat steps 4 to 7 in live mode if Stripe shows them per mode (tax registrations, portal, emails).
4. Add the **live webhook endpoint** (step 8) and copy its new signing secret.
5. Create the **live key** (step 9: `sk_live_...` or a restricted `rk_live_...`).
6. Put the four live values in `config.env` on the server and restart.
7. Forget what the test purchases left in the database (the live webhook never updates them: a test purchase would keep its plan and block a real checkout). From `/var/www/nomad-life`, the command asks the live account about each stored subscription and customer and only forgets those it does not know. Look first with `--dry-run`, then run it without:

    ```bash
    .venv/bin/flask --app app forget-test-billing --dry-run
    ```
8. Make one real purchase with your own card, check that the plan changes, then refund it (step 12).

## 12. Day to day

- **Refund** (the Refund Policy promises a full refund within 14 days of a payment): Stripe -> **Payments** -> the payment -> **Refund**. Then **Subscriptions** -> the subscription -> **Cancel -> immediately**, so it does not renew. The webhook moves the account to Free.
- **Gift a plan:** the plan selector on the admin page. An account that pays through Stripe gets its plan from Stripe, so change those in Stripe instead.
- **Payouts:** Stripe pays out to the company's bank account in EUR; payments in USD are converted, for a fee. Settings -> Payouts sets the schedule.
- **For the accountant:** **Reports** (balance, payouts), **Invoices**, and **Tax -> Reports** (VAT and OSS figures by country and period).
- **Disputes (chargebacks):** Stripe emails you; answer them in **Payments -> Disputes** with the account's history (sign ins, invoices).

## Testing locally (optional)

To test on your Mac before deploying, the Stripe CLI forwards webhooks to the local app:

```bash
brew install stripe/stripe-cli/stripe
```

```bash
stripe login
```

```bash
stripe listen --forward-to localhost:5050/billing/webhook
```

It prints a `whsec_...` secret for this session: use it as `STRIPE_WEBHOOK_SECRET` in your local `config.env`, with the test key and test price ids, and restart the local app. Checkout's return links come from `APP_BASE_URL`, so set it to `http://localhost:5050` locally.
