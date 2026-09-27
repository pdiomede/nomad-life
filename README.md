# Nomad Life

> See [CHANGELOG.md](CHANGELOG.md) for the release history. Current version: **v1.2.8**.

Nomad Life is a small self-hosted web app that helps digital nomads keep track of where they spend each solar year, which matters when establishing a fiscal residence. It also stores the receipts that prove it.

## Features

- A simple landing page that explains the app, with a "Launch app" button to sign in.
- Share buttons for X, LinkedIn, Facebook, WhatsApp, Telegram and Reddit, with a link preview image.
- A friendly 404 page for wrong links.
- One private workspace per user with sign up, sign in and password reset by email (Gmail). New accounts confirm their email with a link, and accounts not confirmed within 20 minutes are deleted.
- One workspace per solar year, each with a base city and country.
- Log movements with date range, country and city.
- The movements list shows 10 per page, with numbered square page buttons and a choice of 10, 25 or 50 per page.
- Searchable country picker with flags. Type a name, a common alternative (UK, USA, Holland) or a country code.
- Edit or delete every entry: years, movements and documents. Deleting always asks twice.
- Accountant package (Pro and Nomad+): one ZIP per year with a PDF summary, your timeline and country totals as spreadsheets, and every receipt.
- Upload receipts per movement (hotel or home rent, flight tickets) and documents for the base (rental contract).
- Account page: change your password or email, or delete your account with all its data.
- Protection against password guessing: failed sign ins and reset requests are limited.
- Admin page for the operator: accounts, storage, per account quotas, disable or delete accounts.
- Size limit per receipt and a storage quota per user, with used and remaining space shown on the dashboard.
- Plans: Free, Pro ($4 a month or $40 a year) and Nomad+ ($9 a month or $90 a year), each with its own storage and receipt limits. The menu under your email in the header shows your plan, and "Upgrade" on the storage card shows the next plan. There is no online payment yet (see [Plans](#plans)).
- A world map under the movements with a pin for each country of the year (the base in its own color) showing its number of days; hovering or focusing a pin shows the flag, country, cities and days.
- Days per country for the year, with a 183 day indicator. Days without a stay count toward the base country once they have passed; logged stays count in full, also future ones.
- Light and dark mode, inspired by the Aave color palette.
- Everything stored locally: SQLite database in `data/`, files in `uploads/`.

## Quick start

Requirements: Python 3.10 or newer.

```bash
./runWebApp.sh
```

The script checks Python, creates `config.env` on first run, sets up a virtual environment, installs the requirements when needed, frees the configured port if it is busy and starts the app. Then open `http://localhost:5050` and click "Launch app" in the top right corner.

## Configuration

All settings live in `config.env` (see `config.env.example`). This file is git ignored and only readable by you when the launcher creates it. Values in `config.env` win over variables exported in your shell; leave a key out of the file to set it from the environment instead. Paths may start with `~` for your home folder.

| Key | Description |
| --- | --- |
| `APP_PORT` | Port the app listens on. Default `5050`. |
| `APP_BASE_URL` | Public URL used in confirmation and reset emails, link previews and share buttons. Empty means `http://localhost:APP_PORT`. When it starts with `https://`, sign in cookies are only sent over HTTPS. |
| `SECRET_KEY` | Random secret for sessions, confirmation and reset links. Generated on first run if empty. |
| `GMAIL_USER` | Gmail address used to send emails. |
| `GMAIL_APP_PASSWORD` | Gmail App Password (not your normal password). |
| `DATABASE_PATH` | SQLite file. Default `data/nomad.db`. |
| `UPLOAD_DIR` | Folder for uploaded files. Default `uploads`. |
| `MAX_RECEIPT_MB` | Max size of a single receipt in MB on the Free plan. Default `10`. Older configs using `MAX_UPLOAD_MB` still work. |
| `USER_QUOTA_MB` | Total receipt storage per user in MB, across all years, on the Free plan. Default `500`. An admin can set a different quota per account. |
| `ADMIN_EMAILS` | Comma separated emails of the accounts that can open the admin page. Empty by default. |
| `PROXY_COUNT` | Reverse proxies in front of the app, so sign in limits see each visitor's address. Default `0`; use `1` behind nginx or Caddy. |

To create a Gmail App Password, enable 2-Step Verification on your Google account and visit https://myaccount.google.com/apppasswords. If Gmail is not configured, confirmation and reset links are printed in the terminal.

## Account and sign in protection

Click **Account** in the footer to:

- Change your password. You stay signed in on this device; every other device is signed out.
- Change your email. A confirmation link goes to the new address, and the change happens when it is opened.
- Delete your account with all years, movements and receipts. It asks for your password, asks twice and needs your email typed.

Password guessing is slowed down. After 5 failed sign ins for one account, or 20 from one network, within 15 minutes, sign in waits until the oldest attempt is 15 minutes old. Wrong current passwords on the account page count the same way. Password reset requests are limited to 5 per network per 15 minutes, on top of one email per account per minute. Behind a reverse proxy, set `PROXY_COUNT=1`.

## Admin page

Accounts listed in `ADMIN_EMAILS` get an **Admin** link (in the menu under their email, and in the footer) to `/admin`, which shows:

- Totals: accounts, disabled accounts, custom quotas, receipt storage used, years and movements.
- Every account with its plan, sign up date, last sign in, number of years and storage used.
- A storage quota per account (in MB; leave it empty for the quota of the account's plan).
- The plan of each account (Free, Pro or Nomad+).
- The monthly and yearly price of Pro and Nomad+ ("Plans and prices"). Leave both fields empty to go back to the default prices in `PLANS`.
- Disable or enable an account. Disabling signs it out everywhere and blocks sign in.
- Delete an account with all its years, movements and receipts. It asks twice and needs the email typed.

Admins cannot disable or delete their own account, and nobody can become an admin from inside the app. Everyone else gets "Page not found" at `/admin`.

## Plans

| Plan | Price | Receipt storage | Per receipt |
| --- | --- | --- | --- |
| Free | 0 | `USER_QUOTA_MB` (500 MB) | `MAX_RECEIPT_MB` (10 MB) |
| Pro | $4 a month ($40 a year) | 5 GB | 25 MB |
| Nomad+ | $9 a month ($90 a year) | 25 GB | 50 MB, plus priority support |

Paid plans never get less than Free, and a quota set on the admin page wins over the plan's. Plans are defined in `PLANS` in `app.py`. There is no payment yet, so an admin moves an account to another plan with the plan selector on the [admin page](#admin-page). Without an admin, change the database (`free`, `pro` or `plus`):

Prices can be changed by an admin on the admin page ("Plans and prices"); the prices in `PLANS` are the defaults. Storage limits and features are changed in `PLANS`.

```bash
sqlite3 data/nomad.db "UPDATE users SET plan = 'pro' WHERE email = 'you@example.com'"
```

## Accountant package

Available on the Pro and Nomad+ plans.

On each year's dashboard, "Download accountant package" builds one ZIP you can hand to your accountant:

- `summary.pdf`: base, days in the base country and the 183 day line, days abroad, days per country, travel timeline, the counting rules and a receipt index. For the current year it shows numbers so far and for the full year with the trips already planned.
- `timeline.csv` and `country-totals.csv`: the same numbers for Excel or LibreOffice (UTF-8, with protection against spreadsheet formulas).
- `receipts/`: the original files, byte for byte, in one folder per stay plus `receipts/base`.
- `manifest.csv`: every file with its size and SHA-256 checksum. Receipts missing on the server are listed as missing.
- `README.txt`: what each file is.

Your notes are left out unless you tick "Include my notes". The package is built when you download it and never stored, so it does not use storage. The numbers are the same as on the dashboard. It is an organizer, not tax advice.

## Tests

```bash
.venv/bin/python -m unittest
```

## Link previews and 404 page

Social networks read the preview image and text from `APP_BASE_URL`, so set it to your public address (for example `https://nomadlife.example.com`) once the app is online. Previews cannot work on `localhost`.

The 404 page is a standalone file, `static/404.html`. The app already serves it for wrong links. If you put a web server in front of the app, you can point its error page to the same file, for example in nginx: `error_page 404 /static/404.html;`.

## SEO

The landing page is ready for search engines: keyword title and description, canonical URL, Open Graph and Twitter card tags, structured data (JSON-LD), `robots.txt` and `sitemap.xml`. Sign in and app pages are marked `noindex` so private pages stay out of search results. Set `APP_BASE_URL` to your public HTTPS address before going live, because the canonical URL, sitemap and previews are built from it.

## Disclaimer

Day counts are an organizational aid, not tax advice. Residence rules differ by country, so check with a tax professional.

## License

[MIT](LICENSE)

The PDF summary is made with [fpdf2](https://github.com/py-pdf/fpdf2) (LGPL-3.0) using the bundled DejaVu Sans fonts (Bitstream Vera license, `static/fonts/LICENSE-DejaVu.txt`) and IPAGothic for Japanese names (IPA Font License, `static/fonts/LICENSE-IPAGothic.txt`).

Country flags are drawn with the Twemoji Country Flags font (`static/fonts/TwemojiCountryFlags.woff2`) from [country-flag-emoji-polyfill](https://github.com/talkjs/country-flag-emoji-polyfill). The flag artwork is from [Twemoji](https://github.com/twitter/twemoji), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
