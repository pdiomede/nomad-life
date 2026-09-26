# Nomad Life

> See [CHANGELOG.md](CHANGELOG.md) for the release history. Current version: **v0.0.10**.

Nomad Life is a small self-hosted web app that helps digital nomads keep track of where they spend each solar year, which matters when establishing a fiscal residence. It also stores the receipts that prove it.

## Features

- A simple landing page that explains the app, with a "Launch app" button to sign in.
- Share buttons for X, LinkedIn, Facebook, WhatsApp, Telegram and Reddit, with a link preview image.
- A friendly 404 page for wrong links.
- One private workspace per user with sign up, sign in and password reset by email (Gmail).
- One workspace per solar year, each with a base city and country.
- Log movements with date range, city and country.
- The movements list shows 10 per page, with numbered square page buttons and a choice of 10, 25 or 50 per page.
- Searchable country picker with flags. Type a name, a common alternative (UK, USA, Holland) or a country code.
- Edit or delete every entry: years, movements and documents. Deleting always asks twice.
- Accountant package: one ZIP per year with a PDF summary, your timeline and country totals as spreadsheets, and every receipt.
- Upload receipts per movement (hotel or home rent, flight tickets) and documents for the base (rental contract).
- Size limit per receipt and a storage quota per user, with used and remaining space shown on the dashboard.
- Days per country for the year, with a 183 day indicator.
- Light and dark mode, inspired by the Aave color palette.
- Everything stored locally: SQLite database in `data/`, files in `uploads/`.

## Quick start

Requirements: Python 3.10 or newer.

```bash
./runWebApp.sh
```

The script checks Python, creates `config.env` on first run, sets up a virtual environment, installs the requirements when needed, frees the configured port if it is busy and starts the app. Then open `http://localhost:5050` and click "Launch app" in the top right corner.

## Configuration

All settings live in `config.env` (see `config.env.example`). This file is git ignored.

| Key | Description |
| --- | --- |
| `APP_PORT` | Port the app listens on. Default `5050`. |
| `APP_BASE_URL` | Public URL used in reset emails, link previews and share buttons. Empty means `http://localhost:APP_PORT`. When it starts with `https://`, sign in cookies are only sent over HTTPS. |
| `SECRET_KEY` | Random secret for sessions and reset links. Generated on first run if empty. |
| `GMAIL_USER` | Gmail address used to send emails. |
| `GMAIL_APP_PASSWORD` | Gmail App Password (not your normal password). |
| `DATABASE_PATH` | SQLite file. Default `data/nomad.db`. |
| `UPLOAD_DIR` | Folder for uploaded files. Default `uploads`. |
| `MAX_RECEIPT_MB` | Max size of a single receipt in MB. Default `10`. Older configs using `MAX_UPLOAD_MB` still work. |
| `USER_QUOTA_MB` | Total receipt storage per user in MB, across all years. Default `500`. |

To create a Gmail App Password, enable 2-Step Verification on your Google account and visit https://myaccount.google.com/apppasswords. If Gmail is not configured, reset links are printed in the terminal.

## Accountant package

On each year's dashboard, "Download accountant package" builds one ZIP you can hand to your accountant:

- `summary.pdf`: base, days in the base country and the 183 day line, days abroad, days per country, travel timeline, the counting rules and a receipt index. For the current year it shows numbers so far and projected for the full year.
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

## Bug hunting

`PROMPT.md` contains a ready to use prompt for a deep, file by file bug hunt with Claude Code.

## Disclaimer

Day counts are an organizational aid, not tax advice. Residence rules differ by country, so check with a tax professional.

## License

[MIT](LICENSE)

The PDF summary is made with [fpdf2](https://github.com/py-pdf/fpdf2) (LGPL-3.0) using the bundled DejaVu Sans fonts (Bitstream Vera license, `static/fonts/LICENSE-DejaVu.txt`) and IPAGothic for Japanese names (IPA Font License, `static/fonts/LICENSE-IPAGothic.txt`).

Country flags are drawn with the Twemoji Country Flags font (`static/fonts/TwemojiCountryFlags.woff2`) from [country-flag-emoji-polyfill](https://github.com/talkjs/country-flag-emoji-polyfill). The flag artwork is from [Twemoji](https://github.com/twitter/twemoji), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
