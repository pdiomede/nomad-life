# Changelog

All notable changes to this project are documented in this file.

## [0.0.6] - 2026-09-26

### Added

- Searchable country picker with flags on every country field. It finds countries by name, common alternatives (UK, USA, Holland, Turkey) and two letter codes, ignores accents, and works with the keyboard, mouse and touch. Free text is still accepted for places outside the list.
- Flags next to country names on the dashboard and on movement and base pages, drawn with a bundled flag font so they also show on Windows.
- Documents can be renamed and their type changed.
- Edit and Delete buttons for each movement on the dashboard.
- Every delete (document, movement, year) asks twice in a confirmation dialog, and the server refuses deletes that were not confirmed twice. Deleting a year also asks you to type the year.
- SEO for the landing page: keyword title and description, canonical URL, structured data, `robots.txt`, `sitemap.xml`, and `noindex` on sign in and app pages.
- Web app manifest and icons, cache friendly static files, and receipts sent with `Cache-Control: private, no-store`.
- `PROMPT.md`, a ready to run prompt for a deep bug hunt.

### Fixed

- Country picker: pressing on the list scrollbar closed the list.
- Country picker: the "No matching country" message was not announced to screen readers.
- Delete: a double click on "Delete" passed both confirmations at once. The final button now waits a moment before it can be pressed.
- Delete: the "Type to confirm" field showed on every delete instead of only on the second step of a year delete.
- Delete: in browsers without dialog support nothing could be deleted. They now ask twice with the browser's own prompts.
- Edit and Delete buttons in lists now say which entry they belong to, for screen readers.
- The document rename form was squeezed on phones.
- Landing page: the "More options" share button showed even on devices that cannot share.
- The footer link no longer relies on color alone.

## [0.0.5] - 2026-09-26

### Added

- New Nomad Life logo in the header, on the sign in pages and as the browser and home screen icon.
- Landing page at `/` that explains the app, with a "Launch app" button in the top right corner. The app itself now lives at `/app`.
- Link preview image and text for social networks (Open Graph and Twitter card tags).
- Share buttons on the landing page for X, LinkedIn, Facebook, WhatsApp, Telegram and Reddit, plus "Copy link" and the device share menu where available.
- A funny standalone 404 page (`static/404.html`), served by the app for wrong links and ready for a web server.

### Fixed

- On screens about 961 to 1020 px wide, the floating receipt card on the landing page covered the "At least 183 days" label.

## [0.0.4] - 2026-09-26

### Added

- `MAX_RECEIPT_MB` in `config.env`: size limit for a single receipt (default 10 MB). Oversized requests are refused before the server reads them. The older `MAX_UPLOAD_MB` key still works.
- `USER_QUOTA_MB` in `config.env`: total receipt storage per user across all years (default 500 MB). Checked and recorded under one database write lock, so parallel uploads cannot exceed it.
- Dashboard "Receipt storage" card with space left, used and total, plus "Almost full", "Full" and "Over quota" states.
- Upload forms show the per receipt limit and the space left, check both in the browser before uploading, and are disabled when storage is full.
- Invalid limit values stop the app at startup with a message naming the setting.

### Fixed

- Size messages no longer contradict themselves because of rounding (for example "10 MB is larger than the 10 MB limit"). Space left and limits round down, rejected file sizes round up.
- A failed disk write (for example a full disk) no longer causes a server error or leaves a partial file that no quota accounts for.
- Storage that is exactly full is labelled "Full" instead of "Almost full".
- The "Choose file" button looks disabled when storage is full.
- An invalid legacy `MAX_UPLOAD_MB` value is reported under its own name instead of `MAX_RECEIPT_MB`.

## [0.0.3] - 2026-09-26

### Fixed

#### User interface

- Links, status messages, pills and danger buttons now meet WCAG AA contrast in light and dark mode.
- The year selector no longer claims to be a tab list without tabs. It is now a labelled navigation with the current year marked.

#### runWebApp.sh

- No longer exits silently when `config.env` has no `APP_PORT` line or uses `export APP_PORT=`.
- Reads `config.env` with the same parser as the app, so inline comments and quotes work.
- An `APP_PORT` exported in the shell no longer makes the app start on a different port than the script freed.
- Rejects ports outside 1 to 65535 instead of letting the app bind a different port.
- Rejects ports that browsers refuse to open (for example 5060 and 5061).
- Recreates a broken `.venv` instead of falling back to the system Python.
- Detects a busy port even when lsof, fuser and ss are not installed.

#### Receipts

- File names with non Latin characters (for example Japanese) are accepted and shown as uploaded. Accents are kept and client side folder paths are removed.
- Empty files are rejected.
- Files over the size limit are caught in the browser before upload, so the rest of the form is not lost. The limit is shown next to the file picker and in the error message.
- Upload dates are shown in local time instead of UTC.

#### Sign up, sign in and password reset

- Sign up rejects invalid email addresses such as `@.` or `a@b.`.
- Session and remember me cookies have unique names, so signing in to another app on localhost no longer signs you out.

#### Days and stays

- A side trip that starts on the same day as a longer stay is no longer hidden by it.
- When stays share days, the movements table shows how many days each stay actually counts, and saving an overlapping stay shows a warning.
- The base country label says "At least 183 days" instead of "Above 183 days", which was wrong at exactly 183.

## [0.0.2] - 2026-09-26

### Added

- Footer shows copyright 2026 and "Built by Paolo Diomede" linking to https://pdiomede.com.

### Fixed

- Country names are matched case insensitively and normalized, so "portugal" and "Portugal" no longer count as two countries.
- Resetting a password now signs out every other session, including "remember me" cookies.
- Reset links no longer embed part of the password hash.
- An empty `SECRET_KEY` no longer falls back to a known constant. A random key is generated and stored in `data/.secret_key`.
- Reset links no longer point to a stale port or trust the request Host header. An empty `APP_BASE_URL` now defaults to `http://localhost:APP_PORT`.
- Documents are served with a content type based on their extension instead of the one sent by the browser.
- Uploading without a file no longer reports "Document uploaded."
- Files are removed from disk only after the database delete succeeds, and a failed insert no longer leaves an orphan file.
- Duplicate sign up or year creation hitting a unique constraint shows a message instead of a server error, and failed writes roll back their transaction.

## [0.0.1] - 2026-09-26

### Added

- Sign up, sign in, sign out and password reset via Gmail SMTP.
- Solar year workspaces with base city and country.
- Movements with date range, city, country and notes.
- Document storage for base location (rental contract) and movements (hotel or home rent, flight tickets).
- Dashboard with days per country and 183 day indicator.
- Light and dark mode with an Aave inspired palette.
- `runWebApp.sh` launcher that checks requirements, installs dependencies and frees the port.
- SQLite storage and `config.env` configuration.

### Fixed

- Dashboard no longer overflows horizontally on mobile. Movements table fits small screens and the city links to the movement.
- Even spacing between stacked cards on the dashboard.
- Sign in keeps the email after a failed attempt.
- Editing a movement keeps the typed values when validation fails.
- Styled 404 page instead of the default server page.
- Expired form tokens show a friendly message instead of a raw 400 page.
- Small files show their size in bytes instead of "0 KB".
