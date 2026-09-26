# Deep bug hunt prompt for Nomad Life

Copy everything below the line into a new Claude Code session opened on this repository.

---

You are doing an extensive, file by file bug hunt on **Nomad Life**, a Flask + SQLite web app that helps digital nomads track the days they spend in each country during a solar year and store receipts (rental contracts, hotel or home rent, flight tickets). Your job is to find **real** bugs, prove each one, fix it, and leave the app better than you found it.

## Ground rules

1. **Real bugs only.** A bug is behavior that is wrong for a user, an attacker, the data, or the operator: a crash, a wrong result, lost or leaked data, a security hole, a broken layout, an inaccessible control, a misleading message, or docs that contradict the code. Style preferences, refactors and "could be nicer" ideas are not bugs.
2. **Reproduce before you fix.** For every suspected bug, write a small script or browser step that shows it failing. If you cannot make it fail, it is not a confirmed bug: record it under "Checked, not a bug" with the reason.
3. **Never invent.** If a file has no bugs, say so. A short honest report beats a long padded one. There is no quota.
4. **Fix minimally.** Change only what the bug needs. Keep existing behavior, names and style. Do not reformat files.
5. **Verify every fix** with the same reproduction, then rerun the full checks listed below.
6. **No em dashes or en dashes** anywhere: code, UI text, markdown, commit messages.
7. Work in a scratch folder outside the repo for scripts, databases and screenshots. Do not commit scratch files.

## Project map

| File | Role |
| --- | --- |
| `app.py` | Flask app factory, config (`config.env`), auth (sign up, email confirmation `verify` and `purge_unverified`, email cooldown `email_allowed`, sign in, password reset tokens, session fingerprint, `send_template_email`), years, movements, movements pagination (`paginate`, `page_window`, `page_size`, `dashboard_url`, `back_to_movement`), day math (`compute_stats`, `stay_order`, `overlap_notes`), uploads (`save_upload`, per receipt limit, per user quota), downloads, accountant package route (`year_package`, `build_package_data`, `can_download_package`), plans (`PLANS`, `plan_catalog`, `current_plan`, `next_plan`, `plan` route, `request_size_limit`), landing, robots.txt, sitemap.xml, SEO and share metadata (`site_meta`), 404 handler, admin page (`admin`, `admin_user`, `ADMIN_EMAILS`, `user_quota`), account page (`account`, `confirm_email`, `delete_account`), sign in limits (`take_attempt`, `forgive`, `auth_events` table, `PROXY_COUNT`) |
| `package.py` | Annual accountant package: summary model, PDF (fpdf2), CSV with formula protection, safe ZIP paths, streaming ZIP with manifest and checksums |
| `tests/` | `unittest` suite (`python -m unittest`), helpers with an isolated app per test |
| `db.py` | SQLite schema, `MIGRATIONS` applied by `migrate()` on start, per request connection, `query`, `execute`, `transaction` (BEGIN IMMEDIATE) |
| `mailer.py` | Gmail SMTP for confirmation and reset emails: `build_message` (text + HTML + inline logo), `send_email`, console fallback |
| `countries.py` | Canonical country names used for normalization and suggestions |
| `runWebApp.sh` | Launcher: Python check, config creation, venv, requirements, port parsing, freeing the port, start |
| `requirements.txt`, `config.env.example` | Dependencies and settings (`APP_PORT`, `APP_BASE_URL`, `SECRET_KEY`, Gmail, storage paths, `MAX_RECEIPT_MB`, `USER_QUOTA_MB`) |
| `templates/base.html` | Layout, head (SEO, Open Graph, Twitter card, robots, manifest), header, flashes, footer |
| `templates/landing.html` | Public landing page, Plans section, JSON-LD, share buttons |
| `templates/dashboard.html` | Year dashboard: stats, movements table, storage card with Upgrade button, days per country |
| `templates/admin.html` | Admin page: totals, accounts, quota, disable and delete forms |
| `templates/account.html`, `templates/email/change_email.*`, `templates/email/finish_signup.*` | Account page (password, email, delete) and the email change confirmation |
| `templates/plan.html` | Plan page: current plan, next tier and price (no payment yet) |
| `templates/movement.html`, `base_location.html`, `year_new.html` | Forms for movements, base location and documents, new year |
| `templates/_macros.html`, `_theme_toggle.html`, `_countries.html` | Upload form, document list, delete form, square page links (`pagination`), plan card (`plan_card`), theme toggle, country data |
| `templates/auth/*.html` | Sign in, sign up, forgot and reset password |
| `templates/email/*` | Email layout (`_layout.html`, light and dark), confirmation and reset emails in HTML and plain text |
| `static/css/style.css` | All styles, light and dark tokens, responsive rules |
| `static/js/theme.js` | Theme toggle, flash message close buttons, delete confirmations, country combobox, file checks (size, quota), download busy state (`nl_download` cookie), copy link, native share |
| `static/404.html` | Standalone 404 page (served by the app and usable by a web server) |
| `static/fonts/*` | Flag font (Twemoji), DejaVu Sans and IPAGothic for the PDF, with license files |
| `static/site.webmanifest`, `static/img/*` | Install metadata, logo, icons, social preview image |
| `README.md`, `CHANGELOG.md`, `LICENSE`, `.gitignore` | Docs and repo config |
| `CLAUDE.md` | Guidance for Claude Code: commands, architecture, conventions |

