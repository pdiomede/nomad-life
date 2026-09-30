# Changelog

All notable changes to this project are documented in this file.

## [1.3.1] - 2026-09-30

### Added

- Admin accounts table. The Users section of `/admin` is now a table with 10 accounts a page, a search box (part of an email or a name, ignoring case) and sortable columns: account, joined, last sign in, years and storage. Plan, flags (Admin, Two-factor on, Disabled, Email not confirmed, Over quota) and the storage used of the quota are shown in each row; on phones the table keeps the account and last sign in.
- Account page for admins (`/admin/users/<id>`, the **View** button or the email in the table): details (joined, email confirmed, last sign in, two-factor, signed in devices, years, movements, receipts, storage, support tickets), the plan and quota forms, disable or enable, delete, and the account's recent security activity. The quota, plan and access actions moved here from the list. **Back to accounts** returns to the same search, sort and page, also after saving.

### Changed

- The question mark in the header glows slowly until the support pages have been opened once in this browser, and again whenever a ticket has a new reply. It stays still for people who ask their system for reduced motion.
- The "Finish setting up your account" email (signing up again for an address waiting for confirmation) has a **Confirm my Account** button.
- The introduction on **Submit a ticket** stays on one line on wider screens.
- `setup_vps.md` uses the real server layout: the app, its database and its receipts in `/var/www/nomad-life`, run as `paolo`. gunicorn now lets running requests finish for up to 120 seconds when it restarts (`--graceful-timeout 120`, `TimeoutStopSec=150`), and the upgrade section explains that nobody is signed out by an upgrade and how to check that nobody is using the app first.

### Fixed

Database calls:

- A page view failed with an error after 10 seconds while another request was writing (for example renaming a large receipt): every page updated the session's "last seen" time and ran the minute cleanup, which both need the write lock. Both are now skipped when the database is busy, retried a minute later, and static files no longer load the user at all.
- Renaming a receipt held the database write lock while rewriting its ZIP (about a second for a 50 MB receipt), stalling every other request. The ZIP is rewritten first and swapped in under the lock.
- A password reset link submitted twice at once (two tabs) succeeded twice: both were told the password was set, only one worked, and the owner got two alerts.
- An email change link opened twice at once (the user and a mail scanner) applied twice, with two alerts and two undo links.
- Parallel requests could pass the 1000 stays per year limit and the 20 open tickets limit, since the count ran outside the write lock.
- Deleting a receipt while `flask zip-receipts` converted it left the deleted receipt's ZIP on disk for good.

Support pages:

- A subject or message made only of invisible marks (such as U+034F or a lone variation selector) was accepted, leaving a blank ticket whose admin link could not be clicked.
- The admin search did not find a name typed with a separate accent (as pasted from a Mac); names and messages are now stored composed, and the search compares both forms.
- A closed ticket said "Writing a message opens it again" even when the user was at the open ticket limit, where writing keeps it closed.
- An admin closing a ticket already closed (or reopening one already open) got no message at all.
- One long subject or email without spaces pushed the status and date columns of the admin table off screen.

## [1.3.0] - 2026-09-30

### Added

- Support tickets. A question mark in the header ("Contact support") and a **Support** item in the menu open `/support`, the list of your tickets with their status and their opened, last updated and closed dates. **Submit a ticket** asks for a type (Report a bug, Feature request, General question), a subject and a description. Each ticket is a conversation between the user and support; both sides can close it, and a user message reopens it.
- Admins get **Support tickets** in the menu (`/admin/support`): every ticket, filterable by status and type, searchable by email, name or `#number`, sortable by every column, with an answer, close, reopen and "reopen and send" on each ticket.
- Notification emails for new tickets and messages (to the admins) and for answers and closing (to the user). They link to the ticket and never contain the message.
- New tables `tickets` and `ticket_messages`, created on start. Deleting an account deletes its tickets.

### Changed

- The footer's author link and the page author in the structured data point to x.com/pdiomede.

### Fixed

Found in a review of the new support pages before release:

