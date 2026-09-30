"""Nomad Life: track your yearly movements and keep your receipts in one place."""
import base64
import hashlib
import hmac
import io
import math
import mimetypes
import os
import re
import secrets
import struct
import tempfile
import threading
import time
import urllib.request
from urllib.parse import quote, urlencode
import uuid
from collections import OrderedDict
import unicodedata
import zipfile
import zlib
from datetime import date, datetime, timedelta, timezone

import click
import segno
from dotenv import load_dotenv
from flask import (Flask, Response, abort, current_app, flash, has_request_context, redirect,
                   render_template, request, send_file, send_from_directory, session, url_for)
from markupsafe import escape
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_url, login_user, logout_user, user_loaded_from_cookie)
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

APP_VERSION = "1.3.1"
# "Contact Us" in the footer of every page, the landing page included (static/404.html, a
# standalone file, repeats the address).
CONTACT_EMAIL = "info@nomadlife.pro"
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
                 "/account", "/settings", "/plan", "/support"]
ADMIN_USERS_PER_PAGE = 10
MAX_QUOTA_MB = 1024 * 1024  # 1 TB, a sanity cap for quotas typed on the admin page
RESET_TOKEN_MAX_AGE = 3600
# A new account must be confirmed from its email within this time, or it is deleted.
VERIFY_MINUTES = 20
PURGE_EVERY_SECONDS = 60
# At most one email per account in this time, so sign up, sign in or "forgot password" cannot
# be used to flood someone's inbox (and get the Gmail account blocked for spam).
EMAIL_COOLDOWN_SECONDS = 60
# Password guessing limits: failed sign ins (or wrong current passwords on the Settings page)
# within LIMIT_WINDOW_MINUTES, per account and per IP address, and password reset requests
# per IP address.
LIMIT_WINDOW_MINUTES = 15
# A browser that signed in before carries a device cookie: its failures count per device
# instead of per account, so strangers failing on purpose cannot lock the owner out.
LIMITS = {("fail", "email"): 5, ("fail", "device"): 5, ("fail", "ip"): 20,
          ("forgot", "ip"): 5, ("signup", "ip"): 10,
          # Wrong two-factor codes count apart from passwords, and only a right code clears
          # them, so typing the password again never buys more guesses.
          ("code", "email"): 5, ("code", "ip"): 20,
          # Support: new tickets and messages per account.
          ("ticket", "email"): 5, ("ticket_msg", "email"): 30}
DEVICE_COOKIE = "nomadlife_device"
DEVICE_MAX_AGE = 365 * 24 * 3600
# Sign up and sign in hash passwords with scrypt (about 32 MB of memory each), so only a few
# may run at once.
HASH_SLOTS = threading.BoundedSemaphore(4)
# Place names and the number of stays per year are capped so one account cannot fill the
# disk or make its dashboard slow for everyone.
PLACE_MAX = 100
# First and last name on the Settings page, each.
NAME_MAX = 60
MOVEMENTS_MAX = 1000
# Security history kept on the admin and Settings pages.
AUDIT_DAYS = 365
ADMIN_AUDIT_ROWS = 100
# Two-factor sign in (TOTP, RFC 6238): 6 digits every 30 seconds, one step of clock drift
# either way, and this long to type the code after the password.
TOTP_STEP_SECONDS = 30
TOTP_PENDING_SECONDS = 300
# A signed in browser not seen for this long is forgotten (the cookies last 30 days).
SESSION_IDLE_DAYS = 31
# Passwords are checked against Have I Been Pwned's list of breached passwords. Only the
# first 5 characters of the password's SHA-1 leave the server (k-anonymity), and the check is
# skipped when the service does not answer in time.
PWNED_URL = "https://api.pwnedpasswords.com/range/"
PWNED_TIMEOUT_SECONDS = 3
# First bytes of each allowed receipt type, so a renamed file (an HTML page saved as .pdf,
# say) is refused. PDFs may start with a little junk before %PDF (allowed by the format).
FILE_SIGNATURES = {
    "png": lambda h: h.startswith(b"\x89PNG\r\n\x1a\n"),
    "jpg": lambda h: h.startswith(b"\xff\xd8\xff"),
    "jpeg": lambda h: h.startswith(b"\xff\xd8\xff"),
    "webp": lambda h: h[:4] == b"RIFF" and h[8:12] == b"WEBP",
    "heic": lambda h: h[4:8] == b"ftyp" and h[8:12] in (b"heic", b"heix", b"hevc", b"hevx",
                                                        b"heim", b"heis", b"mif1", b"msf1"),
    # The header may start anywhere in the first 1024 bytes, so look 4 bytes further.
    "pdf": lambda h: b"%PDF-" in h[:1028],
}
# Content-Security-Policy of every HTML page. Scripts only from static/js (no inline scripts or
# on* attributes anywhere); inline style attributes stay allowed for the meters and map pins.
CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
EMAIL_CHANGE_MAX_AGE = 3600
# How long the "undo this change" link sent to the old address works.
EMAIL_REVERT_MAX_AGE = 7 * 24 * 3600

# config.env is the source of truth, as the README says: it overrides variables that happen to
# be exported in the shell (SECRET_KEY or DATABASE_PATH from another project, for example).
load_dotenv(os.path.join(BASE_DIR, "config.env"), override=True)
mimetypes.add_type("application/manifest+json", ".webmanifest")


def _abs(path):
    path = os.path.expanduser(path)  # "~/NomadReceipts" is the home folder, not a folder named ~
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def clean_place(value):
    """A city or country as typed, with spaces collapsed and control or invisible format
    characters (such as U+202E, which reverses how text is shown) removed."""
    value = "".join(ch for ch in value or "" if unicodedata.category(ch)[0] != "C" or ch.isspace())
    return " ".join(value.split())


# Characters that draw nothing although they count as letters, so a "name" made of them would
# look empty. Zero width joiners (U+200C, U+200D) are kept: Persian, Sinhala and emoji need them.
BLANK_LETTERS = {"\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"}
JOINERS = {"\u200c", "\u200d"}


def visible(text):
    """Text holds something one can read: a character that is not a space, an invisible
    format or control character, a blank letter, or only a mark (a variation selector or
    combining grapheme joiner alone draws nothing)."""
    return any(unicodedata.category(ch)[0] not in "MZC" and ch not in BLANK_LETTERS
               for ch in text)


def clean_name(value):
    """A first or last name as typed: control and format characters removed (U+202E and the
    like) except the joiners, blank letters removed, spaces collapsed, composed (NFC) so the
    same name always compares equal. "" when nothing visible is left."""
    value = unicodedata.normalize("NFC", value or "")
    kept = "".join(ch for ch in value if ch not in BLANK_LETTERS
                   and (unicodedata.category(ch)[0] != "C" or ch.isspace() or ch in JOINERS))
    name = " ".join(kept.split())
    return name if visible(name) else ""


def full_name(row):
    """"First Last" of a users row, or "" when no name was given."""
    keys = row.keys()
    first = row["first_name"] if "first_name" in keys else ""
    last = row["last_name"] if "last_name" in keys else ""
    return " ".join(part for part in (first, last) if part)


def valid_email(email):
    """Pragmatic address check: one @, no spaces or brackets, a dotted domain with a real TLD."""
    if len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    # Control and invisible format characters (such as U+202E, which shows text reversed) are
    # refused, so an address can never be displayed as a different one.
    if not local or any(ch.isspace() or ch in '<>()[],;:"' or unicodedata.category(ch)[0] == "C"
                        for ch in email):
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(labels) and len(labels[-1]) >= 2


