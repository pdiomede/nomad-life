# Running Nomad Life on a VPS

A step by step guide for a small virtual server (1 vCPU and 1 GB RAM are enough for a few hundred users with 2 workers; take 2 GB when many people upload photos at once). It uses **Ubuntu 24.04 LTS**, **gunicorn** as the application server and **nginx + certbot** for HTTPS. Replace `nomad.example.com` with your domain and `admin@example.com` with your address everywhere below.

## 1. What the app needs, and why

Everything the app uses comes either with Python or as a Python package (a "wheel") that pip downloads with its own compiled parts. **No system library has to be installed for PDF, ZIP, images or two-factor sign in.**

| Feature | What provides it | Install |
|---|---|---|
| Web app | Flask, Flask-Login, Flask-WTF | `pip install -r requirements.txt` |
| Production server | gunicorn (pinned in requirements.txt) | same |
| Database | SQLite, built into Python | nothing (backups use `scripts/backup.py`; the `sqlite3` command line tool is optional, to look inside the database) |
| Accountant package PDF | fpdf2; fonts are bundled in `static/fonts` (DejaVu, IPA Gothic for Japanese, Twemoji flags) | pip |
| Right to left names in the PDF (Arabic, Hebrew) | uharfbuzz (HarfBuzz, bundled in its wheel) | pip |
| ZIP files (stored receipts, accountant package) | Python's `zipfile` and `zlib` | nothing |
| Shrinking big photos, checking uploaded files | Pillow (libjpeg, libpng, libwebp bundled in its wheel) | pip |
| Two-factor sign in | TOTP from Python's standard library, QR codes from segno (pure Python) | pip |
| Two-factor codes being accepted | **an accurate clock** (codes change every 30 seconds) | chrony, see section 10 |
| Breached password check | HTTPS requests to `api.pwnedpasswords.com` | outbound port 443 open |
| Emails (confirmations, resets, security alerts) | Gmail over SMTP with an App Password | outbound port 587 open to `smtp.gmail.com` |
| HTTPS certificates | Let's Encrypt through certbot | apt |

Python 3.10 or newer is required; Ubuntu 24.04 ships 3.12, which the tests also run on. HEIC photos (iPhone) are stored as uploaded: they are zipped but not resized, because Pillow cannot read HEIC without extra libraries.

## 2. System packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv git nginx certbot python3-certbot-nginx \
                    chrony sqlite3 ufw unattended-upgrades
sudo apt install -y fail2ban   # optional: blocks IPs that hammer SSH
sudo dpkg-reconfigure -plow unattended-upgrades   # security updates install themselves
```

## 3. Folders for the app

The app lives in `/var/www/nomad-life` and runs as the login user `paolo`, which owns the code, the database and the receipts. Run every command in this guide as `paolo` (`sudo` only where it says so). The database (`data/`) and the receipts (`uploads/`) sit inside the app folder, readable by `paolo` only; nginx never serves files from it (section 7).

```bash
sudo install -d -o paolo -g paolo -m 755 /var/www/nomad-life
git clone https://github.com/pdiomede/nomad-life.git /var/www/nomad-life
mkdir -p /var/www/nomad-life/data /var/www/nomad-life/uploads
chmod 700 /var/www/nomad-life/data /var/www/nomad-life/uploads
```

## 4. Install the app

```bash
cd /var/www/nomad-life
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest     # everything should end with "OK"
```

## 5. config.env for production

Create `/var/www/nomad-life/config.env` (it is git-ignored) from the example and set at least:

```ini
APP_PORT=5050
APP_BASE_URL=https://nomad.example.com
DATABASE_PATH=/var/www/nomad-life/data/nomad.db
UPLOAD_DIR=/var/www/nomad-life/uploads
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
ADMIN_EMAILS=admin@example.com
PROXY_COUNT=1
PWNED_CHECK=1
MAX_RECEIPT_MB=10
USER_QUOTA_MB=500
```

```bash
cp config.env.example config.env && nano config.env
chmod 600 /var/www/nomad-life/config.env
```

- `APP_BASE_URL` must be the public `https://` address: emailed links are built from it, and it turns on secure cookies.
- `PROXY_COUNT=1` because nginx sits in front: without it every visitor would share nginx's address and the sign in limits would lock everyone out together.
- Leave `SECRET_KEY` empty: the app creates `data/.secret_key` (mode 600) on first start. Keep that file in your backups; losing it signs everybody out.
- Set `PWNED_CHECK=0` only if the server cannot reach the internet.

