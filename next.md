# What to build next

Ideas for the releases after 1.2.0, most useful first. The account page and the sign in limits shipped in 1.2.0.

## Still open from 1.0.0 and 1.1.0

- Publish the GitHub Releases for `v1.0.0` (commit `9cc9481`) and `v1.1.0` (commit `99e5f76`). Create each tag from the "Choose a tag" box on https://github.com/pdiomede/nomad-life/releases/new, pointing at those commits.

## 1. Plans: Free, Pro and VIP

Designed, not built.

- Free: 50 MB of receipt storage, €0 per year.
- Pro: 200 MB, €19 per year.
- VIP: 1 GB, €39 per year.

The per account quota on the admin page is the groundwork: an admin could assign plans first, with online payment added later.

## 2. Backup and export

- One click export of all my data across every year (spreadsheets plus receipts).
- A backup of the database and receipts for the operator, from the admin page or the command line.

## 3. Reminders by email

- A warning when the days in the base country are getting close to missing the 183 day line.
- A warning when receipt storage is almost full.

The email system added with email confirmation makes this straightforward.

## 4. Deployment guide

The app runs on Flask's development server. A short guide for going public: a production server (for example gunicorn), HTTPS through a reverse proxy, backups, and the `APP_BASE_URL`, `SECRET_KEY` and Gmail settings.