def password_fingerprint(password_hash):
    """Short digest of the password hash. Changes whenever the password changes."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


class User(UserMixin):
    def __init__(self, row, sid=None):
        self.id = row["id"]
        self.sid = sid  # the user_sessions row of this browser
        self.email = row["email"]
        self.fingerprint = password_fingerprint(row["password_hash"])
        self.version = row["session_version"] if "session_version" in row.keys() else 0
        self.plan = row["plan"] if "plan" in row.keys() and row["plan"] in PLANS else "free"
        self.first_name = row["first_name"] if "first_name" in row.keys() else ""
        self.name = full_name(row)  # shown in the header menu instead of the email

    @property
    def is_admin(self):
        from flask import current_app
        return self.email in current_app.config["ADMIN_EMAILS"]

    def get_id(self):
        # Binding the session to the password means a reset signs out every other session; the
        # version (only added once raised, so older sessions keep working) does the same when
        # an account is disabled.
        base = f"{self.id}:{self.fingerprint}"
        base = f"{base}:{self.version}" if self.version else base
        return f"{base}/{self.sid}" if self.sid else base


def load_or_create_secret(path):
    """Return a persistent random secret so sessions never use a guessable key."""
    try:
        with open(path) as fh:
            key = fh.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    # Written to a temporary file first, then linked into place: the key file never exists
    # half written, and when several server processes start at once only one link succeeds,
    # so they all sign sessions with the same secret.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(32))
        os.chmod(tmp, 0o600)
        try:
            os.link(tmp, path)
        except FileExistsError:
            with open(path) as fh:
                existing = fh.read().strip()
            if existing:
                return existing
            os.replace(tmp, path)  # an empty key file (a full disk once): replace it
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    with open(path) as fh:
        return fh.read().strip()


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
        # not stay useful for long; "Sign out everywhere" on the Settings page ends it at once.
        REMEMBER_COOKIE_DURATION=timedelta(days=30),
        # The admin page needs two-factor sign in (tests of other features turn this off).
        ADMIN_REQUIRE_2FA=True,
        # Refuse passwords found in data breaches (Have I Been Pwned). Set PWNED_CHECK=0 in
        # config.env on a server that cannot reach api.pwnedpasswords.com.
        PWNED_CHECK=os.getenv("PWNED_CHECK", "1").strip().lower() not in ("0", "false", "no", "off"),
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

    @user_loaded_from_cookie.connect_via(app)
    def remembered(_app, user):
        session["nl_remember"] = True  # signed in again from the "Remember me" cookie
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

    stamp_skipped = {}  # sid: when its last seen stamp was skipped (database busy)

    @login_manager.user_loader
    def load_user(session_id):
        session_id, _, sid = session_id.partition("/")
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
        if sid:
            # Signed out (here or with "Sign out everywhere else") or unused for too long.
            seen = db.query("SELECT last_seen_at < datetime('now', '-1 hour') AS stale "
                            "FROM user_sessions WHERE id = ? AND user_id = ? AND "
                            "last_seen_at >= datetime('now', ?)",
                            (sid, row["id"], f"-{SESSION_IDLE_DAYS} days"), one=True)
            if not seen:
                return None
            # At most once an hour, and never a reason to fail the page: an UPDATE takes the
            # database write lock even when it changes nothing, so a page view would otherwise
            # wait for (and fail behind) any other request that is writing.
            # A stamp skipped because the database was busy waits a minute before the next try,
            # so the following pages do not each wait for the lock again.
            if seen["stale"] and time.monotonic() - stamp_skipped.get(sid, -60.0) >= 60:
                if db.try_execute("UPDATE user_sessions SET last_seen_at = CURRENT_TIMESTAMP "
                                  "WHERE id = ?", (sid,)) is None:
                    if len(stamp_skipped) > 10000:
                        stamp_skipped.clear()
                    stamp_skipped[sid] = time.monotonic()
                else:
                    stamp_skipped.pop(sid, None)
        # Sessions from before 1.2.10 carry no sid; they end with the password or a
        # "Sign out everywhere else", as they always did.
        return User(row, sid or None)

    # Unconfirmed accounts are removed once their time is up. The auth pages purge on every
    # request, so a late link or sign in never sees them; this hook cleans up in between.
    last_purge = [0.0]

    @app.before_request
    def request_size_limit():
        # MAX_CONTENT_LENGTH fits the largest plan; each request gets its sender's own cap, so
        # a Free user's oversized upload is still refused before the server reads it.
        if request.endpoint == "static":  # no body, and no need to load the user
            return
        request.max_content_length = receipt_limit() + MB

    # Registered after the size hook on purpose: the CSRF check reads the form, and before
    # request hooks run in order, so the per plan cap must already be in place by then.
    CSRFProtect(app)

    @app.before_request
    def purge_now_and_then():
        if time.monotonic() - last_purge[0] >= PURGE_EVERY_SECONDS:
            last_purge[0] = time.monotonic()
            # Housekeeping only: when another request holds the write lock it waits for the
            # next round instead of failing the page that happened to trigger it.
            if purge_unverified(best_effort=True) is None:
                return
            db.try_execute("DELETE FROM audit_log WHERE created_at < datetime('now', ?)",
                           (f"-{AUDIT_DAYS} days",))
            db.try_execute("DELETE FROM user_sessions WHERE last_seen_at < datetime('now', ?)",
                           (f"-{SESSION_IDLE_DAYS} days",))

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
        if request.endpoint != "static" and current_user.is_authenticated:
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
        return {"app_version": APP_VERSION, "contact_email": CONTACT_EMAIL, "asset_version": asset_version, "countries": COUNTRIES, "country_data": COUNTRY_DATA,
                "document_kinds": DOCUMENT_KINDS,
                "max_receipt_bytes": receipt_limit(),
                "max_receipt_label": format_size(receipt_limit(), "down"),
                "current_plan": current_plan() if current_user.is_authenticated else None,
                "next_plan_name": (next_plan(current_user.plan) or {}).get("name")
                if current_user.is_authenticated else None,
                "storage": storage_summary(current_user.id)
                if current_user.is_authenticated else None,
                "site": site_meta(),
                "support_unread": support_unread(current_user.id)
                if current_user.is_authenticated else 0}

    app.add_template_filter(parse_date, "todate")
    app.add_template_filter(format_size, "filesize")
    app.add_template_filter(local_date, "localdate")
    app.add_template_filter(local_datetime, "localdatetime")
    app.add_template_filter(utc_datetime, "utcdatetime")
    app.add_template_filter(ticket_ref, "ticket_ref")
    app.add_template_global(chat_rows, "chat_rows")
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
            sign_out()
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

    @app.cli.command("zip-receipts")
    @click.option("--no-shrink", is_flag=True, help="Only zip; keep big photos as they are.")
    def zip_receipts(no_shrink):
        """Store receipts uploaded before 1.2.12 as ZIP files (and shrink big photos)."""
        rows = db.query("SELECT * FROM documents WHERE stored_name NOT LIKE '%.zip' ORDER BY id")
        done = missing = failed = skipped = 0
        before = after = 0
        for doc in rows:
            folder = os.path.join(app.config["UPLOAD_DIR"], str(doc["user_id"]))
            old_path = os.path.join(folder, doc["stored_name"])
            if not os.path.isfile(old_path):
                missing += 1
                continue
            try:
                with open(old_path, "rb") as fh:
                    data = fh.read()
                ext, name = doc_format(doc), doc["original_name"]
                shrunk = None if no_shrink else shrink_photo(data, ext)
                if shrunk is not None:
                    data, ext, name = shrunk, "jpg", display_name(stem(name) + ".jpg")
                stored = f"{uuid.uuid4().hex}.zip"
                new_path = os.path.join(folder, stored)
                size = write_receipt_zip(new_path, zip_member_name(name, ext), data)
            except (OSError, ValueError) as exc:
                failed += 1
                click.echo(f"Could not convert document {doc['id']}: {exc}", err=True)
                continue
            try:
                # The row points to the ZIP before the old file goes, so an interruption
                # never leaves a document without its file. The row must still be as read:
                # a rename or delete while the app runs wins, and this one is tried again
                # on the next run.
                with db.transaction() as conn:
                    changed = conn.execute(
                        "UPDATE documents SET stored_name = ?, size = ?, format = ?, "
                        "file_size = ?, original_name = ?, mime = ? WHERE id = ? AND "
                        "stored_name = ? AND original_name = ?",
                        (stored, size, ext, len(data), name, MIME_TYPES[ext], doc["id"],
                         doc["stored_name"], doc["original_name"])).rowcount
            except BaseException:
                os.remove(new_path)
                raise
            if not changed:
                os.remove(new_path)
                skipped += 1
                continue
            try:
                os.remove(old_path)
            except FileNotFoundError:
                pass
            done += 1
            before += doc["size"]
            after += size
        click.echo(f"Converted {done} receipt{'' if done == 1 else 's'} to ZIP: "
                   f"{format_size(before, 'up')} before, {format_size(after, 'up')} after."
                   + (f" {missing} file{'' if missing == 1 else 's'} missing on disk." if missing
                      else "") + (f" {failed} failed." if failed else "")
                   + (f" {skipped} changed meanwhile, run the command again." if skipped
                      else ""))

    @app.cli.command("reset-2fa")
    @click.argument("email")
    def reset_2fa(email):
        """Remove two-factor sign in from an account whose phone was lost."""
        email = email.strip().lower()
        # Emails build their links with url_for, which needs a request.
        with app.test_request_context(environ_base={"REMOTE_ADDR": "command line"}):
            row = db.query("SELECT id, totp_secret FROM users WHERE email = ?", (email,),
                           one=True)
            if row is None:
                raise click.ClickException(f"No account for {email}.")
            if not row["totp_secret"]:
                raise click.ClickException(f"Two-factor sign in is not on for {email}.")
            db.execute("UPDATE users SET totp_secret = NULL, totp_step = 0 WHERE id = ?",
                       (row["id"],))
            audit("2fa_reset", row["id"], email, "", "command line")
            security_alert(email, "Two-factor sign in was removed from your Nomad Life account "
                           "by the person who runs the server.",
                           advice=("This is usually because you lost the phone with your "
                                   "authenticator app: sign in with your password and turn "
                                   "two-factor sign in on again on the Settings page. If you did "
                                   "not ask for this, reset your password now with the link "
                                   "below and contact the person who runs the server."))
        click.echo(f"Two-factor sign in removed from {email}. They can sign in with the "
                   "password alone and turn it on again on the Settings page.")

    return app


# ---------- helpers ----------

def serializer(salt="password-reset"):
    """Signed tokens for emailed links. Each kind of link has its own salt, so a password
    reset link can never confirm an account and a confirmation link can never reset one."""
    from flask import current_app
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


class LinkUsed(Exception):
    """Raised inside db.transaction() to undo it when a one time link lost a race."""


def purge_unverified(best_effort=False):
    """Delete accounts that were not confirmed within VERIFY_MINUTES. An account that holds
    data is never one of them (it can only come from a server running an older version), so it
    is kept rather than deleted with everything in it."""
    # A disabled account is kept too, so a new sign up cannot undo an admin's decision.
    # best_effort returns None instead of failing when the database is busy.
    run = db.try_execute if best_effort else db.execute
    return run("DELETE FROM users WHERE verified_at IS NULL AND disabled = 0 AND "
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


def clear_code_failures(email, ids=()):
    """A right code: drop this attempt and the account's earlier wrong codes (per network
    counts stay, as for passwords)."""
    if ids:
        db.execute(f"DELETE FROM auth_events WHERE id IN ({','.join('?' * len(ids))})", ids)
    db.execute("DELETE FROM auth_events WHERE kind = 'code' AND key = ?", (f"email:{email}",))


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


# ---------- two-factor sign in ----------

def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def totp_code(secret, step):
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", step), "sha1").digest()
    offset = digest[-1] & 15
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def totp_match(secret, code, last_step=0):
    """The time step the code belongs to, or None. Steps at or before last_step were used
    already, so a code seen once (over a shoulder, in a proxy log) never works again."""
    # Phone keyboards may send full width or other digits; compare plain ASCII only.
    code = "".join(unicodedata.normalize("NFKC", code or "").split())
    if not secret or len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    now = int(time.time() // TOTP_STEP_SECONDS)
    for step in (now - 1, now, now + 1):
        if step > last_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


def use_totp_step(user_id, step):
    """Record the step as used. False if another request used it (or a later one) first."""
    with db.transaction() as conn:
        return conn.execute("UPDATE users SET totp_step = ? WHERE id = ? AND totp_step < ?",
                            (step, user_id, step)).rowcount == 1


def totp_uri(secret, email):
    return (f"otpauth://totp/{quote('Nomad Life')}:{quote(email)}?secret={secret}"
            f"&issuer={quote('Nomad Life')}&period={TOTP_STEP_SECONDS}&digits=6")


def totp_qr(secret, email):
    """The setup QR code as a data URI (no inline SVG, so no markup built from user data)."""
    return segno.make(totp_uri(secret, email), error="m").svg_data_uri(
        scale=5, border=4, dark="#000000", light="#ffffff")


# ---------- sessions and passwords ----------

def sign_in_user(row, remember, keep_current=False):
    """Sign this browser in with its own user_sessions row. keep_current reuses the row of the
    current session (the password changed here, so only the other browsers must go)."""
    sid = current_user.sid if keep_current and current_user.is_authenticated else None
    if not sid:
        sid = secrets.token_urlsafe(24)
        db.execute("INSERT INTO user_sessions (id, user_id) VALUES (?, ?)", (sid, row["id"]))
    login_user(User(row, sid), remember=remember)
    # What this browser chose, so a later re-sign in (new password, sign out elsewhere) keeps
    # it; a leftover remember cookie from an ended session must not turn it on.
    session["nl_remember"] = bool(remember)
    return sid


def end_other_sessions(user_id, keep_sid):
    db.execute("DELETE FROM user_sessions WHERE user_id = ? AND id != ?", (user_id, keep_sid or ""))


def stay_signed_in_here(user_id):
    """After a change that ends the other sessions (new password, new session version): keep
    this browser signed in, with the same session row, and forget every other one."""
    remember = session.get("nl_remember", False)
    row = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    end_other_sessions(user_id, sign_in_user(row, remember, keep_current=True))


def sign_out():
    """End this browser's session for good (a copy of its cookies stops working too)."""
    if current_user.is_authenticated and current_user.sid:
        db.execute("DELETE FROM user_sessions WHERE id = ?", (current_user.sid,))
    logout_user()
    # Nothing of this account may stay for the next person on this browser.
    for key in ("totp_setup", "tfa", "nl_remember"):
        session.pop(key, None)


def breach_count(password):
    """How often the password appears in known data breaches, or 0 when it does not, when the
    check is turned off, or when the service cannot be reached (sign up never waits on it)."""
    if not current_app.config.get("PWNED_CHECK") or not password:
        return 0
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    req = urllib.request.Request(PWNED_URL + digest[:5],
                                 headers={"Add-Padding": "true", "User-Agent": "Nomad-Life"})
    try:
        with urllib.request.urlopen(req, timeout=PWNED_TIMEOUT_SECONDS) as resp:
            body = resp.read(4 * MB).decode("ascii", "replace")
    except Exception as exc:  # noqa: BLE001 - offline, slow or changed service: do not block
        current_app.logger.warning("Breached password check skipped: %s", exc)
        return 0
    for line in body.splitlines():
        suffix, _, count = line.strip().partition(":")
        if suffix == digest[5:]:
            try:
                return int(count)
            except ValueError:
                return 0
    return 0