## Method

1. **Read every file completely**, top to bottom, before judging it. Do not skim. Keep notes of assumptions each function makes.
2. **Run the app** with `./runWebApp.sh` (it creates `config.env` on first run). Use a throwaway `DATABASE_PATH` and `UPLOAD_DIR` for experiments, or the Flask test client with `create_app({...})` overrides.
3. **Trace these flows end to end**, in the code and in the running app:
   - Sign up, confirmation link (valid, expired after 20 minutes, replaced by a new sign up, reused, tampered), sign in before confirming, cleanup of unconfirmed accounts, one email per minute, sign in (with and without "remember me"), sign out, forgot password, reset link (valid, expired, reused, tampered), sessions after a reset.
   - Emails: HTML and text versions, inline logo, light and dark mode.
   - Plans: header chip, storage card Upgrade button, `/plan` for each plan, landing Plans section, per plan quota and receipt limit (including oversized requests), unknown plan values.
   - Create a year, edit the base, delete a year (confirm text, files removed, space freed).
   - Add, edit, delete movements, including invalid dates, dates outside the year, leap years, same day stays, overlapping and nested stays, travel days shared by two stays.
   - Movements pages: 10, 11, 20 and 21 movements, bad `page` and `per_page` values, page size choice, deleting the last row of the last page, "Back" from a movement, stats identical on every page.
   - Day math: days per country, base days (only past days without a stay), days abroad, "so far" counts, year elapsed, the 183 day label, counted days per stay, future years. Check against hand computed expectations.
   - Receipts: upload on base and movement pages and while creating a movement, allowed and rejected types, non Latin file names, empty files, exact limit, over limit, over quota, parallel uploads, download, inline view, delete, other users trying to access them.
   - Landing page, "Launch app", share buttons, copy link, robots.txt, sitemap.xml, 404 for unknown URLs and for other users' resources.
   - Launcher: missing or odd `config.env` values, busy port, broken venv, missing tools.
4. **Use a browser** (Playwright with the preinstalled Chromium) for UI: widths 320, 390, 768, 1024, 1280 and 1920, light and dark theme, keyboard only navigation, screen reader names, and a contrast check (WCAG AA). Take screenshots of anything suspicious and look at them.
5. **Think like an attacker** for every route: another user's ids, missing or forged CSRF tokens, crafted file names, huge or chunked bodies, header injection in emails, open redirects via `next`, HTML or script in any stored field, path traversal in stored names.

## Checklists by file type

**Python**
- Logic and edge cases: off by one in date ranges, leap years, empty inputs, `None` handling, integer vs float sizes, rounding in messages.
- SQL: every query scoped by `user_id`, parameters bound (no string formatting), transactions around check then write, rollback on error, no connection used after close.
- Auth: password hashing, session fingerprint, token salt and expiry, login required on every private route, `next` validation.
- Files: path building, cleanup on every failure path, disk errors, MIME from extension, `Cache-Control` for private files.
- Config: parsing, defaults, legacy keys, clear errors.
- Error handling: no raw 500 for user mistakes, correct status codes, flash messages that match what happened.

**Jinja and HTML**
- Autoescaping everywhere, no `|safe` on user data, `tojson` for scripts.
- Forms: CSRF token, `enctype` for uploads, `required`, `min`/`max`, labels tied to inputs, values kept after validation errors.
- Semantics and accessibility: one h1, no skipped heading levels, landmarks, alt text, button vs link, focus order, `aria-*` correctness.
- SEO on the landing page: title length, description length, canonical, robots, Open Graph, Twitter card, JSON-LD validity; app pages must stay `noindex`.

**CSS**
- Tokens defined for light, dark and system dark; no hard coded colors that break one theme.
- Contrast of text, pills, buttons and focus rings in both themes.
- Responsive behavior at the widths above: no horizontal scroll, no overlap, no clipped text, tap targets usable.

**JavaScript**
- Null checks for elements that exist only on some pages, event listeners attached once, feature detection (clipboard, `navigator.share`), `localStorage` in try/catch, file size and quota checks agreeing with the server.

**Shell (`runWebApp.sh`)**
- `set -euo pipefail` side effects, quoting, word splitting, portability between macOS and Linux (sed, sha256sum vs shasum, lsof, fuser, ss), behavior when tools are missing.

**Docs and config**
- README, CHANGELOG and `config.env.example` must match the actual code, defaults and routes.

## Deliverables

1. A findings table in your final message:

   | ID | File:line | Severity (critical, high, medium, low) | Category | How to reproduce | Root cause | Fix | Verified by |
   | --- | --- | --- | --- | --- | --- | --- | --- |

2. A "Checked, not a bug" list for suspicions you ruled out, with one line of reasoning each.
3. A per file summary: every file in the project map with either its bug IDs or "no bugs found".
4. The fixes applied in the repo, each one minimal and verified.

## Before you finish

- Rerun every reproduction script and a full smoke test (sign up, year, movement with receipt, dashboard numbers, reset password, cross user access returns 404).
- Check the landing page and dashboard visually in light and dark mode at 390 and 1280 px.
- `grep` the repo for em dashes and en dashes.
- Bump the patch version in `app.py` (`APP_VERSION`) and `README.md`, add a CHANGELOG entry listing each fixed bug in plain words, then commit with a clear message and push.