- Searching the admin table for `#` followed by a non ASCII digit or a very large number returned an error page.
- An admin answer sent while another admin closed the ticket was saved on the closed ticket; it is now refused with the draft kept, and a closed ticket offers "Reopen and send".
- Searching for a name with accents (Élodie) found nothing when typed in another case.
- A search for the word "all" was dropped from the sort and page links.
- Ticket times were shown in server time without saying so; they are now in UTC and labelled.
- A user message was recorded in the admin activity as "Support ticket answered".
- The Type column sorted by internal key instead of the shown name, and the Messages column could not be sorted.
- The admin table and a ticket from a long email address overflowed on phones.
- Writing on a closed ticket reopened it even when the user already had the maximum of open tickets.
- The ticket rate limit started over after an email change.
- Closing an already closed ticket still said it was closed now.
- The header buttons were squeezed next to a long name on phones, and the close button overflowed its card at 320 pixels.
- The header label counted replies instead of tickets with a reply.
- A subject or description made only of invisible letters was accepted.
- A right to left subject moved the ticket number to the other end of the row.

## [1.2.15] - 2026-09-28

### Changed

- `scripts/backup.py` archives the database and its secret key only. Every nightly archive used to hold every receipt, so `--keep 14` stored the receipts 14 times over on the server's own disk: at 5 GB of receipts that is 70 GB, and a full disk stops the app from saving anything. A receipt never changes once saved, so the off-site copy now takes the receipts folder as it is and stores each receipt once (setup_vps.md, section 11). `--with-receipts` still makes a complete archive for a one-off copy. **Before upgrading, add the receipts folder to the off-site copy**, or the receipts are backed up nowhere from that night on.

### Fixed

- A backup run killed part way (a reboot, the out-of-memory killer) left its hidden `.nomadlife-*.partial` archive behind for good, since no cleanup runs then; the off-site copy then uploaded it every night. A later run removes it once it is six hours old, and never touches a partial that may belong to a run still writing.

## [1.2.14] - 2026-09-27

### Added

- `scripts/backup.py --dest DIR --keep N`: one archive per run with the database, its secret key and every receipt, keeping the newest N. It reads the paths from config.env, is safe while the app runs and needs no `sqlite3` command. setup_vps.md uses it for the nightly backup and before upgrades.

## [1.2.13] - 2026-09-27

### Changed

- Settings: the "Turn off two-factor sign in" button is red, and the two-factor card has space between its intro text and the form.
- The footer's "Nomad Life" links to the home page.

### Fixed

- Email addresses in emails were split anywhere (for example "pdiomed" / "e@yahoo.com"); they now move to the next line whole and are only split when longer than a line.
- setup_vps.md: the gunicorn service turns off the control socket (`--no-control-socket`). gunicorn 25.1 and newer create it in the service user's home folder, which the service hardening makes unreachable, so the log showed "Control server error: Permission denied".

## [1.2.12] - 2026-09-27

### Added

- `setup_vps.md`: how to run Nomad Life on a server (Ubuntu 24.04, gunicorn, nginx, HTTPS with certbot, firewall, clock sync for two-factor codes, backups, first admin, upgrades), with what each feature needs installed.
- `flask --app app zip-receipts [--no-shrink]` converts receipts uploaded before this version to ZIP files (and resizes their big photos). Run it once after upgrading.
- gunicorn is pinned in requirements.txt as the production server.

### Changed

- Receipts are stored as ZIP files, one per receipt in each account's folder, compressed when that helps. Downloads are always a ZIP holding the receipt (no preview in the browser any more).
- Big photos (over 4 megapixels or 1 MB) are resized to about 4 megapixels and saved as JPEG without metadata (location included) when that is smaller; the original is not kept. The storage quota counts the space really used on disk.
- The accountant package holds the receipts taken out of their ZIP files, as stored.

### Fixed

- Several server processes starting at once could each create a different secret key, signing people out at random; an empty key file (after a full disk) is now replaced instead of stopping the app.
- Found in the review of the new storage, before release:
  - Two renames of the same receipt at the same time could corrupt its ZIP or leave the name inside different from the name shown.
  - 16 bit grey images came out almost white after resizing.
  - A tiny image file with huge dimensions made the server use over 400 MB of memory; images over 24 megapixels are no longer decoded and each server process resizes one photo at a time.
  - Long receipt screenshots were shrunk to 260 px wide and became unreadable; photos are now limited by area and never made narrower than 1000 px.
  - PNG images with a transparent colour were not put on white.
  - `zip-receipts` running while people used the app could undo a rename or stop halfway, leaving a stray file.
  - A damaged receipt stopped the accountant package download halfway; it is now listed as missing.
  - The backup script in setup_vps.md failed when receipts changed while it ran.
  - A receipt named like ".pdf" downloaded as ".zip", and names with characters such as ":" or "?" could not be extracted on Windows.

