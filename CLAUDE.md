# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Nomad Life is a Flask + SQLite web app that tracks the days a digital nomad spends in each country per solar year and stores receipts. No build step, no JS bundler, no linter configured.

## Commands

```bash
./runWebApp.sh                      # create config.env and .venv if missing, install requirements when
                                    # requirements.txt changes, free APP_PORT, start the app
.venv/bin/python -m unittest        # full test suite (tests/)
.venv/bin/python -m unittest tests.test_package.PackageContentsTests.test_structure_and_order   # one test
.venv/bin/python -W ignore::ResourceWarning -m unittest   # quieter output
FLASK_DEBUG=1 .venv/bin/python app.py                     # debug mode (templates reload)
```

- Without `FLASK_DEBUG=1`, Jinja templates are cached: restart the server after editing templates.
- Static CSS/JS are cached for 7 days and linked with `?v=<asset_version>`, a hash of `style.css` and `theme.js` computed at startup, so edits reach browsers after a server restart without a version bump.
- `config.env` wins over exported shell variables for every key it contains (`load_dotenv(..., override=True)`); `runWebApp.sh` also reads `APP_PORT` with the same parser and exports it. To point a manual server at a scratch database, pass overrides to `create_app({...})` instead of environment variables.

## Architecture

**Single Flask module.** `app.py` holds config, helpers and all routes (`register_routes`). It ends with `app = create_app()`, so importing `app` loads `config.env` and initializes `data/nomad.db`. Tests call `create_app({...})` with overrides; `tests/helpers.py` (`AppTestCase`) gives each test a temporary database and upload folder with CSRF disabled, blanks the Gmail settings (`config.env` may hold real ones), records emails in `self.outbox` instead of sending, and its `signup()` goes through the confirmation link and signs in.

**Database** (`db.py`). One sqlite connection per request on `g`. `query()` reads, `execute()` commits (and rolls back on error), `transaction()` runs `BEGIN IMMEDIATE` for check-then-write sequences. Schema is `CREATE TABLE IF NOT EXISTS` in `SCHEMA`; there is no migration framework. New columns go in `SCHEMA` and in `db.MIGRATIONS` (add column, backfill), which `migrate()` applies on every start, idempotently and under `BEGIN IMMEDIATE`. Nothing in `SCHEMA` may reference a migrated column, because old databases run `SCHEMA` before the column exists. Foreign keys cascade (years -> movements -> documents), enabled per connection.

**Ownership.** Every private query is scoped by `user_id`. Use `get_year_or_404`, `get_movement_or_404`, `get_document_or_404`: other users' data must return 404, never 403.

**Auth.** Flask-Login with `User.get_id()` = `"<id>:<password fingerprint>"`, so a password reset invalidates every other session and remember cookie. Reset tokens (itsdangerous) carry the same fingerprint. Cookie names are app specific (`nomadlife_session`, `nomadlife_remember`), `SameSite=Lax`, and `Secure` when `APP_BASE_URL` is `https://`. All POST forms carry the Flask-WTF CSRF token; tokens live as long as the session (`WTF_CSRF_TIME_LIMIT=None`), and a stale token on sign out still signs out. Redirects after sign in go through `safe_next()` (same site paths only, no control characters), and the unauthorized handler only puts GET pages in `next`. `<int:...>` URL parts are capped to SQLite's range (`DbIntConverter`), so huge ids are a 404.

**Email confirmation.** Sign up creates the user with `verified_at` NULL and emails a `/verify/<token>` link; it does not sign in. Confirming sets `verified_at` and sends the user to sign in (no auto sign in, to avoid login CSRF). `load_user` ignores unconfirmed users. `purge_unverified()` deletes unconfirmed users older than `VERIFY_MINUTES` (20) that hold no years; it runs at startup, at the top of every auth route, and in a `before_request` hook at most once a minute. Signing up again with a pending email replaces that account; signing in with the right password resends the link; a completed password reset also confirms. Tokens come from `serializer(salt)`: `"password-reset"` and `"email-verify"` must stay distinct. `email_allowed()` allows one email per account per `EMAIL_COOLDOWN_SECONDS` (`users.email_sent_at`).

**Emails.** `send_template_email(to, subject, name, **ctx)` renders `templates/email/<name>.txt` and `.html` (tables, inline light colors, dark overrides in a `prefers-color-scheme` block plus Outlook `[data-ogsc]`/`[data-ogsb]`). `mailer.build_message()` builds multipart/alternative with the logo attached inline as `cid:logo@nomadlife` (never a remote URL: `APP_BASE_URL` may be localhost). `send_email()` returns False only on an SMTP failure; without Gmail settings it prints the text version and returns True.

**Day math is centralized.** `compute_stats(year_row, movements)` is the single source of truth for the dashboard and the accountant package. Rules: dates inclusive; days not covered by a movement count toward the base country only once they have passed (future days without a stay are `upcoming`, counted nowhere), while movements count in full, also in the future; a day shared by two stays goes to the stay that started later; ties on start day go to the shorter stay (`stay_order`); `counted` gives the days each movement actually contributes. Countries are compared through `normalize_country()`/`fold()` (accents, case, aliases and ISO codes from `countries.py` `COUNTRY_DATA`); unknown free text is kept as typed.