## 6. gunicorn as a systemd service

Create `/etc/systemd/system/nomad-life.service`:

```ini
[Unit]
Description=Nomad Life
After=network-online.target
Wants=network-online.target

[Service]
User=paolo
Group=www-data
WorkingDirectory=/var/www/nomad-life
UMask=0077
ExecStart=/var/www/nomad-life/.venv/bin/gunicorn --preload --workers 3 --threads 4 \
          --bind 127.0.0.1:5050 --timeout 120 --graceful-timeout 120 --no-control-socket \
          --access-logfile - app:app
TimeoutStopSec=150
Restart=always
RestartSec=3
# Hardening: the app only writes to its data and uploads folders.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/www/nomad-life/data /var/www/nomad-life/uploads

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nomad-life
systemctl status nomad-life          # "active (running)"
curl -I http://127.0.0.1:5050/       # HTTP/1.1 200 OK
```

- `--preload` creates the app once before the workers start, so the database is migrated and the secret key created only once.
- `--timeout 120` leaves time to build the accountant package of a busy year.
- `--graceful-timeout 120` lets requests that are running when the app stops (a restart for an upgrade) finish, up to the same 120 seconds, instead of the default 30. `TimeoutStopSec=150` keeps systemd from killing gunicorn before that (its default is 90 seconds).
- `--no-control-socket` turns off the `gunicornc` control socket (gunicorn 25.1 and newer). It would be created in the service user's home folder, which the hardening below makes read-only or hidden, so without the flag the log shows `Control server error: Permission denied`. systemd already restarts and stops the app.
- `UMask=0077` keeps the database and receipts readable by `paolo` only (like `umask 077` in `runWebApp.sh`).
- Workers: about 2 x CPU cores + 1. Resizing a big photo can briefly use up to about 200 MB, and each worker resizes one photo at a time, so 3 workers need up to about 600 MB on top of the app itself: take 2 GB of RAM, or use `--workers 2` on a 1 GB server.

## 7. nginx

Create `/etc/nginx/sites-available/nomad-life`:

