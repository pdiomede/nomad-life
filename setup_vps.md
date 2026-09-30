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
    # No root: nginx only forwards to gunicorn and never serves files from /var/www/nomad-life.
    server_name nomad.example.com;

    # The largest receipt of the biggest plan (Nomad+, 50 MB) plus the form around it.
    # If you raise MAX_RECEIPT_MB above 50, raise this to MAX_RECEIPT_MB + 2.
    client_max_body_size 52m;

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

To restore: stop the service, unpack an archive (`tar -xzf nomadlife-....tar.gz`), put `nomad.db` and `.secret_key` back in `/var/www/nomad-life/data` and the receipts from the off-site copy in `/var/www/nomad-life/uploads` (owned by `paolo`, mode 700), start the service.

## 12. The first admin

1. Make sure your address is in `ADMIN_EMAILS` in `config.env` and restart: `sudo systemctl restart nomad-life`.
2. Sign up at `https://nomad.example.com/signup` and open the confirmation email.
3. Sign in, open **Settings** from the menu at the top, and turn on **two-factor sign in** (scan the QR code with Google Authenticator or a similar app). The admin page only opens once it is on.
4. The **Admin** link appears in the menu.

## 13. Upgrading

Nobody has to be signed out for an upgrade. Sessions live in the signed cookie (`data/.secret_key`) and in the `user_sessions` table, so everyone is still signed in after the restart. Requests already running are allowed to finish (`--graceful-timeout`, section 6), and new ones get a 502 from nginx for the few seconds the app takes to start again.

To avoid restarting in the middle of someone's upload or package download, first check that no pages were requested in the last few minutes (no output means nobody is using the app). Do not use `user_sessions.last_seen_at` for this: it is stamped at most once an hour.

```bash
journalctl -u nomad-life --since "-5 min" --no-pager | grep -E '"(GET|POST) ' | grep -v '/static/'
```

Then upgrade:

```bash
cd /var/www/nomad-life
.venv/bin/python scripts/backup.py --dest /var/backups/nomad-life --keep 14   # a fresh backup first
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart nomad-life
systemctl status nomad-life --no-pager && curl -sI http://127.0.0.1:5050/ | head -1   # "active (running)", then 200 OK
```

Always restart, never reload with `kill -HUP`: because of `--preload` the gunicorn master keeps the old code, so a reload would start the new workers on the old version.

A server set up from an older copy of this guide lacks `--graceful-timeout 120` and `TimeoutStopSec=150` in `/etc/systemd/system/nomad-life.service`. Add them as in section 6, then run `sudo systemctl daemon-reload` once.

The database is upgraded by the app on start (new columns are added automatically). Read `CHANGELOG.md` for steps a version needs:

- **1.3.1** numbers support tickets per year (2026-1, 2026-2, ...). Existing tickets are renumbered on start, in the order they were opened; links in emails sent before still open the right ticket.

- **1.2.15** leaves the receipts out of the nightly archive. Before upgrading, make sure the off-site copy also takes the receipts folder (section 11), or from that night on the receipts are backed up nowhere. Archives from before still hold the receipts and are removed by `--keep` as usual.

- **1.2.12** stores receipts as ZIP files and resizes big photos. Receipts uploaded before keep working (they are zipped when downloaded), but to save the space run this once:

  ```bash
  cd /var/www/nomad-life
  .venv/bin/flask --app app zip-receipts            # zip, and resize big photos
  .venv/bin/flask --app app zip-receipts --no-shrink  # or: zip only, keep photos
  ```

  It rewrites receipts, so take a complete archive first (`scripts/backup.py --dest /var/backups/nomad-life --with-receipts`). It prints how much space was saved and can be run again safely.

## 14. Day to day

| Task | Command |
|---|---|
| See the app's log | `journalctl -u nomad-life -f` |
| Watch CPU, memory and disk, with alerts | Beszel, private through an SSH tunnel: [beszel.md](beszel.md) |
| Restart after changing config.env | `sudo systemctl restart nomad-life` |
| Someone lost the phone with their authenticator app | `.venv/bin/flask --app app reset-2fa someone@example.com` (from `/var/www/nomad-life`; they get an email about it) |
| Disk used by receipts | `sudo du -sh /var/www/nomad-life/uploads` |
| Check the pinned packages for known vulnerabilities | `.venv/bin/pip install pip-audit && .venv/bin/pip-audit -r requirements.txt` (the GitHub Action also does this every Monday) |
| Many "Too many attempts" in the log | Somebody is guessing passwords; the limits hold them. With fail2ban you can also block the IP. |

## 15. Checklist

- [ ] `https://nomad.example.com` opens with a valid certificate and `http://` redirects to it
- [ ] `config.env`: `APP_BASE_URL` is https, `PROXY_COUNT=1`, Gmail set, `ADMIN_EMAILS` set, file mode 600
- [ ] `systemctl status nomad-life` is active and starts again after `sudo reboot`
- [ ] Sign up works and the confirmation email arrives
- [ ] Two-factor sign in is on for every admin; `timedatectl` says the clock is synchronized
- [ ] Uploading a receipt works, and downloading it gives a ZIP
- [ ] `ufw status` shows only SSH and Nginx Full
- [ ] The nightly backup runs and a copy leaves the server; a restore was tried once
- [ ] Unattended upgrades are on
