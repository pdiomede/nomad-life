"""Nomad Life: track your yearly movements and keep your receipts in one place."""
import hashlib
import math
import mimetypes
import os
import re
import secrets
import threading
import time
from urllib.parse import quote, urlencode
import uuid
from collections import OrderedDict
import unicodedata
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import (Flask, Response, abort, current_app, flash, redirect, render_template,
                   request, send_from_directory, session, url_for)
from markupsafe import escape
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_url, login_user, logout_user)
from flask_wtf.csrf import CSRFError, CSRFProtect
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.routing import IntegerConverter
from werkzeug.security import check_password_hash, generate_password_hash

import db
import package
from countries import COUNTRIES, COUNTRY_CODES, COUNTRY_DATA, flag_emoji
from countries_geo import COUNTRY_POINTS
from mailer import LOGO_CID, send_email

APP_VERSION = "1.2.7"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Content types are derived from the extension, never from the browser.
MIME_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "heic": "image/heic",
    "webp": "image/webp",
}
ALLOWED_EXTENSIONS = set(MIME_TYPES)
def fold(value):
    """Search key: no accents, no case, single spaces ("Côte d’Ivoire" -> "cote d'ivoire")."""
    value = unicodedata.normalize("NFKD", value.replace("\u2019", "'"))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.casefold().split())


# Canonical names win over codes, and codes over aliases, when keys collide.
COUNTRY_LOOKUP = {}
for _pass in (0, 1, 2):
    for _name, _code, _aliases in COUNTRY_DATA:
        for _key in ((_name,), (_code,), _aliases)[_pass]:
            COUNTRY_LOOKUP.setdefault(fold(_key), _name)
DOCUMENT_KINDS = OrderedDict([
    ("rental_contract", "Rental contract"),
    ("accommodation", "Hotel / home rent"),
    ("flight", "Flight ticket"),
    ("other", "Other"),
])
RESIDENCE_THRESHOLD = 183
# Plans, cheapest first. Free takes its limits from config.env (USER_QUOTA_MB, MAX_RECEIPT_MB);
# a paid plan is never below Free. There is no payment yet: a plan changes in the database.
PLANS = OrderedDict([
    # "year" is the price for a year paid at once (two months free); "package" is the
    # accountant package, from Pro on.
    ("free", {"name": "Free", "price": 0, "year": 0, "quota_mb": None, "receipt_mb": None,
              "package": False, "extras": []}),
    ("pro", {"name": "Pro", "price": 4, "year": 40, "quota_mb": 5 * 1024, "receipt_mb": 25,
             "package": True, "extras": ["Accountant package (PDF, spreadsheets, receipts)"]}),
    ("plus", {"name": "Nomad+", "price": 9, "year": 90, "quota_mb": 25 * 1024, "receipt_mb": 50,
              "package": True, "extras": ["Priority support"]}),
])
PLAN_CURRENCY, PLAN_SYMBOL = "USD", "$"
MAX_PRICE_CENTS = 1_000_000  # $10,000, a sanity cap for prices typed on the admin page
PLAN_FEATURES = ["Unlimited years and stays", "Days per country with the 183 day line"]
NOTES_MAX = 5000
UPLOAD_GONE = "This stay or year no longer exists, so the file was not saved."
MOVEMENTS_PER_PAGE = 10
PER_PAGE_OPTIONS = (10, 25, 50)
SITE_TITLE = "Nomad Life | Every day. Every place."
SITE_DESCRIPTION = ("Track the days you spend in each country and keep your travel receipts safe, "
                    "all in one place. Made for digital nomads.")
SHARE_TEXT = "Track the days you spend in each country and keep your travel receipts safe."
# Search result snippet for the landing page (title under 60 and description under 160 characters).
SEO_TITLE = "Digital nomad day tracker and receipt vault | Nomad Life"
SEO_DESCRIPTION = ("Count your days in each country, watch the 183 day line and keep rental contracts, "
                   "hotel bills and flight tickets in one place. Free for digital nomads.")
# Paths that need an account. Kept out of search engines via robots.txt.
PRIVATE_PATHS = ["/app", "/year/", "/movements/", "/documents/", "/reset/", "/verify/", "/admin",
                 "/account", "/plan"]
ADMIN_USERS_PER_PAGE = 25
MAX_QUOTA_MB = 1024 * 1024  # 1 TB, a sanity cap for quotas typed on the admin page
RESET_TOKEN_MAX_AGE = 3600
# A new account must be confirmed from its email within this time, or it is deleted.
VERIFY_MINUTES = 20
PURGE_EVERY_SECONDS = 60
# At most one email per account in this time, so sign up, sign in or "forgot password" cannot
# be used to flood someone's inbox (and get the Gmail account blocked for spam).
EMAIL_COOLDOWN_SECONDS = 60
# Password guessing limits: failed sign ins (or wrong current passwords on the account page)
# within LIMIT_WINDOW_MINUTES, per account and per IP address, and password reset requests
# per IP address.
LIMIT_WINDOW_MINUTES = 15
# A browser that signed in before carries a device cookie: its failures count per device
# instead of per account, so strangers failing on purpose cannot lock the owner out.
LIMITS = {("fail", "email"): 5, ("fail", "device"): 5, ("fail", "ip"): 20,
          ("forgot", "ip"): 5, ("signup", "ip"): 10}
DEVICE_COOKIE = "nomadlife_device"
DEVICE_MAX_AGE = 365 * 24 * 3600
# Sign up and sign in hash passwords with scrypt (about 32 MB of memory each), so only a few
# may run at once.
HASH_SLOTS = threading.BoundedSemaphore(4)
# Place names and the number of stays per year are capped so one account cannot fill the
# disk or make its dashboard slow for everyone.
PLACE_MAX = 100
MOVEMENTS_MAX = 1000
# Content-Security-Policy of every HTML page. Scripts only from static/js (no inline scripts or
# on* attributes anywhere); inline style attributes stay allowed for the meters and map pins.
CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
EMAIL_CHANGE_MAX_AGE = 3600

# config.env is the source of truth, as the README says: it overrides variables that happen to
# be exported in the shell (SECRET_KEY or DATABASE_PATH from another project, for example).
load_dotenv(os.path.join(BASE_DIR, "config.env"), override=True)
mimetypes.add_type("application/manifest+json", ".webmanifest")


def _abs(path):
    path = os.path.expanduser(path)  # "~/NomadReceipts" is the home folder, not a folder named ~
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def valid_email(email):
    """Pragmatic address check: one @, no spaces or brackets, a dotted domain with a real TLD."""
    if len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not local or any(ch.isspace() or ch in '<>()[],;:"' for ch in email):
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(labels) and len(labels[-1]) >= 2


