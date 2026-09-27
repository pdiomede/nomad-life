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

## 3. A user and folders for the app

The app runs as its own user with no login shell, and keeps its database and receipts outside the web root, readable by that user only.

```bash
sudo adduser --system --group --home /srv/nomad-life --shell /usr/sbin/nologin nomadlife
sudo -u nomadlife git clone https://github.com/pdiomede/nomad-life.git /srv/nomad-life/app
sudo -u nomadlife mkdir -p /srv/nomad-life/data /srv/nomad-life/uploads
sudo chmod 700 /srv/nomad-life/data /srv/nomad-life/uploads
```

## 4. Install the app

```bash
cd /srv/nomad-life/app
sudo -u nomadlife python3 -m venv .venv
sudo -u nomadlife .venv/bin/pip install --upgrade pip
sudo -u nomadlife .venv/bin/pip install -r requirements.txt
sudo -u nomadlife .venv/bin/python -m unittest     # everything should end with "OK"
```

## 5. config.env for production

Create `/srv/nomad-life/app/config.env` (it is git-ignored) from the example and set at least:

```ini
APP_PORT=5050
APP_BASE_URL=https://nomad.example.com
DATABASE_PATH=/srv/nomad-life/data/nomad.db
UPLOAD_DIR=/srv/nomad-life/uploads
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
ADMIN_EMAILS=admin@example.com
PROXY_COUNT=1
PWNED_CHECK=1
MAX_RECEIPT_MB=10
USER_QUOTA_MB=500
```

```bash
sudo -u nomadlife cp config.env.example config.env && sudo -u nomadlife nano config.env
sudo chmod 600 /srv/nomad-life/app/config.env
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
User=nomadlife
Group=nomadlife
WorkingDirectory=/srv/nomad-life/app
UMask=0077
ExecStart=/srv/nomad-life/app/.venv/bin/gunicorn --preload --workers 3 --threads 4 \
          --bind 127.0.0.1:5050 --timeout 120 --no-control-socket --access-logfile - app:app
Restart=on-failure
RestartSec=3
# Hardening: the app only writes to its data and uploads folders.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/nomad-life/data /srv/nomad-life/uploads

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
- `--no-control-socket` turns off the `gunicornc` control socket (gunicorn 25.1 and newer). It would be created in the service user's home folder, which the hardening below makes read-only or hidden, so without the flag the log shows `Control server error: Permission denied`. systemd already restarts and stops the app.
- `UMask=0077` keeps the database and receipts readable by `nomadlife` only (like `umask 077` in `runWebApp.sh`).
- Workers: about 2 x CPU cores + 1. Resizing a big photo can briefly use up to about 200 MB, and each worker resizes one photo at a time, so 3 workers need up to about 600 MB on top of the app itself: take 2 GB of RAM, or use `--workers 2` on a 1 GB server.

## 7. nginx

Create `/etc/nginx/sites-available/nomad-life`:

```nginx
server {
    listen 80;
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

`scripts/backup.py` writes one archive per run with the database, its secret key and every receipt, and keeps the newest `--keep` archives. It reads the paths from `config.env`, is safe while the app runs (SQLite's online backup, half written receipts skipped) and needs nothing beyond the app's own `.venv`. Run it as the app's user, which owns the data:

```bash
sudo install -d -o nomadlife -g nomadlife -m 700 /var/backups/nomad-life
cd /srv/nomad-life/app
sudo -u nomadlife .venv/bin/python scripts/backup.py --dest /var/backups/nomad-life --keep 14
echo "30 3 * * * nomadlife /srv/nomad-life/app/.venv/bin/python /srv/nomad-life/app/scripts/backup.py --dest /var/backups/nomad-life --keep 14" | sudo tee /etc/cron.d/nomad-life-backup
```

Each run prints the archive it wrote, for example `/var/backups/nomad-life/nomadlife-2026-09-27_033000.tar.gz`, readable by `nomadlife` only (mode 600).

Then copy `/var/backups/nomad-life` elsewhere every night, encrypted: for example with `restic` or `borg` to another server or to object storage. To restore: stop the service, unpack an archive (`tar -xzf nomadlife-....tar.gz`), put `nomad.db` and `.secret_key` back in `/srv/nomad-life/data` and `uploads/` in `/srv/nomad-life/uploads` (owned by `nomadlife`, mode 700), start the service.

## 12. The first admin

1. Make sure your address is in `ADMIN_EMAILS` in `config.env` and restart: `sudo systemctl restart nomad-life`.
2. Sign up at `https://nomad.example.com/signup` and open the confirmation email.
3. Sign in, open **Settings** from the menu at the top, and turn on **two-factor sign in** (scan the QR code with Google Authenticator or a similar app). The admin page only opens once it is on.
4. The **Admin** link appears in the menu.

## 13. Upgrading

```bash
cd /srv/nomad-life/app
sudo -u nomadlife .venv/bin/python scripts/backup.py --dest /var/backups/nomad-life --keep 14   # a fresh backup first
sudo -u nomadlife git pull
sudo -u nomadlife .venv/bin/pip install -r requirements.txt
sudo systemctl restart nomad-life
```

The database is upgraded by the app on start (new columns are added automatically). Read `CHANGELOG.md` for steps a version needs:

- **1.2.12** stores receipts as ZIP files and resizes big photos. Receipts uploaded before keep working (they are zipped when downloaded), but to save the space run this once:

  ```bash
  cd /srv/nomad-life/app
  sudo -u nomadlife .venv/bin/flask --app app zip-receipts            # zip, and resize big photos
  sudo -u nomadlife .venv/bin/flask --app app zip-receipts --no-shrink  # or: zip only, keep photos
  ```

  It prints how much space was saved and can be run again safely.

## 14. Day to day

| Task | Command |
|---|---|
| See the app's log | `journalctl -u nomad-life -f` |
| Restart after changing config.env | `sudo systemctl restart nomad-life` |
| Someone lost the phone with their authenticator app | `sudo -u nomadlife .venv/bin/flask --app app reset-2fa someone@example.com` (from `/srv/nomad-life/app`; they get an email about it) |
| Disk used by receipts | `sudo du -sh /srv/nomad-life/uploads` |
| Check the pinned packages for known vulnerabilities | `sudo -u nomadlife .venv/bin/pip install pip-audit && sudo -u nomadlife .venv/bin/pip-audit -r requirements.txt` (the GitHub Action also does this every Monday) |
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
