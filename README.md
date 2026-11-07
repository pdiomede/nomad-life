# Nomad Life

> See [CHANGELOG.md](CHANGELOG.md) for the release history. Current version: **v1.12.12**.

Nomad Life is a small self-hosted web app that helps digital nomads keep track of where they spend each solar year, which matters when establishing a fiscal residence. It also stores the receipts that prove it.

## Features

- One private workspace per user, with sign up, sign in, password reset by email (Gmail), optional two-factor sign in (authenticator app or passkey) and an email confirmation link.
- One workspace per solar year with a base city and country. Log movements with a date range, country and city, and see the days per country with a 183 day indicator and a world map.
- Upload receipts per movement and documents for the base. Each file is stored as a ZIP, with a size limit per receipt and a storage quota per user.
- Accountant package (Pro and Nomad+): one ZIP per year with a PDF summary, your timeline and country totals as spreadsheets, and every receipt.
- Plans (Free, Pro, Nomad+) with online payment through Stripe.
- Support tickets between users and the operator, and an admin area for accounts, plans and security activity.
- A landing page, documentation at `/docs` and public guides at `/guides`, with SEO tags, share buttons and a 404 page.
- Light and dark mode. Everything is stored locally: SQLite in `data/`, files in `uploads/`.

The full user guide is in the app at `/docs`. Developer notes are in [CLAUDE.md](CLAUDE.md).

## Quick start

Requirements: Python 3.11 or newer.

```bash
./runWebApp.sh
```

The script creates `config.env` on first run, sets up a virtual environment, installs the requirements when needed, frees the configured port and starts the app. Then open `http://localhost:5050` and click "Launch app".

To run it on a server (Ubuntu, gunicorn, nginx, HTTPS, backups), follow [setup_vps.md](setup_vps.md).

## Configuration

All settings live in `config.env` (see `config.env.example`), which is git ignored. Its values win over variables exported in your shell. Paths may start with `~`.

| Key | Description |
| --- | --- |
| `APP_PORT` | Port the app listens on. Default `5050`. |
| `APP_BASE_URL` | Public URL used in emails, link previews and share buttons. Empty means `http://localhost:APP_PORT`. With `https://`, cookies are only sent over HTTPS. Set it before going live: the canonical URL, sitemap and previews come from it. |
| `SECRET_KEY` | Random secret for sessions and links. Generated on first run if empty. |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Gmail address and [App Password](https://myaccount.google.com/apppasswords) used to send emails. Without them, links are printed in the terminal. |
| `DATABASE_PATH` | SQLite file. Default `data/nomad.db`. |
| `UPLOAD_DIR` | Folder for uploaded files. Default `uploads`. |
| `MAX_RECEIPT_MB` | Max size of a receipt on the Free plan. Default `10`. (`MAX_UPLOAD_MB` still works.) |
| `USER_QUOTA_MB` | Receipt storage per user on the Free plan. Default `500`. An admin can set a quota per account. |
| `ADMIN_EMAILS` | Comma separated emails of the accounts that can open the admin page (they need two-factor sign in). Empty by default. |
| `PROXY_COUNT` | Reverse proxies in front of the app, so sign in limits see each visitor's address. Default `0`; use `1` behind nginx or Caddy. |
| `PWNED_CHECK` | Refuse new passwords found in known breaches ([Have I Been Pwned](https://haveibeenpwned.com/Passwords); only 5 characters of the SHA-1 hash leave the server). Default `1`; `0` for a server without internet. |
| `HCAPTCHA_SITEKEY`, `HCAPTCHA_SECRET` | hCaptcha on Sign up and Forgot password. Empty turns it off; set both or neither. |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_PLUS` | Online payment (see [stripe.md](stripe.md)). Empty keeps payment off; set all four or none. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google OAuth client for Calendar Integration in Settings (see [google_calendar.md](google_calendar.md)). Empty turns it off; set both or neither. |

## Plans

| Plan | Price | Receipt storage | Per receipt |
| --- | --- | --- | --- |
| Free | 0 | `USER_QUOTA_MB` (500 MB) | `MAX_RECEIPT_MB` (10 MB) |
| Pro | $9.99 a year | 5 GB | 25 MB |
| Nomad+ | $19.99 a year | 25 GB | 50 MB, plus priority support |

Paid plans are yearly, in US dollars before tax, sold by Nemax Tech LLC. Plans live in `PLANS` in `app.py`; yearly prices can be changed on the admin page and must equal the Stripe prices. Stripe's signed webhook (`/billing/webhook`) is the only thing that sets a paid plan; setup is in [stripe.md](stripe.md). An admin can also give a plan from the account page, or without one:

```bash
sqlite3 data/nomad.db "UPDATE users SET plan = 'pro' WHERE email = 'you@example.com'"
```

## Operator commands

```bash
.venv/bin/flask --app app reset-2fa someone@example.com   # remove an account's two-factor sign in
.venv/bin/flask --app app zip-receipts                    # convert receipts from before 1.2.12 to ZIP
.venv/bin/flask --app app forget-test-billing --dry-run   # with live Stripe keys, forget test mode billing
./checkFakeUsers.sh --dry-run                             # list suspected fake accounts (without --dry-run: ask, then delete)
.venv/bin/python scripts/backup.py --dest DIR --keep 14   # backup of the database and secret key
```

Take a backup before deleting accounts. Fake accounts are confirmed, never signed in and hold nothing; the same rule drives the **Suspected fake** filter on the admin page.

## Tests

```bash
.venv/bin/python -m unittest
```

Dependencies in `requirements.txt` are pinned. The GitHub Action in `.github/workflows/security.yml` runs the tests and `pip-audit` on every push and every Monday.

The screenshots in `/docs` are made by a script from a demo account on a scratch copy of the app (needs Node 22 and Google Chrome):

```bash
.venv/bin/python scripts/docs_screenshots.py          # all, or name the ones to redo
```

## Disclaimer

Day counts are an organizational aid, not tax advice. Residence rules differ by country, so check with a tax professional.

## License

[MIT](LICENSE)

The PDF summary uses [fpdf2](https://github.com/py-pdf/fpdf2) (LGPL-3.0), the bundled DejaVu Sans fonts (Bitstream Vera license, `static/fonts/LICENSE-DejaVu.txt`) and IPAGothic (IPA Font License, `static/fonts/LICENSE-IPAGothic.txt`). Country flags use the Twemoji Country Flags font from [country-flag-emoji-polyfill](https://github.com/talkjs/country-flag-emoji-polyfill); the artwork is from [Twemoji](https://github.com/twitter/twemoji), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
