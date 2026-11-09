# Changelog

All notable changes to this project are documented in this file.

## [2.1.3] - 2026-11-09

Fifteen security fixes from an audit of 2.1.2, and three delegate fixes.

### Security

- **A session cookie needs the id of a live session.** Cookies without one (from before 1.2.10, long expired) were never looked up in `user_sessions`, so signing out could not end them, and anyone holding the signing key and a copy of the users table could forge one for any account, an admin included. They are refused now.
- **The backup no longer includes the secret key.** `scripts/backup.py` leaves `data/.secret_key` out unless `--with-secret-key` is given, so the key and the users table do not travel together to an off-site copy. Keep the key in a password manager (section 3 of `setup_vps.md`); restoring without it only signs everyone in again and ends links emailed earlier.
- **Undoing an email change alerts the address the account used until then.** Whoever reads the old mailbox can take the account back with the undo link (that is its purpose, and it removes two-factor sign in); the address the owner moved to now hears about it, with a way to write to support.
- **The Google sync review is bounded.** Matching typed movements with calendar events compared every pair and held the database write lock for about a minute with a thousand stays and ten thousand events, so logins failed with "database is locked". The keys are worked out once, the exact day passes use an index, and the passes on shared days stop after 300,000 comparisons (a real year needs a few thousand): the worst case takes under a tenth of a second.
- **Receipts are capped:** 1,000 a year and 5,000 an account. The quota counted bytes of ZIP only, so tens of thousands of tiny files could fill the disk and every page listing them rendered a row for each. Checked before the file is processed and again under the write lock.
- **Confirming a new email address needs the account's owner.** The link in the new mailbox now asks for the account's current password unless it is opened in a browser signed in to that account (then nothing changes). A mistyped address (alice@gmial.com) belongs to a stranger, who could otherwise confirm it and reset the password there. Wrong passwords count as failed sign ins.
- **hCaptcha answers are limited per network before hCaptcha is asked:** 30 in 15 minutes. A bot posting made up answers tied up a worker per request and could get the server rate limited by hCaptcha, which refuses every real visitor.
- **Signing up with someone's address no longer blocks their Forgot password.** The "You already have an account" email spent the account's one minute email cooldown, so anyone sending a sign up a minute made the owner's reset email silently not go out. It has its own limit now (2 per address in 15 minutes).
- **Securing the account ends a Google Calendar link made before.** Connecting Google needs only a session, so a stolen session could connect someone else's Google account, and the link, with AUTO writing its events into your stays, survived a password reset. A password reset or change, and "Sign out everywhere else", now disconnect Google Calendar and put AUTO years back to MANUAL (movements kept); connect again in Settings if the connection was yours.
- **A form too large from another site can no longer sign a visitor out.** The "too large" answer flashed a message, which sends a new session cookie that replaces the real one, and redirected to the sender's Referer. It now answers like a failed CSRF check from another site: no cookie, back to the home page.
- **Receipts that are mostly empty space are refused** (1 MB or more that shrinks more than 25 times: a 25 MB file of zeros is a 25 KB ZIP, and the accountant package unpacked, hashed and repacked each one, sending 1000 times what was stored). Package downloads are limited to 6 per account in 15 minutes.
- **Checkout and portal are limited** to 10 a quarter of an hour per account: each asks Stripe (whose quota everyone shares) and holds a worker.
- **The Stripe webhook never raises on a bad header.** A signature header with a superscript digit or a non ASCII character made an unauthenticated 500 with a traceback in the log; it is a 400 now.
- **Logs keep addresses and links out.** A refused recipient (which SMTP errors name in full) is logged by its domain, so are the addresses in ticket, audit and alert failures, and `/reset/`, `/verify/` and `/account/email/` links in a failing request's log line read `<token>`.
- **Per network limits count an IPv6 /64** (what one subscriber or server is handed) instead of the single address, so rotating the end of the address no longer gives a thousand "networks" for sign in, sign up, reset and captcha limits. An IPv4 address mapped into IPv6 counts as that IPv4 address.
- **The Google sync leaves out invitations you declined or never answered.** Anyone who knew your address could send a calendar invitation named "Thailand" that Google added to your calendar and AUTO turned into a stay, taking days from a real one. The sync reads your own answer (and nothing about other guests) and skips "declined" and "needs action"; your own events, accepted and tentative ones, and calendars without guests are synced as before.

### Fixed

Delegates:

