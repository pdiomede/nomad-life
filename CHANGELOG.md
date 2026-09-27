# Changelog

All notable changes to this project are documented in this file.

## [1.2.2] - 2026-09-26

### Added

- Plans: Free (500 MB of receipts, 10 MB per receipt), Pro ($4 a month or $40 a year, 5 GB, 25 MB per receipt, accountant package) and Nomad+ ($9 a month or $90 a year, 25 GB, 50 MB per receipt, priority support). Each plan sets its own storage and receipt limits. Everyone starts on Free; online payment is not available yet. The accountant package is a Pro feature: on Free the dashboard card offers "Upgrade to Pro" instead of the download.
- The header shows only your email with an arrow. It opens a menu with your plan (for example FREE, which opens the new plan page), Admin for admins, and Sign out. The Account page is in the footer.
- "Upgrade" on the Receipt storage card opens the plan page, with your plan, its limits and usage, and the next plan with its price.
- The landing page has a Plans section with every plan, its price and features, and a "Pricing" button at the top Prices are in US dollars, and each paid plan says "Everything in <previous plan>, plus:" and highlights what it adds.
- The admin page shows each account's plan and can change it with a plan selector. An account without a custom quota gets the quota of its plan.

### Fixed

- Security: anyone could sign up again with an address still waiting for confirmation, replace its password, and take the account once the owner clicked the newest email. For an admin address this gave the admin page. A pending sign up is never replaced now: the mailbox receives a link to choose the password, and earlier confirmation links stop working.
- Security: enabling a disabled account brought back every session and "remember me" cookie it had before. Disabling now signs the account out for good.
- Security: a mistyped email change could not be cancelled, so the owner of the mistyped address could still take the account. The account page shows a pending change with a Cancel button, and typing your current address again also cancels it.
- Setting a new password with a reset link did not lift the lock after wrong guesses, although the lock message suggested it would.
- Disabled accounts could still get password reset emails, reset their password and confirm email changes.
- An admin's disable or quota on an account waiting for confirmation was undone by signing up again.
- Signing up could delete an unconfirmed account that held data from an older version.
- After a confirmation email failed to send, signing in again said "with the link we sent a moment ago" when nothing was sent.
- Accountant package: for the current or a future year without stays, the PDF still said "Every day counts toward the base country", which contradicted its own numbers.
- Adding a movement while its year was deleted in another tab showed a server error, and editing a movement deleted meanwhile said "Movement updated."
- Opening a confirmation link in a browser signed in to another account said "Please sign in" and then showed the other account. It now says which address was confirmed and how to switch.
- A confirmation link sent again from the sign in page, opened after the account's 20 minutes, said to use the newest email instead of saying it had expired.
- Sizes of 1024 MB and more were shown in MB (for example "5120 MB"). They now show in GB.
- On phones about 300 px wide the header ran past the right margin once it showed the plan chip.
- Uploads over a Free plan's receipt limit were read in full before being refused, once paid plans raised the overall limit. Each request is limited to its user's plan again, so oversized files are refused before they are read.
- Search engine data for the landing page still said the app was only free. It now lists every plan with its price.

## [1.2.1] - 2026-09-26

### Fixed

- Security: an older email change link still worked after a newer change, so whoever owned a mistyped address could take the email back and then the whole account through "Forgot password". Only the newest request can be confirmed now, and only while the email is still the one it started from.
- Security: a password reset link sent to your old address kept working after you changed your email. Reset links are now tied to the address they were sent to.
- Security: sign in attempts sent all at once were not limited (40 parallel guesses all went through). Each attempt is now counted before the password is checked.
- An address that someone started to sign up with, but never confirmed, blocked other people from switching their email to it. Only confirmed accounts own an address now.
- Opening an email change link while signed in to another account said "Your email address is now ..." on the wrong account.
- If the confirmation email could not be sent, trying again right away said "We just sent you an email".
- When the limit for a whole network was reached, the message blamed "too many wrong passwords" on someone who typed theirs correctly. It now says the limit is for the network.
- A long email in the delete dialog was cut off on small phones, so you could not read what to type.

## [1.2.0] - 2026-09-26

### Added

- Account page, opened from your email in the header or **Account** in the footer:
  - change your password with your current one; you stay signed in here and every other device is signed out;
  - change your email: a confirmation link goes to the new address and the change happens when you open it (the link stops working if you change your password first);
  - delete your account with all years, movements and receipts, with your password, two confirmations and your email typed.
- Protection against password guessing:
  - after 5 failed sign ins for an account, or 20 from one network, within 15 minutes, signing in waits (even with the right password) and says for how long;
  - wrong current passwords on the account page count the same way;
  - password reset requests are limited to 5 per network per 15 minutes.
- `PROXY_COUNT` setting so the limits see each visitor's address behind a reverse proxy.

## [1.1.0] - 2026-09-26

### Added

- Admin page at `/admin` for the accounts listed in the new `ADMIN_EMAILS` setting, linked from the header and footer:
  - totals for accounts, disabled accounts, custom quotas, receipt storage, years and movements;
  - every account with sign up date, last sign in, number of years and storage used, 25 per page;
  - a storage quota per account, or the `USER_QUOTA_MB` default;
  - disable and enable accounts (a disabled account is signed out everywhere and cannot sign in);
  - delete an account with all its data, after two confirmations and typing the email.