def password_problem(password, confirm, breaches=True):
    """The error for a new password, or None. breaches=False skips the (network) breach
    check, for callers that run it after their rate limit."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if password != confirm:
        return "Passwords do not match."
    found = breach_count(password) if breaches else 0
    if found:
        times = f"{found:,} time{'' if found == 1 else 's'}"
        return (f"This password appears {times} in known data breaches, so attackers "
                "try it early. Please choose another one.")
    return None


IMAGE_EXTENSIONS = ("jpg", "png", "webp", "heic")


def detect_type(file, ext):
    """The extension the content really has: ext when it matches, another image type when an
    image was saved under the wrong image extension (a WebP downloaded as .jpg is common), or
    None when the content is not an allowed type at all."""
    stream = file.stream
    pos = stream.tell()
    stream.seek(0)
    head = stream.read(1028)
    stream.seek(pos)
    if FILE_SIGNATURES[ext](head):
        return ext
    if ext in IMAGE_EXTENSIONS or ext == "jpeg":
        return next((other for other in IMAGE_EXTENSIONS if FILE_SIGNATURES[other](head)), None)
    return None


# ---------- support tickets ----------

TICKET_KINDS = {"bug": "Report a bug", "feature": "Feature request", "question": "General question"}
TICKET_SUBJECT_MAX = 150
TICKET_BODY_MAX = 5000
OPEN_TICKETS_MAX = 20
TICKETS_PER_PAGE = 25
# Admin table columns that can be sorted, with the SQL behind them. Tickets still open have
# no closed date: they go last in both directions.
TICKET_SORTS = {
    # Type sorts by the name shown (Feature request, General question, Report a bug).
    "id": "t.ref_year, t.ref_seq", "kind": "CASE t.kind WHEN 'feature' THEN 1 WHEN 'question' THEN 2 ELSE 3 END",
    "subject": "t.subject COLLATE NOCASE", "messages": "messages",
    "user": "u.email COLLATE NOCASE", "status": "t.status", "created": "t.created_at",
    "updated": "t.updated_at", "closed": "t.closed_at IS NULL, t.closed_at",
}
# Sortable columns of the admin accounts table. Accounts that never signed in go last.
ADMIN_USER_SORTS = {
    "email": "u.email COLLATE NOCASE", "joined": "u.created_at",
    "last": "u.last_login_at IS NULL, u.last_login_at", "years": "years", "used": "used",
}
ADMIN_ACCOUNT_AUDIT_ROWS = 20


def clean_ticket_body(value):
    """A message as typed: line breaks kept (as \\n), other control and invisible format
    characters removed, surrounding blank space dropped."""
    value = unicodedata.normalize("NFC", value or "").replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(ch for ch in value if ch not in BLANK_LETTERS and (
        ch in "\n\t" or unicodedata.category(ch)[0] != "C" or ch in JOINERS)).strip()
    return value if visible(value) else ""  # only spaces, joiners or marks: nothing to read


def open_ticket_cap_reached(ticket):
    """A closed ticket of a user who already has OPEN_TICKETS_MAX open ones: writing on it
    will not reopen it."""
    if ticket["status"] == "open":
        return False
    return db.query("SELECT COUNT(*) AS n FROM tickets WHERE user_id = ? AND status = 'open'",
                    (ticket["user_id"],), one=True)["n"] >= OPEN_TICKETS_MAX


def ticket_limit_key():
    """Ticket limits count per account id, so changing the email starts no new budget."""
    return f"#{current_user.id}"


def support_unread(user_id):
    """Tickets of this user with an admin message the user has not opened yet."""
    row = db.query("SELECT COUNT(*) AS n FROM tickets t WHERE t.user_id = ? AND EXISTS ("
                   "SELECT 1 FROM ticket_messages m WHERE m.ticket_id = t.id AND m.from_admin = 1 "
                   "AND m.id > t.user_seen_id)", (user_id,), one=True)
    return row["n"] if row else 0


def ticket_ref(ticket):
    """The number people see: the year the ticket was opened and its place in that year, 2026-1
    (ref_year and ref_seq, set by the tickets_ref trigger in db.py)."""
    return f"{ticket['ref_year']}-{ticket['ref_seq']}"


def ticket_args(ticket):
    """URL values of a ticket page: url_for("support_ticket", **ticket_args(t)) is /support/2026-1."""
    return {"year": ticket["ref_year"], "seq": ticket["ref_seq"]}


def notify_admins(ticket, event, user_email):
    """Tell every admin that a ticket needs them. A link only, never the ticket's text."""
    ref = ticket_ref(ticket)
    link = email_link("admin_ticket", **ticket_args(ticket))
    for admin in sorted(current_app.config["ADMIN_EMAILS"]):
        if not send_template_email(admin, f"Support ticket #{ref} needs an answer",
                                   "ticket_admin", ticket_ref=ref, event=event,
                                   user_email=user_email, link=link):
            current_app.logger.error("Could not email %s about ticket %s", admin, ref)


def notify_user(ticket, event):
    """Tell the ticket's owner that support answered or closed it: a link, never the text."""
    owner = db.query("SELECT email FROM users WHERE id = ?", (ticket["user_id"],), one=True)
    if owner is None:
        return
    ref = ticket_ref(ticket)
    subject = (f"Support answered your ticket #{ref}" if event == "reply"
               else f"Your support ticket #{ref} was closed")
    if not send_template_email(owner["email"], subject, "ticket_user", ticket_ref=ref,
                               event=event, link=email_link("support_ticket",
                                                            **ticket_args(ticket))):
        current_app.logger.error("Could not email %s about ticket %s", owner["email"], ref)


CHAT_GROUP_SECONDS = 600  # messages of one side closer than this share a bubble group


def chat_rows(messages, admin_view, owner_name=""):
    """Messages of a ticket laid out as a chat: whose side (mine: support in the admin view, the
    user otherwise), the sender, and where a bubble group or a day (UTC) starts and ends."""
    rows = []
    for m in messages:
        at = datetime.strptime(m["created_at"][:19], "%Y-%m-%d %H:%M:%S")
        party = "support" if m["from_admin"] else "user"
        if party == "support":
            sender = "Nomad Life support"
        else:
            sender = (owner_name or "User") if admin_view else "You"
        rows.append({"id": m["id"], "body": m["body"], "created_at": m["created_at"],
                     "party": party, "mine": bool(m["from_admin"]) == bool(admin_view),
                     "sender": sender, "at": at, "time": at.strftime("%H:%M"),
                     "day": at.date(), "day_label": f"{at.day} {at:%b %Y}"})
    for i, row in enumerate(rows):
        prev = rows[i - 1] if i else None
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        row["new_day"] = prev is None or prev["day"] != row["day"]
        row["group_start"] = row["new_day"] or prev["party"] != row["party"] or \
            (row["at"] - prev["at"]).total_seconds() > CHAT_GROUP_SECONDS
        row["group_end"] = nxt is None or nxt["day"] != row["day"] or nxt["party"] != row["party"] \
            or (nxt["at"] - row["at"]).total_seconds() > CHAT_GROUP_SECONDS
    return rows


def ticket_messages(ticket_id):
    return db.query("SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id",
                    (ticket_id,))


# ---------- security history and alerts ----------

AUDIT_LABELS = {
    "sign_in": "Signed in",
    "sign_in_failed": "Wrong password at sign in",
    "sign_in_code_failed": "Wrong two-factor code at sign in",
    "code_failed": "Wrong two-factor code on the Settings page",
    "email_reverted": "Email change undone from the old address",
    "account_confirmed": "Account confirmed",
    "password_changed": "Password changed",
    "password_reset": "Password reset by email link",
    "email_changed": "Email address changed",
    "sessions_revoked": "Signed out on every other device",
    "profile_changed": "Name changed",
    "ticket_opened": "Support ticket opened",
    "ticket_reply": "Support ticket answered",
    "ticket_message": "Support ticket message from the user",
    "ticket_closed": "Support ticket closed",
    "ticket_reopened": "Support ticket reopened",
    "2fa_enabled": "Two-factor sign in turned on",
    "2fa_disabled": "Two-factor sign in turned off",
    "2fa_reset": "Two-factor sign in removed by the server operator",
    "account_deleted": "Account deleted",
    "admin_plan": "Plan changed by an admin",
    "admin_quota": "Storage quota changed by an admin",
    "admin_disable": "Account disabled by an admin",
    "admin_enable": "Account enabled by an admin",
    "admin_delete": "Account deleted by an admin",
    "admin_price": "Plan prices changed",
}


def audit(event, user_id=None, email="", detail="", actor=""):
    """Record a security event. Never fails the request it belongs to."""
    ip = client_ip() if has_request_context() else ""  # none on the command line
    try:
        db.execute("INSERT INTO audit_log (user_id, email, actor, event, detail, ip) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (user_id, email or "", actor or "", event,
                                                 detail or "", ip))
    except Exception as exc:  # noqa: BLE001 - the history must never break sign in
        current_app.logger.error("Could not record %s for %s: %s", event, email, exc)


def audit_rows(rows):
    return [dict(r, label=AUDIT_LABELS.get(r["event"], r["event"])) for r in rows]


def security_alert(email, what, account=None, link=None, advice=None, button=None):
    """Tell the account's mailbox that something important changed, with a way to act if it
    was not them. Sent every time (no cooldown): each change needs the password or a link.
    By default the way out is a password reset; callers may give another link and wording."""
    ip = client_ip() if has_request_context() else ""
    ok = send_template_email(email, "Security alert for your Nomad Life account",
                             "security_alert", email=email, account=account or email, what=what,
                             ip=ip, when=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                             link=link or email_link("forgot"),
                             advice=advice or ("If this was you, there is nothing to do. If it "
                                               "was not you, reset your password now with the "
                                               "link below. Resetting signs out every browser "
                                               "and device."),
                             button=button or "Reset my password",
                             # Never text the account holder typed: whoever took over an
                             # account could otherwise write "ignore this alert" into it.
                             first_name="")
    if not ok:
        current_app.logger.error("Could not send the security alert to %s", email)
    return ok


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
    if "first_name" not in context:
        # "Hi Paolo," for an account that gave its name on the Settings page, else "Hi,".
        row = db.query("SELECT first_name FROM users WHERE email = ? AND verified_at IS NOT NULL",
                       (to,), one=True)
        context["first_name"] = row["first_name"] if row else ""
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


