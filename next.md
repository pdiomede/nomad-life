# What to build next

Ideas for the releases after 1.1.0, most useful first. Nothing here is started yet.

## Still open from 1.0.0 and 1.1.0

- Publish the GitHub Releases for `v1.0.0` (commit `9cc9481`) and `v1.1.0` (commit `99e5f76`). Create each tag from the "Choose a tag" box on https://github.com/pdiomede/nomad-life/releases/new, pointing at those commits.

## 1. Account page for users (recommended for 1.2.0)

Today a signed in user cannot manage their own account.

- Change password while signed in (current password required). This should sign out other sessions, as a reset already does.
- Change email address, with a confirmation link sent to the new address.
- Delete my account with all years, movements and receipts, after two confirmations and typing the email. Today only an admin can do this, which matters for privacy rules such as GDPR.

## 2. Protection against password guessing (recommended for 1.2.0)

- Limit failed sign in attempts per account and per IP address (for example 5 tries, then a short wait), with a clear message.
- Apply the same kind of limit to "Forgot password", on top of the existing one email per minute per account.

## 3. Plans: Free, Pro and VIP

Designed, not built.

- Free: 50 MB of receipt storage, €0 per year.
- Pro: 200 MB, €19 per year.
- VIP: 1 GB, €39 per year.

The per account quota on the admin page is the groundwork: an admin could assign plans first, with online payment added later.

## 4. Backup and export

- One click export of all my data across every year (spreadsheets plus receipts).
- A backup of the database and receipts for the operator, from the admin page or the command line.

## 5. Reminders by email

- A warning when the days in the base country are getting close to missing the 183 day line.
- A warning when receipt storage is almost full.

The email system added with email confirmation makes this straightforward.

## 6. Deployment guide

The app runs on Flask's development server. A short guide for going public: a production server (for example gunicorn), HTTPS through a reverse proxy, backups, and the `APP_BASE_URL`, `SECRET_KEY` and Gmail settings.
