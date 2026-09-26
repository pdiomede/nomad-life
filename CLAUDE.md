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
- Static CSS/JS are cached for 7 days and linked with `?v=<APP_VERSION>`; a browser that already has them will not see edits until the version changes or the cache is bypassed.
- `runWebApp.sh` reads `APP_PORT` from `config.env` with the same dotenv parser as the app and exports it, so `config.env` wins over a shell variable.

## Architecture

**Single Flask module.** `app.py` holds config, helpers and all routes (`register_routes`). It ends with `app = create_app()`, so importing `app` loads `config.env` and initializes `data/nomad.db`. Tests call `create_app({...})` with overrides; `tests/helpers.py` (`AppTestCase`) gives each test a temporary database and upload folder with CSRF disabled.

**Database** (`db.py`). One sqlite connection per request on `g`. `query()` reads, `execute()` commits (and rolls back on error), `transaction()` runs `BEGIN IMMEDIATE` for check-then-write sequences. Schema is `CREATE TABLE IF NOT EXISTS` in `SCHEMA`; there is no migration framework, so schema changes need an explicit, repeatable migration step. Foreign keys cascade (years -> movements -> documents), enabled per connection.

**Ownership.** Every private query is scoped by `user_id`. Use `get_year_or_404`, `get_movement_or_404`, `get_document_or_404`: other users' data must return 404, never 403.

**Auth.** Flask-Login with `User.get_id()` = `"<id>:<password fingerprint>"`, so a password reset invalidates every other session and remember cookie. Reset tokens (itsdangerous) carry the same fingerprint. Cookie names are app specific (`nomadlife_session`, `nomadlife_remember`), `SameSite=Lax`, and `Secure` when `APP_BASE_URL` is `https://`. All POST forms carry the Flask-WTF CSRF token; tokens live as long as the session (`WTF_CSRF_TIME_LIMIT=None`), and a stale token on sign out still signs out. Redirects after sign in go through `safe_next()` (same site paths only, no control characters), and the unauthorized handler only puts GET pages in `next`. `<int:...>` URL parts are capped to SQLite's range (`DbIntConverter`), so huge ids are a 404.

**Day math is centralized.** `compute_stats(year_row, movements)` is the single source of truth for the dashboard and the accountant package. Rules: dates inclusive; days not covered by a movement count toward the base country; a day shared by two stays goes to the stay that started later; ties on start day go to the shorter stay (`stay_order`); `counted` gives the days each movement actually contributes. Countries are compared through `normalize_country()`/`fold()` (accents, case, aliases and ISO codes from `countries.py` `COUNTRY_DATA`); unknown free text is kept as typed.

**Dashboard pagination is display only.** `dashboard()` loads every movement for `compute_stats` and passes `paginate(...)` (key `rows`, not `items`, which Jinja would resolve to the dict method) to the table. The page size (`PER_PAGE_OPTIONS`) is remembered in `session["per_page"]` by `page_size()`. Build dashboard links with `dashboard_url()`: links land on `#movements`, redirects pass `anchor=False` so flash messages stay in view. `back_to_movement()` finds the page that shows a movement using the same `ORDER BY` as the dashboard.

**Uploads and storage.** `save_upload()` is the only write path for documents: it measures the upload stream, checks type, empty, per-receipt limit and quota, saves to `UPLOAD_DIR/<user_id>/<uuid>.<ext>`, then re-checks the quota and inserts inside `db.transaction()` so parallel uploads cannot exceed it; any failure removes the file. `display_name()` keeps Unicode names; MIME type comes from the extension, never the client. Deletes remove DB rows first and files after (`remove_files()`). Downloads send `Cache-Control: private, no-store`. Sizes: `mb_setting()` (binary MB), `MAX_RECEIPT_MB` (legacy `MAX_UPLOAD_MB`), `USER_QUOTA_MB`, `MAX_CONTENT_LENGTH` = receipt limit + 1 MB; `format_size()` rounds capacities down and rejected sizes up.

**Deletes need two confirmations.** Delete forms use the `ui.delete_form` macro (`templates/_macros.html`, `data-confirm-delete`). `static/js/theme.js` shows a two-step `<dialog>` (native `confirm` fallback) and only then sets `confirm_delete=2`; the server refuses any delete without it (`delete_confirmed()`). Year deletion also needs the typed year (`confirm_year`).

**Accountant package** (`package.py`). `build_package_data()` in `app.py` gathers numbers with `compute_stats` into a `PackageData`; `package.prepare()` builds README, PDF (fpdf2, fonts in `static/fonts`) and CSV files eagerly; `package.stream()` yields the ZIP in chunks through a non-seekable sink, computing SHA-256 for `manifest.csv`. ZIP paths go through `clean_segment()`/`_unique()`/`_check_arcname()`; CSV cells through `csv_safe()`. Access is gated in one place: `can_download_package()`.

**Frontend.** Server-rendered Jinja with progressive enhancement in `static/js/theme.js` (theme toggle, delete dialog, country combobox, file size/quota checks, download busy state via the `nl_download` cookie, share buttons). The combobox reads `#country-data` JSON from `templates/_countries.html`, which is included only on the three pages with country fields. Flags are emoji rendered with the bundled Twemoji Country Flags font (`unicode-range` limited).

**Styles.** `static/css/style.css` defines color tokens three times: `:root`, `:root[data-theme="dark"]` and the `prefers-color-scheme: dark` media block. Change all three together. Text colors use the WCAG AA tokens (`--link`, `--success-text`, `--error-text`, `--danger`), not the raw accent colors. `[hidden]` is forced to `display: none !important` because rules like `.field { display: flex }` would otherwise override it.

**SEO and public pages.** Only the landing page (`/`) is indexable; `base.html` defaults the robots block to `noindex`. `site_meta()` builds Open Graph, Twitter card, share links and JSON-LD from `APP_BASE_URL` (never from the request Host). `PRIVATE_PATHS` feeds `/robots.txt`; `/sitemap.xml` lists the landing page. The 404 page is the standalone `static/404.html`, served by the error handler. The signed-in app lives under `/app`, `/year/...`, `/movements/...`, `/documents/...`.

## Conventions

- No em dashes or en dashes anywhere: code, UI text, markdown, commit messages.
- Releases are patch bumps: update `APP_VERSION` in `app.py`, the "Current version" line in `README.md`, and add a `CHANGELOG.md` entry; then commit and push to `master`.
- Document new settings in `config.env.example` and the README configuration table. `config.env`, `data/` and `uploads/` are git-ignored.
- `PROMPT.md` is a ready-to-run bug hunt prompt with a project map; update its map when adding files or routes.
