# Changelog

All notable changes to this project are documented in this file.

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