**Dashboard pagination is display only.** `dashboard()` loads every movement for `compute_stats` and passes `paginate(...)` (key `rows`, not `items`, which Jinja would resolve to the dict method) to the table. The page size (`PER_PAGE_OPTIONS`) is remembered in `session["per_page"]` by `page_size()`. Build dashboard links with `dashboard_url()`: links land on `#movements`, redirects pass `anchor=False` so flash messages stay in view. `back_to_movement()` finds the page that shows a movement using the same `ORDER BY` as the dashboard.

**Account page and limits.** `/account` changes the password (current password required; `login_user` again with the new fingerprint so only other sessions end), changes the email through a signed `email-change` link (`confirm_email`, stale once the password changes), and deletes the account (`delete_account()`, shared with the admin page). Failed sign ins and wrong current passwords are recorded in `auth_events` by `record_event("fail", email)` and checked with `limited_for()` before the password is verified; reset requests use kind `forgot`. Limits live in `LIMITS` and `LIMIT_WINDOW_MINUTES`. The per IP key uses `request.remote_addr`, so behind a proxy `PROXY_COUNT` enables `ProxyFix`.

**Admin page.** `/admin` and `POST /admin/users/<id>` are only for accounts in `ADMIN_EMAILS` (config.env, parsed by `admin_emails()`); everyone else gets a 404 (`require_admin()`). Per account quota is `users.quota_bytes` (NULL means `USER_QUOTA_BYTES`); always read quotas through `user_quota()`. `users.disabled` makes `load_user` return None and blocks sign in. New columns are added by `db.migrate()` (`MIGRATIONS`, checked with `PRAGMA table_info`), which `init_db` runs on every start; add future columns there.

**Uploads and storage.** `save_upload()` is the only write path for documents: it measures the upload stream, checks type, empty, per-receipt limit and quota, saves to `UPLOAD_DIR/<user_id>/<uuid>.<ext>`, then re-checks the quota and inserts inside `db.transaction()` so parallel uploads cannot exceed it; any failure removes the file. `display_name()` keeps Unicode names; MIME type comes from the extension, never the client. Deletes remove DB rows first and files after (`remove_files()`). Downloads send `Cache-Control: private, no-store`. Sizes: `mb_setting()` (binary MB), `MAX_RECEIPT_MB` (legacy `MAX_UPLOAD_MB`), `USER_QUOTA_MB`, `MAX_CONTENT_LENGTH` = receipt limit + 1 MB; `format_size()` rounds capacities down and rejected sizes up.

**Deletes need two confirmations.** Delete forms use the `ui.delete_form` macro (`templates/_macros.html`, `data-confirm-delete`). `static/js/theme.js` shows a two-step `<dialog>` (native `confirm` fallback) and only then sets `confirm_delete=2`; the server refuses any delete without it (`delete_confirmed()`). Year deletion also needs the typed year (`confirm_year`).

**Accountant package** (`package.py`). `build_package_data()` in `app.py` gathers numbers with `compute_stats` into a `PackageData`; `package.prepare()` builds README, PDF (fpdf2, fonts in `static/fonts`) and CSV files eagerly; `package.stream()` yields the ZIP in chunks through a non-seekable sink, computing SHA-256 for `manifest.csv`. ZIP paths go through `clean_segment()`/`_unique()`/`_check_arcname()`; CSV cells through `csv_safe()`. Access is gated in one place: `can_download_package()`.

**Frontend.** Server-rendered Jinja with progressive enhancement in `static/js/theme.js` (theme toggle, dismissible flash messages, delete dialog, country combobox, file size/quota checks, download busy state via the `nl_download` cookie, share buttons). The combobox reads `#country-data` JSON from `templates/_countries.html`, which is included only on the three pages with country fields. Flags are emoji rendered with the bundled Twemoji Country Flags font (`unicode-range` limited).

**Styles.** `static/css/style.css` defines color tokens three times: `:root`, `:root[data-theme="dark"]` and the `prefers-color-scheme: dark` media block. Change all three together. Text colors use the WCAG AA tokens (`--link`, `--success-text`, `--error-text`, `--danger`), not the raw accent colors. `[hidden]` is forced to `display: none !important` because rules like `.field { display: flex }` would otherwise override it.

**SEO and public pages.** Only the landing page (`/`) is indexable; `base.html` defaults the robots block to `noindex`. `site_meta()` builds Open Graph, Twitter card, share links and JSON-LD from `APP_BASE_URL` (never from the request Host). `PRIVATE_PATHS` feeds `/robots.txt`; `/sitemap.xml` lists the landing page. The 404 page is the standalone `static/404.html`, served by the error handler. The signed-in app lives under `/app`, `/year/...`, `/movements/...`, `/documents/...`.

## Conventions

- No em dashes or en dashes anywhere: code, UI text, markdown, commit messages.
- Releases follow semantic versioning from 1.0.0 (stable): patch bumps for fixes. Update `APP_VERSION` in `app.py`, the "Current version" line in `README.md`, and add a `CHANGELOG.md` entry; then commit and push to `master`.
- Document new settings in `config.env.example` and the README configuration table. `config.env`, `data/` and `uploads/` are git-ignored.
- `PROMPT.md` is a ready-to-run bug hunt prompt with a project map; update its map when adding files or routes.
