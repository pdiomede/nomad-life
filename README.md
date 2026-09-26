# Nomad Life

> See [CHANGELOG.md](CHANGELOG.md) for the release history. Current version: **v0.0.4**.

Nomad Life is a small self-hosted web app that helps digital nomads keep track of where they spend each solar year, which matters when establishing a fiscal residence. It also stores the receipts that prove it.

## Features

- One private workspace per user with sign up, sign in and password reset by email (Gmail).
- One workspace per solar year, each with a base city and country.
- Log movements with date range, city and country.
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

The script checks Python, creates `config.env` on first run, sets up a virtual environment, installs the requirements when needed, frees the configured port if it is busy and starts the app. Then open `http://localhost:5050`.

## Configuration

All settings live in `config.env` (see `config.env.example`). This file is git ignored.

| Key | Description |
| --- | --- |
| `APP_PORT` | Port the app listens on. Default `5050`. |
| `APP_BASE_URL` | Public URL used in reset emails. Empty means `http://localhost:APP_PORT`. |
| `SECRET_KEY` | Random secret for sessions and reset links. Generated on first run if empty. |
| `GMAIL_USER` | Gmail address used to send emails. |
| `GMAIL_APP_PASSWORD` | Gmail App Password (not your normal password). |
| `DATABASE_PATH` | SQLite file. Default `data/nomad.db`. |
| `UPLOAD_DIR` | Folder for uploaded files. Default `uploads`. |
| `MAX_RECEIPT_MB` | Max size of a single receipt in MB. Default `10`. Older configs using `MAX_UPLOAD_MB` still work. |
| `USER_QUOTA_MB` | Total receipt storage per user in MB, across all years. Default `500`. |

To create a Gmail App Password, enable 2-Step Verification on your Google account and visit https://myaccount.google.com/apppasswords. If Gmail is not configured, reset links are printed in the terminal.

## Disclaimer

Day counts are an organizational aid, not tax advice. Residence rules differ by country, so check with a tax professional.

## License

[MIT](LICENSE)