```nginx
server {
    listen 80;
    # No root: nginx forwards to gunicorn and serves nothing from /var/www/nomad-life except the
    # 404 page below (the app sends it itself for its own wrong addresses).
    server_name nomad.example.com;

    # For the 404s nginx makes itself (the blocked paths below): the app's own page.
    error_page 404 /404.html;
    location = /404.html {
        root /var/www/nomad-life/static;
        internal;
    }

    # Never forward probes for dotfiles (.env, .git) or config, log and backup files.
    location ~ /\.(?!well-known) {
        deny all; access_log off; log_not_found off; return 404;
    }
    location ~* \.(env|ini|log|conf|sql|bak|save|md)$ {
        deny all; access_log off; log_not_found off; return 404;
    }

    # The largest receipt of the biggest plan (Nomad+, 50 MB) plus the form around it.
    # If you raise MAX_RECEIPT_MB above 50, raise this to MAX_RECEIPT_MB + 2.
    client_max_body_size 52m;

    # Compress the app's text assets (style.css 66 KB -> 15 KB, theme.js 35 -> 10 KB, the
    # world map SVG 113 -> 40 KB). Only in this server block, so other sites on the same
    # nginx are unchanged. HTML is already compressed by nginx.conf's "gzip on".
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 5;
    gzip_min_length 1024;
    gzip_types text/css application/javascript text/javascript image/svg+xml application/manifest+json application/json;

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 130s;      # a little over gunicorn's --timeout
        proxy_request_buffering on;   # nginx takes slow uploads, gunicorn stays free
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/nomad-life /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Check both kinds of 404 once HTTPS works (section 8): each line should end with `Page not found | Nomad Life` and `404`. If `/.env` shows nginx's plain page instead, nginx (`www-data`) cannot read `static/404.html`: check the folders with `namei -l /var/www/nomad-life/static/404.html`.

```bash
for u in /this-page-does-not-exist /.env; do curl -s -w " %{http_code}\n" https://nomad.example.com$u | grep -oE "<title>[^<]*|[0-9]{3}$" | tr '\n' ' '; echo; done
```

Check that the styles and the map are compressed (`content-encoding: gzip` and `vary: Accept-Encoding`); photos, fonts and receipts are already compressed and are left alone:

```bash
curl -s -o /dev/null -D - -H 'Accept-Encoding: gzip' https://nomad.example.com/static/css/style.css | grep -iE 'content-encoding|vary'
```

The app already sends its own security headers (Content-Security-Policy, X-Frame-Options, nosniff, Referrer-Policy, no-store for private pages), so nginx does not need to add them. Do not serve the `uploads` folder through nginx: receipts must only be reachable through the app, which checks who is signed in.

## 8. HTTPS with Let's Encrypt

Point the domain's DNS A (and AAAA) record to the server first, then:

```bash
sudo certbot --nginx -d nomad.example.com --redirect -m admin@example.com --agree-tos
sudo certbot renew --dry-run          # renewal runs by itself twice a day
```

Once HTTPS works, add HSTS inside the `server { listen 443 ... }` block that certbot created, and reload nginx:

```nginx
add_header Strict-Transport-Security "max-age=31536000" always;
```

### Optional: Cloudflare in front

Cloudflare's free plan can serve the domain's DNS and, with its proxy on (the orange cloud), sit between visitors and the server. The app needs nothing new, but nginx must pass on the visitor's real address, or every sign up IP, sign in limit and ipinfo.io link would show Cloudflare's.

1. **Add the domain.** At https://dash.cloudflare.com choose **Add a domain** and the **Free** plan. Before continuing, check that the imported records match the registrar's: the `A` (and `AAAA`) records of the domain and `www`, and every `MX` and `TXT` record, or mail to the domain stops.
2. **Switch the nameservers** at the registrar to the two Cloudflare gives (turn DNSSEC off there first, and on again in Cloudflare later). Wait for Cloudflare's "site is active" email.
3. **SSL/TLS → Overview: Full (strict).** The server has its own Let's Encrypt certificate (above). "Flexible" talks to the server over plain HTTP, which certbot's redirect answers with HTTPS again: an endless redirect loop. Renewals keep working through the proxy.
4. **Turn off what rewrites pages:** Speed → **Rocket Loader** off, and Scrape Shield → **Email Address Obfuscation** off. Both inject scripts the app's Content-Security-Policy refuses, and the obfuscation also changes the contact addresses on the pages.
5. **Real visitor addresses.** With the proxy off (grey cloud, DNS only) skip this: nothing changes. With it on, let nginx take the address from Cloudflare's `CF-Connecting-IP` header, but only from Cloudflare's own ranges (so nobody reaching the server directly can fake it). A small script writes the ranges, which Cloudflare publishes and changes now and then:

   ```bash
   sudo tee /usr/local/sbin/cloudflare-real-ip >/dev/null <<'EOF'
   #!/bin/sh
   # Cloudflare's address ranges for nginx's real IP module; run weekly by cron.
   set -e
   out=/etc/nginx/conf.d/cloudflare-real-ip.conf
   tmp=$(mktemp)
   for list in ips-v4 ips-v6; do
       curl -fsS "https://www.cloudflare.com/$list" | sed '/^$/d; s/.*/set_real_ip_from &;/' >> "$tmp"
   done
   grep -q set_real_ip_from "$tmp"            # never install an empty list
   echo "real_ip_header CF-Connecting-IP;" >> "$tmp"
   install -m 644 "$tmp" "$out" && rm -f "$tmp"
   nginx -t -q && systemctl reload nginx
   EOF
   sudo chmod 755 /usr/local/sbin/cloudflare-real-ip
   sudo /usr/local/sbin/cloudflare-real-ip
   echo '17 4 * * 1 root /usr/local/sbin/cloudflare-real-ip' | sudo tee /etc/cron.d/cloudflare-real-ip
   ```

   nginx then uses the visitor's address as `$remote_addr`, and the `X-Forwarded-For` line of the server block passes it on as the last entry, which is the one the app reads: keep `PROXY_COUNT=1` in `config.env`. Check it by signing in from your phone on mobile data: on `/admin` your **Last sign in IP** must be the phone's address (its ipinfo.io page names your mobile carrier), not a Cloudflare one.
6. **Limits to know:** on the free plan Cloudflare gives up on a request after 100 seconds (error 524) and refuses uploads over 100 MB. Receipts stay far below that; only a very large accountant package on a slow server could get near the time limit.

hCaptcha and the app's Gmail sending are not affected: the hCaptcha site keeps the same domain, and mail leaves the server directly.

## 9. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow "Nginx Full"
sudo ufw enable
sudo ufw status
```