## [1.2.11] - 2026-09-27

### Added

- Settings page (`/settings`), linked from the menu at the top and the footer. It replaces the Account page (old `/account` links still work) and has jump links to each part: Profile, Password, Two-factor, Email, Devices, Activity, Delete account.
- Profile: optional first and last name (two new database columns, added automatically on start). The name shows in the menu at the top instead of the email, greets you in emails, is printed on the accountant package ("Prepared for: ...") and appears on the admin user list.
- Two-factor setup explained step by step for Google Authenticator and similar apps; the setup key is shown in groups of 4 characters for typing.
- Right to left names and places (Arabic, Hebrew) are drawn correctly in the accountant PDF (new dependency `uharfbuzz`, used only when such text is present).

### Fixed

- Security: the first name was placed at the top of the confirmation email sent to any address typed in the email change form, and of security alerts, so it could be used to send a misleading message ("Support here, call...", or "ignore this alert" after a takeover). These emails no longer include the name.
- After saving the name, and when the admin page sent an admin to turn on two-factor sign in, the page scrolled down past the message, so it was not seen.
- Changing the name (no password needed) recorded a security event each time, so ten saves pushed a stranger's sign in off "Recent security activity". Name changes are no longer listed there (the admin page still has them).
- Names that need zero width joiners (Persian, Sinhala, emoji families) lost them, and names made only of invisible "blank" letters were accepted and left the menu and page title empty.
- A right to left name in the menu at the top was cut at its beginning instead of its end.
- A long first name made HTML emails wider than a phone screen.
- Messages still spoke of "the account page" (the activity list, the reset-2fa command and its email).

## [1.2.10] - 2026-09-27

### Added

- "Sign out" now ends that browser's session on the server: a copied session or "Remember me" cookie stops working at once. Each sign in has its own session record; sessions unused for 31 days are forgotten, and the account page shows how many browsers were used in that time.
- New passwords are checked against Have I Been Pwned's list of breached passwords (only the first 5 characters of the password's SHA-1 hash are sent). A password found there is refused at sign up, on the account page and on reset. If the service does not answer within 3 seconds the password is accepted; `PWNED_CHECK=0` turns the check off.
- Receipts are checked by content, not only by name: a web page renamed to `.pdf` is refused. An image saved under another image extension (a WebP downloaded as `.jpg`) is kept under its real type.
- The email change alert sent to the old address now has an "Undo this change" link (valid 7 days): it puts the old address back, signs out every device and sends a link to choose a new password. Before, it pointed to a password reset that could not work for an address no longer on the account.
- Tests now also run on Python 3.10 (the oldest supported version) and 3.12 in the GitHub Action.

### Fixed

- Two-factor codes: typing the password again reset the count of wrong codes, so someone who knew the password could keep guessing codes. Wrong codes now have their own limit, which only a right code clears.
- Two-factor codes: wrong codes when turning two-factor sign in off were not limited or recorded.
- Two-factor codes typed with full width digits (some phone keyboards) caused a server error; they now work.
- The two-factor setup key stayed in the browser after signing out and was offered to the next account signed in there, so the first person could generate the second one's codes.
- A leftover "Remember me" cookie from a session ended elsewhere turned a later sign in without "Remember me" into a remembered one after a password change, "Sign out everywhere else" or turning on two-factor sign in.
- A browser unused for more than 31 days could stay signed in when its own request happened to run the cleanup.
- Disabling an account left its session records, so the device count was wrong after enabling it again.
- Finishing a sign up with the "choose your password" link sent a "your password was reset" security alert.
- `flask reset-2fa` on an account without two-factor sign in reported success and sent an alert; it now says two-factor sign in is not on. The alert it sends explains the lost phone case.
- The breached password message said "appears 1 times".
- The admin "Security activity" filter lost the history from before an email change; it now follows the account. Paging or saving an account's settings no longer clears the filter.
- A PDF whose header starts in the last bytes of its first kilobyte was refused.
- `fonttools` was pinned to a version that needs Python 3.11, so installing failed on Python 3.10, which the app supports.