def password_fingerprint(password_hash):
    """Short digest of the password hash. Changes whenever the password changes."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.email = row["email"]
        self.fingerprint = password_fingerprint(row["password_hash"])
        self.version = row["session_version"] if "session_version" in row.keys() else 0
        self.plan = row["plan"] if "plan" in row.keys() and row["plan"] in PLANS else "free"

    @property
    def is_admin(self):
        from flask import current_app
        return self.email in current_app.config["ADMIN_EMAILS"]

    def get_id(self):
        # Binding the session to the password means a reset signs out every other session; the
        # version (only added once raised, so older sessions keep working) does the same when
        # an account is disabled.
        base = f"{self.id}:{self.fingerprint}"
        return f"{base}:{self.version}" if self.version else base


def load_or_create_secret(path):
    """Return a persistent random secret so sessions never use a guessable key."""
    try:
        with open(path) as fh:
            key = fh.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key)
    return key


MB = 1024 * 1024


def mb_setting(name, default, legacy=None):
    """Read a size in megabytes from the environment and return bytes.
    Fails fast with a clear message instead of running with a wrong limit."""
    raw = os.getenv(name)
    if (raw is None or not raw.strip()) and legacy and os.getenv(legacy, "").strip():
        name, raw = legacy, os.getenv(legacy)  # report errors against the key the user wrote
    raw = (raw or "").strip() or str(default)
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(f"config.env: {name} must be a number of megabytes (got '{raw}').")
    if not value > 0 or value != value or value == float("inf"):
        raise SystemExit(f"config.env: {name} must be greater than 0 (got '{raw}').")
    # Round up: int(1.2 * MB) is one byte short of 1.2 MB and would be shown as "1.1 MB".
    return math.ceil(value * MB)


def proxy_count():
    raw = (os.getenv("PROXY_COUNT") or "").strip() or "0"
    if not raw.isdigit() or int(raw) > 10:
        raise SystemExit(f"config.env: PROXY_COUNT must be a number from 0 to 10 (got '{raw}').")
    return int(raw)


def admin_emails(raw):
    return frozenset(e.strip().lower() for e in raw.replace(";", ",").split(",") if e.strip())


def port_setting():
    """APP_PORT from config.env; empty means the default, anything else must be a real port."""
    raw = (os.getenv("APP_PORT") or "").strip() or "5050"
    try:
        port = int(raw)
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise SystemExit(f"config.env: APP_PORT must be a number between 1 and 65535 (got '{raw}').")
    return port


class DbIntConverter(IntegerConverter):
    """<int:...> URL parts larger than SQLite can store are a plain 404, not a 500."""
    def __init__(self, url_map, *args, **kwargs):
        kwargs.setdefault("max", 2 ** 63 - 1)
        super().__init__(url_map, *args, **kwargs)


def safe_next(value):
    """Only same site paths are allowed after sign in. Browsers and URL parsers drop tabs and
    newlines, so "/<tab>/evil.com" would become "//evil.com": refuse any control character."""
    if (value.startswith("/") and not value.startswith("//") and "\\" not in value
            and value.isprintable()):
        return value
    return None


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "").strip(),
        APP_PORT=port_setting(),
        APP_BASE_URL=os.getenv("APP_BASE_URL", ""),
        GMAIL_USER=os.getenv("GMAIL_USER", ""),
        # Accounts that may open /admin. Only config.env can grant it, never the app itself.
        ADMIN_EMAILS=admin_emails(os.getenv("ADMIN_EMAILS", "")),
        # Reverse proxies in front of the app (0 when it is reached directly). The sign in
        # limits per IP address need the visitor's address, not the proxy's.
        PROXY_COUNT=proxy_count(),
        GMAIL_APP_PASSWORD=os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", ""),
        DATABASE_PATH=_abs(os.getenv("DATABASE_PATH", "data/nomad.db")),
        UPLOAD_DIR=_abs(os.getenv("UPLOAD_DIR", "uploads")),
        # MAX_UPLOAD_MB is the pre 0.0.4 name of MAX_RECEIPT_MB.
        MAX_RECEIPT_BYTES=mb_setting("MAX_RECEIPT_MB", 10, legacy="MAX_UPLOAD_MB"),
        USER_QUOTA_BYTES=mb_setting("USER_QUOTA_MB", 500),
        # Cookies are shared by every app on localhost regardless of port, so use unique names.
        # Static files are cached for a week; CSS and JS URLs carry ?v=<version> so releases
        # are picked up at once. Receipts override this with no-store.
        SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=7),
        SESSION_COOKIE_NAME="nomadlife_session",
        REMEMBER_COOKIE_NAME="nomadlife_remember",
        SESSION_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SAMESITE="Lax",
        # "Remember me" lasts a month instead of Flask-Login's year, so a copied cookie does
        # not stay useful for long; "Sign out everywhere" on the account page ends it at once.
        REMEMBER_COOKIE_DURATION=timedelta(days=30),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        # Form tokens stay valid as long as the session, instead of expiring after an hour
        # (which made "Sign out" on a page left open fail while the user stayed signed in).
        WTF_CSRF_TIME_LIMIT=None,
    )
    if overrides:
        app.config.update(overrides)
    # Reject oversized request bodies before reading them. The extra megabyte leaves room for
    # the other form fields; the exact per receipt limit of the user's plan is checked in
    # save_upload.
    app.config["MAX_CONTENT_LENGTH"] = max(p["receipt_bytes"]
                                           for p in plan_catalog(app.config)) + MB

    db.init_db(app.config["DATABASE_PATH"])
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    app.teardown_appcontext(db.close_db)
    with app.app_context():
        purge_unverified()

    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = load_or_create_secret(
            os.path.join(os.path.dirname(app.config["DATABASE_PATH"]), ".secret_key"))
    if not app.config["APP_BASE_URL"]:
        # Never build reset links from the request Host header.
        app.config["APP_BASE_URL"] = f"http://localhost:{app.config['APP_PORT']}"
    if app.config["APP_BASE_URL"].lower().startswith("https://"):
        # Served over HTTPS: never send the sign in cookies over plain HTTP.
        app.config["SESSION_COOKIE_SECURE"] = app.config["REMEMBER_COOKIE_SECURE"] = True
    app.url_map.converters["int"] = DbIntConverter
    if app.config["PROXY_COUNT"]:
        n = app.config["PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    login_manager = LoginManager(app)
    login_manager.login_view = "login"
    login_manager.login_message_category = "info"

    @login_manager.unauthorized_handler
    def unauthorized():
        flash(login_manager.login_message, login_manager.login_message_category)
        # The redirect after sign in is a GET, so a form post (Delete, Save, Sign out) must not
        # become `next`: it would end on a "405 Method Not Allowed" page.
        if request.method in ("GET", "HEAD"):
            return redirect(login_url("login", next_url=request.url))
        return redirect(url_for("login"))

    @login_manager.user_loader
    def load_user(session_id):
        uid, _, rest = session_id.partition(":")
        fingerprint, _, version = rest.partition(":")
        row = db.query("SELECT * FROM users WHERE id = ? AND verified_at IS NOT NULL", (uid,),
                       one=True)
        if row is None or password_fingerprint(row["password_hash"]) != fingerprint:
            return None
        if str(row["session_version"]) != (version or "0"):  # signed out by an admin
            return None
        if row["disabled"]:  # disabling an account ends its open sessions too
            return None
        return User(row)

    # Unconfirmed accounts are removed once their time is up. The auth pages purge on every
    # request, so a late link or sign in never sees them; this hook cleans up in between.
    last_purge = [0.0]

    @app.before_request
    def request_size_limit():
        # MAX_CONTENT_LENGTH fits the largest plan; each request gets its sender's own cap, so
        # a Free user's oversized upload is still refused before the server reads it.
        request.max_content_length = receipt_limit() + MB

    # Registered after the size hook on purpose: the CSRF check reads the form, and before
    # request hooks run in order, so the per plan cap must already be in place by then.
    CSRFProtect(app)

    @app.before_request
    def purge_now_and_then():
        if time.monotonic() - last_purge[0] >= PURGE_EVERY_SECONDS:
            last_purge[0] = time.monotonic()
            purge_unverified()

    @app.after_request
    def security_headers(resp):
        # No page may be framed (clickjacking), sniffed into another type, or leak its path to
        # other sites. Signed in pages hold private data: never keep them in a cache.
        resp.headers.setdefault("X-Frame-Options", "DENY")
        # Pages may only run the app's own script files: even if some text ever escaped the
        # template escaping, an injected <script> or onerror= would not run. Receipts (PDFs
        # and images) only get frame-ancestors, as a full policy breaks Chrome's PDF viewer.
        resp.headers.setdefault("Content-Security-Policy", CSP if resp.mimetype == "text/html"
                                else "frame-ancestors 'none'")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if current_user.is_authenticated and request.endpoint != "static":
            resp.headers["Cache-Control"] = "private, no-store"
        return resp

    # CSS and JS are cached for a week, so their URLs change whenever their content does,
    # also between releases.
    digest = hashlib.sha256()
    for name in ("css/style.css", "js/theme.js", "js/theme-init.js"):
        with open(os.path.join(app.static_folder, name), "rb") as fh:
            digest.update(fh.read())
    asset_version = digest.hexdigest()[:10]

    @app.context_processor
    def inject_globals():
        return {"app_version": APP_VERSION, "asset_version": asset_version, "countries": COUNTRIES, "country_data": COUNTRY_DATA,
                "document_kinds": DOCUMENT_KINDS,
                "max_receipt_bytes": receipt_limit(),
                "max_receipt_label": format_size(receipt_limit(), "down"),
                "current_plan": current_plan() if current_user.is_authenticated else None,
                "next_plan_name": (next_plan(current_user.plan) or {}).get("name")
                if current_user.is_authenticated else None,
                "storage": storage_summary(current_user.id)
                if current_user.is_authenticated else None,
                "site": site_meta()}

    app.add_template_filter(parse_date, "todate")
    app.add_template_filter(format_size, "filesize")
    app.add_template_filter(local_date, "localdate")
    app.add_template_filter(country_flag, "flag")

    @app.errorhandler(413)
    def too_large(_e):
        # Also raised for oversized text fields, so do not claim a file was sent.
        flash(f"This form was too large to save. Receipts can be at most "
              f"{format_size(receipt_limit(), 'down')} each.", "error")
        return redirect(request.referrer or url_for("index"))

    @app.errorhandler(CSRFError)
    def csrf_error(_e):
        if request.endpoint == "logout":
            # Never leave someone signed in because the page they signed out from was stale.
            logout_user()
            flash("You have been signed out.", "info")
            return redirect(url_for("login"))
        flash("Your session expired. Please try again.", "error")
        return redirect(request.referrer or url_for("index"))

    @app.errorhandler(404)
    def not_found(_e):
        # Standalone page, so a web server in front of the app can serve the same file.
        resp = send_from_directory(app.static_folder, "404.html", max_age=0)
        resp.status_code = 404
        return resp

    register_routes(app)
    return app


# ---------- helpers ----------

def serializer(salt="password-reset"):
    """Signed tokens for emailed links. Each kind of link has its own salt, so a password
    reset link can never confirm an account and a confirmation link can never reset one."""
    from flask import current_app
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def purge_unverified():
    """Delete accounts that were not confirmed within VERIFY_MINUTES. An account that holds
    data is never one of them (it can only come from a server running an older version), so it
    is kept rather than deleted with everything in it."""
    # A disabled account is kept too, so a new sign up cannot undo an admin's decision.
    db.execute("DELETE FROM users WHERE verified_at IS NULL AND disabled = 0 AND "
               "created_at <= datetime('now', ?) "
               "AND NOT EXISTS (SELECT 1 FROM years WHERE years.user_id = users.id)",
               (f"-{VERIFY_MINUTES} minutes",))


def email_allowed(user_id):
    """Claim the right to email this account now. False if an email went out less than
    EMAIL_COOLDOWN_SECONDS ago."""
    with db.transaction() as conn:
        cur = conn.execute("UPDATE users SET email_sent_at = CURRENT_TIMESTAMP WHERE id = ? AND "
                           "(email_sent_at IS NULL OR email_sent_at <= datetime('now', ?))",
                           (user_id, f"-{EMAIL_COOLDOWN_SECONDS} seconds"))
        return cur.rowcount == 1


def client_ip():
    return request.remote_addr or "unknown"


def limit_keys(kind, email=None, device=None):
    keys = [("ip", f"ip:{client_ip()}")]
    if device:
        keys.append(("device", f"device:{device}"))
    elif email:
        keys.append(("email", f"email:{email}"))
    return [(scope, key) for scope, key in keys if (kind, scope) in LIMITS]


def take_attempt(kind, email=None, device=None):
    """Count this attempt against the limits, or refuse it. Returns (minutes to wait, scope
    that hit its limit, ids of the rows recorded). Checking and recording happen under one
    write lock, so requests sent in parallel cannot all pass the check before any is counted."""
    window = f"-{LIMIT_WINDOW_MINUTES} minutes"
    keys = limit_keys(kind, email, device)
    with db.transaction() as conn:
        conn.execute("DELETE FROM auth_events WHERE created_at <= datetime('now', ?)", (window,))
        wait, hit = 0, None
        for scope, key in keys:
            limit = LIMITS[(kind, scope)]
            rows = conn.execute("SELECT created_at FROM auth_events WHERE kind = ? AND key = ? "
                                "ORDER BY created_at DESC LIMIT ?", (kind, key, limit)).fetchall()
            if len(rows) >= limit:
                # Free again when the oldest of the last N attempts leaves the window.
                oldest = datetime.strptime(rows[-1]["created_at"], "%Y-%m-%d %H:%M:%S")
                free_at = (oldest.replace(tzinfo=timezone.utc)
                           + timedelta(minutes=LIMIT_WINDOW_MINUTES))
                left = (free_at - datetime.now(timezone.utc)).total_seconds()
                minutes = max(math.ceil(left / 60), 1)
                if minutes > wait:
                    wait, hit = minutes, scope
        if wait:
            return wait, hit, []
        ids = [conn.execute("INSERT INTO auth_events (kind, key) VALUES (?, ?)",
                            (kind, key)).lastrowid for _scope, key in keys]
    return 0, None, ids


def forgive(ids, email, device=None):
    """A right password: drop this attempt and the account's (or device's) earlier failures."""
    if ids:
        db.execute(f"DELETE FROM auth_events WHERE id IN ({','.join('?' * len(ids))})", ids)
    db.execute("DELETE FROM auth_events WHERE kind = 'fail' AND key IN (?, ?)",
               (f"email:{email}", f"device:{device}"))


_dummy_hash = []


def hash_password(password):
    with HASH_SLOTS:
        return generate_password_hash(password)


def password_matches(pw_hash, password):
    """Check a password. Without a stored hash (an unknown email), check against a dummy hash
    anyway, so the answer takes as long as for a real account and does not reveal it."""
    if not pw_hash:
        if not _dummy_hash:
            _dummy_hash.append(hash_password(secrets.token_hex(16)))
        pw_hash = _dummy_hash[0]
        with HASH_SLOTS:
            check_password_hash(pw_hash, password)
        return False
    with HASH_SLOTS:
        return check_password_hash(pw_hash, password)


def device_for(email):
    """The device id of this browser, if its device cookie was issued for this email."""
    raw = request.cookies.get(DEVICE_COOKIE)
    if not raw or not email:
        return None
    try:
        data = serializer("device").loads(raw, max_age=DEVICE_MAX_AGE)
    except BadSignature:
        return None
    return data.get("d") if data.get("e") == email else None


def remember_device(resp, email):
    token = serializer("device").dumps({"e": email, "d": device_for(email) or uuid.uuid4().hex})
    resp.set_cookie(DEVICE_COOKIE, token, max_age=DEVICE_MAX_AGE, httponly=True,
                    samesite="Lax", secure=current_app.config.get("SESSION_COOKIE_SECURE", False))
    return resp


def too_many_message(wait, scope, what):
    who = {"ip": "from this network", "device": "on this device"}.get(scope, "for this account")
    return f"Too many {what} {who}. Please wait {plural(wait, 'minute')} and try again."


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def verify_deadline(created_at):
    created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return created + timedelta(minutes=VERIFY_MINUTES)


def minutes_left(row):
    """Whole minutes before an unconfirmed account is deleted (at least 1)."""
    left = verify_deadline(row["created_at"]) - datetime.now(timezone.utc)
    return max(math.ceil(left.total_seconds() / 60), 1)


def email_link(endpoint, **values):
    from flask import current_app
    return current_app.config["APP_BASE_URL"].rstrip("/") + url_for(endpoint, **values)


def send_template_email(to, subject, name, **context):
    """Render templates/email/<name>.txt and .html and send them as one email."""
    from flask import current_app
    site = current_app.config["APP_BASE_URL"].rstrip("/") + "/"
    context.update(subject=subject, email=to, site_url=site, logo_cid=LOGO_CID,
                   site_host=site.split("://", 1)[-1].rstrip("/"))
    text = render_template(f"email/{name}.txt", **context)
    html = render_template(f"email/{name}.html", **context)
    return send_email(to, subject, text, html)


def release_email_wait(user_id):
    """Nothing was sent, so do not make the user wait a minute before trying again."""
    db.execute("UPDATE users SET email_sent_at = NULL WHERE id = ?", (user_id,))


def send_password_link(row, template, subject):
    """Email a link to choose a new password (it also confirms a pending account)."""
    token = serializer().dumps({"uid": row["id"], "h": password_fingerprint(row["password_hash"]),
                                "e": row["email"]})
    sent = send_template_email(row["email"], subject, template,
                               link=email_link("reset", token=token),
                               hours=RESET_TOKEN_MAX_AGE // 3600)
    if not sent:
        release_email_wait(row["id"])
    return sent


def send_verification(row):
    # "c" lets a link for an account that is gone say whether it expired or was replaced.
    token = serializer("email-verify").dumps(
        {"uid": row["id"], "h": password_fingerprint(row["password_hash"]),
         "v": row["session_version"],
         # "c" lets a link for an account that is gone say whether it expired.
         "c": row["created_at"]})
    return send_template_email(row["email"], "Confirm your Nomad Life account", "verify",
                               link=email_link("verify", token=token), minutes=minutes_left(row))


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def local_date(utc_timestamp):
    """SQLite CURRENT_TIMESTAMP is UTC. Show it as a date in the machine's local time zone."""
    try:
        dt = datetime.strptime(utc_timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return utc_timestamp
    return dt.astimezone().strftime("%Y-%m-%d")


def format_size(size, rounding="nearest"):
    """Human readable size. Round capacities ("left", limits) down and offending file sizes up,
    so a message never reads like "10 MB is larger than the 10 MB limit"."""
    step = {"nearest": round, "down": math.floor, "up": math.ceil}[rounding]
    if size < 1024:
        return f"{size} B"
    kb = step(size / 1024)
    if kb < 1024:  # 1,048,300 bytes rounds to 1024 KB: show it as 1 MB instead
        return f"{kb} KB"
    mb = step(size / MB * 10) / 10
    if mb < 1024:  # likewise 1023.96 MB is shown as 1 GB, never "1024 MB"
        return f"{mb:.1f}".rstrip("0").rstrip(".") + " MB"
    return f"{step(size / (MB * 1024) * 10) / 10:.1f}".rstrip("0").rstrip(".") + " GB"


def get_year_or_404(year):
    row = db.query("SELECT * FROM years WHERE user_id = ? AND year = ?",
                   (current_user.id, year), one=True)
    if row is None:
        abort(404)
    return row


def get_movement_or_404(movement_id):
    row = db.query(
        "SELECT m.*, y.year FROM movements m JOIN years y ON y.id = m.year_id "
        "WHERE m.id = ? AND y.user_id = ?", (movement_id, current_user.id), one=True)
    if row is None:
        abort(404)
    return row


def documents_for(year_id, movement_id=None):
    if movement_id is None:
        return db.query("SELECT * FROM documents WHERE year_id = ? AND movement_id IS NULL "
                        "ORDER BY uploaded_at DESC", (year_id,))
    return db.query("SELECT * FROM documents WHERE movement_id = ? ORDER BY uploaded_at DESC",
                    (movement_id,))


def file_ext(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def display_name(filename, limit=150):
    """Human readable file name. Keeps non ASCII letters (the file is stored under a random
    name), drops any client side folder path and invisible control characters."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = " ".join(name.split())
    if len(name) > limit:
        # Keep a real extension; text after a dot in "Booking No. ..." is not one.
        ext = file_ext(name)
        name = (name[:limit - len(ext) - 1].rstrip() + "." + ext if ext in ALLOWED_EXTENSIONS
                else name[:limit])
    return name or "file"


def site_meta():
    """Absolute URLs for link previews and share buttons. Social networks fetch these from
    APP_BASE_URL, so it must be the public address for previews to work."""
    from flask import current_app
    base = current_app.config["APP_BASE_URL"].rstrip("/")
    url = base + url_for("landing")
    q = lambda v: quote(v, safe="")
    links = [
        ("X", "https://twitter.com/intent/tweet?" + urlencode({"text": SHARE_TEXT, "url": url})),
        ("LinkedIn", "https://www.linkedin.com/sharing/share-offsite/?url=" + q(url)),
        ("Facebook", "https://www.facebook.com/sharer/sharer.php?u=" + q(url)),
        ("WhatsApp", "https://wa.me/?text=" + q(f"{SHARE_TEXT} {url}")),
        ("Telegram", "https://t.me/share/url?" + urlencode({"url": url, "text": SHARE_TEXT})),
        ("Reddit", "https://www.reddit.com/submit?" + urlencode({"url": url, "title": SITE_TITLE})),
    ]
    image = base + url_for("static", filename="img/og-image.jpg")
    structured_data = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "@id": url + "#website", "url": url, "name": "Nomad Life",
             "description": SITE_DESCRIPTION, "inLanguage": "en"},
            {"@type": "WebApplication", "@id": url + "#app", "name": "Nomad Life", "url": url,
             "description": SITE_DESCRIPTION, "image": image,
             "applicationCategory": "TravelApplication", "operatingSystem": "Any",
             "browserRequirements": "Requires a modern web browser",
             "isAccessibleForFree": True,
             "offers": [{"@type": "Offer", "name": p["name"], "price": p["price"],
                         "priceCurrency": PLAN_CURRENCY, "url": url + "#pricing"}
                        for p in plan_catalog()],
             "featureList": ["Days per country for each solar year",
                             "183 day indicator for your base country",
                             "Receipt storage for rental contracts, hotel bills and flight tickets",
                             "Private workspace for each user"],
             "author": {"@type": "Person", "name": "Paolo Diomede", "url": "https://pdiomede.com"},
             "isPartOf": {"@id": url + "#website"}},
        ],
    }
    return {"title": SITE_TITLE, "description": SITE_DESCRIPTION, "share_text": SHARE_TEXT,
            "seo_title": SEO_TITLE, "seo_description": SEO_DESCRIPTION, "base": base,
            "url": url, "image": image, "share_links": links, "structured_data": structured_data}


def storage_used(user_id):
    row = db.query("SELECT COALESCE(SUM(size), 0) AS used FROM documents WHERE user_id = ?",
                   (user_id,), one=True)
    return row["used"]


def money(cents):
    """Price for people: 400 -> "4", 450 -> "4.50"."""
    return str(cents // 100) if cents % 100 == 0 else f"{cents / 100:.2f}"


def parse_price(raw):
    """Cents from what an admin typed ("4", "4.5", "$4.50"), or None when it is not a price."""
    raw = raw.strip().removeprefix(PLAN_SYMBOL).strip().replace(",", ".")
    if not re.fullmatch(r"\d{1,6}(\.\d{1,2})?", raw):
        return None
    whole, _, frac = raw.partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def price_overrides():
    """Prices set on the admin page, {plan: (month_cents, year_cents)}, read once a request."""
    from flask import g, has_app_context
    if not has_app_context():
        return {}
    if "plan_prices" not in g:
        g.plan_prices = {r["plan"]: (r["month_cents"], r["year_cents"])
                         for r in db.query("SELECT * FROM plan_prices")}
    return g.plan_prices


def plan_catalog(config=None):
    """Every plan with its limits in bytes and the feature list shown to people. Free lists
    everything; a paid plan lists only what it adds to the plan before it ("includes")."""
    from flask import current_app
    # With a config passed (app startup) only the limits matter: no database yet.
    overrides = {} if config else price_overrides()
    config = config or current_app.config
    free_quota, free_receipt = config["USER_QUOTA_BYTES"], config["MAX_RECEIPT_BYTES"]
    plans = []
    for key, p in PLANS.items():
        quota = max(p["quota_mb"] * MB, free_quota) if p["quota_mb"] else free_quota
        receipt = max(p["receipt_mb"] * MB, free_receipt) if p["receipt_mb"] else free_receipt
        prev = plans[-1] if plans else None
        storage = f"{format_size(quota, 'down')} of receipt storage"
        if prev and quota // prev["quota_bytes"] >= 2:
            storage += f" ({quota // prev['quota_bytes']}x {prev['name']})"
        limits = [storage, f"Receipts up to {format_size(receipt, 'down')} each"]
        month_cents, year_cents = (p["price"] * 100, p["year"] * 100) if key == "free" else \
            overrides.get(key, (p["price"] * 100, p["year"] * 100))
        saving = max(month_cents * 12 - year_cents, 0)
        plans.append({"key": key, "name": p["name"], "price_cents": month_cents,
                      "year_cents": year_cents, "price": money(month_cents),
                      "year": money(year_cents), "year_saving": money(saving) if saving else "",
                      "default_price": money(p["price"] * 100), "default_year": money(p["year"] * 100),
                      "custom_price": key in overrides,
                      "package": p["package"],
                      "quota_bytes": quota, "receipt_bytes": receipt,
                      "includes": prev["name"] if prev else None,
                      "features": limits + p["extras"] if prev else limits + PLAN_FEATURES})
    return plans


def plan_named(key):
    plans = plan_catalog()
    return next((p for p in plans if p["key"] == key), plans[0])


def next_plan(key):
    """The plan after this one, or None on the top plan."""
    keys = list(PLANS)
    i = keys.index(key) if key in keys else 0
    return plan_named(keys[i + 1]) if i + 1 < len(keys) else None


def current_plan():
    return plan_named(current_user.plan if current_user.is_authenticated else "free")


def receipt_limit():
    return current_plan()["receipt_bytes"]


def user_quota(user_id):
    """Storage quota in bytes: the one set on the admin page, else the one of the plan."""
    row = db.query("SELECT quota_bytes, plan FROM users WHERE id = ?", (user_id,), one=True)
    if row is not None and row["quota_bytes"] is not None:
        return row["quota_bytes"]
    return plan_named(row["plan"] if row is not None else "free")["quota_bytes"]


def storage_summary(user_id):
    quota = user_quota(user_id)
    used = storage_used(user_id)
    left = max(quota - used, 0)
    return {"used": used, "quota": quota, "left": left, "over": used > quota,
            "full": left == 0,
            "pct": min(round(used * 100 / quota, 1), 100) if quota else 100,
            "low": left < quota * 0.1}


def quota_message(name, size, left, quota):
    return (f"Not enough storage left for {name} ({format_size(size, 'up')}): "
            f"{format_size(left, 'down')} free of {format_size(quota, 'down')}.")


def upload_size(file):
    """Size of an uploaded file, measured on the spooled upload before it is saved anywhere."""
    stream = file.stream
    pos = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(pos)
    return size


def save_upload(file, kind, year_id, movement_id=None, required=True):
    """Store an uploaded file. Returns an error message or None."""
    from flask import current_app
    if not file or not file.filename:
        return "Please choose a file to upload." if required else None
    name = display_name(file.filename)
    ext = file_ext(name)
    if ext not in ALLOWED_EXTENSIONS:
        return "Unsupported file type. Allowed: " + ", ".join(sorted(ALLOWED_EXTENSIONS))
    if kind not in DOCUMENT_KINDS:
        kind = "other"

    # Check both limits before anything is written to the uploads folder.
    size = upload_size(file)
    limit = receipt_limit()
    quota = user_quota(current_user.id)
    if size == 0:
        return "The file is empty. Please choose another file."
    if size > limit:
        return (f"{name} is {format_size(size, 'up')}, larger than the "
                f"{format_size(limit, 'down')} limit per receipt.")
    left = max(quota - storage_used(current_user.id), 0)
    if size > left:
        return quota_message(name, size, left, quota)

    user_dir = os.path.join(current_app.config["UPLOAD_DIR"], str(current_user.id))
    os.makedirs(user_dir, mode=0o700, exist_ok=True)
    stored = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(user_dir, stored)
    try:
        file.save(path)
    except OSError as exc:
        current_app.logger.error("Could not store upload %s: %s", path, exc)
        if os.path.exists(path):
            os.remove(path)  # do not keep a partial file that no quota accounts for
        return "The server could not store the file (its disk may be full). Please try again later."
    size = os.path.getsize(path)
    try:
        # Re-check the quota and insert under one write lock, so parallel uploads cannot
        # both pass the check above and exceed the quota together.
        with db.transaction() as conn:
            used = conn.execute("SELECT COALESCE(SUM(size), 0) FROM documents WHERE user_id = ?",
                                (current_user.id,)).fetchone()[0]
            if used + size > quota:
                raise QuotaExceeded(max(quota - used, 0))
            conn.execute(
                "INSERT INTO documents (user_id, year_id, movement_id, kind, original_name, "
                "stored_name, mime, size) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (current_user.id, year_id, movement_id, kind, name, stored,
                 MIME_TYPES[ext], size))
    except QuotaExceeded as exc:
        os.remove(path)
        return quota_message(name, size, exc.left, quota)
    except db.IntegrityError:
        os.remove(path)  # the stay or year was deleted while the file was uploading
        return UPLOAD_GONE
    except BaseException:
        os.remove(path)  # no orphan file when the row could not be saved
        raise
    return None


class QuotaExceeded(Exception):
    def __init__(self, left):
        super().__init__(left)
        self.left = left


def delete_account(user_id):
    """Delete an account with all its years, movements and documents, then its files."""
    from flask import current_app
    with db.transaction() as conn:  # list and delete together, see the year delete
        docs = conn.execute("SELECT * FROM documents WHERE user_id = ?", (user_id,)).fetchall()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    remove_files(docs)
    try:
        os.rmdir(os.path.join(current_app.config["UPLOAD_DIR"], str(user_id)))
    except OSError:
        pass  # missing, or holds files no row points to: leave them for the operator


def remove_files(rows):
    """Delete stored files. Call only after the matching rows are deleted and committed."""
    from flask import current_app
    for doc in rows:
        path = os.path.join(current_app.config["UPLOAD_DIR"], str(doc["user_id"]),
                            doc["stored_name"])
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def normalize_country(value):
    """Collapse spacing and map names, aliases and ISO codes to the canonical country name.
    Unknown places (free text) are kept as typed."""
    value = " ".join(value.split())
    return COUNTRY_LOOKUP.get(fold(value), value)


def country_flag(name):
    """Emoji flag for a stored country name, or an empty string for unknown places."""
    return flag_emoji(COUNTRY_CODES.get(normalize_country(name or "")))


def map_pins(year_row, movements, stats=None):
    """Pins for the dashboard map: one per country (base first), with its cities and its days
    from compute_stats (the same numbers as "Days per country"). Places the map cannot show
    (free text, or no point for the country) are returned apart."""
    stats = stats or compute_stats(year_row, movements)
    days = {fold(r["country"]): r["days"] for r in stats["rows"]}
    pins, missing, seen = OrderedDict(), [], set()
    places = [(year_row["base_country"], year_row["base_city"], True)]
    places += [(m["country"], m["city"], False) for m in movements]
    for country, city, is_base in places:
        name = normalize_country(country)
        code = COUNTRY_CODES.get(name)
        point = COUNTRY_POINTS.get(code)
        if point is None:
            # Spellings that Days per country counts as one place are listed once.
            if ("missing", fold(name)) not in seen:
                seen.add(("missing", fold(name)))
                missing.append(name)
            continue
        pin = pins.setdefault(code, {"country": name, "flag": flag_emoji(code), "cities": [],
                                     "days": days.get(fold(name), 0),
                                     "is_base": False, "left": point[0], "top": point[1]})
        pin["is_base"] = pin["is_base"] or is_base
        if (code, fold(city)) not in seen:  # "Lisbon" and "lisbon" are one
            seen.add((code, fold(city)))
            pin["cities"].append(city)
    for pin in pins.values():
        # Keep the tooltip inside the map near its edges.
        pin["side"] = "right" if pin["left"] < 18 else "left" if pin["left"] > 82 else ""
        pin["below"] = pin["top"] < 22
    # Stays on top of the base, so a trip is never hidden under the bigger base pin.
    return sorted(pins.values(), key=lambda p: not p["is_base"]), missing


def delete_confirmed():
    """Deletes must come from the two step confirmation dialog, which sets confirm_delete=2."""
    if request.form.get("confirm_delete") == "2":
        return True
    flash("Please confirm the deletion twice.", "error")
    return False


def validate_movement(form, year, current_notes=None):
    city = " ".join(form.get("city", "").split())
    country = normalize_country(form.get("country", ""))
    start = parse_date(form.get("start_date"))
    end = parse_date(form.get("end_date"))
    if not city or not country:
        return None, "Country and city are required."
    if len(city) > PLACE_MAX or len(country) > PLACE_MAX:
        return None, f"Country and city can be at most {PLACE_MAX} characters."
    if not start or not end:
        return None, "Please provide valid start and end dates."
    if end < start:
        return None, "The end date must be on or after the start date."
    if start.year != year or end.year != year:
        return None, f"Dates must fall within {year}."
    # Browsers send each line break as CRLF but count it as one character for maxlength.
    notes = form.get("notes", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    # Notes saved before the limit existed can stay as they are while other fields change.
    if len(notes) > NOTES_MAX and notes != (current_notes or "").replace("\r\n", "\n").strip():
        return None, f"Notes can be at most {NOTES_MAX} characters."
    return {"city": city, "country": country, "start_date": start.isoformat(),
            "end_date": end.isoformat(), "notes": notes}, None


def stay_length(m):
    """Calendar days of a stay, counting both the first and the last day."""
    return (parse_date(m["end_date"]) - parse_date(m["start_date"])).days + 1


def stay_order(movements):
    """Start date ascending, then longer stays first, then oldest entry first."""
    ordered = sorted(movements, key=lambda r: r["id"])
    ordered.sort(key=lambda r: r["end_date"], reverse=True)
    ordered.sort(key=lambda r: r["start_date"])
    return ordered


def overlap_notes(year_id, data, exclude_id=None):
    """Describe stays that overlap the given one by more than a single travel day."""
    rows = db.query("SELECT * FROM movements WHERE year_id = ? AND id IS NOT ? "
                    "AND start_date <= ? AND end_date >= ?",
                    (year_id, exclude_id, data["end_date"], data["start_date"]))
    notes = []
    for r in rows:
        shared = (min(parse_date(r["end_date"]), parse_date(data["end_date"]))
                  - max(parse_date(r["start_date"]), parse_date(data["start_date"]))).days + 1
        if shared > 1:
            notes.append(f"{r['city']} ({r['start_date']} to {r['end_date']}, {shared} shared days)")
    return notes


def flash_overlaps(year_id, data, movement_id):
    notes = overlap_notes(year_id, data, movement_id)
    if notes:
        flash("This stay overlaps " + "; ".join(notes) + ". A shared day counts toward the stay "
              "that started later (on the same start day, the shorter stay; with identical dates, "
              "the newest), so check the dates if that is not intended.", "info")


def to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def per_page_arg(value):
    """Allowed page sizes only; anything else falls back to the default."""
    n = to_int(value, MOVEMENTS_PER_PAGE)
    return n if n in PER_PAGE_OPTIONS else MOVEMENTS_PER_PAGE


def page_size():
    """Movements per page: a ?per_page choice is remembered for the session, so opening a
    movement and coming back (or deleting it) keeps the page size."""
    if "per_page" in request.args:
        session["per_page"] = per_page_arg(request.args.get("per_page"))
    return per_page_arg(session.get("per_page"))


def page_window(page, pages):
    """Page numbers to show, with None for a gap: 1 ... 4 5 6 ... 20. Short lists show every
    page, and a gap of a single page shows that number instead of an ellipsis."""
    if pages <= 7:
        return list(range(1, pages + 1))
    keep = {1, pages, page - 1, page, page + 1}
    if page <= 4:
        keep |= set(range(1, 6))
    if page >= pages - 3:
        keep |= set(range(pages - 4, pages + 1))
    out, prev = [], 0
    for n in sorted(k for k in keep if 1 <= k <= pages):
        if n - prev == 2:
            out.append(prev + 1)
        elif n - prev > 2:
            out.append(None)
        out.append(n)
        prev = n
    return out


def paginate(items, page, per_page, url):
    """Slice a list for display. Invalid or out of range pages are clamped, never an error.
    `url(page, per_page)` builds the link for a page."""
    total = len(items)
    pages = max(1, -(-total // per_page))
    page = min(max(to_int(page, 1), 1), pages)
    start = (page - 1) * per_page
    return {
        "rows": items[start:start + per_page], "page": page, "pages": pages, "total": total,
        "per_page": per_page, "first": start + 1 if total else 0,
        "last": min(start + per_page, total),
        "prev": page - 1 if page > 1 else None, "next": page + 1 if page < pages else None,
        "window": page_window(page, pages),
        "url": lambda n: url(n, per_page),
        # Changing the page size keeps the first visible item on screen.
        "size_url": lambda size: url(start // size + 1, size),
    }


def movement_page(year_id, movement_id, per_page=MOVEMENTS_PER_PAGE):
    """Dashboard page that shows a movement, using the dashboard's ordering."""
    ids = [r["id"] for r in db.query("SELECT id FROM movements WHERE year_id = ? "
                                     "ORDER BY start_date, end_date DESC, id", (year_id,))]
    return ids.index(movement_id) // per_page + 1 if movement_id in ids else 1


def dashboard_url(year, page=1, per_page=MOVEMENTS_PER_PAGE, anchor=True):
    """Dashboard link to a page of the movements table. Links land on the table; redirects
    after an action pass anchor=False so the flash message at the top stays in view. The page
    size is left out only when the dashboard would pick it anyway."""
    remembered = per_page_arg(session.get("per_page"))
    explicit = per_page != MOVEMENTS_PER_PAGE or per_page != remembered
    return url_for("dashboard", year=year, page=page if page > 1 else None,
                   per_page=per_page if explicit else None,
                   _anchor="movements" if anchor else None)


def back_to_movement(year_row, movement_id):
    """Dashboard link to the page of the movements table that shows a movement."""
    per_page = page_size()
    return dashboard_url(year_row["year"], movement_page(year_row["id"], movement_id, per_page),
                         per_page)


def can_download_package(user):
    """Who may download the annual accountant package: plans from Pro on."""
    return plan_named(user.plan)["package"]


def build_package_data(year_row, include_notes):
    """Collect everything the accountant package shows, using the same queries and helpers as
    the dashboard so the numbers always match the screen."""
    from flask import current_app
    y = year_row["year"]
    period = package.Period(date(y, 1, 1), date(y, 12, 31), str(y))
    movements = db.query("SELECT * FROM movements WHERE year_id = ? "
                         "ORDER BY start_date, end_date DESC, id", (year_row["id"],))
    stats = compute_stats(year_row, movements)
    today = date.today()
    status = "past" if today > period.end else "future" if today < period.start else "current"
    iso = lambda name: COUNTRY_CODES.get(normalize_country(name), "")
    base_name = normalize_country(year_row["base_country"])
    base_row = next((r for r in stats["rows"] if r["is_base"]), None)
    base_so_far = base_row["so_far"] if base_row else 0

    stays = []
    for i, m in enumerate(movements, start=1):
        stays.append(package.Stay(
            number=i, start=m["start_date"], end=m["end_date"], city=m["city"],
            country=normalize_country(m["country"]), iso=iso(m["country"]),
            calendar_days=stay_length(m), counted_days=stats["counted"].get(m["id"], 0),
            notes=m["notes"] or ""))
    stay_by_movement = {m["id"]: s for m, s in zip(movements, stays)}

    receipts = []
    docs = db.query("SELECT * FROM documents WHERE year_id = ? "
                    "ORDER BY movement_id IS NOT NULL, uploaded_at, id", (year_row["id"],))
    folder = os.path.join(current_app.config["UPLOAD_DIR"], str(year_row["user_id"]))
    for d in docs:
        stay = stay_by_movement.get(d["movement_id"])
        if stay:
            stay.receipts += 1
        path = os.path.join(folder, d["stored_name"])
        exists = os.path.isfile(path)
        receipts.append(package.Receipt(
            stay_number=stay.number if stay else 0,
            kind=DOCUMENT_KINDS.get(d["kind"], "Other"), name=d["original_name"],
            ext=file_ext(d["stored_name"]), path=path, uploaded=local_date(d["uploaded_at"]),
            size=os.path.getsize(path) if exists else d["size"], exists=exists))

    countries = [package.CountryTotal(country=r["country"], iso=iso(r["country"]), days=r["days"],
                                      share=r["pct"], so_far=r["so_far"], is_base=r["is_base"])
                 for r in stats["rows"]]
    return package.PackageData(
        period=period, base_city=year_row["base_city"], base_country=base_name,
        base_iso=iso(base_name), generated_at=datetime.now(), app_version=APP_VERSION,
        status=status, as_of=today, total_days=stats["total"], elapsed=stats["elapsed"],
        base_days=stats["base_days"], abroad_days=stats["abroad_days"], base_so_far=base_so_far,
        abroad_so_far=stats["elapsed"] - base_so_far, threshold=stats["threshold"],
        base_ok=stats["base_ok"], countries=countries, stays=stays, receipts=receipts,
        include_notes=include_notes, upcoming_days=stats["upcoming"])


def compute_stats(year_row, movements):
    """Count days per country. Past days not covered by a movement count as base."""
    y = year_row["year"]
    first, last = date(y, 1, 1), date(y, 12, 31)
    total = (last - first).days + 1
    base_name = normalize_country(year_row["base_country"])
    base = fold(base_name)
    # Countries are compared like the country search: case, accents, apostrophes and Unicode
    # forms are ignored, so "portugal", "Transnístria" and "Hawai’i" match their plain spelling.
    names = {base: base_name}
    assigned = {}
    assigned_mv = {}
    # A shared day belongs to the stay that started later (the arrival). On the same start
    # day the shorter stay wins, so a side trip is never hidden by the longer stay around it.
    for m in stay_order(movements):
        name = normalize_country(m["country"])
        key = fold(name)
        names.setdefault(key, name)
        d, end = parse_date(m["start_date"]), parse_date(m["end_date"])
        while d <= end:
            assigned[d] = key
            assigned_mv[d] = m["id"]
            d += timedelta(days=1)

    today = date.today()
    elapsed_end = min(today, last) if today >= first else first - timedelta(days=1)

    counted = {}  # days each movement actually contributes after overlaps
    for mid in assigned_mv.values():
        counted[mid] = counted.get(mid, 0) + 1

    # Logged stays count in full, also future ones. A day without a stay counts toward the
    # base country only once it has passed: nobody knows yet where the rest of the year goes.
    per_country = {base: 0}
    per_country_so_far = {}
    d = first
    while d <= last:
        c = assigned.get(d, base if d <= elapsed_end else None)
        if c is not None:
            per_country[c] = per_country.get(c, 0) + 1
            if d <= elapsed_end:
                per_country_so_far[c] = per_country_so_far.get(c, 0) + 1
        d += timedelta(days=1)

    rows = [{"country": names[c], "days": n, "so_far": per_country_so_far.get(c, 0),
             "pct": round(n * 100 / total, 1), "is_base": c == base,
             "over_threshold": n >= RESIDENCE_THRESHOLD}
            for c, n in per_country.items()]
    rows.sort(key=lambda r: (-r["days"], r["country"]))
    base_days = per_country[base]
    counted_days = sum(per_country.values())
    return {"total": total, "rows": rows, "base_days": base_days, "counted": counted,
            "abroad_days": counted_days - base_days, "upcoming": total - counted_days,
            "threshold": RESIDENCE_THRESHOLD,
            "base_ok": base_days >= RESIDENCE_THRESHOLD,
            "elapsed": max((elapsed_end - first).days + 1, 0)}


# ---------- routes ----------

def register_routes(app):

    # --- auth ---

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        if request.method == "POST":
            purge_unverified()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            if not valid_email(email):
                flash("Please enter a valid email address.", "error")
            elif len(password) < 8:
                flash("Password must be at least 8 characters.", "error")
            elif password != confirm:
                flash("Passwords do not match.", "error")
            else:
                wait, scope, _attempt = take_attempt("signup")
                if wait:
                    flash(too_many_message(wait, scope, "sign ups"), "error")
                    return render_template("auth/signup.html"), 429
                # The same answer whether or not the address has an account, so sign up cannot
                # be used to find out who uses Nomad Life. The mailbox learns the rest.
                sent = (f"We sent an email to {email}. Open the link in it within "
                        f"{VERIFY_MINUTES} minutes to continue.")
                old = db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)
                if not old:
                    pw_hash = hash_password(password)  # only for a new account: it is costly
                    with db.transaction() as conn:
                        old = conn.execute("SELECT * FROM users WHERE email = ?",
                                           (email,)).fetchone()
                        if not old:
                            uid = conn.execute(
                                "INSERT INTO users (email, password_hash, email_sent_at) "
                                "VALUES (?, ?, CURRENT_TIMESTAMP)", (email, pw_hash)).lastrowid
                if old and (old["verified_at"] is not None or old["disabled"]):
                    if not old["disabled"] and email_allowed(old["id"]):
                        if not send_template_email(email, "You already have a Nomad Life account",
                                                   "account_exists", email=email,
                                                   link=email_link("login"),
                                                   forgot_link=email_link("forgot")):
                            release_email_wait(old["id"])
                    flash(sent, "info")
                    return redirect(url_for("login"))
                if old:
                    # A sign up for this address is already waiting. Never replace it: whoever
                    # signs up again could be a stranger choosing the password. Instead the
                    # mailbox gets a link to choose the password, which also confirms.
                    if not email_allowed(old["id"]):
                        flash(sent, "info")
                        return redirect(url_for("login"))
                    # Earlier confirmation links stop working: only the mailbox owner, through
                    # the link below, decides the password now.
                    db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?",
                               (old["id"],))
                    old = db.query("SELECT * FROM users WHERE id = ?", (old["id"],), one=True)
                    if not send_password_link(old, "finish_signup",
                                              "Finish setting up your Nomad Life account"):
                        flash("We could not send the email. Please try again in a few minutes.",
                              "error")
                        return render_template("auth/signup.html")
                    flash(sent, "info")
                    return redirect(url_for("login"))
                row = db.query("SELECT * FROM users WHERE id = ?", (uid,), one=True)
                if not send_verification(row):
                    db.execute("DELETE FROM users WHERE id = ? AND verified_at IS NULL", (uid,))
                    flash("We could not send the confirmation email. Please try again in a few "
                          "minutes.", "error")
                    return render_template("auth/signup.html")
                flash(sent, "info")
                return redirect(url_for("login"))
        return render_template("auth/signup.html")

    @app.route("/verify/<token>")
    def verify(token):
        purge_unverified()
        try:
            data = serializer("email-verify").loads(token, max_age=VERIFY_MINUTES * 60)
        except SignatureExpired:
            flash("This confirmation link has expired, so the account was removed. Please sign "
                  "up again.", "error")
            return redirect(url_for("signup"))
        except BadSignature:
            flash("This confirmation link is not valid.", "error")
            return redirect(url_for("signup"))
        row = db.query("SELECT * FROM users WHERE id = ?", (data.get("uid"),), one=True)
        signed_in = current_user.is_authenticated
        if row and row["verified_at"] is not None:
            if signed_in and current_user.id == row["id"]:
                flash("Your email is already confirmed.", "info")
                return redirect(url_for("index"))
            flash(f"{row['email']} is already confirmed. "
                  + ("To use it, sign out and sign in with it." if signed_in else "Please sign in."),
                  "info")
            return redirect(url_for("index" if signed_in else "login"))
        if not row and data.get("c"):
            try:
                expired = verify_deadline(data["c"]) <= datetime.now(timezone.utc)
            except (TypeError, ValueError):
                expired = False
            if expired:
                flash("This confirmation link has expired, so the account was removed. Please "
                      "sign up again.", "error")
                return redirect(url_for("signup"))
        if row and row["disabled"]:
            flash("This account is disabled. Please contact the administrator.", "error")
            return redirect(url_for("login"))
        # A later sign up attempt for this address raises the version, so a confirmation link
        # sent before it (possibly for a stranger's password) stops working.
        if (not row or password_fingerprint(row["password_hash"]) != data.get("h")
                or data.get("v", 0) != row["session_version"]):
            flash("This confirmation link is no longer valid. If you signed up more than once, "
                  "use the link in the newest email; otherwise, sign up again.", "error")
            return redirect(url_for("signup"))
        db.execute("UPDATE users SET verified_at = CURRENT_TIMESTAMP WHERE id = ? AND "
                   "verified_at IS NULL", (row["id"],))
        if signed_in:  # confirmed from a browser signed in to another account
            flash(f"{row['email']} is confirmed. To use that account, sign out and sign in "
                  "with it.", "success")
            return redirect(url_for("index"))
        flash("Your email is confirmed. Please sign in to start.", "success")
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        if request.method == "POST":
            purge_unverified()
            email = request.form.get("email", "").strip().lower()
            device = device_for(email)
            wait, scope, attempt = take_attempt("fail", email, device)
            if wait:
                flash(too_many_message(wait, scope, "failed sign in attempts")
                      + ("" if scope == "ip" else " You can also reset your password."), "error")
                return render_template("auth/login.html"), 429
            row = db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)
            if password_matches(row["password_hash"] if row else None,
                                request.form.get("password", "")):
                forgive(attempt, email, device)
                if row["disabled"]:
                    flash("This account is disabled. Please contact the administrator.", "error")
                    return render_template("auth/login.html")
                if row["verified_at"] is None and row["session_version"]:
                    # Someone signed up again for this address, so the password on this row may
                    # be a stranger's: only the mailbox's choose-a-password link can activate it.
                    flash(f"Please use the link we emailed to {email} to choose your password "
                          "and activate the account.", "info")
                    return render_template("auth/login.html")
                if row["verified_at"] is None:
                    if not email_allowed(row["id"]):
                        flash(f"Please confirm your email first, with the link we sent to {email} "
                              "a moment ago.", "info")
                    elif send_verification(row):
                        flash(f"Please confirm your email first. We sent a new link to {email}; "
                              f"it works for {plural(minutes_left(row), 'more minute')}.", "info")
                    else:
                        release_email_wait(row["id"])
                        flash("Please confirm your email first, with the link we sent when you "
                              "signed up.", "error")
                    return render_template("auth/login.html")
                db.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
                           (row["id"],))
                login_user(User(row), remember=bool(request.form.get("remember")))
                return remember_device(
                    redirect(safe_next(request.args.get("next", "")) or url_for("index")), email)
            flash("Invalid email or password.", "error")
        return render_template("auth/login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        logout_user()
        flash("You have been signed out.", "info")
        return redirect(url_for("login"))

    @app.route("/forgot", methods=["GET", "POST"])
    def forgot():
        if request.method == "POST":
            purge_unverified()
            email = request.form.get("email", "").strip().lower()
            wait, scope, _attempt = take_attempt("forgot")
            if wait:
                flash(too_many_message(wait, scope, "reset requests"), "error")
                return render_template("auth/forgot.html"), 429
            row = db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)
            if row and not row["disabled"] and email_allowed(row["id"]):
                send_password_link(row, "reset", "Reset your Nomad Life password")
            flash("If that email is registered, a reset link is on its way.", "info")
            return redirect(url_for("login"))
        return render_template("auth/forgot.html")

    @app.route("/reset/<token>", methods=["GET", "POST"])
    def reset(token):
        purge_unverified()
        try:
            data = serializer().loads(token, max_age=RESET_TOKEN_MAX_AGE)
        except SignatureExpired:
            flash("This reset link has expired. Please request a new one.", "error")
            return redirect(url_for("forgot"))
        except BadSignature:
            flash("This reset link is not valid.", "error")
            return redirect(url_for("forgot"))
        row = db.query("SELECT * FROM users WHERE id = ?", (data.get("uid"),), one=True)
        if not row:
            flash("This reset link is no longer valid.", "error")
            return redirect(url_for("forgot"))
        if password_fingerprint(row["password_hash"]) != data.get("h"):
            flash("This reset link has already been used.", "error")
            return redirect(url_for("forgot"))
        if data.get("e") != row["email"]:  # the email changed since: the old mailbox has no say
            flash("This reset link is no longer valid. Please request a new one.", "error")
            return redirect(url_for("forgot"))
        if row["disabled"]:
            flash("This account is disabled. Please contact the administrator.", "error")
            return redirect(url_for("login"))
        if request.method == "POST":
            password = request.form.get("password", "")
            if len(password) < 8:
                flash("Password must be at least 8 characters.", "error")
            elif password != request.form.get("confirm", ""):
                flash("Passwords do not match.", "error")
            else:
                # The link came by email, so it also confirms an account still waiting for it.
                db.execute("UPDATE users SET password_hash = ?, verified_at = COALESCE(verified_at, "
                           "CURRENT_TIMESTAMP) WHERE id = ?",
                           (hash_password(password), row["id"]))
                # Proving the mailbox ends a lock from wrong guesses (by anyone) on this account.
                db.execute("DELETE FROM auth_events WHERE kind = 'fail' AND key = ?",
                           (f"email:{row['email']}",))
                flash("Your password has been updated. Please sign in.", "success")
                return redirect(url_for("login"))
        return render_template("auth/reset.html", token=token)

    # --- workspace ---

    @app.route("/")
    def landing():
        return render_template("landing.html", plans=plan_catalog())

    @app.route("/robots.txt")
    def robots_txt():
        base = app.config["APP_BASE_URL"].rstrip("/")
        lines = ["User-agent: *", "Allow: /"] + [f"Disallow: {p}" for p in PRIVATE_PATHS]
        lines += ["", f"Sitemap: {base}{url_for('sitemap_xml')}", ""]
        return Response("\n".join(lines), mimetype="text/plain")

    @app.route("/sitemap.xml")
    def sitemap_xml():
        base = app.config["APP_BASE_URL"].rstrip("/")
        landing_file = os.path.join(app.root_path, app.template_folder, "landing.html")
        lastmod = date.fromtimestamp(os.path.getmtime(landing_file)).isoformat()
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               f"  <url><loc>{escape(base + url_for('landing'))}</loc><lastmod>{lastmod}</lastmod>"
               "<changefreq>monthly</changefreq><priority>1.0</priority></url>\n"
               "</urlset>\n")
        return Response(xml, mimetype="application/xml")

    @app.route("/app")
    @login_required
    def index():
        years = db.query("SELECT year FROM years WHERE user_id = ? ORDER BY year DESC",
                         (current_user.id,))
        if not years:
            return redirect(url_for("new_year"))
        wanted = date.today().year
        pick = wanted if any(r["year"] == wanted for r in years) else years[0]["year"]
        return redirect(url_for("dashboard", year=pick))

    @app.route("/plan")
    @login_required
    def plan():
        mine = current_plan()
        return render_template("plan.html", plan=mine, upgrade=next_plan(mine["key"]))

    @app.route("/year/new", methods=["GET", "POST"])
    @login_required
    def new_year():
        if request.method == "POST":
            try:
                year = int(request.form.get("year", ""))
            except ValueError:
                year = 0
            city = " ".join(request.form.get("base_city", "").split())
            country = normalize_country(request.form.get("base_country", ""))
            if not 1970 <= year <= 2100:
                flash("Please enter a valid year.", "error")
            elif not city or not country:
                flash("Base country and city are required.", "error")
            elif len(city) > PLACE_MAX or len(country) > PLACE_MAX:
                flash(f"Base country and city can be at most {PLACE_MAX} characters.", "error")
            else:
                try:
                    db.execute("INSERT INTO years (user_id, year, base_city, base_country) "
                               "VALUES (?, ?, ?, ?)", (current_user.id, year, city, country))
                except db.IntegrityError:  # UNIQUE (user_id, year)
                    flash(f"You already have a workspace for {year}.", "error")
                else:
                    flash(f"Year {year} created with base in {city}, {country}.", "success")
                    return redirect(url_for("dashboard", year=year))
        return render_template("year_new.html", default_year=date.today().year)

    @app.route("/year/<int:year>")
    @login_required
    def dashboard(year):
        year_row = get_year_or_404(year)
        movements = db.query(
            "SELECT m.*, (SELECT COUNT(*) FROM documents d WHERE d.movement_id = m.id) AS doc_count "
            "FROM movements m WHERE m.year_id = ? ORDER BY m.start_date, m.end_date DESC, m.id",
            (year_row["id"],))
        years = db.query("SELECT year FROM years WHERE user_id = ? ORDER BY year DESC",
                         (current_user.id,))
        base_docs = documents_for(year_row["id"])
        # Stats always use every movement; only the table is paginated.
        pager = paginate(movements, request.args.get("page"), page_size(),
                         lambda n, size: dashboard_url(year, n, size))
        stats = compute_stats(year_row, movements)
        return render_template("dashboard.html", y=year_row, movements=movements, pager=pager,
                               per_page_options=PER_PAGE_OPTIONS,
                               years=years, stats=stats,
                               base_docs=base_docs, map=map_pins(year_row, movements, stats),
                               today=date.today().isoformat())

    @app.route("/year/<int:year>/base", methods=["GET", "POST"])
    @login_required
    def base(year):
        year_row = get_year_or_404(year)
        if request.method == "POST":
            action = request.form.get("action")
            if action == "update":
                city = " ".join(request.form.get("base_city", "").split())
                country = normalize_country(request.form.get("base_country", ""))
                err = ("Base country and city are required." if not city or not country else
                       f"Base country and city can be at most {PLACE_MAX} characters."
                       if len(city) > PLACE_MAX or len(country) > PLACE_MAX else None)
                if err:
                    flash(err, "error")
                    return render_template("base_location.html", y=year_row, form=request.form,
                                           docs=documents_for(year_row["id"]))
                else:
                    db.execute("UPDATE years SET base_city = ?, base_country = ? WHERE id = ?",
                               (city, country, year_row["id"]))
                    flash("Base location updated.", "success")
            elif action == "upload":
                err = save_upload(request.files.get("file"), request.form.get("kind"),
                                  year_row["id"])
                flash(err or "Document uploaded.", "error" if err else "success")
                if err == UPLOAD_GONE:  # this page is gone too, so do not send the user back to it
                    return redirect(url_for("index"))
            elif action == "delete_year":
                if not delete_confirmed():
                    pass
                elif request.form.get("confirm_year", "").strip() != str(year):
                    flash("Type the year to confirm deletion.", "error")
                else:
                    # List and delete under one write lock, so an upload finishing in
                    # between cannot leave a file that no row points to.
                    with db.transaction() as conn:
                        docs = conn.execute("SELECT * FROM documents WHERE year_id = ?",
                                            (year_row["id"],)).fetchall()
                        conn.execute("DELETE FROM years WHERE id = ?", (year_row["id"],))
                    remove_files(docs)
                    flash(f"Year {year} deleted.", "info")
                    return redirect(url_for("index"))
            return redirect(url_for("base", year=year))
        return render_template("base_location.html", y=year_row, docs=documents_for(year_row["id"]))

    @app.route("/year/<int:year>/movements/new", methods=["GET", "POST"])
    @login_required
    def movement_new(year):
        year_row = get_year_or_404(year)
        form = request.form if request.method == "POST" else {}
        if request.method == "POST":
            data, err = validate_movement(request.form, year)
            count = db.query("SELECT COUNT(*) AS n FROM movements WHERE year_id = ?",
                             (year_row["id"],), one=True)["n"]
            if not err and count >= MOVEMENTS_MAX:
                err = f"A year can hold at most {MOVEMENTS_MAX} movements."
            if err:
                flash(err, "error")
            else:
                try:
                    mid = db.execute(
                        "INSERT INTO movements (year_id, city, country, start_date, end_date, "
                        "notes) VALUES (?, ?, ?, ?, ?, ?)",
                        (year_row["id"], data["city"], data["country"], data["start_date"],
                         data["end_date"], data["notes"]))
                except db.IntegrityError:  # the year was deleted meanwhile (another tab)
                    flash(f"The year {year} no longer exists, so the movement was not saved.",
                          "error")
                    return redirect(url_for("index"))
                err = save_upload(request.files.get("file"), request.form.get("kind"),
                                  year_row["id"], mid, required=False)
                if err:
                    flash(err, "error")
                flash(f"Movement to {data['city']} added.", "success")
                flash_overlaps(year_row["id"], data, mid)
                return redirect(url_for("movement_edit", movement_id=mid))
        return render_template("movement.html", y=year_row, m=None, form=form, docs=[])

    @app.route("/movements/<int:movement_id>", methods=["GET", "POST"])
    @login_required
    def movement_edit(movement_id):
        m = get_movement_or_404(movement_id)
        year_row = get_year_or_404(m["year"])
        if request.method == "POST":
            action = request.form.get("action")
            if action == "update":
                data, err = validate_movement(request.form, m["year"], m["notes"])
                if err:
                    # Re-render so the user keeps what they typed.
                    flash(err, "error")
                    return render_template("movement.html", y=year_row, m=m, form=request.form,
                                           docs=documents_for(year_row["id"], movement_id),
                                           back_url=back_to_movement(year_row, movement_id))
                else:
                    db.execute("UPDATE movements SET city = ?, country = ?, start_date = ?, "
                               "end_date = ?, notes = ? WHERE id = ?",
                               (data["city"], data["country"], data["start_date"],
                                data["end_date"], data["notes"], movement_id))
                    if not db.query("SELECT 1 FROM movements WHERE id = ?", (movement_id,),
                                    one=True):  # deleted meanwhile, in another tab
                        flash("This movement no longer exists, so the changes were not saved.",
                              "error")
                        return redirect(url_for("index"))
                    flash("Movement updated.", "success")
                    flash_overlaps(year_row["id"], data, movement_id)
            elif action == "upload":
                err = save_upload(request.files.get("file"), request.form.get("kind"),
                                  year_row["id"], movement_id)
                flash(err or "Document uploaded.", "error" if err else "success")
                if err == UPLOAD_GONE:  # see the base page
                    return redirect(url_for("index"))
            elif action == "delete":
                if not delete_confirmed():
                    return redirect(request.referrer or url_for("movement_edit", movement_id=movement_id))
                # Return to the dashboard page the user was on (dashboard rows send it), or to
                # the page that showed this movement; the dashboard clamps it if it is now empty.
                per_page = page_size()
                page = to_int(request.form.get("page"), 0) or movement_page(
                    year_row["id"], movement_id, per_page)
                with db.transaction() as conn:  # see the year delete
                    docs = conn.execute("SELECT * FROM documents WHERE movement_id = ?",
                                        (movement_id,)).fetchall()
                    conn.execute("DELETE FROM movements WHERE id = ?", (movement_id,))
                remove_files(docs)
                flash("Movement deleted.", "info")
                return redirect(dashboard_url(m["year"], page, per_page, anchor=False))
            return redirect(url_for("movement_edit", movement_id=movement_id))
        return render_template("movement.html", y=year_row, m=m, form=m,
                               docs=documents_for(year_row["id"], movement_id),
                               back_url=back_to_movement(year_row, movement_id))

    # --- account ---

    def check_current_password(row):
        """Wrong current passwords count as failed sign ins, so the account page cannot be
        used to guess a password either. Returns an error message or None."""
        device = device_for(row["email"])
        wait, scope, attempt = take_attempt("fail", row["email"], device)
        if wait:
            return too_many_message(wait, scope, "wrong passwords")
        if not password_matches(row["password_hash"], request.form.get("current_password", "")):
            return "Your current password is not correct."
        forgive(attempt, row["email"], device)
        return None

    @app.route("/account", methods=["GET", "POST"])
    @login_required
    def account():
        row = db.query("SELECT * FROM users WHERE id = ?", (current_user.id,), one=True)
        if request.method == "POST":
            action = request.form.get("action")
            if action == "cancel_email":  # stopping a change never needs the password
                if row["pending_email"]:
                    db.execute("UPDATE users SET pending_email = NULL WHERE id = ?", (row["id"],))
                    flash(f"The change to {row['pending_email']} was cancelled. The link we sent "
                          "no longer works.", "success")
                return redirect(url_for("account"))
            if action == "sessions":  # ending other sessions only takes access away
                db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?",
                           (row["id"],))
                remember = app.config["REMEMBER_COOKIE_NAME"] in request.cookies
                login_user(User(db.query("SELECT * FROM users WHERE id = ?", (row["id"],),
                                         one=True)), remember=remember)
                flash("Every other browser and device has been signed out.", "success")
                return redirect(url_for("account"))
            err = check_current_password(row)
            if action == "password":
                password = request.form.get("password", "")
                if err:
                    flash(err, "error")
                elif len(password) < 8:
                    flash("Password must be at least 8 characters.", "error")
                elif password != request.form.get("confirm", ""):
                    flash("Passwords do not match.", "error")
                elif password_matches(row["password_hash"], password):
                    flash("The new password is the same as the current one.", "error")
                else:
                    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                               (hash_password(password), row["id"]))
                    # The new password changes the session fingerprint: stay signed in here,
                    # every other browser and remember cookie is signed out.
                    remember = app.config["REMEMBER_COOKIE_NAME"] in request.cookies
                    login_user(User(db.query("SELECT * FROM users WHERE id = ?", (row["id"],),
                                             one=True)), remember=remember)
                    flash("Your password has been changed. Other devices have been signed out.",
                          "success")
            elif action == "email":
                new = request.form.get("email", "").strip().lower()
                if err:
                    flash(err, "error")
                elif not valid_email(new):
                    flash("Please enter a valid email address.", "error")
                elif new == row["email"]:
                    if row["pending_email"]:
                        db.execute("UPDATE users SET pending_email = NULL WHERE id = ?", (row["id"],))
                        flash("Your pending email change was cancelled. The link we sent no "
                              "longer works.", "success")
                    else:
                        flash("That is already your email address.", "error")
                # An address used by another account gets the link too (confirming it then
                # explains the problem), so this form cannot tell who has an account.
                elif not email_allowed(row["id"]):
                    flash("We just sent you an email. Please wait a minute and try again.", "info")
                else:
                    # Only the newest request can be confirmed, and only while the email is
                    # still the one it started from, so an older link (to a mistyped address,
                    # say) can never take the account.
                    db.execute("UPDATE users SET pending_email = ? WHERE id = ?", (new, row["id"]))
                    token = serializer("email-change").dumps(
                        {"uid": row["id"], "email": new, "old": row["email"],
                         "h": password_fingerprint(row["password_hash"])})
                    if send_template_email(new, "Confirm your new Nomad Life email", "change_email",
                                           link=email_link("confirm_email", token=token),
                                           old_email=row["email"],
                                           hours=EMAIL_CHANGE_MAX_AGE // 3600):
                        flash(f"We sent a confirmation link to {new}. Your email changes when you "
                              "open it.", "info")
                    else:
                        # Nothing went out, so do not make the user wait for a minute.
                        db.execute("UPDATE users SET email_sent_at = NULL, pending_email = NULL "
                                   "WHERE id = ?", (row["id"],))
                        flash("We could not send the confirmation email. Please try again later.",
                              "error")
            elif action == "delete":
                if not delete_confirmed():
                    pass
                elif request.form.get("confirm_email", "").strip().lower() != row["email"]:
                    flash("Type your email address to confirm the deletion.", "error")
                elif err:
                    flash(err, "error")
                else:
                    delete_account(row["id"])
                    logout_user()
                    flash("Your account and all its data have been deleted.", "info")
                    return redirect(url_for("landing"))
            return redirect(url_for("account"))
        storage = storage_summary(row["id"])
        counts = db.query("SELECT (SELECT COUNT(*) FROM years WHERE user_id = ?) AS years, "
                          "(SELECT COUNT(*) FROM documents WHERE user_id = ?) AS documents",
                          (row["id"], row["id"]), one=True)
        return render_template("account.html", row=row, counts=counts, account_storage=storage)

    @app.route("/account/email/<token>")
    def confirm_email(token):
        try:
            data = serializer("email-change").loads(token, max_age=EMAIL_CHANGE_MAX_AGE)
        except SignatureExpired:
            flash("This link has expired. Please ask for the email change again.", "error")
            return redirect(url_for("account"))
        except BadSignature:
            flash("This link is not valid.", "error")
            return redirect(url_for("account"))
        row = db.query("SELECT * FROM users WHERE id = ?", (data.get("uid"),), one=True)
        new = data.get("email", "")
        if row and row["email"] == new:
            flash("This email address is already confirmed.", "info")
            return redirect(url_for("account") if current_user.is_authenticated else url_for("login"))
        if row and row["disabled"]:
            flash("This account is disabled. Please contact the administrator.", "error")
            return redirect(url_for("login"))
        # Stale when the password changed, the email changed since, or a newer change was asked.
        if (not row or password_fingerprint(row["password_hash"]) != data.get("h")
                or row["email"] != data.get("old") or row["pending_email"] != new):
            flash("This link is no longer valid. Please ask for the email change again.", "error")
            return redirect(url_for("account") if current_user.is_authenticated else url_for("login"))
        try:
            with db.transaction() as conn:
                # Opening the link proves this mailbox is theirs: an unconfirmed sign up with
                # the same address (anyone can start one) gives way, as it does on sign up.
                conn.execute("DELETE FROM users WHERE email = ? AND verified_at IS NULL AND NOT "
                             "EXISTS (SELECT 1 FROM years WHERE years.user_id = users.id)", (new,))
                conn.execute("UPDATE users SET email = ?, pending_email = NULL WHERE id = ?",
                             (new, row["id"]))
        except db.IntegrityError:
            flash("Another account started using this email address in the meantime.", "error")
            return redirect(url_for("account") if current_user.is_authenticated else url_for("login"))
        if current_user.is_authenticated and current_user.id != row["id"]:
            flash(f"That link was for another account; its email address is now {new}.", "info")
            return redirect(url_for("account"))
        flash(f"Your email address is now {new}.", "success")
        return redirect(url_for("account") if current_user.is_authenticated else url_for("login"))

    # --- admin ---

    def require_admin():
        # Same answer as for any page that is not yours: nothing to see here.
        if not current_user.is_admin:
            abort(404)

    def admin_url(page=1):
        return url_for("admin", page=page if page > 1 else None)

    @app.route("/admin")
    @login_required
    def admin():
        require_admin()
        users = db.query(
            "SELECT u.id, u.email, u.created_at, u.last_login_at, u.disabled, u.quota_bytes, "
            "u.verified_at, u.plan, "
            "(SELECT COUNT(*) FROM years y WHERE y.user_id = u.id) AS years, "
            "(SELECT COALESCE(SUM(size), 0) FROM documents d WHERE d.user_id = u.id) AS used "
            "FROM users u ORDER BY u.created_at DESC, u.id DESC")
        default_quota = app.config["USER_QUOTA_BYTES"]
        plans = {p["key"]: p for p in plan_catalog()}
        # Without a custom quota an account gets the quota of its plan.
        users = [dict(u, plan_name=plans.get(u["plan"], plans["free"])["name"],
                      plan_key=u["plan"] if u["plan"] in plans else "free",
                      quota=u["quota_bytes"] if u["quota_bytes"] is not None
                      else plans.get(u["plan"], plans["free"])["quota_bytes"],
                      quota_mb=f"{u['quota_bytes'] / MB:.1f}".removesuffix(".0")
                      if u["quota_bytes"] is not None else "",
                      is_admin=u["email"] in app.config["ADMIN_EMAILS"])
                 for u in users]
        totals = {
            "users": len(users),
            "disabled": sum(1 for u in users if u["disabled"]),
            "custom_quota": sum(1 for u in users if u["quota_bytes"] is not None),
            "storage": sum(u["used"] for u in users),
            "years": db.query("SELECT COUNT(*) AS n FROM years", one=True)["n"],
            "movements": db.query("SELECT COUNT(*) AS n FROM movements", one=True)["n"],
            "documents": db.query("SELECT COUNT(*) AS n FROM documents", one=True)["n"],
        }
        pager = paginate(users, request.args.get("page"), ADMIN_USERS_PER_PAGE,
                         lambda n, _size: admin_url(n))
        return render_template("admin.html", pager=pager, totals=totals,
                               default_quota=default_quota, plans=list(plans.values()))

    @app.route("/admin/plans/<key>", methods=["POST"])
    @login_required
    def admin_plan_price(key):
        """Monthly and yearly price of a paid plan. Both empty: back to the prices in PLANS."""
        require_admin()
        if key not in PLANS or key == "free":
            abort(404)
        name = PLANS[key]["name"]
        back = url_for("admin") + "#plans"
        raw_month, raw_year = request.form.get("month", "").strip(), request.form.get("year", "").strip()
        if not raw_month and not raw_year:
            db.execute("DELETE FROM plan_prices WHERE plan = ?", (key,))
            p = PLANS[key]
            flash(f"{name} is back to its default prices (${p['price']} / month, "
                  f"${p['year']} / year).", "success")
            return redirect(back)
        month, year = parse_price(raw_month), parse_price(raw_year)
        if not month or not year or month > MAX_PRICE_CENTS or year > MAX_PRICE_CENTS:
            flash(f"Enter both prices for {name} as amounts in dollars, for example 4 or 4.50 "
                  "(up to 10000), or leave both empty for the default.", "error")
            return redirect(back)
        db.execute("INSERT INTO plan_prices (plan, month_cents, year_cents) VALUES (?, ?, ?) "
                   "ON CONFLICT(plan) DO UPDATE SET month_cents = excluded.month_cents, "
                   "year_cents = excluded.year_cents, updated_at = CURRENT_TIMESTAMP",
                   (key, month, year))
        note = (" The yearly price is not below 12 months, so no saving is shown."
                if year >= month * 12 else "")
        flash(f"{name} now costs ${money(month)} / month and ${money(year)} / year.{note}",
              "success")
        return redirect(back)

    @app.route("/admin/users/<int:user_id>", methods=["POST"])
    @login_required
    def admin_user(user_id):
        require_admin()
        row = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
        if row is None:
            abort(404)
        back = admin_url(to_int(request.form.get("page"), 1))
        action = request.form.get("action")
        email = row["email"]
        if action in ("disable", "delete") and row["id"] == current_user.id:
            flash("You cannot disable or delete your own admin account.", "error")
        elif action == "quota":
            raw = request.form.get("quota_mb", "").strip()
            if not raw:
                db.execute("UPDATE users SET quota_bytes = NULL WHERE id = ?", (user_id,))
                flash(f"{email} now uses the storage quota of the {plan_named(row['plan'])['name']} "
                      f"plan ({format_size(user_quota(user_id), 'down')}).", "success")
            else:
                try:
                    mb = float(raw)
                except ValueError:
                    mb = -1
                if not 1 <= mb <= MAX_QUOTA_MB:
                    flash(f"Enter a storage quota between 1 and {MAX_QUOTA_MB} MB, or leave it "
                          "empty for the default.", "error")
                else:
                    quota = math.ceil(mb * MB)
                    db.execute("UPDATE users SET quota_bytes = ? WHERE id = ?", (quota, user_id))
                    note = (" They already use more than that, so new uploads are blocked until "
                            "they delete receipts.") if storage_used(user_id) > quota else ""
                    flash(f"Storage quota for {email} set to {format_size(quota, 'down')}.{note}",
                          "success")
        elif action == "plan":
            key = request.form.get("plan", "")
            if key not in PLANS:
                flash("Choose one of the plans.", "error")
            else:
                db.execute("UPDATE users SET plan = ? WHERE id = ?", (key, user_id))
                plan = plan_named(key)
                if row["quota_bytes"] is not None:
                    note = (f" Their custom storage quota ({format_size(row['quota_bytes'], 'down')})"
                            " still applies; clear it to use the plan's.")
                elif storage_used(user_id) > plan["quota_bytes"]:
                    note = (" They already use more than its storage, so new uploads are blocked "
                            "until they delete receipts.")
                else:
                    note = ""
                flash(f"{email} is now on the {plan['name']} plan "
                      f"({format_size(plan['quota_bytes'], 'down')} of storage).{note}", "success")
        elif action == "disable":
            # Also end every session and remember cookie for good, so enabling the account
            # later does not bring a stolen one back.
            db.execute("UPDATE users SET disabled = 1, session_version = session_version + 1 "
                       "WHERE id = ?", (user_id,))
            flash(f"{email} is disabled and signed out everywhere.", "info")
        elif action == "enable":
            db.execute("UPDATE users SET disabled = 0 WHERE id = ?", (user_id,))
            flash(f"{email} can sign in again.", "success")
        elif action == "delete":
            if not delete_confirmed():
                pass
            elif request.form.get("confirm_email", "").strip().lower() != email:
                flash("Type the email address to confirm the deletion.", "error")
            else:
                delete_account(user_id)
                flash(f"The account {email} and all its data were deleted.", "info")
        return redirect(back)

    # --- documents ---

    def get_document_or_404(doc_id):
        row = db.query("SELECT * FROM documents WHERE id = ? AND user_id = ?",
                       (doc_id, current_user.id), one=True)
        if row is None:
            abort(404)
        return row

    @app.route("/year/<int:year>/package")
    @login_required
    def year_package(year):
        """Annual accountant package: one ZIP streamed to the browser, built on demand and
        never stored, so it does not count against the storage quota."""
        year_row = get_year_or_404(year)
        if not can_download_package(current_user):
            flash("The accountant package is available from the Pro plan. See Upgrade on "
                  "your plan page.", "error")
            return redirect(url_for("dashboard", year=year))
        data = build_package_data(year_row, include_notes=request.args.get("notes") == "1")
        entries = package.prepare(data)
        resp = Response(package.stream(data, entries), mimetype="application/zip")
        resp.headers["Content-Disposition"] = f'attachment; filename="{package.zip_name(year)}"'
        resp.headers["Cache-Control"] = "private, no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        token = request.args.get("dl", "")
        if re.fullmatch(r"[A-Za-z0-9]{8,40}", token):
            # Lets the page know the download started, so the button can leave its busy state.
            resp.set_cookie("nl_download", token, max_age=120, samesite="Lax", path="/")
        return resp

    @app.route("/documents/<int:doc_id>")
    @login_required
    def document(doc_id):
        doc = get_document_or_404(doc_id)
        folder = os.path.join(app.config["UPLOAD_DIR"], str(current_user.id))
        mime = MIME_TYPES.get(file_ext(doc["stored_name"]), "application/octet-stream")
        resp = send_from_directory(folder, doc["stored_name"], mimetype=mime,
                                   as_attachment=request.args.get("download") == "1",
                                   download_name=doc["original_name"])
        resp.headers["X-Content-Type-Options"] = "nosniff"
        # Personal documents must never sit in a shared or browser cache.
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @app.route("/documents/<int:doc_id>/delete", methods=["POST"])
    @login_required
    def document_delete(doc_id):
        doc = get_document_or_404(doc_id)
        if delete_confirmed():
            db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            remove_files([doc])
            flash("Document deleted.", "info")
        return redirect(request.referrer or url_for("index"))

    @app.route("/documents/<int:doc_id>/edit", methods=["POST"])
    @login_required
    def document_edit(doc_id):
        doc = get_document_or_404(doc_id)
        # A typed name is not a path: keep "Rent 03/2026" instead of dropping "Rent 03/".
        typed = request.form.get("name", "").replace("/", "-").replace("\\", "-").strip()
        name = display_name(typed) if typed else ""
        kind = request.form.get("kind", "")
        ext = file_ext(doc["stored_name"])
        if not name:
            flash("Please enter a name for the document.", "error")
        elif kind not in DOCUMENT_KINDS:
            flash("Please choose a document type.", "error")
        else:
            if file_ext(name) != ext:
                name = f"{name}.{ext}"  # keep the real extension so downloads still open
            db.execute("UPDATE documents SET original_name = ?, kind = ? WHERE id = ?",
                       (name, kind, doc_id))
            flash("Document updated.", "success")
        return redirect(request.referrer or url_for("index"))


app = create_app()

if __name__ == "__main__":
    os.umask(0o077)  # the database and receipts are private to the account running the app
    app.run(host="127.0.0.1", port=app.config["APP_PORT"],
            debug=os.getenv("FLASK_DEBUG") == "1")