- Admins cannot disable or delete themselves. Everyone else gets "Page not found" at `/admin`.
- The last sign in date is recorded for each account.
- Existing databases get the new account columns automatically on the next start.

### Changed

- The typed confirmation in the delete dialog ignores upper and lower case and shows the right keyboard on phones (numbers for a year, email for an account).

## [1.0.0] - 2026-09-26

First stable release. Nomad Life now covers the full workflow it was built for: track every day of a solar year by country, keep the receipts that prove it, and hand your accountant one package with the summary, spreadsheets and files. From here on, versions follow semantic versioning.

### Added

- New accounts confirm their email address. Signing up sends a confirmation link, and the account can be used only after opening it. An account not confirmed within 20 minutes is deleted, so nobody can register with an address they do not own. Signing up again before confirming starts over with the new password, signing in before confirming sends a fresh link, and resetting the password also confirms the account. Accounts created before this change are already confirmed.
- Confirmation and password reset emails are designed emails with the Nomad Life logo, readable in light and dark mode, with a plain text version for mail apps that prefer it.
- At most one email per account per minute, so sign up, sign in or "Forgot password" cannot be used to flood someone's inbox.
- Messages at the top of the page (for example "You have been signed out.") have an x on the right to dismiss them.

### Changed

- Days without a stay count toward the base country only once they have passed; logged stays still count in full, including future ones. A year that has not started, or the rest of the current year, no longer shows every remaining day as spent in the base country. The accountant package lists those days as "Days still to come without a stay".
- Forms ask for the country before the city.
- The footer link is no longer underlined (it still underlines on hover).
- Stylesheet and script links change whenever those files change, so browsers load updates at once instead of keeping a cached copy for up to a week.

### Fixed

- Notes with line breaks were refused as "more than 5000 characters" even though the text box allowed them, because each line break counted twice. Stays saved with longer notes before 0.0.11 can be edited again without shortening them.
- If a stay or year was deleted while one of its receipts was uploading, you landed on "Page not found" and the explanation appeared later on another page. You now go to your dashboard with the message.
- Files just under 1 MB were shown as "1024 KB". They now show as "1 MB".
- Accountant package: the counting rules and the note under the timeline now explain that with identical dates the stay added last counts, so a stay showing 0 counted days is explained.
- The chosen file name in a file picker ran under the Upload button or off the page when it was long.
- A very long place name (for example Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch) made the movement and base pages and their messages scroll sideways; long country names also broke the dashboard cards.
- The red delete buttons faded on hover, dropping their text below readable contrast, and the Delete link on the row of today's stay did the same in the dark theme.
- The 404 page never told screen readers that the page was not found ("404").
- On very small phones (300 to 310 px) the landing page scrolled sideways since 0.0.11.

## [0.0.11] - 2026-09-26

### Fixed

- Dark theme: text in the row of the stay you are in today was slightly below the contrast needed to read it comfortably.
- Keyboard focus disappeared after pressing "Download accountant package"; it now stays on the button.
- The "Nomad Life" name in the header wrapped onto two lines on small phones and next to long email addresses. Long emails are now shortened with "..." (hover shows the full address).
- The white "PDF" and "IMG" labels on document badges were hard to read on the lighter part of the colored background.
- Screen readers did not read the second step of the delete dialog ("Are you sure? ... It cannot be undone.").
- "1 days" now reads "1 day", both in days per country and in "1 day short of 183".
- The rule for shared days did not explain what happens when two stays start on the same day. The dashboard and the overlap message now say it: the shorter stay counts, and with identical dates the newest one.
- The base location form lost what you typed when it showed an error.
- "Cancel" on a movement page went back to page 1 of the movements list instead of the page you came from.
- Notes are now limited to 5000 characters. An oversized form no longer says a file was too large when no file was sent.
- Deleting a stay or year while one of its receipts was uploading could leave a file behind that no quota counted, or show a server error. The upload now says the stay no longer exists.
- `runWebApp.sh` said "no lsof, fuser or ss is available" even when they were installed. It now explains that ports below 1024 need administrator rights, or that another user's process holds the port.
- Variables exported in the shell (such as `SECRET_KEY` or `DATABASE_PATH` from another project) silently replaced the values in `config.env`. `config.env` now wins.
- A `~` in `DATABASE_PATH` or `UPLOAD_DIR` created a folder literally named `~` inside the app folder instead of using your home folder.
- `config.env`, which holds your secret key and Gmail App Password, was created readable by every user on the computer. It is now private to you.
- `PROMPT.md`: the project map now lists `CLAUDE.md`, the country picker and the download busy state.

## [0.0.10] - 2026-09-26

### Fixed