def utc_datetime(utc_timestamp):
    """A stored UTC time as "2026-09-30 12:43 UTC": the same for every reader, whatever the
    server's time zone (support tickets, like the security activity list)."""
    try:
        dt = datetime.strptime(utc_timestamp, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return utc_timestamp or ""
    return dt.strftime("%Y-%m-%d %H:%M") + " UTC"


def local_datetime(utc_timestamp):
    """Like local_date, with hours and minutes."""
    try:
        dt = datetime.strptime(utc_timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return utc_timestamp or ""
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


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
             "author": {"@type": "Person", "name": "Paolo Diomede", "url": "https://x.com/pdiomede"},
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
    real = detect_type(file, ext)
    if real is None:
        return (f"{name} is not a real {ext.upper()} file (its content does not match its name). "
                "Please upload the original file.")
    if real != ext:  # keep the image, under the name of what it really is
        name = display_name(name.rsplit(".", 1)[0] + "." + real)
        ext = real
    if size > limit:
        return (f"{name} is {format_size(size, 'up')}, larger than the "
                f"{format_size(limit, 'down')} limit per receipt.")
    left = max(quota - storage_used(current_user.id), 0)
    if left == 0:  # the exact check, with the size on disk, comes after compressing
        return quota_message(name, size, left, quota)
    file.stream.seek(0)
    data = file.stream.read()
    shrunk = shrink_photo(data, ext)
    if shrunk is not None:  # a big photo: keep a smaller JPEG instead
        data, ext = shrunk, "jpg"
        name = display_name(stem(name) + ".jpg")

    user_dir = os.path.join(current_app.config["UPLOAD_DIR"], str(current_user.id))
    os.makedirs(user_dir, mode=0o700, exist_ok=True)
    stored = f"{uuid.uuid4().hex}.zip"
    path = os.path.join(user_dir, stored)
    try:
        size = write_receipt_zip(path, zip_member_name(name, ext), data)
    except OSError as exc:
        current_app.logger.error("Could not store upload %s: %s", path, exc)
        if os.path.exists(path):
            os.remove(path)  # do not keep a partial file that no quota accounts for
        return "The server could not store the file (its disk may be full). Please try again later."
    try:
        # Check the quota with the size really used on disk and insert under one write lock,
        # so parallel uploads cannot both pass the check and exceed the quota together.
        with db.transaction() as conn:
            used = conn.execute("SELECT COALESCE(SUM(size), 0) FROM documents WHERE user_id = ?",
                                (current_user.id,)).fetchone()[0]
            if used + size > quota:
                raise QuotaExceeded(max(quota - used, 0))
            conn.execute(
                "INSERT INTO documents (user_id, year_id, movement_id, kind, original_name, "
                "stored_name, mime, size, format, file_size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (current_user.id, year_id, movement_id, kind, name, stored,
                 MIME_TYPES[ext], size, ext, len(data)))
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


# ---------- receipt files: stored as one file per ZIP, big photos shrunk ----------

# Photos over either limit are resized to PHOTO_MAX_SIDE and re-saved as JPEG; the original
# is not kept. Larger images than PHOTO_MAX_PIXELS are never decoded (decompression bombs).
PHOTO_MAX_AREA = 4_000_000   # pixels kept at most (about 2300 x 1730): receipts stay legible
PHOTO_MIN_SIDE = 1000        # never narrower than this, so long screenshots keep their text
PHOTO_SHRINK_BYTES = 1024 * 1024
PHOTO_MAX_PIXELS = 24_000_000  # bigger images are never decoded (memory, decompression bombs)
PHOTO_QUALITY = 85
SHRINKABLE = ("jpg", "jpeg", "png", "webp")
# Decoding a 24 megapixel image with transparency takes up to about 200 MB, so one at a time
# per server process.
IMAGE_SLOTS = threading.BoundedSemaphore(1)


def photo_target(width, height):
    """(width, height) a photo is resized to: at most PHOTO_MAX_AREA pixels, but never with
    its shorter side under PHOTO_MIN_SIDE (a long receipt screenshot stays readable)."""
    scale = min(1.0, (PHOTO_MAX_AREA / (width * height)) ** 0.5)
    scale = max(scale, min(1.0, PHOTO_MIN_SIDE / min(width, height)))
    return max(1, round(width * scale)), max(1, round(height * scale))


def shrink_photo(data, ext):
    """Smaller JPEG bytes for a big photo, or None to keep the file as it is (small, not a
    photo, unreadable, too many pixels, or not smaller once re-saved)."""
    if ext not in SHRINKABLE:
        return None
    from PIL import Image, ImageOps
    with IMAGE_SLOTS:
        try:
            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
                if width * height > PHOTO_MAX_PIXELS:
                    return None
                if width * height <= PHOTO_MAX_AREA and len(data) <= PHOTO_SHRINK_BYTES:
                    return None
                target = photo_target(width, height)
                if img.format == "JPEG":
                    img.draft("RGB", target)  # decode at a reduced size, much less memory
                if img.getexif().get(0x0112) in (5, 6, 7, 8):  # shown turned by 90 degrees
                    target = target[::-1]
                img = ImageOps.exif_transpose(img)  # the photo stays upright without its EXIF
                transparent = (img.mode in ("RGBA", "LA", "P", "PA")
                               or "transparency" in img.info)
                if img.mode.startswith("I"):
                    # 16 bit grey: scale to 8 bits (a plain RGB conversion clips it to white).
                    img = img.convert("I").point(lambda v: v / 256).convert("L")
                elif transparent:
                    img = img.convert("RGBA")
                elif img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                # Resize first, then flatten and convert the small image: less memory.
                img.thumbnail(target, Image.LANCZOS)
                if transparent and img.mode == "RGBA":
                    background = Image.new("RGB", img.size, "white")
                    background.paste(img, mask=img.getchannel("A"))
                    img = background
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                out = io.BytesIO()
                # No EXIF or other metadata is written, so the location of the photo is gone.
                img.save(out, "JPEG", quality=PHOTO_QUALITY, optimize=True, progressive=True)
        except Exception as exc:  # noqa: BLE001 - a photo Pillow cannot handle is kept as is
            current_app.logger.warning("Photo kept unshrunk: %s", exc)
            return None
    smaller = out.getvalue()
    return smaller if len(smaller) < len(data) else None


# Characters Windows cannot have in a file name: the receipt must extract everywhere.
UNSAFE_NAME_CHARS = str.maketrans({c: "-" for c in '/\\:*?"<>|'})


def zip_member_name(name, ext):
    """The file name inside a receipt ZIP: the receipt's name, never a path."""
    name = name.translate(UNSAFE_NAME_CHARS).strip()
    if name in ("", ".", "..") or name.startswith("."):
        name = f"receipt.{ext}"
    return name


def write_receipt_zip(path, member, data):
    """Write a one file ZIP to path through a temporary file of its own, so a crash, or two
    requests writing the same receipt, never leave a half written one. Deflate is used when it
    makes the file smaller, else the file is stored."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    os.close(fd)
    info = zipfile.ZipInfo(member, date_time=time.localtime()[:6])
    info.external_attr = 0o600 << 16
    try:
        for method in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED):
            info.compress_type = method
            with zipfile.ZipFile(tmp, "w") as zf:
                zf.writestr(info, data, compresslevel=6 if method == zipfile.ZIP_DEFLATED else None)
                written = zf.getinfo(member).compress_size
            if method == zipfile.ZIP_STORED or written < len(data):
                break
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return os.path.getsize(path)


def read_receipt(path):
    """(member name, bytes) of a stored receipt; legacy files (before 1.2.12) are plain."""
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            info = zf.infolist()[0]
            return info.filename, zf.read(info)
    with open(path, "rb") as fh:
        return os.path.basename(path), fh.read()


def receipt_readable(path):
    """The stored receipt is there and, for a ZIP, not damaged (every CRC checked)."""
    if not os.path.isfile(path):
        return False
    if not path.endswith(".zip"):
        return True
    try:
        with zipfile.ZipFile(path) as zf:
            return bool(zf.infolist()) and zf.testzip() is None
    except (OSError, zipfile.BadZipFile, zlib.error):
        return False


def doc_format(doc):
    """Extension of the file inside a stored receipt."""
    keys = doc.keys()
    return (doc["format"] if "format" in keys and doc["format"] else file_ext(doc["stored_name"]))


def stem(name):
    return name.rsplit(".", 1)[0] if "." in name else name


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
    city = clean_place(form.get("city", ""))
    country = normalize_country(clean_place(form.get("country", "")))
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
        exists = receipt_readable(path)
        receipts.append(package.Receipt(
            stay_number=stay.number if stay else 0,
            kind=DOCUMENT_KINDS.get(d["kind"], "Other"), name=d["original_name"],
            ext=doc_format(d), path=path, uploaded=local_date(d["uploaded_at"]),
            # The size of the receipt itself (inside its ZIP), as the accountant receives it.
            size=d["file_size"] or d["size"], exists=exists))

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
        include_notes=include_notes, upcoming_days=stats["upcoming"],
        holder=package_holder(year_row["user_id"]))


def package_holder(user_id):
    """Whose package it is, for the accountant: the name from Settings, or else the email."""
    row = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if not row:
        return ""
    name = full_name(row)
    return f"{name} ({row['email']})" if name else row["email"]


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
            elif problem := password_problem(password, confirm, breaches=False):
                flash(problem, "error")
            else:
                wait, scope, _attempt = take_attempt("signup")
                if wait:
                    flash(too_many_message(wait, scope, "sign ups"), "error")
                    return render_template("auth/signup.html"), 429
                # Checked for every address, known or not, so the answer reveals nothing.
                problem = password_problem(password, confirm)
                if problem:
                    flash(problem, "error")
                    return render_template("auth/signup.html")
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
        audit("account_confirmed", row["id"], row["email"])
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
                remember = bool(request.form.get("remember"))
                next_url = safe_next(request.args.get("next", "")) or url_for("index")
                if row["totp_secret"]:
                    # The password was right; the code from the authenticator app comes next.
                    # Nothing is signed in until then.
                    session["tfa"] = {"uid": row["id"], "h": password_fingerprint(row["password_hash"]),
                                      "v": row["session_version"], "remember": remember,
                                      "next": next_url, "t": int(time.time())}
                    return redirect(url_for("login_code"))
                return finish_sign_in(row, remember, next_url)
            if row:
                audit("sign_in_failed", row["id"], row["email"])
            flash("Invalid email or password.", "error")
        return render_template("auth/login.html")

    def finish_sign_in(row, remember, next_url, detail=""):
        db.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
        sign_in_user(row, remember)
        audit("sign_in", row["id"], row["email"], detail)
        return remember_device(redirect(next_url), row["email"])

    @app.route("/login/code", methods=["GET", "POST"])
    def login_code():
        """Second step of signing in to an account with two-factor sign in turned on."""
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        pending = session.get("tfa") or {}
        row = db.query("SELECT * FROM users WHERE id = ?", (pending.get("uid"),), one=True)
        # Stale when too old, or when the password, sessions or two-factor changed meanwhile.
        if (not row or row["disabled"] or not row["totp_secret"]
                or time.time() - pending.get("t", 0) > TOTP_PENDING_SECONDS
                or password_fingerprint(row["password_hash"]) != pending.get("h")
                or row["session_version"] != pending.get("v")):
            session.pop("tfa", None)
            flash("Please sign in again.", "info")
            return redirect(url_for("login"))
        if request.method == "POST":
            wait, scope, attempt = take_attempt("code", row["email"])
            if wait:
                flash(too_many_message(wait, scope, "wrong codes"), "error")
                return render_template("auth/login_code.html"), 429
            step = totp_match(row["totp_secret"], request.form.get("code"), row["totp_step"])
            if step is not None and use_totp_step(row["id"], step):
                clear_code_failures(row["email"], attempt)
                session.pop("tfa", None)
                return finish_sign_in(row, pending.get("remember", False),
                                      safe_next(pending.get("next", "")) or url_for("index"),
                                      "with two-factor code")
            audit("sign_in_code_failed", row["id"], row["email"])
            flash("That code is not correct, or was already used. Please enter the current code "
                  "from your authenticator app.", "error")
        return render_template("auth/login_code.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        sign_out()
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
            problem = password_problem(password, request.form.get("confirm", ""))
            if problem:
                flash(problem, "error")
            else:
                new_hash = hash_password(password)
                with db.transaction() as conn:
                    # The link came by email, so it also confirms an account still waiting for
                    # it. Only while the password is still the one the link was made for: two
                    # submits at once (two tabs) must not both succeed.
                    used = conn.execute(
                        "UPDATE users SET password_hash = ?, verified_at = COALESCE(verified_at, "
                        "CURRENT_TIMESTAMP) WHERE id = ? AND password_hash = ?",
                        (new_hash, row["id"], row["password_hash"])).rowcount
                    if used:
                        # Proving the mailbox ends a lock from wrong guesses (by anyone).
                        conn.execute("DELETE FROM auth_events WHERE kind = 'fail' AND key = ?",
                                     (f"email:{row['email']}",))
                        # The new password already ends every session; forget their rows too.
                        conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (row["id"],))
                if not used:
                    flash("This reset link has already been used.", "error")
                    return redirect(url_for("forgot"))
                if row["verified_at"] is None:
                    # The first password of an account still waiting for confirmation: this
                    # completes the sign up, it does not change anything the owner had.
                    audit("account_confirmed", row["id"], row["email"])
                else:
                    audit("password_reset", row["id"], row["email"])
                    security_alert(row["email"], "The password of your Nomad Life account was "
                                   "reset with a link sent to this address.")
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
            city = clean_place(request.form.get("base_city", ""))
            country = normalize_country(clean_place(request.form.get("base_country", "")))
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
                city = clean_place(request.form.get("base_city", ""))
                country = normalize_country(clean_place(request.form.get("base_country", "")))
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
            mid = None
            if not err:
                try:
                    # Count and insert under one write lock, so parallel posts cannot pass the
                    # limit together.
                    with db.transaction() as conn:
                        count = conn.execute("SELECT COUNT(*) FROM movements WHERE year_id = ?",
                                             (year_row["id"],)).fetchone()[0]
                        if count >= MOVEMENTS_MAX:
                            err = f"A year can hold at most {MOVEMENTS_MAX} movements."
                        else:
                            mid = conn.execute(
                                "INSERT INTO movements (year_id, city, country, start_date, "
                                "end_date, notes) VALUES (?, ?, ?, ?, ?, ?)",
                                (year_row["id"], data["city"], data["country"],
                                 data["start_date"], data["end_date"], data["notes"])).lastrowid
                except db.IntegrityError:  # the year was deleted meanwhile (another tab)
                    flash(f"The year {year} no longer exists, so the movement was not saved.",
                          "error")
                    return redirect(url_for("index"))
            if err:
                flash(err, "error")
            else:
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
        """Wrong current passwords count as failed sign ins, so the Settings page cannot be
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
        """The Settings page was called Account before 1.2.11: bookmarks land on Settings, and
        forms in pages left open still work."""
        if request.method == "POST":
            return settings()
        return redirect(url_for("settings"))

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        row = db.query("SELECT * FROM users WHERE id = ?", (current_user.id,), one=True)
        if request.method == "POST":
            action = request.form.get("action")
            if action == "profile":  # only display text, so no password needed
                first = clean_name(request.form.get("first_name", ""))
                last = clean_name(request.form.get("last_name", ""))
                if len(first) > NAME_MAX or len(last) > NAME_MAX:
                    flash(f"First and last name can be at most {NAME_MAX} characters each.",
                          "error")
                elif (first, last) != (row["first_name"], row["last_name"]):
                    db.execute("UPDATE users SET first_name = ?, last_name = ? WHERE id = ?",
                               (first, last, row["id"]))
                    audit("profile_changed", row["id"], row["email"])
                    flash("Your name was saved." if first or last else "Your name was removed.",
                          "success")
                else:
                    flash("Nothing changed.", "info")
                return redirect(url_for("settings"))  # no anchor: the message stays in view
            if action == "cancel_email":  # stopping a change never needs the password
                if row["pending_email"]:
                    db.execute("UPDATE users SET pending_email = NULL WHERE id = ?", (row["id"],))
                    flash(f"The change to {row['pending_email']} was cancelled. The link we sent "
                          "no longer works.", "success")
                return redirect(url_for("settings"))
            if action == "sessions":  # ending other sessions only takes access away
                db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?",
                           (row["id"],))
                stay_signed_in_here(row["id"])
                audit("sessions_revoked", row["id"], row["email"])
                flash("Every other browser and device has been signed out.", "success")
                return redirect(url_for("settings"))
            err = check_current_password(row)
            if action == "password":
                password = request.form.get("password", "")
                if err:
                    flash(err, "error")
                elif problem := password_problem(password, request.form.get("confirm", ""),
                                                 breaches=False):
                    flash(problem, "error")
                elif password_matches(row["password_hash"], password):
                    flash("The new password is the same as the current one.", "error")
                elif problem := password_problem(password, request.form.get("confirm", "")):
                    flash(problem, "error")
                else:
                    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                               (hash_password(password), row["id"]))
                    # The new password changes the session fingerprint: stay signed in here,
                    # every other browser and remember cookie is signed out.
                    stay_signed_in_here(row["id"])
                    audit("password_changed", row["id"], row["email"])
                    security_alert(row["email"], "The password of your Nomad Life account was "
                                   "changed.")
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
                    # The new address can be anyone's until confirmed: no text typed by the
                    # account holder goes to it.
                    if send_template_email(new, "Confirm your new Nomad Life email", "change_email",
                                           first_name="",
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
            elif action == "2fa_enable":
                setup = session.get("totp_setup")
                secret = (setup.get("secret") if isinstance(setup, dict)
                          and setup.get("uid") == row["id"] else None)
                step = totp_match(secret, request.form.get("code"))
                if err:
                    flash(err, "error")
                elif row["totp_secret"]:
                    flash("Two-factor sign in is already on.", "info")
                elif step is None:
                    flash("That code is not correct. Scan the QR code again, or check that the "
                          "time on your phone is right, and type the current code.", "error")
                else:
                    # Sessions from before were signed in without a code: end them.
                    db.execute("UPDATE users SET totp_secret = ?, totp_step = ?, "
                               "session_version = session_version + 1 WHERE id = ?",
                               (secret, step, row["id"]))
                    session.pop("totp_setup", None)
                    stay_signed_in_here(row["id"])
                    audit("2fa_enabled", row["id"], row["email"])
                    security_alert(row["email"], "Two-factor sign in was turned on for your "
                                   "Nomad Life account.")
                    flash("Two-factor sign in is on. From now on, signing in also asks for a code "
                          "from your authenticator app. Other devices have been signed out.",
                          "success")
            elif action == "2fa_disable":
                step = totp_match(row["totp_secret"], request.form.get("code"), row["totp_step"])
                if err:
                    flash(err, "error")
                elif not row["totp_secret"]:
                    flash("Two-factor sign in is already off.", "info")
                elif (wait := take_attempt("code", row["email"]))[0]:
                    flash(too_many_message(wait[0], wait[1], "wrong codes"), "error")
                elif step is None or not use_totp_step(row["id"], step):
                    audit("code_failed", row["id"], row["email"])
                    flash("That code is not correct. Type the current code from your "
                          "authenticator app.", "error")
                else:
                    clear_code_failures(row["email"], wait[2])
                    db.execute("UPDATE users SET totp_secret = NULL, totp_step = 0 WHERE id = ?",
                               (row["id"],))
                    audit("2fa_disabled", row["id"], row["email"])
                    security_alert(row["email"], "Two-factor sign in was turned off for your "
                                   "Nomad Life account.")
                    note = (" The admin page needs it: turn it on again to use it."
                            if current_user.is_admin else "")
                    flash("Two-factor sign in is off." + note, "success")
            elif action == "delete":
                if not delete_confirmed():
                    pass
                elif request.form.get("confirm_email", "").strip().lower() != row["email"]:
                    flash("Type your email address to confirm the deletion.", "error")
                elif err:
                    flash(err, "error")
                else:
                    delete_account(row["id"])
                    audit("account_deleted", row["id"], row["email"])
                    logout_user()
                    flash("Your account and all its data have been deleted.", "info")
                    return redirect(url_for("landing"))
            return redirect(url_for("settings"))
        storage = storage_summary(row["id"])
        counts = db.query("SELECT (SELECT COUNT(*) FROM years WHERE user_id = ?) AS years, "
                          "(SELECT COUNT(*) FROM documents WHERE user_id = ?) AS documents",
                          (row["id"], row["id"]), one=True)
        totp = None
        if not row["totp_secret"]:
            # A new secret for this setup, for this account only; it counts once a code from
            # it is confirmed.
            setup = session.get("totp_setup")
            if not isinstance(setup, dict) or setup.get("uid") != row["id"]:
                setup = session["totp_setup"] = {"uid": row["id"], "secret": new_totp_secret()}
            secret = setup["secret"]
            totp = {"secret": secret, "qr": totp_qr(secret, row["email"]),
                    # Groups of 4, as authenticator apps show and accept a typed key.
                    "grouped": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))}
        activity = audit_rows(db.query(
            # Name changes are not security events: they must not push a stranger's sign in
            # off this short list.
            "SELECT * FROM audit_log WHERE user_id = ? AND event != 'profile_changed' "
            "AND event NOT LIKE 'ticket\\_%' ESCAPE '\\' "
            "ORDER BY id DESC LIMIT 10", (row["id"],)))
        devices = db.query("SELECT COUNT(*) AS n FROM user_sessions WHERE user_id = ?",
                           (row["id"],), one=True)["n"]
        return render_template("settings.html", row=row, counts=counts, account_storage=storage,
                               totp=totp, activity=activity, devices=max(devices, 1),
                               idle_days=SESSION_IDLE_DAYS, name_max=NAME_MAX)

    @app.route("/account/email/<token>")
    def confirm_email(token):
        try:
            data = serializer("email-change").loads(token, max_age=EMAIL_CHANGE_MAX_AGE)
        except SignatureExpired:
            flash("This link has expired. Please ask for the email change again.", "error")
            return redirect(url_for("settings"))
        except BadSignature:
            flash("This link is not valid.", "error")
            return redirect(url_for("settings"))
        row = db.query("SELECT * FROM users WHERE id = ?", (data.get("uid"),), one=True)
        new = data.get("email", "")
        if row and row["email"] == new:
            flash("This email address is already confirmed.", "info")
            return redirect(url_for("settings") if current_user.is_authenticated else url_for("login"))
        if row and row["disabled"]:
            flash("This account is disabled. Please contact the administrator.", "error")
            return redirect(url_for("login"))
        # Stale when the password changed, the email changed since, or a newer change was asked.
        if (not row or password_fingerprint(row["password_hash"]) != data.get("h")
                or row["email"] != data.get("old") or row["pending_email"] != new):
            flash("This link is no longer valid. Please ask for the email change again.", "error")
            return redirect(url_for("settings") if current_user.is_authenticated else url_for("login"))
        try:
            with db.transaction() as conn:
                # Opening the link proves this mailbox is theirs: an unconfirmed sign up with
                # the same address (anyone can start one) gives way, as it does on sign up.
                conn.execute("DELETE FROM users WHERE email = ? AND verified_at IS NULL AND NOT "
                             "EXISTS (SELECT 1 FROM years WHERE years.user_id = users.id)", (new,))
                # The checks above hold only if nothing changed meanwhile (the same link opened
                # twice at once, by the user and a mail scanner, would apply and alert twice).
                if not conn.execute("UPDATE users SET email = ?, pending_email = NULL WHERE id = ? "
                                    "AND email = ? AND pending_email = ? AND password_hash = ?",
                                    (new, row["id"], row["email"], new,
                                     row["password_hash"])).rowcount:
                    raise LinkUsed
        except LinkUsed:
            flash("This link is no longer valid. Please ask for the email change again.", "error")
            return redirect(url_for("settings") if current_user.is_authenticated else url_for("login"))
        except db.IntegrityError:
            flash("Another account started using this email address in the meantime.", "error")
            return redirect(url_for("settings") if current_user.is_authenticated else url_for("login"))
        audit("email_changed", row["id"], new, f"{row['email']} to {new}")
        # The old mailbox hears about it too: if the account was taken over, that is where the
        # owner still reads.
        # This address has no account any more, so a password reset link would not work here:
        # it gets a link that puts itself back instead.
        revert = serializer("email-revert").dumps({"uid": row["id"], "old": row["email"],
                                                    "new": new})
        security_alert(row["email"], f"The email address of your Nomad Life account was changed "
                       f"from {row['email']} to {new}. This address can no longer sign in.",
                       account=new, link=email_link("revert_email", token=revert),
                       advice=("If this was you, there is nothing to do. If it was not you, undo "
                               f"the change with the link below within "
                               f"{EMAIL_REVERT_MAX_AGE // 86400} days: it puts this address "
                               "back, signs out every browser and device, and sends you a link "
                               "to choose a new password."),
                       button="Undo this change")
        if current_user.is_authenticated and current_user.id != row["id"]:
            flash(f"That link was for another account; its email address is now {new}.", "info")
            return redirect(url_for("settings"))
        flash(f"Your email address is now {new}.", "success")
        return redirect(url_for("settings") if current_user.is_authenticated else url_for("login"))

    @app.route("/account/email/undo/<token>", methods=["GET", "POST"])
    def revert_email(token):
        """The old address takes the account back after an email change it did not ask for."""
        try:
            data = serializer("email-revert").loads(token, max_age=EMAIL_REVERT_MAX_AGE)
        except SignatureExpired:
            flash("This link has expired. Please contact the person who runs this Nomad Life "
                  "server.", "error")
            return redirect(url_for("login"))
        except BadSignature:
            flash("This link is not valid.", "error")
            return redirect(url_for("login"))
        row = db.query("SELECT * FROM users WHERE id = ?", (data.get("uid"),), one=True)
        if not row or row["email"] != data.get("new"):  # already undone, or changed again
            flash("This link is no longer valid.", "error")
            return redirect(url_for("login"))
        if request.method == "GET":  # a mail scanner opening the link must not act on it
            return render_template("auth/revert_email.html", old=data["old"], new=data["new"])
        try:
            with db.transaction() as conn:
                conn.execute("DELETE FROM users WHERE email = ? AND verified_at IS NULL AND NOT "
                             "EXISTS (SELECT 1 FROM years WHERE years.user_id = users.id)",
                             (data["old"],))
                conn.execute("UPDATE users SET email = ?, pending_email = NULL, session_version "
                             "= session_version + 1 WHERE id = ?", (data["old"], row["id"]))
                conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (row["id"],))
        except db.IntegrityError:
            flash(f"{data['old']} is used by another account now. Please contact the person who "
                  "runs this Nomad Life server.", "error")
            return redirect(url_for("login"))
        if current_user.is_authenticated:
            sign_out()
        audit("email_reverted", row["id"], data["old"], f"{data['new']} to {data['old']}")
        fresh = db.query("SELECT * FROM users WHERE id = ?", (row["id"],), one=True)
        send_password_link(fresh, "reset", "Reset your Nomad Life password")
        flash(f"The account uses {data['old']} again and every device was signed out. We sent "
              "you a link to choose a new password.", "success")
        return redirect(url_for("login"))

    # --- admin ---

    def require_admin():
        # Same answer as for any page that is not yours: nothing to see here.
        if not current_user.is_admin:
            abort(404)
        # Admins act on everyone's accounts, so a password alone is not enough.
        if app.config["ADMIN_REQUIRE_2FA"] and not db.query(
                "SELECT totp_secret FROM users WHERE id = ?", (current_user.id,), one=True)[0]:
            flash("Turn on two-factor sign in to use the admin page.", "info")
            abort(redirect(url_for("settings")))  # no anchor: the message stays in view

    def admin_args(values):
        """URL arguments without the defaults, so plain /admin stays plain."""
        return {k: v for k, v in values.items() if v not in (None, "", 1)
                and not (k == "sort" and v == "joined") and not (k == "dir" and v == "desc")}

    def admin_url(**values):
        # The accounts search, sort and page, and the activity filter (q), stay while paging,
        # on the account page and after saving an account's settings.
        return url_for("admin", **admin_args(values))

    def admin_account_url(user_id, state):
        return url_for("admin_account", user_id=user_id, **admin_args(state))

    def admin_list_state(source):
        """Search, sort and page of the accounts table, from the query string or a form."""
        sort = source.get("sort", "joined")
        return {"search": source.get("search", "").strip()[:254],
                "sort": sort if sort in ADMIN_USER_SORTS else "joined",
                "dir": "asc" if source.get("dir") == "asc" else "desc",
                "page": max(to_int(source.get("page"), 1), 1)}

    def admin_accounts(where="1 = 1", params=(), order="u.created_at DESC"):
        """Accounts with their plan, quota, storage and years, ready for the admin pages."""
        rows = db.query(
            "SELECT u.id, u.email, u.created_at, u.last_login_at, u.disabled, u.quota_bytes, "
            "u.verified_at, u.plan, u.totp_secret IS NOT NULL AS has_2fa, u.first_name, u.last_name, "
            "(SELECT COUNT(*) FROM years y WHERE y.user_id = u.id) AS years, "
            "(SELECT COALESCE(SUM(size), 0) FROM documents d WHERE d.user_id = u.id) AS used "
            f"FROM users u WHERE {where} ORDER BY {order}", params)
        plans = {p["key"]: p for p in plan_catalog()}
        # Without a custom quota an account gets the quota of its plan.
        return [dict(u, plan_name=plans.get(u["plan"], plans["free"])["name"],
                     plan_key=u["plan"] if u["plan"] in plans else "free",
                     quota=u["quota_bytes"] if u["quota_bytes"] is not None
                     else plans.get(u["plan"], plans["free"])["quota_bytes"],
                     quota_mb=f"{u['quota_bytes'] / MB:.1f}".removesuffix(".0")
                     if u["quota_bytes"] is not None else "",
                     is_admin=u["email"] in app.config["ADMIN_EMAILS"], full_name=full_name(u))
                for u in rows]

    def account_activity(email, limit):
        """Events of an account (or all, for an empty email): its whole history, also from
        before an email change, and what it did as an admin."""
        return audit_rows(db.query(
            "SELECT * FROM audit_log WHERE (? = '' OR email = ? OR actor = ? OR user_id = "
            "(SELECT id FROM users WHERE email = ?)) ORDER BY id DESC LIMIT ?",
            (email, email, email, email, limit)))

    @app.route("/admin")
    @login_required
    def admin():
        require_admin()
        state = admin_list_state(request.args)
        activity_q = request.args.get("q", "").strip().lower()[:254]
        where, params = "1 = 1", []
        if state["search"]:
            # casefold (registered in db.get_db) folds every script and composes accents.
            where = ("(casefold(u.email) LIKE ? ESCAPE '\\' OR casefold(u.first_name || ' ' "
                     "|| u.last_name) LIKE ? ESCAPE '\\')")
            params = [db.search_like(state["search"])] * 2
        direction = state["dir"].upper()
        order = ", ".join(f"{part} {direction}" if not part.endswith("IS NULL") else part
                          for part in ADMIN_USER_SORTS[state["sort"]].split(", "))
        users = admin_accounts(where, params, f"{order}, u.id {direction}")
        # Totals always count every account, also while the table shows a search.
        counts = db.query(
            "SELECT COUNT(*) AS users, COALESCE(SUM(disabled), 0) AS disabled, "
            "COALESCE(SUM(quota_bytes IS NOT NULL), 0) AS custom_quota FROM users", one=True)
        totals = {
            **dict(counts),
            "storage": db.query("SELECT COALESCE(SUM(size), 0) AS n FROM documents", one=True)["n"],
            "years": db.query("SELECT COUNT(*) AS n FROM years", one=True)["n"],
            "movements": db.query("SELECT COUNT(*) AS n FROM movements", one=True)["n"],
            "documents": db.query("SELECT COUNT(*) AS n FROM documents", one=True)["n"],
        }
        list_state = {k: v for k, v in state.items() if k != "page"}
        pager = paginate(users, state["page"], ADMIN_USERS_PER_PAGE,
                         lambda n, _size: admin_url(**list_state, page=n, q=activity_q) + "#accounts")
        state["page"] = pager["page"]
        activity = account_activity(activity_q, ADMIN_AUDIT_ROWS)
        return render_template("admin.html", pager=pager, totals=totals, state=state,
                               admin_url=admin_url,
                               account_url=admin_account_url,
                               default_quota=app.config["USER_QUOTA_BYTES"],
                               plans=plan_catalog(), activity=activity, activity_q=activity_q,
                               activity_limit=ADMIN_AUDIT_ROWS, audit_days=AUDIT_DAYS)

    @app.route("/admin/users/<int:user_id>")
    @login_required
    def admin_account(user_id):
        require_admin()
        found = admin_accounts("u.id = ?", (user_id,))
        if not found:
            abort(404)
        u = found[0]
        counts = db.query(
            "SELECT (SELECT COUNT(*) FROM movements m JOIN years y ON y.id = m.year_id "
            "WHERE y.user_id = :id) AS movements, "
            "(SELECT COUNT(*) FROM documents WHERE user_id = :id) AS documents, "
            "(SELECT COUNT(*) FROM user_sessions WHERE user_id = :id) AS devices, "
            "(SELECT COUNT(*) FROM tickets WHERE user_id = :id) AS tickets, "
            "(SELECT COUNT(*) FROM tickets WHERE user_id = :id AND status = 'open') AS open_tickets",
            {"id": user_id}, one=True)
        state = admin_list_state(request.args)
        return render_template("admin_account.html", u=u, counts=counts, state=state,
                               back=admin_url(**state), plans=plan_catalog(),
                               activity=account_activity(u["email"], ADMIN_ACCOUNT_AUDIT_ROWS),
                               activity_limit=ADMIN_ACCOUNT_AUDIT_ROWS,
                               history=admin_url(q=u["email"]) + "#activity",
                               tickets_url=url_for("admin_support", q=u["email"]))

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
            audit("admin_price", None, "", f"{name}: default prices", current_user.email)
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
        audit("admin_price", None, "", f"{name}: ${money(month)} / month, ${money(year)} / year",
              current_user.email)
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
        state = admin_list_state(request.form)
        back = admin_account_url(user_id, state)
        action = request.form.get("action")
        email = row["email"]
        who = current_user.email
        if action in ("disable", "delete") and row["id"] == current_user.id:
            flash("You cannot disable or delete your own admin account.", "error")
        elif action in ("disable", "delete") and email in app.config["ADMIN_EMAILS"]:
            # One admin account, if taken over, must not be able to lock out the others.
            flash(f"{email} is an admin. Remove it from ADMIN_EMAILS in config.env first.",
                  "error")
        elif action == "quota":
            raw = request.form.get("quota_mb", "").strip()
            if not raw:
                db.execute("UPDATE users SET quota_bytes = NULL WHERE id = ?", (user_id,))
                audit("admin_quota", user_id, email, "plan default", who)
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
                    audit("admin_quota", user_id, email, format_size(quota, "down"), who)
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
                audit("admin_plan", user_id, email, PLANS[key]["name"], who)
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
            with db.transaction() as conn:  # both or neither: no sessions left on a disabled account
                conn.execute("UPDATE users SET disabled = 1, session_version = session_version + 1 "
                             "WHERE id = ?", (user_id,))
                conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            audit("admin_disable", user_id, email, "", who)
            flash(f"{email} is disabled and signed out everywhere.", "info")
        elif action == "enable":
            db.execute("UPDATE users SET disabled = 0 WHERE id = ?", (user_id,))
            audit("admin_enable", user_id, email, "", who)
            flash(f"{email} can sign in again.", "success")
        elif action == "delete":
            if not delete_confirmed():
                pass
            elif request.form.get("confirm_email", "").strip().lower() != email:
                flash("Type the email address to confirm the deletion.", "error")
            else:
                delete_account(user_id)
                audit("admin_delete", user_id, email, "", who)
                flash(f"The account {email} and all its data were deleted.", "info")
                back = admin_url(**state)
        return redirect(back)

    # --- support ---

    def get_ticket_or_404(year, seq):
        row = db.query("SELECT * FROM tickets WHERE ref_year = ? AND ref_seq = ? AND user_id = ?",
                       (year, seq, current_user.id), one=True)
        if row is None:
            abort(404)
        return row

    def ticket_url(endpoint, ticket):
        return url_for(endpoint, **ticket_args(ticket))

    @app.route("/support")
    @login_required
    def support():
        status = request.args.get("status", "all")
        status = status if status in ("open", "closed") else "all"
        tickets = db.query(
            "SELECT t.*, (SELECT COUNT(*) FROM ticket_messages m WHERE m.ticket_id = t.id "
            "AND m.from_admin = 1 AND m.id > t.user_seen_id) AS unread FROM tickets t "
            "WHERE t.user_id = ? AND (? = 'all' OR t.status = ?) "
            "ORDER BY t.updated_at DESC, t.id DESC", (current_user.id, status, status))
        counts = db.query("SELECT COUNT(*) AS total, COALESCE(SUM(status = 'open'), 0) AS open "
                          "FROM tickets WHERE user_id = ?", (current_user.id,), one=True)
        pager = paginate(tickets, request.args.get("page"), TICKETS_PER_PAGE,
                         lambda n, _size: url_for("support", status=None if status == "all"
                                                  else status, page=n if n > 1 else None))
        return render_template("support.html", pager=pager, status=status, counts=counts,
                               kinds=TICKET_KINDS)

    @app.route("/support/new", methods=["GET", "POST"])
    @login_required
    def support_new():
        form = request.form if request.method == "POST" else {}
        if request.method == "POST":
            kind = request.form.get("kind", "")
            subject = clean_name(request.form.get("subject", ""))
            body = clean_ticket_body(request.form.get("body", ""))
            open_count = db.query("SELECT COUNT(*) AS n FROM tickets WHERE user_id = ? AND "
                                  "status = 'open'", (current_user.id,), one=True)["n"]
            if kind not in TICKET_KINDS:
                err = "Please choose what the ticket is about."
            elif not subject or not body:
                err = "Please write a subject and a description."
            elif len(subject) > TICKET_SUBJECT_MAX:
                err = f"The subject can be at most {TICKET_SUBJECT_MAX} characters."
            elif len(body) > TICKET_BODY_MAX:
                err = f"The description can be at most {TICKET_BODY_MAX} characters."
            elif open_count >= OPEN_TICKETS_MAX:
                err = (f"You already have {OPEN_TICKETS_MAX} open tickets. Please close the ones "
                       "that are solved first.")
            else:
                wait, scope, _attempt = take_attempt("ticket", ticket_limit_key())
                err = too_many_message(wait, scope, "new tickets") if wait else None
            ticket_id = None
            if not err:
                with db.transaction() as conn:
                    # Counted again under the write lock: parallel posts cannot pass the cap.
                    if conn.execute("SELECT COUNT(*) FROM tickets WHERE user_id = ? AND "
                                    "status = 'open'", (current_user.id,)).fetchone()[0] \
                            >= OPEN_TICKETS_MAX:
                        err = (f"You already have {OPEN_TICKETS_MAX} open tickets. Please close "
                               "the ones that are solved first.")
                    else:
                        ticket_id = conn.execute("INSERT INTO tickets (user_id, kind, subject) "
                                                 "VALUES (?, ?, ?)",
                                                 (current_user.id, kind, subject)).lastrowid
                        message_id = conn.execute("INSERT INTO ticket_messages (ticket_id, "
                                                  "author_id, body) VALUES (?, ?, ?)",
                                                  (ticket_id, current_user.id, body)).lastrowid
                        conn.execute("UPDATE tickets SET user_seen_id = ? WHERE id = ?",
                                     (message_id, ticket_id))
                        # Numbered by the tickets_ref trigger during the insert.
                        ticket = conn.execute("SELECT * FROM tickets WHERE id = ?",
                                              (ticket_id,)).fetchone()
            if err:
                flash(err, "error")
                return render_template("support_new.html", form=form, kinds=TICKET_KINDS,
                                       subject_max=TICKET_SUBJECT_MAX,
                                       body_max=TICKET_BODY_MAX), 400
            ref = ticket_ref(ticket)
            audit("ticket_opened", current_user.id, current_user.email, f"#{ref}")
            notify_admins(ticket, "new", current_user.email)
            flash(f"Ticket #{ref} was sent. We will answer here and email you when we do.",
                  "success")
            return redirect(ticket_url("support_ticket", ticket))
        return render_template("support_new.html", form=form, kinds=TICKET_KINDS,
                               subject_max=TICKET_SUBJECT_MAX, body_max=TICKET_BODY_MAX)

    @app.route("/support/<int:ticket_id>", methods=["GET", "POST"])
    @login_required
    def support_ticket_by_id(ticket_id):
        """Links from before 1.3.1 name the ticket by its id. 307 keeps a POST, so a page opened
        before the upgrade still sends its form."""
        ticket = db.query("SELECT ref_year, ref_seq FROM tickets WHERE id = ? AND user_id = ?",
                          (ticket_id, current_user.id), one=True)
        if ticket is None:
            abort(404)
        return redirect(ticket_url("support_ticket", ticket), code=307)

    @app.route("/support/<int:year>-<int:seq>", methods=["GET", "POST"])
    @login_required
    def support_ticket(year, seq):
        ticket = get_ticket_or_404(year, seq)
        ticket_id, ref = ticket["id"], ticket_ref(ticket)
        if request.method == "POST":
            action = request.form.get("action")
            if action == "reply":
                body = clean_ticket_body(request.form.get("body", ""))
                if not body:
                    flash("Please write a message.", "error")
                elif len(body) > TICKET_BODY_MAX:
                    flash(f"A message can be at most {TICKET_BODY_MAX} characters.", "error")
                elif (wait := take_attempt("ticket_msg", ticket_limit_key()))[0]:
                    flash(too_many_message(wait[0], wait[1], "messages"), "error")
                else:
                    with db.transaction() as conn:
                        message_id = conn.execute(
                            "INSERT INTO ticket_messages (ticket_id, author_id, body) "
                            "VALUES (?, ?, ?)", (ticket_id, current_user.id, body)).lastrowid
                        # Writing on a closed ticket opens it again, unless the account is
                        # already at its limit of open tickets (the message is kept).
                        open_now = conn.execute("SELECT COUNT(*) FROM tickets WHERE user_id = ? "
                                                "AND status = 'open'",
                                                (current_user.id,)).fetchone()[0]
                        reopened = conn.execute(
                            "UPDATE tickets SET status = 'open', closed_at = NULL, closed_by = '' "
                            "WHERE id = ? AND status = 'closed' AND ? < ?",
                            (ticket_id, open_now, OPEN_TICKETS_MAX)).rowcount
                        still_closed = conn.execute("SELECT status FROM tickets WHERE id = ?",
                                                    (ticket_id,)).fetchone()[0] == "closed"
                        conn.execute("UPDATE tickets SET updated_at = CURRENT_TIMESTAMP, "
                                     "user_seen_id = MAX(user_seen_id, ?) WHERE id = ?",
                                     (message_id, ticket_id))
                    audit("ticket_reopened" if reopened else "ticket_message", current_user.id,
                          current_user.email, f"#{ref}")
                    notify_admins(ticket, "reopened" if reopened else "reply",
                                  current_user.email)
                    flash("Your message was sent." + (
                        " The ticket is open again." if reopened else
                        f" The ticket stays closed: you already have {OPEN_TICKETS_MAX} open "
                        "tickets. Close the solved ones to open it again." if still_closed
                        else ""), "success")
                    return redirect(ticket_url("support_ticket", ticket))
                return render_template("support_ticket.html", ticket=ticket,
                                       messages=ticket_messages(ticket_id), admin_view=False,
                                       kinds=TICKET_KINDS, body_max=TICKET_BODY_MAX,
                                       draft=request.form.get("body", ""),
                                       at_cap=open_ticket_cap_reached(ticket)), 400
            if action == "close":
                closed = db.execute_rowcount(
                    "UPDATE tickets SET status = 'closed', closed_at = CURRENT_TIMESTAMP, "
                    "closed_by = 'user', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND "
                    "status = 'open'", (ticket_id,))
                if closed:
                    audit("ticket_closed", current_user.id, current_user.email, f"#{ref}")
                    flash("The ticket is closed. Write on it any time to open it again.",
                          "success")
                else:
                    flash("This ticket was already closed.", "info")
            return redirect(ticket_url("support_ticket", ticket))
        messages = ticket_messages(ticket_id)
        if messages and messages[-1]["id"] > ticket["user_seen_id"]:
            # Housekeeping: skipped when the database is busy (the dot stays until next time),
            # so reading a ticket never fails behind another request's write.
            db.try_execute("UPDATE tickets SET user_seen_id = MAX(user_seen_id, ?) WHERE id = ?",
                           (messages[-1]["id"], ticket_id))
        return render_template("support_ticket.html", ticket=ticket, messages=messages,
                               admin_view=False, kinds=TICKET_KINDS, body_max=TICKET_BODY_MAX,
                               draft="", at_cap=open_ticket_cap_reached(ticket))

    def admin_support_url(**values):
        # "all" is the default of the filters only: a search for the word "all" is kept.
        args = {k: v for k, v in values.items() if v not in (None, "", 1)
                and not (k in ("status", "kind") and v == "all")}
        return url_for("admin_support", **args)

    @app.route("/admin/support")
    @login_required
    def admin_support():
        require_admin()
        status = request.args.get("status", "all")
        status = status if status in ("open", "closed") else "all"
        kind = request.args.get("kind", "all")
        kind = kind if kind in TICKET_KINDS else "all"
        q = request.args.get("q", "").strip()[:254]
        sort = request.args.get("sort", "updated")
        sort = sort if sort in TICKET_SORTS else "updated"
        direction = "asc" if request.args.get("dir") == "asc" else "desc"
        where, params = ["1 = 1"], []
        if status != "all":
            where.append("t.status = ?")
            params.append(status)
        if kind != "all":
            where.append("t.kind = ?")
            params.append(kind)
        if q:
            number = re.fullmatch(r"#?([0-9]{4})-([0-9]+)", q)
            if number:
                # A ticket number, 2026-12 or #2026-12. Beyond SQLite's range matches nothing.
                where.append("t.ref_year = ? AND t.ref_seq = ?")
                seq = int(number[2])
                params += [int(number[1]), seq if seq < 2 ** 63 else -1]
            elif valid_email(q.lower()):
                # A whole address is one account: ann@x.com must not also list joann@x.com
                # (the Support tickets link of the admin account page searches this way).
                where.append("u.email = ?")
                params.append(q.lower())
            else:
                # casefold (registered in db.get_db) folds every script, not only A to Z,
                # and composes accents, so "élodie" finds "Élodie" however it was typed.
                like = db.search_like(q)
                where.append("(casefold(u.email) LIKE ? ESCAPE '\\' OR casefold(u.first_name "
                             "|| ' ' || u.last_name) LIKE ? ESCAPE '\\')")
                params += [like, like]
        order = ", ".join(f"{part} {direction.upper()}" if not part.endswith("IS NULL")
                          else part for part in TICKET_SORTS[sort].split(", "))
        tickets = db.query(
            "SELECT t.*, u.email, u.first_name, u.last_name, "
            "(SELECT COUNT(*) FROM ticket_messages m WHERE m.ticket_id = t.id) AS messages, "
            "(SELECT COUNT(*) FROM ticket_messages m WHERE m.ticket_id = t.id "
            "AND m.from_admin = 0 AND m.id > t.admin_seen_id) AS unread "
            "FROM tickets t JOIN users u ON u.id = t.user_id WHERE " + " AND ".join(where)
            + f" ORDER BY {order}, t.id {direction.upper()}", params)
        tickets = [dict(t, full_name=full_name(t)) for t in tickets]
        state = {"status": status, "kind": kind, "q": q, "sort": sort, "dir": direction}
        pager = paginate(tickets, request.args.get("page"), TICKETS_PER_PAGE,
                         lambda n, _size: admin_support_url(**state, page=n))
        counts = db.query("SELECT COUNT(*) AS total, COALESCE(SUM(status = 'open'), 0) AS open "
                          "FROM tickets", one=True)
        return render_template("admin_support.html", pager=pager, state=state, counts=counts,
                               kinds=TICKET_KINDS, sorts=list(TICKET_SORTS),
                               support_url=admin_support_url)

    @app.route("/admin/support/<int:ticket_id>", methods=["GET", "POST"])
    @login_required
    def admin_ticket_by_id(ticket_id):
        """Links from before 1.3.1 (admin emails) name the ticket by its id."""
        require_admin()
        ticket = db.query("SELECT ref_year, ref_seq FROM tickets WHERE id = ?", (ticket_id,),
                          one=True)
        if ticket is None:
            abort(404)
        return redirect(ticket_url("admin_ticket", ticket), code=307)

    @app.route("/admin/support/<int:year>-<int:seq>", methods=["GET", "POST"])
    @login_required
    def admin_ticket(year, seq):
        require_admin()
        ticket = db.query("SELECT t.*, u.email, u.first_name, u.last_name FROM tickets t "
                          "JOIN users u ON u.id = t.user_id WHERE t.ref_year = ? AND t.ref_seq = ?",
                          (year, seq), one=True)
        if ticket is None:
            abort(404)
        ticket_id, ref = ticket["id"], ticket_ref(ticket)
        back = ticket_url("admin_ticket", ticket)
        if request.method == "POST":
            action = request.form.get("action")
            if action in ("reply", "reopen_reply"):
                body = clean_ticket_body(request.form.get("body", ""))
                reopen = action == "reopen_reply"
                if ticket["status"] != "open" and not reopen:
                    flash("This ticket was closed meanwhile. Your answer is below: reopen the "
                          "ticket and send it, or leave it closed.", "error")
                elif not body:
                    flash("Please write a message.", "error")
                elif len(body) > TICKET_BODY_MAX:
                    flash(f"A message can be at most {TICKET_BODY_MAX} characters.", "error")
                else:
                    with db.transaction() as conn:
                        if reopen:
                            reopened = conn.execute(
                                "UPDATE tickets SET status = 'open', closed_at = NULL, "
                                "closed_by = '' WHERE id = ? AND status = 'closed'",
                                (ticket_id,)).rowcount
                        # Checked again under the write lock: another admin (or the user)
                        # may have closed the ticket since the page was read.
                        still_open = conn.execute("SELECT status FROM tickets WHERE id = ?",
                                                  (ticket_id,)).fetchone()[0] == "open"
                        if still_open:
                            message_id = conn.execute(
                                "INSERT INTO ticket_messages (ticket_id, author_id, from_admin, "
                                "body) VALUES (?, ?, 1, ?)",
                                (ticket_id, current_user.id, body)).lastrowid
                            conn.execute("UPDATE tickets SET updated_at = CURRENT_TIMESTAMP, "
                                         "admin_seen_id = MAX(admin_seen_id, ?) WHERE id = ?",
                                         (message_id, ticket_id))
                    if still_open:
                        if reopen and reopened:
                            audit("ticket_reopened", ticket["user_id"], ticket["email"],
                                  f"#{ref}", current_user.email)
                        audit("ticket_reply", ticket["user_id"], ticket["email"],
                              f"#{ref}", current_user.email)
                        notify_user(ticket, "reply")
                        flash("Your answer was sent. The user gets an email.", "success")
                        return redirect(back)
                    flash("This ticket was closed meanwhile. Your answer is below: reopen the "
                          "ticket and send it, or leave it closed.", "error")
                    ticket = db.query("SELECT t.*, u.email, u.first_name, u.last_name FROM "
                                      "tickets t JOIN users u ON u.id = t.user_id WHERE t.id = ?",
                                      (ticket_id,), one=True)
                return render_template("support_ticket.html", ticket=ticket,
                                       messages=ticket_messages(ticket_id), admin_view=True,
                                       kinds=TICKET_KINDS, body_max=TICKET_BODY_MAX,
                                       draft=request.form.get("body", ""),
                                       owner_name=full_name(ticket)), 400
            if action == "close":
                if db.execute_rowcount(
                        "UPDATE tickets SET status = 'closed', closed_at = CURRENT_TIMESTAMP, "
                        "closed_by = 'admin', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND "
                        "status = 'open'", (ticket_id,)):
                    audit("ticket_closed", ticket["user_id"], ticket["email"], f"#{ref}",
                          current_user.email)
                    notify_user(ticket, "closed")
                    flash("The ticket is closed. The user gets an email.", "success")
                else:
                    flash("This ticket was already closed.", "info")
            elif action == "reopen":
                if db.execute_rowcount(
                        "UPDATE tickets SET status = 'open', closed_at = NULL, closed_by = '', "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'closed'",
                        (ticket_id,)):
                    audit("ticket_reopened", ticket["user_id"], ticket["email"],
                          f"#{ref}", current_user.email)
                    flash("The ticket is open again.", "success")
                else:
                    flash("This ticket is already open.", "info")
            return redirect(back)
        messages = ticket_messages(ticket_id)
        if messages and messages[-1]["id"] > ticket["admin_seen_id"]:
            db.try_execute("UPDATE tickets SET admin_seen_id = MAX(admin_seen_id, ?) WHERE id = ?",
                           (messages[-1]["id"], ticket_id))
        return render_template("support_ticket.html", ticket=ticket, messages=messages,
                               admin_view=True, kinds=TICKET_KINDS, body_max=TICKET_BODY_MAX,
                               draft="", owner_name=full_name(ticket))

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
        download_name = f"{stem(zip_member_name(doc['original_name'], doc_format(doc)))}.zip"
        if doc["stored_name"].endswith(".zip"):
            resp = send_from_directory(folder, doc["stored_name"], mimetype="application/zip",
                                       as_attachment=True, download_name=download_name)
        else:
            # Stored before 1.2.12 and not converted yet (flask zip-receipts): zip it now, so
            # every download is a ZIP.
            path = os.path.join(folder, doc["stored_name"])
            if not os.path.isfile(path):
                abort(404)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(path, zip_member_name(doc["original_name"], doc_format(doc)))
            buf.seek(0)
            resp = send_file(buf, mimetype="application/zip", as_attachment=True,
                             download_name=download_name)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        # Personal documents must never sit in a shared or browser cache.
        resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @app.route("/documents/<int:doc_id>/delete", methods=["POST"])
    @login_required
    def document_delete(doc_id):
        get_document_or_404(doc_id)
        if delete_confirmed():
            # List and delete together, like the year delete: the file to remove is the one the
            # row points to at the moment it goes (zip-receipts may have replaced it meanwhile).
            with db.transaction() as conn:
                docs = conn.execute("SELECT * FROM documents WHERE id = ? AND user_id = ?",
                                    (doc_id, current_user.id)).fetchall()
                conn.execute("DELETE FROM documents WHERE id = ? AND user_id = ?",
                             (doc_id, current_user.id))
            remove_files(docs)
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
        ext = doc_format(doc)
        if not name:
            flash("Please enter a name for the document.", "error")
        elif kind not in DOCUMENT_KINDS:
            flash("Please choose a document type.", "error")
        else:
            if file_ext(name) != ext:
                name = f"{name}.{ext}"  # keep the real extension so downloads still open
            # The file inside the ZIP carries the name too. It is rewritten into a temporary file
            # outside the database write lock (a big receipt takes seconds and would stall every
            # other request), then moved into place under the lock only if nobody changed the
            # receipt meanwhile; two renames at once (a double click, two tabs) simply retry.
            folder = os.path.join(app.config["UPLOAD_DIR"], str(current_user.id))
            for _attempt in range(3):
                current = db.query("SELECT * FROM documents WHERE id = ? AND user_id = ?",
                                   (doc_id, current_user.id), one=True)
                if current is None:
                    break
                path = os.path.join(folder, current["stored_name"])
                size, tmp = current["size"], None
                if name != current["original_name"] and path.endswith(".zip"):
                    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
                    try:
                        _member, data = read_receipt(path)
                        size = write_receipt_zip(tmp, zip_member_name(name, ext), data)
                    except (OSError, zipfile.BadZipFile, zlib.error, IndexError) as exc:
                        app.logger.error("Could not rename the file in %s: %s", path, exc)
                        size = current["size"]
                        if os.path.exists(tmp):
                            os.remove(tmp)
                        tmp = None
                try:
                    with db.transaction() as conn:
                        changed = conn.execute(
                            "UPDATE documents SET original_name = ?, kind = ?, size = ? "
                            "WHERE id = ? AND original_name = ? AND size = ?",
                            (name, kind, size, doc_id, current["original_name"],
                             current["size"])).rowcount
                        if changed and tmp:
                            os.replace(tmp, path)
                            tmp = None
                finally:
                    if tmp and os.path.exists(tmp):
                        os.remove(tmp)
                if changed:
                    break
            flash("Document updated.", "success")
        return redirect(request.referrer or url_for("index"))


app = create_app()

if __name__ == "__main__":
    os.umask(0o077)  # the database and receipts are private to the account running the app
    app.run(host="127.0.0.1", port=app.config["APP_PORT"],
            debug=os.getenv("FLASK_DEBUG") == "1")