## [1.2.9] - 2026-09-27

### Added

- Security alerts: changing the password, resetting it, changing the email address (the alert goes to the old address) and turning two-factor sign in on or off now email the account, with the time, the network address and a link to reset the password if it was not you.
- Two-factor sign in with an authenticator app (QR code or key, 6 digit codes, each usable once), on the account page for everyone. The admin page now requires it. Turning it on signs out other devices. Whoever runs the server can remove it from an account whose phone was lost: `flask --app app reset-2fa <email>`.
- Security history: sign ins, failed sign ins, password, email and two-factor changes, and every admin action (with the admin who made it) are recorded for 365 days. Admins see the last 100 events on the admin page, filterable by email; everyone sees their own recent activity on the account page.
- A GitHub Action runs the tests and checks the dependencies for known vulnerabilities (pip-audit) on every push and every week.

### Changed

- Dependencies are pinned to exact versions, so every install runs the tested code.

### Security

- An admin could disable or delete another admin's account, so one taken over admin account could lock out the others. Admin accounts can now only be removed from `ADMIN_EMAILS` in config.env.

## [1.2.8] - 2026-09-27

Script injection (XSS) review. Attack text was typed into every field a user controls (email addresses, cities, countries, notes, receipt names, admin fields, links and query parameters) and every page, email, download and accountant package that shows it was opened in a browser. No script ever ran: everything is shown as plain text.

### Added

- Content-Security-Policy on every page: the browser only runs the app's own script files, so even a future escaping mistake could not run injected code. The theme is now applied by a small script file instead of an inline script.

### Fixed

- Email addresses, cities and countries accepted invisible control characters, such as the one that shows text backwards. An address could look like a different one, for example in the admin list. Such addresses are now refused, and the characters are removed from place names.

## [1.2.7] - 2026-09-27

Security release. A review of the whole app found no way for one user to see or change another user's data (every private page, download and form answers "not found" for someone else's years, stays and receipts). It did find the issues below, all fixed.

### Added

- Account page: "Sign out everywhere else" signs out every other browser and device at once, including "Remember me" ones.

### Security