- Security: a crafted sign in link with a tab in it (`/login?next=%2F%09%2Fevil.com`) sent you to another website after signing in. A newline in the same place showed an error page. Only pages of this app are accepted now.
- Signing out from a page left open for more than an hour failed with "Your session expired" and left you signed in. Form tokens now last as long as your session, and signing out always signs you out.
- After your session ended, pressing Delete, Save or Sign out and then signing in again ended on a bare "Method Not Allowed" page. You now land on your dashboard.
- Very large numbers in a year, movement or document address showed a server error instead of "Page not found".
- When `APP_BASE_URL` starts with `https://`, the sign in cookies are now marked Secure (never sent over plain HTTP) and SameSite=Lax.
- An empty or invalid `APP_PORT` crashed the app with a Python traceback when it was started without `runWebApp.sh`. Empty now means 5050, and anything else gets a clear message.
- `runWebApp.sh`: a virtual environment left half created (for example on Ubuntu without `python3-venv`, or after pressing Ctrl+C) made every later run fail. It is now detected and rebuilt, and a failed creation cleans up after itself.
- `runWebApp.sh`: an `APP_PORT` with leading zeros was read as an octal number, so `0022` passed the checks and could stop whatever listens on port 22. Ports are now always read as decimal.
- Day counts: country names that differ only by accents, a curly apostrophe or Unicode form (for example "Transnístria" and "Transnistria") were counted as two countries, even for the base country. They now count as one.
- Accountant package: a very long city or country name made the download fail. Long names are shortened in the PDF tables; the spreadsheets keep them in full.
- Accountant package PDF: the text "{nb}" in a city or file name was replaced by the page count, so the receipt index could name a file that was not in the ZIP.
- Renaming a document to something with a slash, such as "Rent 03/2026", silently dropped everything before the slash.
- Decimal sizes in `config.env` (for example `MAX_RECEIPT_MB=1.2`) were shown 0.1 MB too small.
- The storage card could say "30 MB used of 30 MB" next to "40 KB left". Used space is no longer rounded up.
- New movement: the receipt type went back to "Hotel / home rent" after a date error.
- File pickers now tell screen readers the allowed types, the size limit and the space left, and announce the chosen file or why it was rejected.
- Delete dialog: long file names ran off the edge of the dialog and could not be read.
- Movements table: at some widths (around 1000 px, around 620 px, and on phones when a stay showed "counted" days) the Delete buttons or the Days column were cut off. Very long place names now wrap too.
- "Copy link" on the landing page could stay stuck on "Link copied" after two quick clicks.
- Keyboard focus on text fields and the highlighted country in the country picker were too faint to see in the light theme.

## [0.0.9] - 2026-09-26

### Added

- The movements list on each year's dashboard is split into pages of 10, with numbered square page buttons, previous and next arrows, and "Showing 11 to 20 of 34 movements" under the table.
- Choose 10, 25 or 50 movements per page. The choice is remembered as you move around the app, and switching keeps the first movement you were looking at on screen.
- You keep your place: "Back" on a movement, deleting a movement from the list and deleting it from its own page all return to the page of the list it was on.
- Day totals, days per country and counted days always use every movement, whatever page you are on.

### Fixed

- Movements pages: going back to page 1 jumped to the top of the dashboard, while every other page kept the list in view.
- Movements pages: after deleting a movement from page 2 or later, the "Movement deleted." message was scrolled out of view.
- Movements pages: a choice of 25 or 50 per page was forgotten after opening a movement, so "Back" returned to 10 per page on the wrong page.

## [0.0.8] - 2026-09-26

### Fixed

- Accountant package: some unzip tools that read archives as a stream (for example Java based ones) rejected the ZIP because receipts were stored uncompressed with their sizes written after the data. Every entry is now deflated (fastest level for PDFs and images), and receipts stay byte for byte identical.
- Accountant package: receipts in the PDF index and `manifest.csv` were listed in upload order, mixing stays. They are now grouped by stay, base documents first.
- Accountant package: long city, country or file names could create paths too long to extract with Windows "Extract All" (over 260 characters). Folder and file names now use shorter limits.
- Accountant package PDF: a long base city pushed the header line past the page edge. Header lines now wrap.
- Accountant package PDF: a long base country name overlapped the day count in the headline. Labels now wrap inside their column.
- Accountant package `README.txt`: it now says the manifest lists every other file, and explains how to open the comma separated CSV files in Excel regions that use a semicolon.

## [0.0.7] - 2026-09-26

### Added

- Accountant package: "Download accountant package" on each year's dashboard builds one ZIP with `summary.pdf`, `timeline.csv`, `country-totals.csv`, the original receipts in one folder per stay, `manifest.csv` with SHA-256 checksums and a `README.txt`.
- The PDF shows base, days in the base country and the 183 day line, days abroad, days per country, travel timeline with counted days, the counting rules, a receipt index and the not tax advice note on every page. The current year shows numbers so far and projected.
- Optional "Include my notes" (off by default).
- The package is streamed as it is built, with flat memory use, and is never stored, so it does not count against storage.
- Safe file names inside the ZIP (no path tricks, reserved or control characters, duplicates renamed), spreadsheet formula protection in CSV files, and missing receipts reported instead of failing.
- Automated test suite in `tests/` (`python -m unittest`).
- `fpdf2` dependency and bundled PDF fonts (DejaVu Sans, IPAGothic for Japanese names) with their licenses.

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