Port 5050 stays closed to the outside: gunicorn only listens on 127.0.0.1. Outgoing traffic (ports 443 and 587) is allowed by default.

## 10. Accurate time (needed for two-factor sign in)

Codes from authenticator apps are only accepted within about 30 seconds of the server's clock.

```bash
sudo systemctl enable --now chrony
timedatectl          # "System clock synchronized: yes"
chronyc tracking     # "System time" should be a few milliseconds off at most
```

## 11. Backups

The database and the receipts are everything. Back them up every night, keep copies off the server, encrypted, and try a restore once.

The database needs a proper copy; the receipts do not. `scripts/backup.py` writes one small archive per run with the database and its secret key, and keeps the newest `--keep` archives. It reads the paths from `config.env`, is safe while the app runs (SQLite's online backup) and needs nothing beyond the app's own `.venv`. Run it as `paolo`, who owns the data:

```bash
sudo install -d -o paolo -g paolo -m 700 /var/backups/nomad-life
cd /var/www/nomad-life
.venv/bin/python scripts/backup.py --dest /var/backups/nomad-life --keep 14
echo "30 3 * * * paolo /var/www/nomad-life/.venv/bin/python /var/www/nomad-life/scripts/backup.py --dest /var/backups/nomad-life --keep 14" | sudo tee /etc/cron.d/nomad-life-backup
```

Each run prints the archive it wrote, for example `/var/backups/nomad-life/nomadlife-2026-09-27_033000.tar.gz`, readable by `paolo` only (mode 600).

Then copy **both** `/var/backups/nomad-life` and the receipts folder `/var/www/nomad-life/uploads` elsewhere every night, encrypted: for example with `restic` or `borg` to another server or to object storage, run as root or `paolo` (the only accounts that can read them). A receipt never changes once saved (the app writes a temporary file and renames it), so the tool can read the folder while the app runs and stores each receipt once; skip `*.tmp`, which are uploads still being written. With restic, for example:

```bash
restic backup --exclude '*.tmp' /var/backups/nomad-life /var/www/nomad-life/uploads
```

Receipts are deliberately not in the nightly archive: there they were stored `--keep` times over on the server's own disk, which fills up as receipts grow. For a one-off complete archive (before moving to a new server, or before `zip-receipts`), add `--with-receipts`.

The database runs in write-ahead log mode (since 1.5.6): recent changes may sit in `nomad.db-wal` until SQLite folds them into `nomad.db`. Copy it only with `scripts/backup.py`, never `nomad.db` alone with `cp`, `rsync` or a backup tool reading `data/` while the app runs. To put a backup back, see section 12.

## 12. Restoring a backup

Restore when the database is damaged, data was lost or changed by mistake, or on a new server. Everything that happened after the archive was made (sign ups, stays, receipts uploaded) is lost, so pick the newest archive that is still good. Work as `paolo`, `sudo` only where it says so.

1. Stop the app, so nothing writes while you restore:

   ```bash
   sudo systemctl stop nomad-life
   ```

2. Keep the current state aside, in case you need something from it later (with the app stopped, a plain copy is complete):

   ```bash
   mkdir -p ~/before-restore && cp -a /var/www/nomad-life/data ~/before-restore/data-$(date +%F_%H%M)
   ```

3. Delete the write-ahead log files of the current database. They belong to the database being replaced: left next to the restored one, SQLite would apply them to it and could damage it.

   ```bash
   cd /var/www/nomad-life/data && rm -f nomad.db-wal nomad.db-shm
   ```

4. Choose an archive. The newest ones on the server:

   ```bash
   ls -lt /var/backups/nomad-life | head
   ```

   If the server's own copies are gone (a new server), get them back from the off-site copy first, for example with restic: `restic snapshots`, then `restic restore latest --target /tmp/restore --include /var/backups/nomad-life` and use the archive under `/tmp/restore`.

5. Unpack it into `data/`. It holds `nomad.db` and, when the app created one, `.secret_key`; both replace the current files:

   ```bash
   tar -xzf /var/backups/nomad-life/nomadlife-YYYY-MM-DD_HHMMSS.tar.gz -C /var/www/nomad-life/data
   chmod 600 /var/www/nomad-life/data/nomad.db /var/www/nomad-life/data/.secret_key 2>/dev/null
   ```

6. Only if receipts were lost too (a new server, a deleted folder): get `uploads/` back from the off-site copy, owned by `paolo`, mode 700:

   ```bash
   restic restore latest --target / --include /var/www/nomad-life/uploads
   chmod 700 /var/www/nomad-life/uploads
   ```

7. Check the restored database (it should print `ok`), then start the app:

   ```bash
   sqlite3 /var/www/nomad-life/data/nomad.db "PRAGMA integrity_check"
   sudo systemctl start nomad-life
   ```

8. Check that it is healthy, as after an upgrade (section 14, step 7), then sign in and open a few pages.

Good to know:

- With the archive's `.secret_key` back, everyone who was signed in when the archive was made stays signed in. Without it (a new key is made), everyone signs in again; nothing else is lost.
- `config.env` is not in the archive (it holds the Gmail App Password). On a new server, set it up again from section 5, or keep a copy in your password manager.
- Receipts uploaded after the archive stay on disk without a row pointing to them: they use space but nobody sees them. Receipts deleted after the archive are listed as missing in the accountant package.
- The app turns the restored database back to write-ahead log mode on start (section 11); nothing to do.

## 13. The first admin

1. Make sure your address is in `ADMIN_EMAILS` in `config.env` and restart: `sudo systemctl restart nomad-life`.
2. Sign up at `https://nomad.example.com/signup` and open the confirmation email.
3. Sign in, open **Settings** from the menu at the top, and turn on **two-factor sign in** (scan the QR code with Google Authenticator or a similar app). The admin page only opens once it is on.
4. The **Admin** link appears in the menu.

## 14. Upgrading

Nobody has to be signed out for an upgrade. Sessions live in the signed cookie (`data/.secret_key`) and in the `user_sessions` table, so everyone is still signed in after the restart. Requests already running are allowed to finish (`--graceful-timeout`, section 6), and new ones get a 502 from nginx for the few seconds the app takes to start again.

Upgrade step by step, as `paolo`:

1. Go to the app and make sure nothing was changed on the server (no output means clean; if files are listed, check them before going on):

   ```bash
   cd /var/www/nomad-life && git status --short
   ```

2. Check that nobody is using the app. This lists the pages requested in the last 5 minutes:

   ```bash
   sudo journalctl -u nomad-life --since "-5 min" --no-pager -q | grep -E '"(GET|POST) ' | grep -v '/static/'
   ```

   - No output, or only `/`, `/robots.txt`, `/sitemap.xml` and bots (bingbot, Googlebot): nobody is working in the app, go on.
   - `/app`, `/year/...`, `/support...`, `/settings`, `/admin` or any `POST`: someone is active. Wait a few minutes and run it again.

   Do not use `user_sessions.last_seen_at` for this: it is stamped at most once an hour. Without `sudo`, journalctl adds a "Hint" about other users' messages; the app's lines are shown anyway, since the app runs as you.

3. Back up the database and note the archive it prints (until `/var/backups/nomad-life` exists, section 11, any folder you own works, for example `/home/paolo/backups/nomad-life`):

   ```bash
   .venv/bin/python scripts/backup.py --dest /var/backups/nomad-life --keep 14
   ```

4. Get the new code and its packages. The running app is not touched yet; `--ff-only` refuses to pull if the history does not match instead of merging:

   ```bash
   git pull --ff-only
   .venv/bin/pip install -r requirements.txt
   ```

5. Check the last minute once more, right before the restart:

   ```bash
   sudo journalctl -u nomad-life --since "-1 min" --no-pager -q | grep -E '"(GET|POST) ' | grep -v '/static/'
   ```

6. Restart. Running requests get up to 120 seconds to finish, nobody is signed out, and the database is upgraded on start:

   ```bash
   sudo systemctl restart nomad-life
   ```

7. Check that it is healthy: `active (running)`, the new version, and no errors in the log. The `sleep` gives the app a few seconds to start (it upgrades the database first), so the version check does not run into a server that is not listening yet:

   ```bash
   sleep 5; systemctl status nomad-life --no-pager | head -5
   curl -s http://127.0.0.1:5050/ | grep -o 'v1\.[0-9]*\.[0-9]*' | head -1
   sudo journalctl -u nomad-life -n 30 --no-pager -q | grep -iE 'error|traceback' || echo "no errors"
   ```

8. Open https://nomad.example.com, sign in and look at the pages the release changed (its `CHANGELOG.md` entry). Browsers load the new styles by themselves after the restart.

If something is wrong, go back to the previous version: find it with `git log --oneline -3`, then `git checkout <previous id> && sudo systemctl restart nomad-life`. Database upgrades only add tables, columns and indexes, so the previous version keeps working with the upgraded database. If the database itself is damaged, stop the service, delete `data/nomad.db-wal` and `data/nomad.db-shm`, unpack the archive from step 3 into `data/` (section 12) and start it again.

Always restart, never reload with `kill -HUP`: because of `--preload` the gunicorn master keeps the old code, so a reload would start the new workers on the old version.

A server set up from an older copy of this guide lacks `--graceful-timeout 120` and `TimeoutStopSec=150` in `/etc/systemd/system/nomad-life.service`. Add them as in section 6, then run `sudo systemctl daemon-reload` once.

The database is upgraded by the app on start (new columns are added automatically). Read `CHANGELOG.md` for steps a version needs:

- **1.6.0** adds hCaptcha to Sign up and Forgot password (off until configured). Create a site at https://dashboard.hcaptcha.com, add your domain to it, then put its site key in `HCAPTCHA_SITEKEY` and your account's secret in `HCAPTCHA_SECRET` in `config.env`, and restart. Set both or neither: one alone stops the app at start.
- **1.5.8** adds the `email_changes` table on start (numbered email changes, so an **Undo this change** link keeps working when the address is changed again). Nothing to run. Undo links sent before the upgrade keep the old rule: they work only while the account still uses the new address.

- **1.5.7** adds gzip for the styles, scripts and map to the nginx server block (section 7): add the `gzip` lines to an existing server, then `sudo nginx -t && sudo systemctl reload nginx`. Optional, but a first visit then downloads about 65 KB of them instead of 214 KB.

- **1.5.6** switches the database to write-ahead logging on the first start (readers and writers stop waiting for each other); `nomad.db-wal` and `nomad.db-shm` appear next to `nomad.db`, private like it. Nothing to run: the nightly `scripts/backup.py` and the restic command stay as they are. Only restoring changes: delete those two files first (section 12). It also adds an index to the security history, built on start in well under a second.

- **1.4.0** records the IP address of each sign up and sign in (two columns added on start; the last sign in IP of existing accounts is taken from the security history), and the email confirmation link now opens a page with a button. Accounts that company mail scanners confirmed before, by opening the link (confirmed seconds after signing up, never signed in), stay until you delete them from their admin page.

- **1.3.1** numbers support tickets per year (2026-1, 2026-2, ...). Existing tickets are renumbered on start, in the order they were opened; links in emails sent before still open the right ticket. To see the numbers after the restart: `sqlite3 data/nomad.db "SELECT ref_year || '-' || ref_seq, subject FROM tickets ORDER BY id"`.

- **1.2.15** leaves the receipts out of the nightly archive. Before upgrading, make sure the off-site copy also takes the receipts folder (section 11), or from that night on the receipts are backed up nowhere. Archives from before still hold the receipts and are removed by `--keep` as usual.

- **1.2.12** stores receipts as ZIP files and resizes big photos. Receipts uploaded before keep working (they are zipped when downloaded), but to save the space run this once:

  ```bash
  cd /var/www/nomad-life
  .venv/bin/flask --app app zip-receipts            # zip, and resize big photos
  .venv/bin/flask --app app zip-receipts --no-shrink  # or: zip only, keep photos
  ```

  It rewrites receipts, so take a complete archive first (`scripts/backup.py --dest /var/backups/nomad-life --with-receipts`). It prints how much space was saved and can be run again safely.

## 15. Day to day

| Task | Command |
|---|---|
| See the app's log | `journalctl -u nomad-life -f` |
| Watch CPU, memory and disk, with alerts | Beszel, private through an SSH tunnel: [beszel.md](beszel.md) |
| Restart after changing config.env | `sudo systemctl restart nomad-life` |
| Someone lost the phone with their authenticator app | `.venv/bin/flask --app app reset-2fa someone@example.com` (from `/var/www/nomad-life`; they get an email about it) |
| Disk used by receipts | `sudo du -sh /var/www/nomad-life/uploads` |
| Check the pinned packages for known vulnerabilities | `.venv/bin/pip install pip-audit && .venv/bin/pip-audit -r requirements.txt` (the GitHub Action also does this every Monday) |
| Accounts that look fake (confirmed, never signed in, no data) | `./checkFakeUsers.sh --dry-run` to see them, `./checkFakeUsers.sh` to choose and delete them (from `/var/www/nomad-life`, after a backup) |
| Many "Too many attempts" in the log | Somebody is guessing passwords; the limits hold them. With fail2ban you can also block the IP. |

## 16. Checklist

- [ ] `https://nomad.example.com` opens with a valid certificate and `http://` redirects to it
- [ ] `config.env`: `APP_BASE_URL` is https, `PROXY_COUNT=1`, Gmail set, `ADMIN_EMAILS` set, file mode 600
- [ ] `systemctl status nomad-life` is active and starts again after `sudo reboot`
- [ ] Sign up works and the confirmation email arrives
- [ ] Two-factor sign in is on for every admin; `timedatectl` says the clock is synchronized
- [ ] Uploading a receipt works, and downloading it gives a ZIP
- [ ] `ufw status` shows only SSH and Nginx Full
- [ ] The nightly backup runs and a copy leaves the server; a restore was tried once
- [ ] Unattended upgrades are on