- **Send again used up the invitation limit without sending anything.** A press inside the one minute wait was answered with the wait but still counted against the 3 invitations an address can get in 15 minutes (and the owner's 10), so after a few early presses, or after sending kept failing, the owner was blocked for 15 minutes with nothing delivered. The wait is now checked before anything is counted.
- **Inviting an address that is already a delegate (or past the limit of 5) used up the same limits.** Retyping the address three times locked it out for every owner for 15 minutes. These refusals are now answered before anything is counted.
- **Send again said nothing when the invitation was removed in another tab at the same moment.** It now says the invitation was withdrawn.

## [2.1.2] - 2026-11-09

### Added

- **Send emails through any SMTP mailbox, not only Gmail.** `SMTP_HOST` and `SMTP_PORT` in `config.env` choose the server (default `smtp.gmail.com`, `587`); `GMAIL_USER` and `GMAIL_APP_PASSWORD` stay the mailbox address and password. Port 465 uses TLS at once, any other port STARTTLS, and the certificate is always checked. Needed when the Gmail account cannot make an App Password (Google's Advanced Protection blocks them). Hostinger: `SMTP_HOST=smtp.hostinger.com`, `SMTP_PORT=587` (many hosts, Hetzner for one, block outgoing port 465).
- **A log file for email.** Every email attempt is logged to `data/nomadlife.log` (`LOG_FILE`, rotating, owner only): the server and mailbox used, the stage that failed (connect, login or send), the server's own answer and a plain hint at the likely cause (wrong password, a blocked login, a wrong port). The recipient's domain is logged, never the address or the password. The app's other warnings and errors go to the same file. At start the log says which mail settings were loaded.
- **`flask --app app test-email [address]`** checks the mail settings step by step (DNS, connect, login, send) and stops at the step that fails, saying why.

## [2.1.1] - 2026-11-08

### Added

- **A city of your own on AUTO movements.** When the Google Calendar event names only a country ("Italy"), the movement's page now lets you type its city. The city stays in Nomad Life only (the movements table, the Nomad Life calendar and the map), nothing is written to Google, and every sync keeps it. When the event names a city ("Rome, Italy"), Google's city wins and the field stays locked, like the dates. If the event later moves to another country, the typed city is cleared; if it names the same city, the field locks quietly.

- **Delete a movement's event in Google Calendar too.** In AUTO, a synced movement can be deleted again: its event in Google Calendar goes with it (otherwise the next sync would bring it back), and the second confirmation says so. In MANUAL, a movement that came from an event offers **Also delete its event in Google Calendar** in the second confirmation, unticked. Nomad Life asks Google for the permission to change your calendar (`calendar.events`) only the first time a delete needs it, for the Google account already connected; then press Delete again. Connecting Google Calendar still asks for reading only, and a movement you typed yourself has no event to delete.

### Changed

- Turning a year to AUTO no longer erases a city you typed when the matching event names only the country: "Rome, Italy" against an event "Italy" on the same days is now linked as it is, and a change of dates keeps Rome (the review shows the city kept).
- Every tooltip in the app is now a styled CSS tooltip, like the gear and calendar ones, instead of the browser's plain one: the "From Google Calendar" icon and the "Not in Google Calendar" tag in Movements, the "counted" days note, + Year, the map zoom buttons, the account menu, the plan chip, receipt links, the calendar colors and weekdays, the date picker, the country picker's base and favorite icons, and the admin's IP links. They show on hover, and on focus for the keyboard.
- Saving a synced movement writes only its notes (and its city when it is yours), never the place and dates read when the page opened, so a sync meanwhile always wins.

### Fixed

Deleting events from MANUAL years, before release:

- An event changed in Google Calendar after its year left AUTO (another place or other dates) could be deleted with a movement it no longer matched, taking another trip with it. The event is now read first and deleted only while it is still the movement's stay; otherwise nothing is deleted and the message says why. The same check covers AUTO between two syncs.
- After allowing the permission, the message said only to press Delete again: in MANUAL the box must be ticked again, and it now says so.
- Cancelling on Google's permission page showed "Google Calendar was not connected." on Settings: it now goes back to the movement (or the dashboard page) and says nothing was deleted.
- Google failing after its permission page (an error, or no token) also sent you to Settings with a connection message: same fix.
- The second confirmation in MANUAL did not warn that an event reaching into another year is deleted there too (AUTO did): it now does.
- An event already deleted in Google Calendar was reported as "deleted, and its event in Google Calendar": the message now says it was already gone.

## [2.1.0] - 2026-11-08

### Added

- **Delegates.** In Settings, under **Delegates**, invite up to 5 people, such as your accountant or your partner, to view your account: type their email and your password, and they get an invitation by email. They sign in with their own Nomad Life account under that address and accept it under **Shared with you**, a new item in the menu at the top (with a badge for invitations waiting). They then see your years, dashboard, map, Nomad Life calendar, movements, notes, receipts and base documents, and can download receipts and the accountant package when your plan has it. They cannot change anything, and never see your Google Calendar, your storage or your settings. A note at the top of every page says whose account they are viewing.
- Each delegate shows the day they accepted and the last day they viewed your account. **Send again** emails a waiting invitation once more; **Remove** ends their access at once, and a delegate can leave with **Stop viewing**. You get a security email when someone accepts.
- The documentation has a Delegates section, with a screenshot.

### Changed

- The dashboard's Calendar is open when the page opens, on the Nomad Life calendar. Click its title to fold it.
- An account that only views other accounts lands on **Shared with you** after signing in.
- Undoing an email change from the old address also removes every delegate, in case whoever changed it invited themselves.
- The privacy policy, the landing page and the documentation say who else can see your data: the delegates you invite.

### Fixed

Found in a bug hunt of Delegates before release (13 bugs):

- An invitation sent at the very moment the owner took the account back (undoing an email change) survived the undo, giving whoever knew the old password a way to view the account. The password and address are checked again when the invitation is saved.
- An account whose only link was a waiting invitation was sent to Shared with you at every sign in, and no page led to starting its own year. A waiting invitation no longer does that, and Shared with you (and the note on shared pages) offers **Start your own year** to an account without years.
- An account invited under two addresses (it changed its email in between) could accept both: removing one of them said access had ended while the other kept it. An account now holds one access per owner.
- An owner who changed their own email to an address they had invited kept an invitation nobody could accept, and Send again mailed the owner an invitation to view themselves. It is removed with the change.
- A waiting invitation showed the owner's name, which is free text, to a stranger before they accepted. It shows the owner's email, as the invitation email does; the name shows once accepted.
- Removing and inviting the same address again could send it an invitation every minute and a half. An address gets at most 3 invitations in 15 minutes, whoever sends them.
- Remove and Stop viewing asked "Delete ...? This permanently deletes ... It cannot be undone." with Delete buttons. The dialog now says Remove or Stop viewing, and what happens.
- An invitee without an account was sent by the email's button to the sign in page. The button creates the account first.
- A delegate's memory of having opened the overlap report mixed up the accounts they view and their own: triangles blinked again each time. Each account viewed remembers its own.
- The overlap report and the "Not in Google Calendar" tag asked a delegate to fix the dates or delete the movement, which they cannot do. Those lines are left out for them.
- The "Can view" tag had no color of its own, so it looked like "Invited".
- The security email's last line said every change to delegates is emailed; only an accepted invitation is.
- The documentation, the landing page and the privacy policy said only you see your notes, stays and receipts.

## [2.0.1] - 2026-11-07

### Changed

- The calendar legends no longer show the Base tag.
- The Base card's **Manage Base country** link is a gear at the card's top right, with a tooltip.
- Turning the 183 marker on adds only the marker: "N days short of 183" (or "At least 183 days") is now a tooltip on the Days in Base country bar, and screen readers still hear it.
- The four top cards keep their numbers on one line, whatever is under them (a bar or a sentence).
- Two of the 15 colors are a shade darker so white day numbers read well on them: red #CF4545 (was #D94848) and brown #A36741 (was #A76A43). Today's ring takes each day's number color, so it is clear on every color.

### Fixed

The three bugs of the 2.0.0 bug hunt left over by its cap:

- The Google icon of synced movements and the "Not in Google Calendar" tag had explanations nobody could reach, and the icon vanished in Windows high contrast. They have a tooltip and text for screen readers; a flagged movement's page explains it too.
- Right-to-left city names (Arabic, Hebrew) jumbled the dates next to them on the review page.
- Red and brown day numbers, and today's ring on several colors, were below the contrast for reading.

Found in a review of the recent UI (7; the last two were fixed later in this release, see below):

- The AUTO label's hidden tooltip made every phone page scroll sideways. Its text is now read by screen readers too.
- The Manage Base country tooltip was squeezed to one letter.
- Stacked top cards touched each other, and the space under them shrank to 4 pixels.
- The AUTO switch's tooltip ran off phone screens, and screen readers never heard why the switch is off.
- Google Calendar events named "City, Country" did not take, nor set, their country's color.

Found in a bug hunt of switching a year between MANUAL and AUTO (12 bugs; the 10 most serious first, then the 2 left over by the cap):

- Turning AUTO on again never matched movements still linked to events of another calendar, or to events made again under a new id: the review removed them and added them again, losing their notes, and a movement with receipts was silently flagged and doubled. They are now matched like typed movements, and one with receipts is listed unticked instead of flagged.
- "City, ST" titles became another country: "Chicago, IL" counted for Israel, "Boston, MA" for Morocco, "Los Angeles, CA" for Canada. A city with its US state, Canadian province or Australian state now counts for that country ("Austin, Texas", "New York, NY", "Toronto, ON", "Sydney, NSW"); a state code that is also a country's is left out rather than guessed; "Atlanta, Georgia" is in the United States, "Tbilisi, Georgia" in Georgia.
- Disconnecting Google, clearing the chosen calendar, choosing another one or Google taking the access back left AUTO years stored as AUTO: meanwhile they worked as MANUAL, and choosing the calendar again resumed AUTO without a review, doubling movements typed meanwhile or silently undoing edits. Every AUTO year now goes back to MANUAL at that moment, with a message, and only the review turns it on again.
- Opening the review page took the database's write lock and could fail behind another request. It reads without it.
- Events past the limit of movements a year were retried at every view: each one took the write lock, showed "left out" again and made the Google tab reload the page. They are a note under the year, and the review says which additions will not fit.
- The page did not reload after a sync when it was already on the Google tab's address (the second Refresh Calendar spun forever), nor when a refresh Google did not answer had put the year back to MANUAL.
- A deleted or unshared calendar, a permission taken back or a server without Google keys all said "Google Calendar is no longer connected, or another calendar was chosen", and the review said "Try again in a moment" forever. Each case now says what happened and what to do.
- A movement page opened in MANUAL and saved after the year turned AUTO said "Movement updated." while its new dates were dropped. It says only the notes were saved.
- "Not in Google Calendar" stayed on movements after the year went back to MANUAL, where nothing checks it any more. It shows in AUTO only.
- An older copy of Google's events, cached by another server process, took the write lock at every view for up to a minute. It is skipped before.
- A double click on the switch, or on the review's button without JavaScript, gave the same message twice, or "is in AUTO mode" and then "is already in AUTO mode". Each is said once.
- A year deleted in another tab while its review was applied said "changed meanwhile, check the changes again" before "no longer exists". It only says it no longer exists.

The two left over by the cap of the UI review above:

- The "days short of 183" tooltip of the Days in Base country bar only opened on mouse hover. With the marker on, the bar can be focused with Tab or a tap, which shows it, and screen readers read it as the bar's name.
- In browsers without CSS subgrid (before about 2023), the four top numbers were 14 pixels out of line. Each card's foot is at least one line high, so they line up there too.

## [2.0.0] - 2026-11-07

### Added

- **AUTO movements from Google Calendar.** Next to the year on the dashboard, a label says **MANUAL** (movements are added by hand) or **AUTO** (movements are added automatically from the connected Google Calendar), with a switch to change it. Every year starts in MANUAL.
  - In AUTO, the year's movements follow the chosen Google calendar each time the year is opened (also right after signing in), and Google Calendar wins any difference. Events count when named after a place: a country ("Italy", "IT") or "City, Country" ("Rome, Italy"); others are left out.
  - Turning AUTO on first opens a review of the differences (movements to add, change, remove, and already matching), each with a checkbox. An unticked change stays out of the sync. Removing a movement that has receipts starts unticked.
  - Notes and receipts stay yours. A synced movement whose event is gone is removed, or kept and marked **Not in Google Calendar** when it has receipts. Its place and dates are read-only in AUTO; **+ Add movement** is hidden.
  - If Google Calendar is disconnected, another calendar is chosen, or the calendar is deleted, the year goes back to MANUAL and keeps its movements. If Google does not answer, nothing changes.
- Documentation: Movements from Google Calendar, with two new screenshots.

### Changed

- The calendar uses 15 colors (red, orange, yellow, lime, green, teal, cyan, blue, indigo, purple, magenta, pink, raspberry, brown, slate), the same in both themes, 5 a row in the color menu; pink is the base's. Day numbers are dark or white, whichever reads better on each color. Saved choices of the two colors that no longer exist go back to automatic.
- The docs' demo calendar names its events after places, and the screenshots are made again.

### Fixed

Found in a bug hunt of the new sync (15 bugs):

- A calendar with more events than Nomad Life reads at once removed the movements of the events not read. A partial read now only adds and changes.
- Choosing another Google calendar or account replaced every synced movement without asking, losing their notes. The year remembers its calendar and goes back to MANUAL until it is reviewed again.
- Movements typed while Google Calendar was disconnected were doubled when AUTO resumed. A year that lost its connection now needs the review again.
- A deleted calendar, or a permission taken back, was asked again at every view and left the year read-only. The year goes back to MANUAL.
- While Google was failing, every dashboard view waited on it again after 10 seconds. It waits a minute.
- Every view of an AUTO year took the database's write lock and could fail behind another request. It only writes when something changes.
- A copy of Google's events cached by another server process could undo a newer sync and lose notes. An older read is never applied after a newer one.
- A receipt uploaded while the review was open could be deleted with its movement. The review shows again instead.
- The review matched stays by country only, so a travel day moved by one day could swap two cities. It matches the same city first, then the most shared days.
- An event Google listed twice made the dashboard fail. It counts once, and ids are never cut.
- The switch's hidden tooltip made the dashboard scroll sideways on phones.
- The date buttons of a synced movement still opened their calendar. They are read-only.
- A movement page opened in AUTO and saved after the year left AUTO failed with "Country is required." and emptied the fields. Place and dates stay as stored.
- The Movements table showed Delete for synced movements, which the server then refused. It is hidden.
- Coming back to the review with the Back button offered to turn AUTO on again, undoing the changes unticked before. It goes back to the dashboard.

## [1.12.19] - 2026-11-07

### Changed

- **Google Calendar drives the colors.** With Google Calendar connected, you choose colors in the Google Calendar tab's legend, and the Nomad Life tab follows: an event named after a country (Italy), or after a city of the year's stays (Rome, which says "Rome (Italy)"), sets that country's color in both tabs and every year. Any other event (a dentist appointment) can have its own color too, shown in the Google tab. Without Google, the colors are chosen in the Nomad Life legend as before. Colors already chosen keep working.
- Both calendar legends (Nomad Life and Google Calendar) list the base country first, then the other countries by the most days.
- The calendar tooltips read "Sat 23 Jan 2027 🇮🇹 Italy", without a colon after the date.

## [1.12.18] - 2026-11-07

### Fixed

Three small inconsistencies noted by the 1.12.17 reviews:

- The Admin page showed Joined and Last sign in in the server's time zone, beside a Security Activity in UTC, so a sign in near midnight could show two different days. Every date on the admin pages is in UTC and says so.
- Choosing or clearing the Google calendar Nomad Life uses filled Settings' short Recent security activity list, pushing sign ins off it. Only connecting and disconnecting Google are listed.
- The summary PDF wrote a receipt's size as "2.0 MB" where the app shows "2 MB". It writes sizes as the app does.

## [1.12.17] - 2026-11-07

### Changed

- The accountant package's read me is **ReadMe.md**, in Markdown (headings, a table of the files, the rules as a list), instead of README.txt. Names and places in it cannot change its formatting.
- The summary PDF and the ReadMe write dates like **15-Jan-2027** (period, generation date, "so far" date and the travel timeline); the CSV files keep ISO dates for spreadsheets. The timeline's Receipts header no longer breaks over two lines.
- Sorting or paging the Movements table keeps the page where it was, instead of scrolling the table to the top of the window.
- The Google Calendar tab's tooltips show the country's flag, as the Nomad Life tab does: for an event named after a country (Italy, IT) or after a city of the year's stays (Bangkok).

### Fixed

Found in a review of the accountant package and its summary PDF (7 bugs):

- A receipt deleted while the package was downloading was still listed as included. The ReadMe and the PDF are now written after the receipts and list it as missing.
- A right-to-left name could turn a whole PDF paragraph around. Paragraphs are always laid out left to right.
- A heading could end a PDF page with its table starting on the next one. It now moves with its first rows.
- Names in the ReadMe could strike text through (`~~`), start a list (`2.`, `+`) or show an HTML entity. They are escaped.
- The generation time was the server's local time, labelled "local time". It is UTC and says so.
- The Total share in Days per country could differ from the sum of the rows by rounding. It adds up the rows.
- A test file stopped its run before its last tests when started on its own.

Found in a review of the dashboard's cards (Days in Base country, Year elapsed, Days outside Base country, Days per Country) and the Map (8):

- In Windows high contrast, the day bars and the 183 day tick disappeared. They use the system colors.
- "U.S.A." or "St. Lucia" counted apart from "USA" and "St Lucia". Dots no longer make another country.
- "Today" was the server's date, so near midnight Year elapsed and the "so far" days were a day off for people far from the server. It is the date where you are.
- The line under Days outside Base country counted every stay, the base's included. It counts the stays outside it.
- Countries with the same number of days were listed in the order of their stays. They are listed A to Z.
- Map pins drifted off their countries when the card changed width without the window changing (a sidebar, the zoom). They follow the map's own size.
- In Windows high contrast, the map's land and its pins disappeared. They use the system colors.
- The pin tooltip took the font of the button it belongs to. It uses the page's text font.

Found in a review of receipts and storage (6):

- A big photo that did not get smaller as a JPEG was kept as it was, with the location the phone stored in it, though the docs promise that big photos lose it. Big photos with EXIF or XMP data are always saved again without it.
- Downloading a receipt stored before 1.2.12 whose file date was before 1980 (a restore) failed. It downloads.
- Renaming a receipt could take an account past its storage quota (the ZIP grows with the name). At the quota the name changes, but not the file inside.
- An account over its quota read "500 MB used of 500 MB". The used size rounds up once it is over.
- A file named ".pdf" or with an invisible name was saved under that name, which Rename refuses. It is saved as "receipt.pdf".
- A receipt named CON.pdf (or NUL, COM1...) could not be extracted on Windows. Its file gets a "_" in front.

Found in a review of the Settings page (9):

- The account's Recent security activity showed the IP address of an admin who changed its plan or quota. Actions by an admin, Stripe or the server show no address.
- Pressing Save twice (password, sessions, two-factor) could sign the browser out while the change went through. These forms send once.
- Every view of Settings waited for the database's write lock, so a busy moment made it fail. Only the first view writes the setup key.
- Changing the email to an address that has an account sent a link that could never work, and Confirm then gave a wrong reason. The owner of that address now hears that someone tried (the page answers the same, so nobody learns who has an account).
- A long Google address made Settings scroll sideways on phones. It wraps.
- Changing the password cancelled a pending email change without saying so. The message says so.
- Removing an authenticator app already removed in another tab said two-factor was off, while passkeys kept it on. It names what is left.
- A crafted passkey number made Rename and Remove fail with an error page. It is ignored.
- Every passkey's buttons read "Rename" and "Remove" to screen readers. They name the passkey.

Found in a review of Support (3) and of the admin's Support tickets (2):

- A ticket or message sent twice at the rate limit (a retry) answered "Too many" though it had been sent, and each copy used up the limit. A copy is answered as sent and counts once.
- Emoji newer than the server's Python (Unicode 15.1 and 16) were removed from tickets and names, and a subject made of them was refused. They are kept.
- A ticket refused by the rate limit answered 400 without saying when to retry. It answers 429 with Retry-After, as sign in does.
- An answer refused because the ticket was closed meanwhile failed with an error page when the account was deleted at that moment. It says the ticket no longer exists.
- **Close ticket** closed a ticket the user had just written on ("still broken"). It now refuses, so the admin reads the new message first.

Found in a review of the Admin page (5):

- Purging the Security Activity left no trace of who did it. The purge is recorded.
- Disabling an account that still pays gave no warning, and its owner could no longer reach Manage billing. The account page and the message say to cancel in Stripe first.
- A purge loaded the whole log into memory, and the page could reload before a slow purge answered, losing the only copy of the file. It reads in chunks into a temporary file, and the page waits longer than the server's timeout.
- On phones the "Paid with Stripe" note squeezed the plan menu. It takes a line of its own.
- The first click on the Account and Event columns sorted Z to A. It sorts A to Z.

Found in a review of signing in, out and up (6):

- Sign in errors were not announced to screen readers, and the email field took the focus over them. Messages are announced, and an error keeps the focus off the fields.
- The form token planted before sign in stayed valid after it. Signing in gives a new one.
- Opening a page signed out said "Please log in to access this page." It says "Please sign in to see this page."
- After Sign out, the device cookie still held the email address for a year (shared computers). It holds a keyed hash of it.
- Any website could sign people out (and end sessions without "Remember me") with a form posted to Nomad Life. Requests from other sites no longer change the session.
- A sign up that an admin disabled and enabled before it was confirmed could not be finished: signing in promised a link that was never sent. Its confirmation link works again.

## [1.12.16] - 2026-11-07

### Added

- **Choose a country's color.** Point at (or tab to) a country's square in the Nomad Life calendar's legend: a menu offers 17 colors (16 new ones and the base pink) and **Automatic**. The choice is saved with the account for every year, the days and the legend change at once, and screen readers hear "Thailand is now Coral". Without JavaScript the menu is a plain form.
- **The same colors in the Google Calendar tab.** An event named after a country (Thailand, TH) or after a city of that year's stays (Bangkok) takes the country's Nomad Life color; other events get colors no country of the year uses.

### Changed

- README.md no longer has a Plans section, as the landing page hides its plans for now.
- The calendar palette has 16 colors (it had 8), all readable in both themes, so colors repeat only after 16 countries; automatic colors skip the ones chosen for the year's countries.

### Fixed

Found in a review of the new color features and their sync with the Google tab (6 bugs):

- A click, a tap or Enter on a country's square closed the menu that pointing or focus had just opened, so phones needed two taps and the keyboard could not reach the colors. A click now opens it (and says so to screen readers), a second click, Escape or a click outside closes it.
- A free-text country typed two ways ("Atlantis", "atlantis") kept two saved colors, so **Automatic** could leave one in place. A country has one saved color, whatever its spelling.
- A city of two countries (Santiago in Spain and in Chile) gave a Google event a different color in the server's view and in the loaded tab. Such a city now matches neither country.
- Choosing the base's pink for another country gave both the same color. The base then takes an automatic color.
- Without JavaScript, an error landed on the calendar with its message out of view at the top. It now lands at the top.
- A crafted year number made the color answer fail after saving. It is ignored.

Found in a review of every database call (2):

- A transaction whose commit failed (disk full, an I/O error) stayed open on its connection. It is rolled back.
- The tests' helper connections were committed but never closed. They close.

## [1.12.15] - 2026-11-07

### Added

- **Sortable Movements table.** Click **Dates**, **Location**, **Days** or **Docs** to sort by it and again to reverse it; Location starts A to Z, the others newest or largest first. The newest dates come first by default, and the choice stays while you use the app, in the page links and when coming back from a movement.
- **Movements folds** like the Calendar and the Map and starts folded, with "9 movements in 2026" in its head. Links to the table (Back from a movement, its pages, sorting, page size) and deleting a movement open it.

### Changed

- The Movements table writes dates as **18-Jan-2027 to 30-Jan-2027**, and the place as its flag, the **country** in bold, a bar and the city (the city on its own line on phones). The overlap report uses the same dates.
- Pointing at a day of the Nomad Life calendar shows a tooltip in the app's style with the date and the country with its flag; the Google tab's days show the event. The calendar legends no longer show flags.
- The receipt type list of a movement is wide enough for **Hotel / Home rent** on one line.
- The Google Calendar tab no longer shows its line above the legend ("Events of your Google calendar ..., read from Google. ...").
- The documentation's Movements, calendar and receipts screenshots show the new table, tooltips and type list.

### Fixed

Found in a review of the Movements widget and its new sorting (7 bugs):

- The sort lived only in the session, so another tab (or Back to an older page) could change the order between two pages: rows shown twice, others never. The order now travels with the page links, the page size links, Back links and the row Delete button.
- Deleting a movement brought you back to a folded table. It stays open.
- A test read the wrong CSS rule after the overlap triangle moved into the folding head.
- The column headers dropped the calendar tab chosen with a link, which the pager kept.
- The place's hidden comma for screen readers was copied with the table ("Spain |, Madrid"). Screen readers get "Spain, Madrid" from the link's label instead.
- The documentation's screenshot still showed the old table.
- The overlap report wrote dates as 2027-01-18 next to a table that shows 18-Jan-2027.

## [1.12.14] - 2026-11-07

### Added

- **Refresh Calendar**: a round arrow icon right after the Google Calendar tab (tooltip "Refresh Calendar") reads the chosen calendar from Google again. The calendar stays on screen until the new one arrives, the icon spins meanwhile, and screen readers hear "Calendar refreshed". Clicks within 10 seconds of the last read reuse it, so Google's quota is spared.
- **Open in Google Calendar**: an arrow out of a box next to it (tooltip: Open the Google calendar "<name>") opens the year in Google Calendar in a new tab, in the connected Google account.
- Both icons show only while the **Google Calendar** tab is the one selected.
- The documentation shows the Google Calendar tab in a new screenshot (light and dark), and the calendar screenshot shows the two tabs.
- The landing page has a **Your whole year at a glance** section with the calendar screenshot, before How it works.

### Changed

- The Google tab's line says the same as the Days per Country card: "Events with the same title share a color. A day shared by two events, such as a travel day, counts toward the event that started later."
- The receipt type **Hotel / home rent** reads **Hotel / Home rent**.

### Fixed

Found in a review of the Google Calendar widget and its new icons (6 bugs):

- A refresh that Google did not answer (an outage or a rate limit) replaced the calendar on screen with "Google did not answer". It now keeps the calendar it has and says the refresh failed.
- At 320 px the tabs and the icons stuck out of the card. They are a little tighter on small phones.
- Refreshing with the card folded refreshed out of sight. It opens the card.
- A refresh pressed while the first load was still running could end with the older answer on screen, or let a second refresh start. Only the newest answer changes the view now.
- While Google failed, each refresh and Try again still called it twice, though it was asking the app to slow down. A failure is not retried for 10 seconds.
- Refresh Calendar was announced as a link, did nothing on Space, and its result was not announced. It is a button with a status screen readers hear.

## [1.12.13] - 2026-11-07

### Added

- **Nomad Life and Google Calendar tabs** in the middle of the dashboard Calendar card's head, shown only while a Google Calendar is connected and chosen. **Nomad Life** (tooltip "Nomad Life local calendar") is the calendar as before; **Google Calendar** (tooltip: This is the Google calendar "<name>") shows the chosen calendar's events of the year on the same 12 months, read from Google when the tab is opened and not stored. Events with the same title share a color, a day shared by two events counts for the one that started later, all-day events end the day before Google's end date, and days without an event are grey ("No event"). A click on a tab opens a folded card and swaps the view in place; without JavaScript the tabs are links. On phones the tabs get their own row under the title.
- Connecting asks Google for the calendars' events too, read only (`calendar.events.readonly`). A connection made before shows **Allow events** in the Google Calendar tab: Google asks for that one permission and brings you back to the tab, with the chosen calendar kept.

### Changed

- The landing page's **Plans** section, the hero's **Pricing** button and the footer's **Refunds** link are hidden for now (template comments, ready to show again); the plan and refund pages stay as they are.
- The privacy policy says the chosen calendar's events are read only to show them and are not stored, and that the use of Google data follows the Google API Services User Data Policy, including the Limited Use requirements.

### Fixed

Found in a review of the new calendar tabs (10 bugs):

- With a long Google calendar name (often an email address), the Google tab's tooltip, though invisible, made phone pages scroll sideways. It now takes no room until shown, wraps, and stays on screen.
- After a tab click the address kept `?cal=google`, so every reload waited on Google before the dashboard appeared, and every click read Google again. The address no longer keeps it, and the events are kept for a minute.
- The tabs came after the 12-month grid in the page, so keyboards and screen readers met the whole calendar before them. They now come first.
- Between 721 and 726px wide the title wrapped and the gear and tabs sat beside their places. The head now stacks from 820px down and the title never wraps.
- A tab link dropped the Movements page shown, and the pager dropped the tab chosen.
- The **Nomad Life** tab's link (without JavaScript, or opened in a new tab) showed the calendar folded.
- An event that began in December and one that began on January 1 counted as starting together, so the shorter one took the shared days. The real start dates decide now.
- Event titles that read as a country or its code (such as "PT" or "Jordan") got that country's flag.
- A click between the two tabs folded the card.
- Clicking **Google Calendar** and then **Nomad Life** before Google answered could still jump to the Google view if loading failed.

Found in use:

- A connection from before the events permission showed **Connect again** in the Google tab, as if it were broken: it now explains the one missing permission, with an **Allow events** button that brings you back to the tab.
- **Refresh list** showed next to a chosen calendar; it now shows only while choosing.

Found in a review of the whole Google Calendar integration and how events show (8 bugs):

- A busy calendar could always show "Google did not answer": a page of 2500 events in Google's indented JSON passed the 1 MB the app reads. Pages are now compact (`prettyPrint=false`) and of 1000 events.
- Event titles that differ only by a mark, such as Japanese ビザ (visa) and ピザ (pizza), counted as one, with one color and one name. Titles are grouped by case only now.
- A chosen calendar that was deleted, or no longer shared, showed "Google did not answer, try again" forever. The tab now says the calendar is no longer available and to choose another in Settings; a permission taken back asks to **Allow events** again; only real outages and rate limits say to try again.
- The minute's cache of events never dropped expired entries and could hold gigabytes per server process. It now drops them and holds at most 50,000 events in all.
- Reading the calendar list could loop without end if Google kept sending a next page. It reads at most 10 pages and stops when a page comes back twice.
- With more than 250 calendars the list was cut before it was sorted, so the main calendar (the Google address) or the chosen one could be left out, and a reconnect could then take the account for another one and revoke the new access. The list is read whole, sorted, then cut, keeping both, and the access is never revoked when the account is not known.
- Past 10,000 events in a year the rest was left out silently, shown as "No event". The tab now says so.
- With no calendar chosen (changed in another tab), or Calendar Integration turned off on the server, the Google tab said "Google Calendar is not connected". It names the real reason.

Found in use:

- Google picked the account already signed in to the browser without asking, so an account blocked by Google's Advanced Protection could not be swapped for another. Google now always shows its account chooser.

## [1.12.12] - 2026-11-07

### Added

- **Calendar Integration.** A gear next to the dashboard's **Calendar (2026)** title, with the tooltip "Calendar Integration", opens a new Settings section of that name, right after Profile. **Connect Google Calendar** signs in with Google (OAuth with PKCE); back in Settings, the calendars you can edit are listed, your main one first, and you choose one with **Save calendar**. Once saved, the chosen calendar shows as **Connected** with its own **Disconnect**, which brings the list back to choose another one (the Google account stays connected); the section's head says **Choose a calendar** until one is chosen. **Refresh list** reads them again and **Disconnect Google account** removes the connection and revokes Nomad Life's access at Google, as deleting the account does. Google is only asked for the list of calendars for now; what the chosen calendar is used for comes later. Off until `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set (google_calendar.md). The Settings activity list records connecting, choosing and disconnecting.

### Fixed

Found in a review of the Google integration before release (6 bugs):

- Disconnecting, or deleting the account, cut every other Nomad Life account connected to the same Google account (Google keeps one access per Google account and app): the access is now revoked only when no other connection uses it.
- A reconnect that failed (the permission unticked, or Google not sending the list) revoked the new access, which also ended the connection that still worked. A failed connection now changes nothing.
- Undoing an email change from the old address kept a Google Calendar the person who changed it may have connected, where the owner's stays would go. The undo now removes it, as it does with two-factor sign in.
- **Refresh list** wrote back the calendar chosen when it started, over one saved meanwhile in another tab; and choosing on a page made before a refresh elsewhere was refused although the calendar was still listed. Both now read the connection again when saving.
- Tests did not blank `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, so a real client in `config.env` changed what they saw.
- The calendar names took the muted color of form labels, and "Choose one calendar" touched the line above it.

## [1.12.11] - 2026-11-06

### Added

- **Calendar** on the year dashboard, before the Map, with a calendar icon and its year in the title, such as **Calendar (2027)**. It starts folded and opens with a click on its head. It shows the selected year's 12 months (four a row on wide screens, then three, two and one), each day in the color of the country it counts for, with the same rules as Days per Country: a shared day goes to the stay that started later, past days without a stay are the base, and days still to come without a stay are grey ("Not planned yet"). A legend lists each country with its flag and days; pointing at a day shows its date, country and city, and today has an outline. The colors are set for the light and dark themes, with every day number readable. The documentation has a section on it.
- **Show 183 marker** switch, iOS style, in the top right of the Days in Base country and Days per Country cards, one setting for both: off hides the 183 day tick and its message ("N days short of 183", "At least 183 days") and the tick on every country bar. This browser remembers the choice for every year, applied before the page is drawn so it never flashes.
- **Favorite countries.** In the country picker, a star next to each country (yellow once chosen) keeps it in a **Favorites** tab next to **All countries**. Favorites are saved with the account, so they follow it on every device (up to 50); Shift+Enter stars the highlighted country and Alt+arrows switch tabs.
- The year's **base country comes first** in the country picker, with a blue home icon and no star.
- The **receipt type** lists (new movement, upload and rename forms) are a styled list in the country picker's look, with an icon per type: Rental contract, Hotel / home rent, Flight ticket, Other. The form posts the same values.

### Changed

- **Only logged days count.** A day without a movement no longer counts toward the base once it has passed: it counts for no country, past or future, and shows grey ("Not logged") in the calendar. Days at the base count through a movement there. The documentation's rules and worked example, the New year page, the guide and the accountant package say so.
- **The city of a movement is optional.** Only the country is required; a movement without a city shows as its country (such as "Italy") in the table, its page, messages, the calendar, the map and the accountant package (receipt folders like "01_2026-03-01_Italy"). The base keeps its city. The calendar names only the country of each day.
- The year tabs and **+ Add movement** have the same height, and every year tab (and **+ Year**) the same width.
- **Date calendar.** From and To of a movement open a month calendar in the app's look instead of the browser's: Monday first, today ringed, the chosen day filled, days outside the year greyed, a Today button, and keys (arrows, Page Up / Page Down, Home / End, Enter, Escape). An empty To opens on the month of From; an empty date says "Please choose a date." The form posts the same dates.
- The "Show 183 marker" switches start off; this browser remembers once they are turned on. The Base card's link reads **Manage Base country**.
- The year tabs run from the earliest year forward (2026 before 2027).
- On Days in Base country the 183 marker switch sits on the title's line with only a tooltip ("Show 183 marker", also its name for screen readers), so the four top cards' titles line up.
- **Set up your year:** the Year field is as wide as Base country, **Create year** is as wide as its label and on the left, and **Back** and **Cancel** return to the year the "+ Year" tab was pressed on (or the dashboard; a first year has none).
- Outlined buttons (such as **Choose file**, **Cancel** and **Download ZIP**) have a border that is easy to see in both themes (3:1 against the card; it was 1.2:1).
- The Accountant Package button says **Download** with a download icon, and the tooltip "Download Accountant Package" (also its name for screen readers).
- The Accountant Package card reads "Everything for 2026 in one ZIP: a PDF summary, spreadsheets with your timeline and country totals, and all your receipts, ready to share with your accountant."
- The cards **Days in base country** and **Days abroad** are now **Days in Base country** and **Days outside Base country**. The accountant package keeps its wording.
- The titles of the four top cards (Base, Days in Base country, Days outside Base country, Year elapsed) have the style of the card titles below them, such as Days per Country; on Days in Base country the switch sits above the title.
- The lines in the Calendar and Map heads read **Your 2027 day-by-day plan** and **Your 2027 locations on the map**, with the year of the open tab.
- The Days per Country help text is shorter: "A day shared by two stays, such as a travel day, counts toward the stay that started later."
- **Content at the bottom of the cards.** In the four top cards and the three cards under the Map, the title stays at the top and the numbers and text sit at the bottom of the card, so cards of one row line up.

### Fixed

Found in a review of the whole app and its interface (10 bugs):

- **Low.** The accountant package's Days per country table gave the elapsed days as the "So far" total, which no longer added up to the rows now that days without a stay count nowhere. It adds up the rows, and the So far block says how many past days have no stay.
- **Low.** Texts still described the old counting or a required city: the documentation (a movement as "a stay away from your base", the troubleshooting tip, the calendar's hover text, two screenshot descriptions) and the package README's folder names. They say what the app does.
- **Low.** Starring a country, or switching tabs, in a country list opened on a chosen country shrank the list to that country alone (the Favorites tab then said nothing matched). The list keeps the view it was opened with.
- **Low.** In the date calendar, paging to January or December, or a click on its title or padding, sent the keyboard focus to the page, after which Escape and the arrow keys did nothing. The focus stays in the calendar.
- **Low.** Escape did not close the date calendar while the focus was on its date button. It does.
- **Low.** "+ Year" offered a year the account already had (the current one), which could only fail. It offers the first year not taken yet.
- **Low.** The year setup screenshot in the documentation showed the country list open over the buttons (the screenshot browser had no focus, so the list never closed). It is taken with focus now.
- **Low.** While the package was being prepared, screen readers still heard "Download Accountant Package" instead of "Preparing package...". They hear the busy state.
- **Low.** In the dark theme the border of outlined buttons fell to 2.7:1 on the highlighted row of today's stay. It is above 3:1 everywhere.
- **Low.** In Safari and Firefox, which do not focus a clicked button, a receipt type list could stay open after a click outside it. A click outside closes it.

Found in a review of the new pickers (3 bugs):

- **Low.** The favorite star was an 18 px target inside a clickable row, so a near miss chose the country instead of starring it. It is now a 30 px target.
- **Low.** The keyboard ways to the favorites (Shift+Enter, Alt+arrows) were not said anywhere a keyboard or screen reader user would find them. The country field now describes them, as does the documentation.
- **Low.** Starring and unstarring a country quickly could leave the star showing the opposite of what was saved, when the server's answers arrived out of order. Only the newest answer counts now.

Found in a review of the country picker (2 bugs):

- **Low.** The country search matched letters in the middle of words, so "ita" also offered Mauritania and United Kingdom (through "Great Britain"), and "it" a list of unrelated countries. It now matches the start of a name, of one of its words, of an alias or the country code.
- **Low.** A country found through another name showed no reason: "eng" offered "United Kingdom". The name it matched now shows next to it, as in "United Kingdom (England)" or "Netherlands (Holland)".

Found in a review of the interface, including the new switch and calendar (2 bugs):

- **Low.** After switching the 183 marker on another page, the Back button showed the old setting (the browser restores the page as it was) until a reload, with both switches saying the wrong state. The saved setting is applied again when a page comes back.
- **Low.** The same with the light and dark theme: Back could show a page in the theme you had just left. It now shows the saved theme.

Found in a review of the new calendar (2 bugs):

- **Low.** Months of five weeks had taller days than months of six in the same row, since each month was stretched to the tallest one. Every day now has the same height.
- **Low.** Where a day counts was only in its hover text and its color, which screen readers do not reliably read and keyboard and touch users never see. Each day now also says its country and city to screen readers.

## [1.12.10] - 2026-11-06

### Changed

- **Dashboard layout.** Movements and the Map now span the full width under the four summary cards, and Days per Country, Receipt Storage and Accountant Package sit side by side in one row below them. On narrow screens the three cards stack, in the same order as before.
- **Equal card heights.** On a year with only the base, the three cards are the same height. Receipt Storage and Accountant Package always match each other; only Days per Country grows as more countries are added.
- The three card titles are in title case: **Days per Country**, **Receipt Storage**, **Accountant Package**.
- **The Map folds.** It starts folded on every visit and opens with a click on its head (a chevron shows which way); the zoom buttons sit just above the map.
- **Card icons.** A globe before **Map** and a plane taking off before **Movements**, line icons drawn in CSS that follow the light and dark theme.

### Security

- **Dependencies updated** for known vulnerabilities reported by the weekly audit: Pillow 13.0.0 (40 advisories in 12.3.0; Pillow reads every uploaded photo), Werkzeug 3.1.9 and pyasn1 0.6.5.
- **Python 3.11 or newer is now required**, since Pillow 13 dropped Python 3.10 (which reached its end of life in October 2026). `runWebApp.sh`, the README, setup_vps.md and the tests in GitHub Actions (now 3.11 and 3.12) follow; Ubuntu 24.04 servers run 3.12 and need nothing.

### Fixed

From a review of how data is stored and of the interface (7 bugs):

- **Medium.** An account could be deleted while Stripe still charged it: the check read only the app's own records, so with a late webhook (or one arriving during the delete) Stripe kept renewing a plan every year for an account that no longer existed, with nothing left to cancel it from. Deleting (in Settings and on the admin page) now asks Stripe first, refuses when it cannot answer, and the delete itself repeats the rule.
- **Low.** Receipt names lost their zero width joiners on upload and rename, misspelling Persian names and splitting emoji families. They are kept, as in names and places.
- **Low.** Renaming a receipt to 146 to 150 characters without its extension saved up to 155 characters, and saving the form again with only the type changed then cut the name. The limit now counts the extension.
- **Low.** After a failed sign in, **Remember me** was unticked, so fixing the password gave a session that ended with the browser.
- **Low.** The browser refused a receipt larger than the space left, although receipts are stored compressed and many PDFs would fit. Only the server decides now, as it already did for photos.
- **Low.** In the dark theme, **Purge last 30 days** and **Purge all** in the Security Activity menu were too faint to read (2.5:1). They use the readable red of error messages.
- **Low.** Cancelling a Purge left the keyboard focus nowhere, since the menu holding the button had closed. It returns to the menu.

## [1.12.9] - 2026-11-06

### Changed

- The help text of the **Days per country** card on the dashboard is shorter: it now only says that a day shared by two stays counts toward the stay that started later, and what the marker shows. The full rules stay on the documentation page.

## [1.12.8] - 2026-10-02

### Added

- **Security Policy** at `/security`: how to report a vulnerability, the rules while looking (own accounts only, no denial of service, no social engineering), the promise not to take legal action against good faith research, and the scope. It is one of the legal pages (indexable, in the sitemap), and `/.well-known/security.txt` names it as its `Policy`.

## [1.12.7] - 2026-10-02

### Fixed

From a second review of the payment flow (5 bugs):

- **Medium.** A payment whose webhook was late could be made twice, since checkout only looked at the app's own records. Checkout now first asks Stripe whether the customer already has a plan; if Stripe cannot answer, no payment page opens.
- **Medium.** A plan cancelled in Manage billing kept blocking "Delete my account" until the end of its paid year. Only a plan that still renews (or whose payment is being retried) blocks deleting now.
- **Low.** Settings now has a **Plan** card showing the plan, its storage and where it stands (renews, cancelled and ends on, last payment failed), with **Manage my plan**.
- **Low.** Plan and payment events from Stripe recorded Stripe's server address as the account's IP in its security activity. They now record none.
- **Low.** With a plan unpaid or paused, the plan page offered to switch plans. It now says to pay the open invoice or resume the plan.

## [1.12.6] - 2026-10-02

### Fixed

- **High.** **Upgrade** and **Manage billing** did nothing: the pages' `form-action 'self'` policy also blocks the redirect that answers a form, so the jump to Stripe was blocked silently. The plan page alone now also allows `checkout.stripe.com` and `billing.stripe.com`.

## [1.12.5] - 2026-10-02

### Fixed

From a review of the whole Stripe payment flow (6 bugs):

- **Medium.** A plan cancelled in the customer portal still showed "Renews on". Both `cancel_at_period_end` and Stripe's flexible billing `cancel_at` are read now, and the page shows "Cancelled: ends on".
- **Medium.** A Stripe customer that Stripe no longer knows (test mode, or deleted in the dashboard) broke checkout for good. The app now forgets it and makes a new one.
- **Medium.** Test purchases stayed in the database after switching to live keys. New command `flask --app app forget-test-billing` (live keys only, `--dry-run` to look first) forgets what the live account does not know; documented in `stripe.md`.
- **Low.** An email change (or its undo) now updates the address of the Stripe customer.
- **Low.** Back from checkout, the plan page no longer waits 30 seconds for a change the webhook already made.
- **Low.** Moving down from Nomad+ to Pro no longer sends a "Welcome" email; it says the plan is now Pro and what happens to storage over the limit.

### Changed

- The two **Upgrade** buttons on the dashboard are teal, the color of the map pins.

## [1.12.4] - 2026-10-02

### Fixed

From a review of the Stripe integration against Stripe's guidance:

- **Medium.** The webhook applied subscription events in arrival order, so swapped events could leave a paying account on Free. It now reads the subscription as it is from Stripe first; if Stripe is unreachable it answers with an error so Stripe retries.
- **Low.** Every request now names the API version (`2026-08-26.dahlia`).
- **Low.** An unpaid or paused subscription now counts as existing: no second checkout and no account deletion while Stripe may still charge it.
- **Low.** The admin "Stripe" link opens the test dashboard for any test key (`rk_test_...` too).
- **Low.** Agreeing to the Terms and Refund Policy is a checkbox recorded by Stripe with the payment, with a text fallback until Stripe holds the Terms URL.
- "Contact Us" is the last link of the footer.

### Changed

- Stripe test account set up (products, yearly prices of $9.99 and $19.99, tax code, customer portal rules); ids in `stripe.md`, which now recommends a restricted API key with read access to Subscriptions.

## [1.12.3] - 2026-10-02

### Added

- **Online payment with Stripe.** **Upgrade** opens Stripe's hosted checkout (cards, Apple Pay, Google Pay, Stripe Tax, VAT numbers for businesses); Stripe's signed webhook sets the plan within seconds. **Manage billing** opens the customer portal (cancel, switch plans, card, invoices).
- Renewals, failed payments and cancellations move the plan automatically, with an email and a Security Activity row. An account that still pays cannot be deleted until its plan is cancelled.
- Off until the four `STRIPE_*` settings exist ("Online payment is coming soon"); see [stripe.md](stripe.md).
- **Terms, Privacy and Refund Policy** pages (`/terms`, `/privacy`, `/refunds`), in the sitemap and every footer. Plans are sold by Nemax Tech LLC, Sofia, Bulgaria.

### Changed

- **Yearly plans only:** Pro is $9.99 a year and Nomad+ $19.99, shown with "about $0.83 a month"; the admin page edits one yearly price per plan.
- The footer credits Nemax Tech and links Terms, Privacy and Refunds.

## [1.12.2] - 2026-10-02

### Changed

- **Fonts served by the app.** Inter and Outfit come from `static/fonts/web`, no longer from Google Fonts: faster first paint, no visitor addresses sent to Google, and the security policy allows fonts from the site only. Each character set is its own file (about 60 KB for an English page), and the text font is preloaded.

## [1.12.1] - 2026-10-02

### Added

- **Export and purge the Security Activity.** A gear menu on the admin page offers **Export last 30 days** and **Export all** (CSV, UTF-8, formulas neutralized), and **Purge last 30 days** and **Purge all** (two step dialog, PURGE typed, then a CSV of exactly what was deleted). Purging also removes the "Account confirmed" events that the Likely fake chip reads.
- Admin titles: **Plans & Prices**, **Registered Users**, **Security Activity**.

### Fixed

- **Medium.** A purge's CSV could list events that were not deleted (an event at the 30 day line, or events another admin purged at the same time). The cutoff is now fixed and the deleted rows are read under the write lock.
- **Low.** The page now reloads at the top after a purge; the menu fits on phones; export links no longer save the sign in page as a CSV when the session ended.

## [1.12.0] - 2026-10-02

### Added

- **Guides** at `/guides`, public and indexable: the 183 day rule, counting your days per country, and the documents that prove where you lived. Each has its own title, link preview, Article structured data, a "not tax advice" note and a "Start free" box. They are in the sitemap, on the landing page, in the footer and in the docs.

## [1.11.1] - 2026-10-02

### Added

- **Passkeys for two-factor sign in** (Face ID, Touch ID, Windows Hello, security keys, password managers), up to 10 per account, next to the authenticator app. Signing in offers a passkey, the code, or both; adding or removing one needs the password and sends a security alert. Undoing an email change and `flask reset-2fa` remove passkeys too. Checked with py_webauthn; each prompt works once and a passkey whose counter goes back is refused.

### Fixed

- **Medium.** The authenticator setup key travelled in the signed but readable session cookie. It now stays on the server for that browser only.
- **Low.** Code fields now accept full width digits; adding an app to an account with passkeys no longer says two-factor "was turned on"; only the newest passkey prompt of an account is kept.

## [1.11.0] - 2026-10-02

### Added

From a security scan of nomadlife.pro and a search engine review:

- **Permissions-Policy header** on every response, refusing browser features no page uses.
- **`/.well-known/security.txt`** (RFC 9116) with an expiry always 180 days ahead.
- **`Retry-After` header** on every 429 answer.
- Documentation link previews with their own title and description, Organization structured data, a "Documentation" breadcrumb for `/docs`, and full length snippets for the landing page and `/docs`.

## [1.10.1] - 2026-10-02

### Fixed

From a second review of the hCaptcha protection and the IP location links:

- **Medium.** With an unknown visitor address (gunicorn on a Unix socket) the app sent hCaptcha `remoteip=unknown`, which hCaptcha refuses, so nobody could sign up. The address is sent only when it is one.
- **Low.** The test site key with a real secret is logged as the configuration error it is; a captcha script that cannot load says so; copying an IP from the admin tables no longer copies hidden screen reader text.

## [1.10.0] - 2026-10-02

### Added

- IP addresses in the Security activity tables open their ipinfo.io page in a new tab. Local and private addresses stay plain text.

## [1.9.0] - 2026-10-02

### Added

- The IP column of the admin accounts table opens the address's ipinfo.io page, as the account page does.

## [1.8.1] - 2026-10-02

### Fixed

From a review of the hCaptcha and honeypot protection (1.6.0, 1.7.0):

- **Medium.** An hCaptcha HTTP error such as 429 let the form through, so a bot could make every form pass. Only an unreachable hCaptcha or a 5xx lets forms through now.
- **Medium.** The honeypot was named "website", which password managers fill, so real people got "We sent an email" and nothing else. It has an unfillable name and "ignore" attributes now.
- **Low.** A double click on **Create account** or **Send reset link** reused a one time hCaptcha answer (both forms now send once); refused checks are logged as warnings, wrong keys as errors; Forgot password keeps the typed address; the captcha follows a theme switch; two tests turn the captcha off.

## [1.8.0] - 2026-10-02

### Added

- On the admin account page, the Sign up IP and Last sign in IP open ipinfo.io in a new tab; the search for other accounts from the same address is the small **Same IP** link.

## [1.7.0] - 2026-10-02

### Added

- **Gmail dot rule on Sign up.** A gmail.com or googlemail.com address with more than 2 dots before the `@` (the Gmail dot trick bots use) gets the usual "We sent an email" answer but no account and no email.

## [1.6.0] - 2026-10-02

### Added

- **hCaptcha on Sign up and Forgot password**, on when `HCAPTCHA_SITEKEY` and `HCAPTCHA_SECRET` are set: a form without a passed check is refused before any account, rate limit attempt or email. Only these two pages allow hCaptcha's hosts in their CSP. If hCaptcha cannot be reached, the form goes through (logged).
- **Honeypot field** on the same two forms: a form that fills it gets the usual answer, but no account and no email.

## [1.5.8] - 2026-10-01

### Fixed

From a review for edge cases: pages left open, racing requests, links opened late or twice, account recovery, timing, limits and narrow screens.

- **High.** Changing the address twice made the owner's **Undo this change** link useless. Email changes are now numbered and the link carries its number: it works for its 7 days whatever address the account moved on to and undoes the later changes too, so whoever made them cannot take the account back. Older links work as before.
- **Medium.** Deleting the account in Settings went ahead although the owner had reset the password meanwhile; it now deletes only while the password is the one it checked. A password reset now saves only while the email is still the same, and says why when it loses a race.
- **Medium.** Signing in with a huge "email" stored all of it (up to 500 KB) in the sign in limits table, so many addresses could fill the disk. It is cut to 255 characters.
- **Low.** Forgot password still answered a registered address about half a millisecond later than an unknown one; the lookup and email now happen after the answer for every address.
- **Low.** Starting an email change after a reset, signing up again for an address confirmed at that moment, enabling a never confirmed account, pressing Undo after the address was changed back, and Cancel on a stale Settings page now give accurate messages instead of wrong or silent ones.
- **Low.** Receipts could be renamed to invisible characters only; refused now. The undo password link no longer greets by the (attacker settable) first name. Several confirmation, undo and ticket pages scrolled sideways on phones with long addresses or names.
- **Low.** Delete dialogs and the package say "its receipt" or "2 receipts" instead of "receipt(s)".

## [1.5.7] - 2026-10-01

### Changed

- The overlap report is easier to read (short lines per overlap, the rule in three points, the page's font), and the message after saving an overlapping stay is shorter.
- Day counts are 2 to 3 times faster (1,000 movements in 9 ms instead of 25 ms): each stored date is read once and who gets each day is worked out once.
- `setup_vps.md` has a new section **12. Restoring a backup** (Upgrading is now 14).

### Fixed

- Remembering dates would have kept the text of any date field sent, up to 4,096 of them; only text of a date's length is remembered.

Security:

- Undoing an email change now makes the old password useless: only the emailed link sets a new one.
- The email change link now opens a **Confirm my new email** button page, so a mail scanner checking a mistyped mailbox cannot confirm it.
- A password change or two-factor turn on that ran while the owner reset the password by email no longer overwrites the reset.

Third review:

- Sign up no longer shows by its response time whether an address has an account (every email is sent after answering, every address hashed once).
- Old confirmation links no longer name the account's current address.
- Undoing an email change now also turns two-factor sign in off, since an intruder may have set up their own phone; the alert says to write to support.
- Pressing **Undo the change** twice, an admin page listing a deleted account's history, a wrong Settings password leaving no activity row, and sideways scrolling of the admin and docs pages are fixed.

Things deleted or changed while a page was open: admin answers on a deleted ticket, signing in to an account deleted during the check, confirming a removed account, saving the base of a deleted year and double clicking year creation now answer correctly. Opening a used confirmation link says it is already confirmed. Long emails fit phone screens, a receipt name ending in a dot no longer saves "Hotel..pdf", and the docs diagram's "today" line is readable in dark mode.

## [1.5.6] - 2026-10-01

### Added

- With the **Suspected fake** filter on, **Delete these N accounts** deletes every account the list shows (all pages, within the search). It asks twice and needs the number typed; if the list changed meanwhile nothing is deleted. `./checkFakeUsers.sh` stays.
- Every deletion is in the **Security activity**: one row per account (with the reason and the admin) and one bulk summary row. The activity search also finds events by name and details.

### Changed

Faster, measured on 2,000 accounts, 61,000 receipts and 400,000 events:

- The database runs in write-ahead log mode: the slowest page read went from 560 ms to under 1 ms, and writes from 371 ms to 0.4 ms. `nomad.db-wal` and `nomad.db-shm` appear next to it; delete them before a restore and never copy `nomad.db` by hand while the app runs.
- The admin page opens in 28 ms instead of 387 ms (an index on confirmations), and the Security activity sorts in 53 to 66 ms instead of 347 to 713 ms.
- The accountant package no longer compresses photos again (0.4% saved at high CPU).

### Fixed

- `scripts/backup.py` failed on a stopped app (no `-shm`); it now reads either way.
- The documented restore would have kept the old `-wal` file; the steps delete it first.
- An account passing the 24 hour line between the page and the bulk delete could be deleted without an activity row or instead of one that signed in; the deletion now works on the list as the page was made.
- Deleting many accounts held the database for seconds (index on ticket message authors), the recorded reason ignored `--min-age-hours`, a number of thousands of digits caused a server error, and searching "_" listed every event.

## [1.5.5] - 2026-10-01

### Security

- Emails went to Gmail over a connection whose certificate was never checked. The certificate and host name are checked now.
- **Forgot password** answered a registered address seconds later than an unknown one; the email is now sent after the answer.
- An address blocked by an admin could be taken by changing another account's email to it; it stays blocked.

### Fixed

- Dashboard and movements: a repeated movement dropped its receipt and notes without a word (it now says so); pages left open after a year, stay or receipt was deleted go back to the dashboard with a message instead of a bare "Page not found"; the overlap triangle is readable in dark mode; renaming a `.jpeg` to `.jpg` no longer saves `.jpg.jpeg`; a very long place no longer widens the dashboard on phones.
- Accountant package: a receipt with a damaged ZIP header no longer fails the whole download.
- Countries: "Guinea Bissau", "Timor Leste" and "Bosnia & Herzegovina" now match (hyphens count as spaces, "&" as "and"); common short names (Brunei, Macau, The Gambia, St Lucia...) are names of their country; Enter on an empty country field sends the form instead of filling in Afghanistan.
- Sign in and account: a password reset lifts the device lock; confirmation and reset emails give the real time left; a new password ends a pending email change; the undo link no longer signs out another account's browser; "within 1 minutes", the lost phone text and screen reader hints on password fields are fixed; the text alert says why it was sent.
- Admin: searching tickets for "1" kept the search across pages.
- `checkFakeUsers.sh` uses the first Python that can load the app. An email Gmail accepted is no longer reported as unsent when only closing the connection failed.

## [1.5.4] - 2026-10-01

### Fixed

- Pages left open after a movement or year was deleted now go back to the dashboard with a message instead of "Page not found". On the admin account page, actions on an account another admin just deleted say so; two admins deleting the same account no longer both read "deleted".
- The red overlap triangle blinks until you open its report, then stays red (remembered per year in your browser), and only the icon blinks, so its tooltip is readable. On phones each plan price label stays with its field.

### Changed

- The Accountant package card on Free has an **Upgrade** button in its head, like Receipt storage.

## [1.5.3] - 2026-10-01

### Changed

- The admin **Security activity** search works like the Users search (half as wide, **Search** and **Clear**): an account's email shows its history, anything else finds events by part of an email or IP.

### Fixed

Admin page:

- An account's history no longer shows events of another account that had used its address before; a whole IP finds only itself; saving plan prices keeps the search, sort and page; typing the default prices no longer saves a "custom" price; quotas keep two decimals; the **Suspected fake** count is readable in dark mode.

Movement pages:

- A double click on **Add movement** or **Upload** no longer saves twice, and a movement with the same place and dates as an existing one opens that one instead. Places made only of invisible characters are refused and zero width joiners are kept. Clicking a free text country field no longer highlights Afghanistan. The overlap report names a stay only when it gives up days of the pair.

## [1.5.2] - 2026-10-01

### Added

- When movements of a year overlap, a red triangle blinks next to the **Movements** title and opens a report: which movements share which days, where those days really count, and the counting rule, with **Copy** and **Close**. Every movement of the year is checked; a normal travel day is not reported.

### Changed

- The header shows Documentation, Contact support and the theme button first, then your name with its menu. Admin search boxes are half as wide, and who gets each day is worked out in one place for the day counts, "N counted" notes, map and report.

### Fixed

- Two one day stays on the same day, or one inside a longer stay, are now reported. A year of 1,000 overlapping stays no longer takes about 2 minutes to report (first 50 pairs detailed, "... and N more"). The warning on save names at most five stays, since a long message in the session cookie could sign the person out.

## [1.5.1] - 2026-10-01

### Added

- The admin accounts table marks accounts that look fake with a red **Likely fake** chip and a reason on hover or focus, using the same rule as `./checkFakeUsers.sh`. **All accounts**, **Suspected fake** and **Hide suspected** filter the table and stay with the search, sort and page.

### Changed

- The Years column is gone from the accounts table. New default prices: Pro $9.99 a year, Nomad+ $19.99 a year (kept in cents; admin overrides still win).
- Docs point to support@nomadlife.pro and every section ends with **Back to the top**. The theme button has a tooltip.

### Fixed

- Dismissing a message no longer makes the page jump. On phones the Check column, chip reason and filter buttons no longer break the accounts table, and the landing page header fits from 320 pixels.

## [1.5.0] - 2026-10-01

### Added

- Documentation at `/docs`, public and indexable: account, years and base, movements, how days are counted (with a diagram), dashboard and map, receipts, accountant package, plans, settings, support, troubleshooting and shortcuts. It has a search box (press `/`), contents that follow the reading position, and light and dark screenshots of the real app. Limits and prices come from the app's settings.
- A question mark button next to the chat bubble opens it; the menu, footer and 404 page link to it too.
- `scripts/docs_screenshots.py` remakes the screenshots from a demo account (headless Chrome, Node 22).

### Changed

- The admin **Security activity** is a table (Time UTC, Event, Account, IP), 20 a page, sortable by every column, covering the whole 365 days. `checkFakeUsers.sh` marks a confirmation within 2 minutes as "likely scanner", not proof.

### Fixed

- Accented emails sorted after z in the admin tables; alphabetical columns now ignore case and accents. Old activity emails link to the account's full history.
- Docs review: wrong sign up button name, photo metadata claims, password reset sign out wording, the worked example, `Esc` behavior, notes and manifest wording, contents names, search for "overlap" and cut words were corrected, and the header leaves room for your name on 375 pixel phones.
- `checkFakeUsers.sh` no longer needs `.venv/bin/flask` (it failed in GitHub Actions).

## [1.4.1] - 2026-10-01

### Added

- `./checkFakeUsers.sh` (= `flask --app app check-fake-users`) lists accounts that look fake (confirmed, never signed in, no years, receipts or tickets, not admins, not disabled, no custom plan or quota, at least 24 hours old) with the confirmation delay and IPs, then asks which to delete and for `DELETE` typed. `--dry-run` only reports. Each deletion is checked again in the DELETE and recorded in the security history.

### Changed

- The IP column is called **Last known IP** and says "at last sign in" or "at sign up".

## [1.4.0] - 2026-10-01

### Added

- The admin accounts table has an IP column (last sign in, or sign up address); the account page shows both, linked to a search, and the search finds part of an IP. New columns `signup_ip` and `last_login_ip` are added on start.

### Fixed

- Company mail scanners (Microsoft Defender, Mimecast, Proofpoint) confirmed accounts that bots signed up with other people's work addresses just by opening the link. The link now opens a page with a **Confirm my account** button and only the button confirms.

## [1.3.2] - 2026-10-01

### Changed

- The Support page introduction fits on one line on wider screens.

### Fixed

- An account disabled before its address joined `ADMIN_EMAILS` can be enabled again. "Back to all tickets" keeps the list's filters, search, sort and page. A double click on "Submit ticket" or "Send message" no longer sends twice (the form sends once and the server treats the same message within a minute as sent).

## [1.3.1] - 2026-09-30

### Added

- **Admin accounts table:** 10 per page, search by email or name, sortable columns, with plan, flags and storage per row.
- **Admin account page** (`/admin/users/<id>`): details, plan and quota forms, disable or enable, delete and the account's recent security activity. **Back to accounts** keeps the search, sort and page.
- **Support tickets are numbered per year** (`2026-1`, then `2027-1`), in lists, emails, subjects and addresses (`/support/2026-1`). The database assigns numbers through a trigger and a counter, never reusing one; existing tickets are renumbered on start and old `/support/3` links still work. The admin search finds a ticket by number, and a whole email finds only that account's tickets.
- "Contact Us" in every footer writes to info@nomadlife.pro.

### Changed

- The support button is a chat bubble that glows until the support pages have been opened, and again on a new reply. The ticket conversation is a chat with blue (user) and purple (support) bubbles grouped within 10 minutes. "Back to ..." links are rounded inverted buttons, "Close ticket" is red, and the account menu has icons.
- `scripts/backup.py` explains how to create the backup folder. `setup_vps.md` follows the real server layout (`/var/www/nomad-life`), gunicorn lets requests finish for up to 120 seconds on restart, and upgrading is a numbered procedure.

### Fixed

- Database: pages no longer fail after 10 seconds while another request writes (the last seen stamp, the minute cleanup and ticket read marks are skipped when busy); renaming a receipt no longer holds the write lock while rewriting its ZIP; double submitted reset and email change links apply once; parallel requests can no longer pass the 1000 stays and 20 open tickets limits; a receipt deleted during `zip-receipts` no longer leaves its ZIP behind; an index by account (`idx_documents_user`) makes storage totals fast (the admin table takes about 10 ms instead of 2 s); disabling an account is one transaction.
- Support pages: subjects of invisible marks are refused, the admin search finds names typed with separate accents, closed tickets say the right thing at the open limit, closing a closed ticket gives a message, and long subjects no longer push table columns off screen.

## [1.3.0] - 2026-09-30

### Added

- **Support tickets.** A header button and a **Support** menu item open `/support`: your tickets with status and dates, and **Submit a ticket** (Report a bug, Feature request, General question). Each ticket is a conversation; both sides can close it and a user message reopens it.
- Admins get **Support tickets** (`/admin/support`): filter by status and type, search by email, name or `#number`, sort by every column, answer, close, reopen, or reopen and send.
- Notification emails link to the ticket and never contain the message. New tables `tickets` and `ticket_messages`.

### Changed

- The footer author link and the page author in the structured data point to x.com/pdiomede.

### Fixed

Found in review before release: searches with non ASCII digits or huge numbers, an admin answer racing a close, accented searches in another case, lost "all" in links, ticket times now in UTC, wrong activity labels, sorting by Type and Messages, phone overflow, writing on a closed ticket at the open limit, the rate limit restarting after an email change, invisible letters in subjects and a right to left subject moving the number.

## [1.2.15] - 2026-09-28

### Changed

- `scripts/backup.py` archives the database and its secret key only. Nightly archives used to hold every receipt, so `--keep 14` stored them 14 times over (70 GB at 5 GB of receipts). The off-site copy now takes the receipts folder as it is (setup_vps.md, section 11); `--with-receipts` makes a complete archive. **Before upgrading, add the receipts folder to the off-site copy.**

### Fixed

- A backup killed part way left a `.nomadlife-*.partial` archive that was uploaded every night; a later run removes it once it is six hours old.

## [1.2.14] - 2026-09-27

### Added

- `scripts/backup.py --dest DIR --keep N`: one archive per run with the database, its secret key and every receipt, safe while the app runs. setup_vps.md uses it for the nightly backup and before upgrades.

## [1.2.13] - 2026-09-27

### Changed

- The "Turn off two-factor sign in" button is red; the footer's "Nomad Life" links home.

### Fixed

- Email addresses in emails move to the next line whole. setup_vps.md: gunicorn runs with `--no-control-socket` (25.1 and newer failed with "Permission denied" under the service hardening).

## [1.2.12] - 2026-09-27

### Added

- `setup_vps.md`: running Nomad Life on Ubuntu 24.04 (gunicorn, nginx, certbot, firewall, clock sync, backups, first admin, upgrades). gunicorn is pinned as the production server.
- `flask --app app zip-receipts [--no-shrink]` converts earlier receipts to ZIP files and resizes their big photos. Run it once after upgrading.

### Changed

- Receipts are stored as ZIP files (compressed when that helps); downloads are always a ZIP. Big photos (over 4 megapixels or 1 MB) are resized to about 4 megapixels and saved as JPEG without metadata when that is smaller; the quota counts the space really used. The accountant package holds the receipts taken out of their ZIPs.

### Fixed

- Parallel server processes could each create a different secret key; an empty key file is replaced.
- Found in review: concurrent renames could corrupt a ZIP, 16 bit grey images turned white, a tiny file with huge dimensions used over 400 MB (images over 24 megapixels are no longer decoded, one resize at a time per process), long screenshots became unreadable (now never under 1000 px wide), transparent PNGs go on white, `zip-receipts` could undo a rename, a damaged receipt stopped the package (now listed as missing), the backup script failed on changing receipts, and names like ".pdf" or with ":" and "?" extracted badly on Windows.

## [1.2.11] - 2026-09-27

### Added

- **Settings page** (`/settings`) replaces Account (old links work), with jump links: Profile, Password, Two-factor, Email, Devices, Activity, Delete account.
- Profile: optional first and last name (new columns, added on start), shown in the menu, greeting you in emails, printed on the accountant package and on the admin list.
- Step by step two-factor setup with the key in groups of 4. Right to left names are drawn correctly in the PDF (`uharfbuzz`, used only when needed).

### Fixed

- Security: the first name was in confirmation emails to any typed address and in security alerts, so it could carry a misleading message. These emails no longer include it.
- Scrolling past messages after saving the name or being sent to turn on two-factor; name changes no longer fill "Recent security activity"; zero width joiners are kept and blank names refused; right to left names are cut at the end; long names fit phone emails; messages say "Settings page".

## [1.2.10] - 2026-09-27

### Added

- "Sign out" ends that browser's session on the server, so a copied session or "Remember me" cookie stops working at once. Sessions unused for 31 days are forgotten.
- New passwords are checked against Have I Been Pwned (only 5 characters of the SHA-1 hash are sent; accepted if the service does not answer in 3 seconds; `PWNED_CHECK=0` turns it off).
- Receipts are checked by content (a web page renamed `.pdf` is refused; an image under a wrong image extension keeps its real type).
- The email change alert to the old address has an **Undo this change** link (7 days): it restores the address, signs out every device and sends a link to choose a new password.
- Tests run on Python 3.10 and 3.12 in GitHub Actions.

### Fixed

- Two-factor: typing the password again no longer resets the wrong code count (wrong codes have their own limit, cleared only by a right one); turning off two-factor is limited and recorded; full width digits work; the setup key no longer leaks to the next account on that browser.
- A leftover "Remember me" cookie no longer turns a later sign in into a remembered one; idle browsers cannot stay signed in; disabling an account removes its sessions; finishing sign up no longer sends a "password was reset" alert; `flask reset-2fa` on an account without two-factor says so; "appears 1 times"; the admin activity filter follows an account across email changes; a PDF header in the last bytes of the first kilobyte is accepted; `fonttools` no longer needs Python 3.11.

## [1.2.9] - 2026-09-27

### Added

- **Security alerts** by email for password changes and resets, email changes (to the old address) and two-factor changes, with time, network address and a reset link.
- **Two-factor sign in** with an authenticator app (QR code or key, 6 digit codes used once). The admin page requires it. `flask --app app reset-2fa <email>` removes it from an account whose phone was lost.
- **Security history** of sign ins, failed sign ins, password, email and two-factor changes and every admin action, kept 365 days. Everyone sees their own recent activity.
- A GitHub Action runs the tests and pip-audit on every push and every week.

### Changed

- Dependencies are pinned to exact versions.

### Security

- An admin could disable or delete another admin. Admin accounts can only be removed from `ADMIN_EMAILS`.

## [1.2.8] - 2026-09-27

Script injection (XSS) review: attack text typed into every user controlled field was shown in every page, email, download and package, and no script ever ran.

### Added

- Content-Security-Policy on every page: only the app's own script files run. The theme is applied by a script file instead of an inline script.

### Fixed

- Emails, cities and countries accepted invisible control characters (such as the one that reverses text); addresses with them are refused and place names are cleaned.

## [1.2.7] - 2026-09-27

Security release. A review found no way for one user to see or change another's data (everything answers "not found"), and these issues, all fixed.

### Added

- Settings: "Sign out everywhere else".

### Security

- Sign up is limited to 10 per network every 15 minutes, passwords are hashed only for new accounts, and at most 4 hashes run at once.
- Sign up and the email change form answer the same whether or not an address has an account (the owner gets an email); sign in takes the same time for unknown emails.
- Failing on purpose could lock the owner out; a browser that signed in before now has its own limit.
- The map slowed down sharply with thousands of stays; it is fast now and a year holds at most 1,000 stays. City and country names are limited to 100 characters.
- Signed in pages are marked private and never stored; every response forbids framing, type guessing and referrer leaks; "Remember me" lasts 30 days instead of a year; the database and receipts are readable only by the account that runs the app.

## [1.2.6] - 2026-09-27

### Added

- Map zoom: +, - and a "whole world" button, up to 8x; drag or use the arrow keys to move, and + - 0 keys zoom. Tabbing to a pin outside the view brings it into view.

### Fixed

- Security: a stranger who signed up first with someone's address could take over the account by signing in after the owner signed up. When a sign up is retried, only the link to choose a password can activate the account.
- A disabled account waiting for confirmation no longer gets emails or confirmations and is not re-created by signing up again. Free plan uploads over the limit are refused before being read.
- Map zoom polish (buttons over pins, touch taps, keyboard focus), plans page and movements table wrapping on phones, long emails in emails, a wrong claim on the landing page, and tests independent of local size settings.

## [1.2.5] - 2026-09-27

### Fixed

- Map: neighboring pins no longer cover each other (they are nudged apart), tapping a pin on a phone opens its tooltip, and cities in a tooltip and "Not on the map" no longer repeat with different spellings.

## [1.2.4] - 2026-09-27

### Changed

- Map pins show the number of days in each country, the same as Days per country (also in the tooltip and for screen readers). Pin colors are slightly darker in the light theme for contrast of at least 5:1.

## [1.2.3] - 2026-09-27

### Added

- A world map on each year's dashboard with a pin per country of the base and stays (the base in its own color); a tooltip shows the flag, country and cities, and places the map cannot show are listed under it. Everything is bundled.
- `scripts/build_map.py` rebuilds the map data from Natural Earth (public domain).

## [1.2.2] - 2026-09-26

### Added

- **Plans:** Free (500 MB, 10 MB per receipt), Pro (5 GB, 25 MB, accountant package) and Nomad+ (25 GB, 50 MB, priority support), each with its own limits. Everyone starts on Free; payment was not available yet. On Free the dashboard offers "Upgrade to Pro" instead of the package.
- The header menu shows your plan (opens the plan page), Admin and Sign out. "Upgrade" on the storage card opens the plan page, and the landing page has a Plans section (prices in US dollars, "Everything in <previous plan>, plus:").
- Admins see and change each account's plan and the plan prices; the landing page, plan page and search engine data follow.

### Changed

- The dashboard's right column shows Days per country, then Receipt storage, then the Accountant package.

### Removed

- `PROMPT.md`.

### Fixed

- Security: signing up again for an address awaiting confirmation could replace its password and take the account. A pending sign up is never replaced now; the mailbox gets a link to choose the password and earlier links stop working. Enabling a disabled account no longer brings back old sessions; a mistyped email change can be cancelled; a reset sets lift the lock; disabled accounts can no longer get reset emails or confirm changes.
- Package PDF wording for years without stays, errors when a year or movement was deleted in another tab, GB instead of "5120 MB", phone header overflow, uploads over a Free limit read in full once paid plans existed, and landing page data that still said "free only".

## [1.2.1] - 2026-09-26

### Fixed

- Security: an older email change link still worked after a newer change; only the newest can be confirmed, only while the email is still the one it started from. Reset links are tied to the address they were sent to. Parallel sign in attempts are now counted before the password is checked (40 parallel guesses used to pass).
- Unconfirmed sign ups no longer block others from switching to an address; wrong account messages, a retry after a failed email, a clearer network limit message and a cut off delete dialog on small phones were fixed.

## [1.2.0] - 2026-09-26

### Added

- Account page: change the password (other devices signed out), change the email through a confirmation link, delete the account with password, two confirmations and the email typed.
- Protection against password guessing: 5 failed sign ins per account or 20 per network in 15 minutes make sign in wait (with the time); wrong current passwords count too; reset requests are limited to 5 per network per 15 minutes.
- `PROXY_COUNT` setting for limits behind a reverse proxy.

## [1.1.0] - 2026-09-26

### Added

- Admin page at `/admin` for `ADMIN_EMAILS`: totals, accounts (25 a page), a storage quota per account, disable and enable (signed out everywhere), and delete with two confirmations and the email typed. Admins cannot disable or delete themselves; everyone else gets "Page not found". The last sign in date is recorded; existing databases get the new columns on start.

### Changed

- The typed confirmation in the delete dialog ignores case and shows the right phone keyboard.

## [1.0.0] - 2026-09-26

First stable release: track every day of a solar year by country, keep the receipts that prove it, and hand your accountant one package. Versions follow semantic versioning from here.

### Added

- New accounts confirm their email: sign up sends a link, and an account not confirmed within 20 minutes is deleted, so nobody can register an address they do not own. Signing in sends a fresh link, and a password reset also confirms.
- Designed confirmation and reset emails with the logo, readable in light and dark mode, plus a plain text version. At most one email per account per minute.
- Messages at the top of the page can be dismissed.

### Changed

- Days without a stay count toward the base country only once they have passed; stays still count in full, also future ones. The package lists the rest as "Days still to come without a stay".
- Forms ask for the country before the city. Static files are linked with a version so updates load at once.

### Fixed

- Notes with line breaks were refused as too long; a deleted stay or year during an upload now ends on the dashboard with a message; "1024 KB" shows as "1 MB"; the package explains stays with 0 counted days.
- Layout and accessibility: long file names and place names no longer scroll pages sideways, red delete buttons keep readable text on hover, the 404 page announces "404", and tiny phones no longer scroll the landing page.

## [0.0.11] - 2026-09-26

### Fixed

- Dark theme contrast, keyboard focus after downloading the package, header wrapping on small phones, document badge labels, screen reader text for the delete dialog, "1 day", the same day start rule text, the base form keeping typed values, "Cancel" returning to the right page, a 5000 character limit on notes, uploads racing a deleted stay.
- `runWebApp.sh` port messages. `config.env` now wins over exported shell variables, `~` in paths means your home folder, and `config.env` is private to you.

## [0.0.10] - 2026-09-26

### Fixed

- Security: a crafted sign in link with a tab (`/login?next=%2F%09%2Fevil.com`) redirected to another site. Only pages of this app are accepted.
- Signing out from a page open for over an hour failed; form tokens now last as long as the session. After a session ended, Delete, Save or Sign out then signing in ended on "Method Not Allowed"; you land on the dashboard now. Huge numbers in addresses are "Page not found". With an `https://` base URL cookies are Secure and SameSite=Lax. An invalid `APP_PORT` gives a clear message.
- `runWebApp.sh` rebuilds a half created virtual environment and reads ports as decimal (`0022` was read as octal).
- Day counts: country names differing only by accents, apostrophes or Unicode form count as one country.
- Package: very long names no longer fail the download, and `{nb}` in a name is no longer replaced by the page count. Renaming to a name with a slash no longer drops the start.
- Sizes: decimal sizes in `config.env`, "30 MB used of 30 MB" next to "40 KB left", the receipt type after a date error, file pickers announcing types and limits, and long names in the delete dialog and movements table.
- "Copy link" stuck on "Link copied" and faint focus colors in the light theme.

## [0.0.9] - 2026-09-26

### Added

- The movements list is paged (10 per page, numbered buttons, previous and next, "Showing 11 to 20 of 34 movements"), with a choice of 10, 25 or 50 that is remembered. Back and delete return to the page you were on. Day totals always use every movement.

### Fixed

- Returning to page 1 or deleting from page 2 or later jumped or hid the message, and a 25 or 50 choice was forgotten after opening a movement.

## [0.0.8] - 2026-09-26

### Fixed

- Accountant package: every ZIP entry is deflated (stream reading unzip tools rejected stored receipts), receipts are grouped by stay with base documents first, long names no longer make paths too long for Windows, long base city and country names wrap in the PDF, and `README.txt` explains the manifest and semicolon regions for CSV files.

## [0.0.7] - 2026-09-26

### Added

- **Accountant package:** one ZIP per year with `summary.pdf` (base, days, 183 day line, days per country, timeline, counting rules, receipt index), `timeline.csv`, `country-totals.csv`, the receipts in a folder per stay, `manifest.csv` with SHA-256 checksums and `README.txt`. "Include my notes" is optional. The package is streamed, never stored, uses safe file names, protects CSV cells from formulas and reports missing receipts.
- Automated test suite in `tests/`, and the `fpdf2` dependency with bundled DejaVu Sans and IPAGothic fonts.

## [0.0.6] - 2026-09-26

### Added

- Searchable country picker with flags (names, alternatives like UK, USA, Holland, and two letter codes; free text still allowed), flags on the dashboard and movement pages, renaming documents, and Edit and Delete buttons per movement.
- Every delete asks twice and the server refuses unconfirmed deletes; deleting a year also needs the year typed.
- SEO for the landing page (title, canonical URL, structured data, `robots.txt`, `sitemap.xml`, `noindex` on private pages), a web app manifest and icons, and receipts sent with `Cache-Control: private, no-store`.

### Fixed

- Country picker scrollbar and screen reader messages, double click passing both delete confirmations, the "Type to confirm" field showing on every delete, deleting without dialog support, labels on list buttons for screen readers, the rename form on phones, the "More options" share button, and the footer link relying on color alone.

## [0.0.5] - 2026-09-26

### Added

- New logo, a landing page at `/` with "Launch app" (the app moved to `/app`), link previews (Open Graph and Twitter card), share buttons (X, LinkedIn, Facebook, WhatsApp, Telegram, Reddit, Copy link, device share), and a funny standalone 404 page.

### Fixed

- The floating receipt card on the landing page covered "At least 183 days" at 961 to 1020 px.

## [0.0.4] - 2026-09-26

### Added

- `MAX_RECEIPT_MB` (default 10 MB; `MAX_UPLOAD_MB` still works) and `USER_QUOTA_MB` (default 500 MB, checked under one write lock so parallel uploads cannot exceed it). A Receipt storage card shows space left and used with "Almost full", "Full" and "Over quota" states. Upload forms show and check the limits in the browser. Invalid limits stop the app at startup with the setting's name.

### Fixed

- Size messages no longer contradict themselves from rounding, a failed disk write leaves no partial file or server error, and the "Full" label and disabled "Choose file" button were corrected.

## [0.0.3] - 2026-09-26

### Fixed

- Interface: links, messages, pills and danger buttons meet WCAG AA contrast in both themes; the year selector is a labelled navigation.
- `runWebApp.sh`: handles `config.env` without `APP_PORT`, reads it like the app, rejects invalid and browser blocked ports, recreates a broken `.venv` and detects a busy port without lsof, fuser or ss.
- Receipts: non Latin names are kept, empty files are rejected, oversized files are caught in the browser, and upload dates are in local time.
- Sign up rejects invalid emails like `@.`, and cookies have unique names so another app on localhost does not sign you out.
- Days: a side trip starting the same day as a longer stay is no longer hidden; the table shows how many days each stay counts and saving an overlap warns; the base label says "At least 183 days".

## [0.0.2] - 2026-09-26

### Added

- Footer with copyright and "Built by Paolo Diomede".

### Fixed

- Country names match regardless of case. A password reset signs out every other session. Reset links no longer embed part of the password hash, an empty `SECRET_KEY` generates a random key stored in `data/.secret_key`, and reset links no longer trust the Host header. Documents are served with a type from their extension, an upload without a file no longer reports success, files are removed only after the database delete succeeds, and unique constraint errors show a message and roll back.

## [0.0.1] - 2026-09-26

### Added

- Sign up, sign in, sign out and password reset via Gmail SMTP; solar year workspaces with a base city and country; movements with dates, city, country and notes; document storage for the base and movements; a dashboard with days per country and the 183 day indicator; light and dark mode; the `runWebApp.sh` launcher; SQLite storage and `config.env`.

### Fixed

- Mobile overflow of the dashboard and movements table, even card spacing, sign in keeping the email after a failure, edit forms keeping typed values, a styled 404 page, a friendly message for expired form tokens, and small files shown in bytes.