- Sign up had no limit: anyone could create accounts and send emails without end, and many sign ups at once could use up the server's memory (each password hash takes about 32 MB). Sign ups are now limited to 10 per network every 15 minutes, a password is only hashed for a new account, and at most 4 password checks run at the same time.
- Sign up and the email change form said when an address already had an account, which let anyone check who uses Nomad Life. Both now give the same answer either way; the owner of the address gets an email saying they already have an account.
- Signing in answered faster for an unknown email than for a real one, which also revealed who has an account. Both now take the same time.
- Anyone could keep an account (an admin's too) locked out of sign in by failing 5 times on purpose every 15 minutes. A browser that signed in to the account before now has its own limit, so the owner can still sign in there; new browsers stay limited as before.
- The dashboard map slowed down sharply with many stays (tens of seconds for a few thousand), long enough to slow the app for everyone. It is now fast, and a year can hold at most 1,000 stays.
- City and country names had no length limit, so one account could fill the server's disk without using its storage quota. They are now limited to 100 characters.
- Signed in pages (dashboard, stays, account, plan, admin) could be kept by the browser or a shared cache and shown again after signing out. They are now marked private and never stored.
- Pages could be shown inside another site's frame (clickjacking), and the browser could guess content types. Every response now forbids framing, turns off type guessing and keeps page addresses from being sent to other sites.
- "Remember me" lasted a year, so a copied cookie stayed useful for that long even after signing out. It now lasts 30 days.
- The database and receipts were readable by every account on the server's machine. `runWebApp.sh` and the app now create them readable by the account that runs the app only, and fix the permissions of existing files.

## [1.2.6] - 2026-09-27

### Added

- Map zoom: +, - and a "whole world" button next to the map title. Zoom goes up to 8x around the middle of the view, the map always fills its frame, and pins keep their size. While zoomed, drag the map (mouse or finger) or use the arrow keys to move around; the + - and 0 keys zoom too. Tabbing to a pin outside the view brings it into view.

### Fixed

- Security: a stranger who signed up first with someone's address could still take over the account, including an admin address, by signing in after the owner signed up: that sent the owner a new confirmation link for the stranger's password. When a sign up was retried, only the link to choose a password can activate the account now.
- An account disabled by an admin while it was waiting for confirmation still received confirmation emails, could still be confirmed, and was removed after 20 minutes, so signing up again undid the admin's decision.
- The upload size of the Free plan was only checked after the whole request had been read, so an oversized upload was still received in full.
- Map zoom: the tests found and fixed, before release, that the buttons could cover pins in the lower left corner, that a tap on a button after dragging with a finger was ignored, that the keyboard zoom keys lost the focus, that moving with the arrow keys could hide the focused pin, and that a tooltip followed the pointer during a drag.
- Map zoom: pressing Enter on "Zoom in" at the maximum jumped the focus to "Zoom out", so the next press zoomed back out.
- Plans page: the "Upgrade to..." button text ran past the button on phones and made the page scroll sideways.
- Movements table: place names and "counted" were split in the middle of a word on phones (for example "Amsterda m").
- Emails: a long email address made confirmation and reset emails scroll sideways on small phones.
- Landing page: the "Private by design" card said the app runs on your own computer, next to the hosted storage plans.
- Tests no longer depend on the size settings in your local config.env.

## [1.2.5] - 2026-09-27

### Fixed

- Map: pins of neighbouring countries covered each other, so their day counts could not be read, and the base could show a misleading "1" for 348 days when a trip next door sat on top of it. Overlapping pins are now moved slightly apart (and a bit smaller on phones), staying close to their country and inside the map.
- Map: tapping a pin on a phone did not open its tooltip. It opens on tap now, as on hover or keyboard focus.
- Map: the cities in a tooltip could repeat with different capitals ("Lisbon, lisbon").
- Map: "Not on the map" listed one place twice when it was typed two ways ("Transnístria, Transnistria"), although Days per country counts them as one.

## [1.2.4] - 2026-09-27

### Changed

- Map pins show the number of days in each country, the same number as in Days per country (a future year's base shows 0, since only passed days count toward it). The tooltip adds it under the cities, for example "15 days", and screen readers hear it too.
- Pin colors are slightly darker in the light theme so the numbers are easy to read (contrast of at least 5:1 in both themes).

## [1.2.3] - 2026-09-27

### Added

- Map on each year's dashboard, under Movements: a world map with a pin for every country of the year's base and stays. The base has its own color and a bigger pin. Hovering a pin, or reaching it with the keyboard, shows a tooltip with the flag, the country and its cities (two stays in Italy make one Italy pin listing both). Places the map cannot show, such as a country typed as free text, are listed under it. Everything is bundled; the map loads nothing from the internet.
- `scripts/build_map.py` rebuilds the map data (`static/img/world.svg` and `countries_geo.py`) from Natural Earth, which is public domain.

## [1.2.2] - 2026-09-26

### Added

- Plans: Free (500 MB of receipts, 10 MB per receipt), Pro ($4 a month or $40 a year, 5 GB, 25 MB per receipt, accountant package) and Nomad+ ($9 a month or $90 a year, 25 GB, 50 MB per receipt, priority support). Plan cards show the yearly price in bold and how much it saves ("Save $8 a year"). Each plan sets its own storage and receipt limits. Everyone starts on Free; online payment is not available yet. The accountant package is a Pro feature: on Free the dashboard card offers "Upgrade to Pro" instead of the download.
- The header shows only your email with an arrow. It opens a menu with your plan (for example FREE, which opens the new plan page), Admin for admins, and Sign out. The Account page is in the footer.
- "Upgrade" on the Receipt storage card opens the plan page, with your plan, its limits and usage, and the next plan with its price.
- The landing page has a Plans section with every plan, its price and features, and a "Pricing" button at the top Prices are in US dollars, and each paid plan says "Everything in <previous plan>, plus:" and highlights what it adds.
- The admin page shows each account's plan and can change it with a plan selector that shows each plan's monthly and yearly price. Admins can also change the monthly and yearly price of Pro and Nomad+ ("Plans and prices"); the landing page, the plan page, the saving badge and the search engine data follow at once. An account without a custom quota gets the quota of its plan.

### Changed

- The right column of the dashboard shows Days per country first, then Receipt storage, then the Accountant package.

### Removed

- `PROMPT.md`, the bug hunt prompt, is no longer part of the project.

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
