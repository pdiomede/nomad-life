# Nomad Life

> See [CHANGELOG.md](CHANGELOG.md) for the release history. Current version: **v2.1.7**.

Nomad Life is a small self-hosted web app that helps digital nomads keep track of where they spend each solar year, which matters when establishing a fiscal residence. It also stores the receipts that prove it.

## Features

- One private workspace per user, with sign up, sign in, password reset by email (Gmail), optional two-factor sign in (authenticator app or passkey) and an email confirmation link.
- One workspace per solar year with a base city and country. Log movements with a date range, country and city, and see the days per country with a 183 day indicator and a world map.
- Upload receipts per movement and documents for the base. Each file is stored as a ZIP, with a size limit per receipt and a storage quota per user.
- Accountant package (Pro and Nomad+): one ZIP per year with a PDF summary, your timeline and country totals as spreadsheets, and every receipt.
- Calendar Integration: connect a Google Calendar in Settings and choose one calendar Nomad Life can use.
- AUTO movements (2.0.0): switch a year from MANUAL to AUTO next to its number, and its movements follow the connected Google calendar (events named after a place, like "Italy", "Rome, Italy" or "Austin, Texas"). Turning AUTO on first shows the differences to apply; Google Calendar wins afterwards, while notes and receipts stay. Disconnecting or changing the calendar puts AUTO years back to MANUAL until they are reviewed again.
- Delegates (2.1.0): in Settings, invite up to 5 people, such as your accountant, to view your account read only with their own Nomad Life account: the dashboard, movements, notes, receipts and the accountant package. Remove them at any time.
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
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Gmail address and [App Password](https://myaccount.google.com/apppasswords) used to send emails (or, with `SMTP_HOST`, the address and password of another mailbox). Without them, links are printed in the terminal. |
| `LOG_FILE` | Where emails (sent, or failed and why) and the app's errors and warnings are logged. Default `data/nomadlife.log`; empty turns it off. Test the mail settings with `.venv/bin/flask --app app test-email`. |
| `SMTP_HOST`, `SMTP_PORT` | The mail server behind those two (default `smtp.gmail.com`, `587`). Port 465 uses TLS at once, any other STARTTLS. Hostinger: `smtp.hostinger.com`, `587` (use 587 rather than 465: many hosts, such as Hetzner, block outgoing port 465). |
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

## Google Calendar

Calendar Integration (Settings, or the gear next to the dashboard's Calendar title) lets each user connect a Google account and choose one calendar they can edit. It stays off until the server has a Google OAuth client:

1. In [Google Cloud](https://console.cloud.google.com), create a project and enable the **Google Calendar API** (APIs & Services -> Library).
2. Set up the consent screen (Google Auth Platform): app name, support email, privacy policy and terms URLs, audience **External**, and the scopes `https://www.googleapis.com/auth/calendar.calendarlist.readonly` and `https://www.googleapis.com/auth/calendar.events.readonly` (the chosen calendar's events, shown in the dashboard calendar's Google Calendar tab). While the app is in Testing, add the Google addresses that may connect as test users.
3. Create an OAuth client (Clients -> Create client) of type **Web application**, with the authorized redirect URI `<APP_BASE_URL>/settings/calendar/google/callback`, for example `https://nomadlife.pro/settings/calendar/google/callback` (add `http://localhost:5050/settings/calendar/google/callback` for a local test).
4. Put its values in `config.env` and restart the app:

   ```
   GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=...
   ```

5. Open Settings > Calendar Integration, press **Connect Google Calendar**, sign in with a test user, then choose a calendar and press **Save calendar**.

Calendar permissions are sensitive for Google: before anyone outside the test users can connect, publish the app and pass Google's verification. Each connection is stored in the `calendar_links` table; **Disconnect Google account** or deleting the account removes it and revokes the access at Google. Details are in [google_calendar.md](google_calendar.md).

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
