"""Nomad Life: track your yearly movements and keep your receipts in one place."""
import base64
import csv
import functools
import hashlib
import hmac
import io
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlsplit
import uuid
from collections import OrderedDict
import unicodedata
import zipfile
from datetime import date, datetime, timedelta, timezone

import click
import segno
import webauthn
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, AuthenticatorTransport,
                                      PublicKeyCredentialDescriptor, ResidentKeyRequirement,
                                      UserVerificationRequirement)
from dotenv import load_dotenv
from flask import (Flask, Response, abort, after_this_request, current_app, flash, g,
                   has_request_context, redirect, render_template, request, send_file,
                   send_from_directory, session, stream_with_context, url_for)
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

APP_VERSION = "1.12.5"
# "Contact Us" in the footer of every page, the landing page included (static/404.html, a
# standalone file, repeats the address).
CONTACT_EMAIL = "info@nomadlife.pro"
# Where the documentation sends people who need help (a lost phone, questions).
SUPPORT_EMAIL = "support@nomadlife.pro"
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
# Hyphens and dashes count as a space, so "Guinea Bissau" is Guinea-Bissau (theme.js folds
# the same way).
FOLD_SPACES = {ord(ch): " " for ch in "-\u2010\u2011\u2012\u2013\u2014\u2015"}


def fold(value):
    """Search key: no accents, no case, single spaces, a hyphen as a space and "&" as "and"
    ("Côte d’Ivoire" -> "cote d'ivoire", "Trinidad & Tobago" -> "trinidad and tobago")."""
    value = unicodedata.normalize("NFKD", value.replace("\u2019", "'"))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(value.translate(FOLD_SPACES).replace("&", " and ").casefold().split())


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
# a paid plan is never below Free. Paid plans are yearly only (Stripe, see "billing"): a
# monthly payment of a few cents would mostly go to card fees. users.plan follows Stripe's
# webhook, or an admin's choice for a gift.
PLANS = OrderedDict([
    # "year_cents": the price of a year, in US cents, before VAT or sales tax (Stripe adds it
    # at checkout where it applies). It must match the plan's Stripe price. "package" is the
    # accountant package, from Pro on.
    ("free", {"name": "Free", "year_cents": 0, "quota_mb": None, "receipt_mb": None,
              "package": False, "extras": []}),
    ("pro", {"name": "Pro", "year_cents": 999, "quota_mb": 5 * 1024, "receipt_mb": 25,
             "package": True, "extras": ["Accountant package (PDF, spreadsheets, receipts)"]}),
    ("plus", {"name": "Nomad+", "year_cents": 1999, "quota_mb": 25 * 1024, "receipt_mb": 50,
              "package": True, "extras": ["Priority support"]}),
])
PLAN_CURRENCY, PLAN_SYMBOL = "USD", "$"
# Stripe (billing): API, timeouts, how old a webhook signature may be, the largest event body
# read, and the subscription states that still pay for a plan (past_due: Stripe is retrying
# the card, so the plan stays meanwhile).
STRIPE_API_URL = "https://api.stripe.com/v1/"
# Every request names this API version, so what Stripe sends back never changes under the app
# when the account's default version moves on; create the webhook endpoint with the same one.
STRIPE_API_VERSION = "2026-08-26.dahlia"
STRIPE_TIMEOUT_SECONDS = 10
STRIPE_TOLERANCE_SECONDS = 300
BILLING_WEBHOOK_MAX = 256 * 1024
PAID_STATUSES = ("active", "trialing", "past_due")
# States in which a subscription still exists and Stripe may charge it again: no second
# checkout, no account deletion, "Manage billing" instead (Stripe counts these as active).
OPEN_STATUSES = PAID_STATUSES + ("unpaid", "paused")
# The company that runs Nomad Life and sells its plans (legal pages, footer, structured data).
COMPANY = {"name": "Nemax Tech LLC", "short": "Nemax Tech", "url": "https://nemax.tech",
           "city": "Sofia", "country": "Bulgaria", "country_code": "BG", "uic": "207405380",
           "vat": "BG207405380"}
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
# Public guides (/guides/<slug>, templates/guides/<slug>.html), for people searching about
# day counting and tax residency. Indexable and in the sitemap; updated is the date shown on
# the page, in its structured data and as the sitemap's lastmod: change it with the text.
GUIDES = [
    {"slug": "183-day-rule", "updated": "2026-10-02",
     "title": f"The {RESIDENCE_THRESHOLD} day rule, explained for digital nomads",
     "seo_title": f"The {RESIDENCE_THRESHOLD} day rule explained for digital nomads | Nomad Life",
     "description": (f"What the {RESIDENCE_THRESHOLD} day rule really says about tax residency, "
                     "why staying under it "
                     "everywhere does not make you tax free, and how to keep a record that holds "
                     "up.")},
    {"slug": "count-days-in-a-country", "updated": "2026-10-02",
     "title": "How to count the days you spend in each country",
     "seo_title": "How to count your days in each country | Nomad Life",
     "description": ("Arrival and departure days, overlapping stays, short trips and leap years: "
                     "how to count your days per country correctly, and how to rebuild a past "
                     "year.")},
    {"slug": "proof-of-residence-documents", "updated": "2026-10-02",
     "title": "Which documents prove where you lived",
     "seo_title": "Proof of residence documents for digital nomads | Nomad Life",
     "description": ("The rental contracts, bills, tickets and statements that show where you "
                     "spent your year, which ones carry the most weight, and how long to keep "
                     "them.")},
]
GUIDES_BY_SLUG = {g["slug"]: g for g in GUIDES}
# Legal pages (/terms, /privacy, /refunds, templates/legal/<endpoint>.html): public and
# indexable; updated is the date on the page and the sitemap's lastmod.
LEGAL_PAGES = {
    "terms": {"title": "Terms of Service", "updated": "2026-10-02",
              "description": "The terms for using Nomad Life and its paid plans."},
    "privacy": {"title": "Privacy Policy", "updated": "2026-10-02",
                "description": "What Nomad Life stores about you, why, for how long, and your "
                               "rights."},
    "refunds": {"title": "Refund Policy", "updated": "2026-10-02",
                "description": "How cancelling and refunds work for Nomad Life's yearly plans."},
}
# Days after a payment (the first one or a renewal) in which it is refunded in full on request.
REFUND_DAYS = 14
GUIDES_TITLE = "Guides for digital nomads | Nomad Life"
GUIDES_DESCRIPTION = (f"Plain guides on the {RESIDENCE_THRESHOLD} day rule, counting your days per "
                      "country and the "
                      "documents that prove where you lived.")
DOCS_TITLE = "Documentation | Nomad Life"
DOCS_DESCRIPTION = ("How to use Nomad Life: set up a year and your base, log your stays, see how "
                    "days are counted toward the 183 day line, keep receipts and download the "
                    "accountant package.")
SEO_TITLE = "Digital nomad day tracker and receipt vault | Nomad Life"
SEO_DESCRIPTION = ("Count your days in each country, watch the 183 day line and keep rental contracts, "
                   "hotel bills and flight tickets in one place. Free for digital nomads.")
# Table of contents of /docs: (id, title, [(id, title) of the subsections]), in page order.
# tests/test_docs.py checks it against the sections of templates/docs.html.
DOCS_TOC = [
    ("getting-started", "Getting started", [("sign-up", "Create your account"),
                                            ("sign-in", "Sign in and passwords")]),
    ("years", "Years and your base", [("create-year", "Create a year"),
                                      ("base", "Change your base or delete a year")]),
    ("movements", "Movements", [("add-movement", "Add a movement"),
                                ("country-picker", "The country picker"),
                                ("edit-movement", "Edit or delete a movement")]),
    ("counting", "How days are counted", [("counting-example", "A worked example"),
                                          ("threshold", "The 183 day line")]),
    ("dashboard", "Your dashboard", [("cards", "The four cards"),
                                     ("movements-table", "The Movements table"),
                                     ("days-per-country", "Days per country"),
                                     ("map", "The map")]),
    ("receipts", "Receipts", [("upload", "Upload a receipt"),
                              ("manage-receipts", "Download, rename or delete"),
                              ("storage", "Storage")]),
    ("package", "Accountant package", []),
    ("plans", "Plans", [("billing", "Upgrade and billing")]),
    ("settings", "Settings", [("profile", "Name, password and email"),
                              ("two-factor", "Two-factor sign in"),
                              ("devices", "Devices and activity"),
                              ("delete-account", "Delete your account")]),
    ("support", "Support", []),
    ("troubleshooting", "Troubleshooting", [("no-email", "No confirmation email"),
                                            ("too-many", "Too many failed sign in attempts"),
                                            ("lost-phone", "Lost phone with the authenticator app"),
                                            ("refused", "A receipt is refused"),
                                            ("numbers", "The day count looks wrong")]),
    ("shortcuts", "Keyboard shortcuts", []),
]
# Paths that need an account. Kept out of search engines via robots.txt.
PRIVATE_PATHS = ["/app", "/year/", "/movements/", "/documents/", "/reset/", "/verify/", "/admin",
                 "/account", "/settings", "/plan", "/support"]
ADMIN_USERS_PER_PAGE = 10
# Free page that shows where an IP address is (city, country, network owner), opened from the
# admin account page.
IP_LOOKUP_URL = "https://ipinfo.io/"
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
ADMIN_AUDIT_PER_PAGE = 20
# Security Activity menu: "last 30 days" exports and purges cover this many days; exports
# read the log this many rows at a time, so a year of events is never held in memory.
AUDIT_EXPORT_DAYS = 30
AUDIT_EXPORT_CHUNK = 1000
AUDIT_RANGES = {"30": f"last-{AUDIT_EXPORT_DAYS}-days", "all": "all"}
# Two-factor sign in (TOTP, RFC 6238): 6 digits every 30 seconds, one step of clock drift
# either way, and this long to type the code after the password.
# Passkeys: at most this many per account; names as long as a first name; a ceremony (the
# browser's passkey prompt) must finish within these seconds; the credential JSON a form may
# carry (a few KB in practice).
PASSKEYS_MAX = 10
PASSKEY_CEREMONY_SECONDS = 300
PASSKEY_CREDENTIAL_MAX = 64 * 1024
TOTP_STEP_SECONDS = 30
TOTP_PENDING_SECONDS = 300
# A signed in browser not seen for this long is forgotten (the cookies last 30 days).
SESSION_IDLE_DAYS = 31
# Passwords are checked against Have I Been Pwned's list of breached passwords. Only the
# first 5 characters of the password's SHA-1 leave the server (k-anonymity), and the check is
# skipped when the service does not answer in time.
PWNED_URL = "https://api.pwnedpasswords.com/range/"
PWNED_TIMEOUT_SECONDS = 3
# hCaptcha on Sign up and Forgot password (off while config.env has no keys).
HCAPTCHA_VERIFY_URL = "https://api.hcaptcha.com/siteverify"
HCAPTCHA_TIMEOUT_SECONDS = 5
# The off screen field no person sees: only bots fill it in. Not called "website" or the like,
# which password managers fill from a saved identity (the person would get no email).
HONEYPOT_FIELD = "hp_check"
# hCaptcha refusals that mean config.env is wrong, not that a bot answered: every form fails.
# (not-using-dummy-secret: the test site key with a real secret.)
HCAPTCHA_CONFIG_ERRORS = frozenset({"missing-input-secret", "invalid-input-secret",
                                    "sitekey-secret-mismatch", "not-using-dummy-passcode",
                                    "not-using-dummy-secret"})
# Gmail ignores dots, so bots sign up as t.o.n.yluu.5.5.95@gmail.com: one mailbox, endless
# "new" addresses. People rarely use more than two (first.middle.last).
GMAIL_DOMAINS = ("gmail.com", "googlemail.com")
GMAIL_MAX_DOTS = 2
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
       "style-src 'self' 'unsafe-inline'; "
       "font-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
# The same policy on the pages that show the hCaptcha widget (its script, iframe, styles and
# requests come from hcaptcha.com); every other page keeps CSP.
HCAPTCHA_HOSTS = "https://hcaptcha.com https://*.hcaptcha.com"
CAPTCHA_CSP = (CSP.replace("script-src 'self'", f"script-src 'self' {HCAPTCHA_HOSTS}")
               .replace("style-src 'self'", f"style-src 'self' {HCAPTCHA_HOSTS}")
               .replace("connect-src 'self'", f"connect-src 'self' {HCAPTCHA_HOSTS}")
               .replace("object-src", f"frame-src {HCAPTCHA_HOSTS}; object-src"))
# Browser features no page uses: refused for the page and anything it embeds (hCaptcha too).
PERMISSIONS_POLICY = ("accelerometer=(), browsing-topics=(), camera=(), display-capture=(), "
                      "geolocation=(), gyroscope=(), magnetometer=(), microphone=(), midi=(), "
                      "payment=(), usb=()")
# /.well-known/security.txt (RFC 9116): its Expires is always this far ahead.
SECURITY_TXT_DAYS = 180
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
    """A city or country as typed: composed (NFC), spaces collapsed, control and invisible
    format characters (such as U+202E, which reverses how text is shown) and blank letters
    removed, but the zero width joiners kept (Persian and Sinhala names need them). "" when
    nothing visible is left, so a place made of invisible characters counts as missing."""
    value = unicodedata.normalize("NFC", value or "")
    value = "".join(ch for ch in value if ch not in BLANK_LETTERS
                    and (unicodedata.category(ch)[0] != "C" or ch.isspace() or ch in JOINERS))
    value = " ".join(value.split())
    return value if visible(value) else ""


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


def hcaptcha_settings():
    """(site key, secret) from config.env: both empty turns the captcha off, one alone is a
    mistake that would either show a widget nobody can pass or check nothing."""
    sitekey = (os.getenv("HCAPTCHA_SITEKEY") or "").strip()
    secret = (os.getenv("HCAPTCHA_SECRET") or "").strip()
    if bool(sitekey) != bool(secret):
        raise SystemExit("config.env: set both HCAPTCHA_SITEKEY and HCAPTCHA_SECRET, or "
                         "leave both empty to turn the captcha off.")
    return sitekey, secret


BILLING_KEYS = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_PRO",
                "STRIPE_PRICE_PLUS")


def billing_settings():
    """The Stripe settings from config.env: all four set turns online payment on, all empty
    leaves it off ("coming soon"); a partial setup would take money without knowing which plan
    it pays for, or never hear of it, so it stops the app."""
    values = {k: (os.getenv(k) or "").strip() for k in BILLING_KEYS}
    if any(values.values()) and not all(values.values()):
        missing = ", ".join(k for k, v in values.items() if not v)
        raise SystemExit(f"config.env: online payment needs all of {', '.join(BILLING_KEYS)} "
                         f"(missing: {missing}), or none of them to keep it off.")
    return values


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
    hcaptcha_sitekey, hcaptcha_secret = hcaptcha_settings()
    billing = billing_settings()
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
        # hCaptcha on Sign up and Forgot password, so bots are refused before any email.
        HCAPTCHA_SITEKEY=hcaptcha_sitekey,
        HCAPTCHA_SECRET=hcaptcha_secret,
        # Stripe: yearly plans paid on Stripe's hosted checkout (off while empty).
        **billing,
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
        # "Forgot password" sends its email after answering, so how long Gmail takes does not
        # tell whether the address has an account (tests send at once, to read the outbox).
        EMAIL_IN_BACKGROUND=True,
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
            # Numbered email changes are only read by their undo links (a day of margin).
            db.try_execute("DELETE FROM email_changes WHERE created_at < datetime('now', ?)",
                           (f"-{EMAIL_REVERT_MAX_AGE // 86400 + 1} days",))
            db.try_execute("DELETE FROM billing_events WHERE received_at < datetime('now', ?)",
                           (f"-{AUDIT_DAYS} days",))
            db.try_execute("DELETE FROM passkey_challenges WHERE created_at < datetime('now', ?)",
                           (f"-{PASSKEY_CEREMONY_SECONDS} seconds",))

    @app.after_request
    def security_headers(resp):
        # No page may be framed (clickjacking), sniffed into another type, or leak its path to
        # other sites. Signed in pages hold private data: never keep them in a cache.
        resp.headers.setdefault("X-Frame-Options", "DENY")
        # Pages may only run the app's own script files: even if some text ever escaped the
        # template escaping, an injected <script> or onerror= would not run. Receipts (PDFs
        # and images) only get frame-ancestors, as a full policy breaks Chrome's PDF viewer.
        if resp.mimetype == "text/html":
            policy = CAPTCHA_CSP if g.get("captcha") else CSP
        else:
            policy = "frame-ancestors 'none'"
        resp.headers.setdefault("Content-Security-Policy", policy)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        if resp.status_code == 429 and g.get("retry_after"):
            resp.headers.setdefault("Retry-After", str(g.retry_after))
        if request.endpoint != "static" and current_user.is_authenticated:
            resp.headers["Cache-Control"] = "private, no-store"
        return resp

    # CSS and JS are cached for a week, so their URLs change whenever their content does,
    # also between releases.
    digest = hashlib.sha256()
    for name in ("css/style.css", "js/theme.js", "js/theme-init.js", "js/docs.js",
                 "js/captcha.js", "js/passkeys.js", "js/billing.js"):
        with open(os.path.join(app.static_folder, name), "rb") as fh:
            digest.update(fh.read())
    asset_version = digest.hexdigest()[:10]

    @app.context_processor
    def inject_globals():
        return {"app_version": APP_VERSION, "contact_email": CONTACT_EMAIL, "support_email": SUPPORT_EMAIL, "asset_version": asset_version, "countries": COUNTRIES, "country_data": COUNTRY_DATA,
                "document_kinds": DOCUMENT_KINDS,
                "billing_on": billing_on(), "company": COMPANY,
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
    app.add_template_global(ip_lookup_url, "ip_lookup_url")
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
            if not has_two_factor(row):
                raise click.ClickException(f"Two-factor sign in is not on for {email}.")
            # Both methods: the authenticator app and every passkey.
            with db.transaction() as conn:
                conn.execute("UPDATE users SET totp_secret = NULL, totp_step = 0 WHERE id = ?",
                             (row["id"],))
                conn.execute("DELETE FROM passkeys WHERE user_id = ?", (row["id"],))
            audit("2fa_reset", row["id"], email, "", "command line")
            security_alert(email, "Two-factor sign in was removed from your Nomad Life account "
                           "by the person who runs the server.",
                           advice=("This is usually because you lost the phone with your "
                                   "authenticator app or your passkey: sign in with your password "
                                   "and set up two-factor sign in again on the Settings page. If "
                                   "you did "
                                   "not ask for this, reset your password now with the link "
                                   "below and contact the person who runs the server."))
        click.echo(f"Two-factor sign in removed from {email}. They can sign in with the "
                   "password alone and turn it on again on the Settings page.")

    @app.cli.command("forget-test-billing")
    @click.option("--dry-run", is_flag=True, help="Only show the report, change nothing.")
    def forget_test_billing(dry_run):
        """After switching to live Stripe keys: forget the subscriptions and customers made in
        test mode, which the live webhook never updates (a test purchase would keep its plan,
        block a real checkout and the account's deletion). Asks Stripe about each one with the
        live key: only what Stripe answers "No such ..." for is forgotten."""
        if not billing_on() or "_live_" not in app.config["STRIPE_SECRET_KEY"]:
            raise click.ClickException("Put the live Stripe keys in config.env first: this "
                                       "asks the live account which ids it does not know.")
        with app.test_request_context(environ_base={"REMOTE_ADDR": "command line"}):
            def unknown(kind, object_id):
                if stripe_api("GET", f"{kind}s/{object_id}") is not None:
                    return False
                if f"no such {kind}" in g.get("stripe_error", "").lower():
                    return True
                raise click.ClickException(f"Stripe did not answer about {object_id}; nothing "
                                           "was changed. Try again later.")
            subs = [r for r in db.query("SELECT * FROM subscriptions ORDER BY id")
                    if unknown("subscription", r["subscription_id"])]
            customers = [r for r in db.query("SELECT id, email, stripe_customer_id FROM users "
                                             "WHERE stripe_customer_id IS NOT NULL ORDER BY id")
                         if unknown("customer", r["stripe_customer_id"])]
            if not subs and not customers:
                click.echo("Nothing from test mode: every stored subscription and customer "
                           "exists in the live account.")
                return
            for r in subs:
                click.echo(f"Subscription {r['subscription_id']} ({r['plan']}, {r['status']}), "
                           f"account {r['user_id'] or 'deleted'}")
            for r in customers:
                click.echo(f"Customer {r['stripe_customer_id']} of {r['email']}")
            if dry_run:
                click.echo("Dry run: nothing changed.")
                return
            if not click.confirm(f"Forget these {len(subs)} subscriptions and {len(customers)} "
                                 "customers?"):
                click.echo("Nothing changed.")
                return
            changed = []
            with db.transaction() as conn:
                for r in subs:
                    conn.execute("DELETE FROM subscriptions WHERE id = ? AND subscription_id = ?",
                                 (r["id"], r["subscription_id"]))
                for r in customers:
                    conn.execute("UPDATE users SET stripe_customer_id = NULL WHERE id = ? AND "
                                 "stripe_customer_id = ?", (r["id"], r["stripe_customer_id"]))
                # Accounts whose plan a test subscription paid for: what is left pays, or Free
                # (a plan an admin gave, which no test subscription paid for, stays).
                for uid in {r["user_id"] for r in subs if r["user_id"]}:
                    user = conn.execute("SELECT email, plan FROM users WHERE id = ?",
                                        (uid,)).fetchone()
                    test_paid = {r["plan"] for r in subs
                                 if r["user_id"] == uid and r["status"] in PAID_STATUSES}
                    after = paid_plan(conn, uid)
                    if user and user["plan"] in test_paid and after != user["plan"]:
                        conn.execute("UPDATE users SET plan = ? WHERE id = ?", (after, uid))
                        audit("plan_downgraded", uid, user["email"],
                              f"{plan_named(user['plan'])['name']} to {plan_named(after)['name']}"
                              " (test mode purchase forgotten)", "command line", conn=conn)
                        changed.append(user["email"])
        click.echo(f"Forgot {len(subs)} subscriptions and {len(customers)} customers."
                   + (f" Back on a lower plan: {', '.join(changed)}." if changed else ""))

    @app.cli.command("check-fake-users")
    @click.option("--min-age-hours", type=click.IntRange(0, None), default=FAKE_MIN_AGE_HOURS,
                  show_default=True, help="Only accounts that joined at least this long ago.")
    @click.option("--dry-run", is_flag=True, help="Only show the report, delete nothing.")
    def check_fake_users(min_age_hours, dry_run):
        """Report accounts that look fake and offer to delete them.

        Listed: confirmed, never signed in, no years, receipts or tickets, not an admin, not
        disabled, no plan or quota set by an admin, joined at least --min-age-hours ago. Bots sign people's work addresses up, and until 1.4.0 the
        company mail scanners that open every link confirmed those accounts."""
        admins = app.config["ADMIN_EMAILS"]
        rows = fake_user_candidates(min_age_hours, admins)
        if not rows:
            click.echo("No accounts look fake: every confirmed account has signed in or holds "
                       "data.")
            return
        head = ("#", "Email", "Joined (UTC)", "Confirmed after", "Confirm IP", "Sign up IP")
        table = [head] + [
            (str(i), r["email"], r["created_at"][:16],
             short_duration(r["confirm_seconds"]) + (", likely scanner" if r["scanner"] else ""),
             r["confirm_ip"] or "not recorded", r["signup_ip"] or "not recorded")
            for i, r in enumerate(rows, 1)]
        widths = [max(len(line[c]) for line in table) for c in range(len(head))]
        for line in table:
            click.echo("  ".join(cell.ljust(w) for cell, w in zip(line, widths)).rstrip())
        scanners = sum(r["scanner"] for r in rows)
        click.echo(f"\n{len(rows)} account{'' if len(rows) == 1 else 's'} confirmed but never "
                   f"signed in, with no data, joined over {min_age_hours} hours ago. "
                   f"{scanners} {'was' if scanners == 1 else 'were'} confirmed within "
                   f"{SCANNER_SECONDS // 60} minutes of signing up. That is typical of a mail "
                   "scanner opening the link, not proof: check the Confirm IP (a cloud network "
                   "such as Microsoft or Amazon, not a home or office line).")
        if dry_run:
            click.echo("Dry run: nothing deleted.")
            return
        click.echo("Take a backup first: .venv/bin/python scripts/backup.py --dest DIR --keep 14")
        chosen = None
        while chosen is None:
            chosen = parse_selection(click.prompt(
                f"Delete all {len(rows)} (a), some (numbers like 1,3,5-7), or none (n)?",
                default="n", show_default=False), len(rows))
            if chosen is None:
                click.echo(f"Please answer a, n, or numbers from 1 to {len(rows)}.")
        if not chosen:
            click.echo("Nothing deleted.")
            return
        picked = [rows[i - 1] for i in chosen]
        if click.prompt(f"Type DELETE to delete {len(picked)} account"
                        f"{'' if len(picked) == 1 else 's'}", default="",
                        show_default=False) != "DELETE":
            click.echo("Nothing deleted.")
            return
        deleted, kept = [], []
        for r in picked:
            # The same conditions again in the DELETE itself: an account that signed in or
            # added data since the report stays (and an admin is never touched).
            fake_sql, fake_params = fake_user_sql(admins, min_age_hours)
            gone = r["email"] not in admins and db.execute_rowcount(
                f"DELETE FROM users WHERE id = ? AND id IN (SELECT u.id FROM users u WHERE "
                f"{fake_sql}) AND email = ?", (r["id"], *fake_params, r["email"]))
            if not gone:
                kept.append(r["email"])
                continue
            deleted.append(r["email"])
            audit("admin_delete_fake", r["id"], r["email"],
                  fake_reason(r["created_at"], r["confirmed_at"], min_age_hours),
                  "check-fake-users")
            try:  # an account without receipts has at most an empty folder
                os.rmdir(os.path.join(app.config["UPLOAD_DIR"], str(r["id"])))
            except OSError:
                pass
        if deleted:  # one row for the whole run too, as for "Delete these N accounts"
            audit("admin_fake_cleanup", None, "", plural(len(deleted), "account"),
                  "check-fake-users")
        click.echo(f"Deleted {len(deleted)} account{'' if len(deleted) == 1 else 's'}"
                   + (": " + ", ".join(deleted) if deleted else "") + ".")
        if kept:
            click.echo("Kept, because they signed in, added data or were changed by an admin "
                       "meanwhile: "
                       + ", ".join(kept) + ".")

    return app


# ---------- helpers ----------

FAKE_MIN_AGE_HOURS = 24  # check-fake-users leaves newer accounts alone: they may sign in yet
SCANNER_SECONDS = 120  # confirmed this soon after sign up: likely a mail scanner opening the link

# An account check-fake-users may list and delete: confirmed, never signed in, holding nothing
# (no years, so no stays or receipts, and no tickets), and never touched by an admin: a disabled
# account blocks its address from signing up again, and a plan or quota set by hand means a
# real customer. Used again in the DELETE itself, so an account that signs in, adds data or is
# changed by an admin between the report and the answer is kept.
FAKE_USER_WHERE = (
    "u.verified_at IS NOT NULL AND u.last_login_at IS NULL AND u.disabled = 0 "
    "AND u.plan = 'free' AND u.quota_bytes IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM years y WHERE y.user_id = u.id) "
    "AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.user_id = u.id) "
    "AND NOT EXISTS (SELECT 1 FROM tickets t WHERE t.user_id = u.id)")


def quota_mb_text(quota_bytes):
    """A custom quota in MB for the admin form, to two decimals as it was typed (1.25, 1.04,
    500): saving the form unchanged keeps the same quota."""
    if quota_bytes is None:
        return ""
    return f"{quota_bytes / MB:.2f}".rstrip("0").rstrip(".")


def fake_user_sql(admins, min_age_hours=FAKE_MIN_AGE_HOURS, asof=None):
    """(sql, params): the condition on `users u` for an account that looks fake. The one rule
    behind check-fake-users, its DELETE, and the "Likely fake" chip and filter of the admin
    table: FAKE_USER_WHERE, joined at least min_age_hours ago, and not an admin. asof (UTC,
    "YYYY-MM-DD HH:MM:SS") fixes "ago" to a moment, so a list and its deletion agree."""
    sql = f"({FAKE_USER_WHERE} AND u.created_at <= datetime(?, ?)"
    params = [asof or "now", f"-{int(min_age_hours)} hours"]
    if admins:
        sql += f" AND u.email NOT IN ({', '.join('?' * len(admins))})"
        params += sorted(admins)
    return sql + ")", params


def fake_reason(created_at, confirmed_at, min_age_hours=FAKE_MIN_AGE_HOURS):
    """Why an account looks fake, for the chip: how soon after sign up it was confirmed (a
    mail scanner when under SCANNER_SECONDS), then the common part."""
    seconds = confirm_delay(created_at, confirmed_at)
    if seconds is not None and 0 <= seconds < SCANNER_SECONDS:
        first = f"Confirmed {short_duration(seconds)} after sign up, likely by a mail scanner"
    elif seconds is not None and seconds >= 0:
        first = f"Confirmed {short_duration(seconds)} after sign up"
    else:
        first = "Confirmed"
    return f"{first}; never signed in, no data after {plural(int(min_age_hours), 'hour')}."


def confirm_delay(created_at, confirmed_at):
    """Seconds between sign up and email confirmation, or None when unknown."""
    if not created_at or not confirmed_at:
        return None
    try:
        return (datetime.strptime(confirmed_at[:19], "%Y-%m-%d %H:%M:%S")
                - datetime.strptime(created_at[:19], "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return None


def fake_user_candidates(min_age_hours, admins):
    """Accounts that look fake, oldest first, with the evidence: when and from where the email
    was confirmed (the first account_confirmed event) and the sign up IP. Admins never."""
    fake_sql, fake_params = fake_user_sql(admins, min_age_hours)
    rows = db.query(
        "SELECT u.id, u.email, u.created_at, u.signup_ip, "
        "(SELECT a.created_at FROM audit_log a WHERE a.user_id = u.id "
        " AND a.event = 'account_confirmed' ORDER BY a.id LIMIT 1) AS confirmed_at, "
        "(SELECT a.ip FROM audit_log a WHERE a.user_id = u.id "
        " AND a.event = 'account_confirmed' ORDER BY a.id LIMIT 1) AS confirm_ip "
        f"FROM users u WHERE {fake_sql} "
        "ORDER BY u.created_at, u.id", fake_params)
    out = []
    for r in rows:
        if r["email"] in admins:
            continue
        seconds = confirm_delay(r["created_at"], r["confirmed_at"])
        out.append(dict(r, confirm_seconds=seconds,
                        scanner=seconds is not None and 0 <= seconds < SCANNER_SECONDS))
    return out


def short_duration(seconds):
    """30 s, 4 min, 3 h, 2 d."""
    if seconds is None:
        return "not recorded"
    seconds = max(int(seconds), 0)
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds // size} {unit}"
    return f"{seconds} s"


def parse_selection(answer, count):
    """The accounts chosen at the prompt: "a" for all, "n" or nothing for none, or numbers
    and ranges ("1,3,5-7"). None when the answer cannot be read."""
    answer = answer.strip().lower()
    if answer in ("a", "all"):
        return list(range(1, count + 1))
    if answer in ("", "n", "no", "none"):
        return []
    chosen = set()
    for part in answer.replace(" ", ",").split(","):
        if not part:
            continue
        low, _, high = part.partition("-")
        if not low.isdigit() or (high and not high.isdigit()):
            return None
        low, high = int(low), int(high or low)
        if not 1 <= low <= high <= count:
            return None
        chosen.update(range(low, high + 1))
    return sorted(chosen)


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


def ip_lookup_url(ip):
    """The ipinfo.io page of a public address, or None for text that is not an IP or for an
    address with no location (local, private and documentation ranges)."""
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if not addr.is_global:
        return None
    return IP_LOOKUP_URL + quote(str(addr), safe=":.")


def client_ip():
    return request.remote_addr or "unknown"


def limit_keys(kind, email=None, device=None):
    keys = [("ip", f"ip:{client_ip()}")]
    if device:
        # The account is part of the key, so a password reset can lift every device's lock.
        keys.append(("device", f"device:{email}:{device}"))
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
               (f"email:{email}", f"device:{email}:{device}"))


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


def totp_setup_secret(user_id, create=True):
    """The authenticator key this browser is setting up, kept in its user_sessions row: the
    session cookie is signed but readable, so a key in it could be read from any copy of the
    cookie and make codes for good. One per browser, so a key someone else saw while signed in
    elsewhere is never the one turned on here; gone at sign out. None for a session without a
    row (from before 1.2.10)."""
    sid = getattr(current_user, "sid", None)
    if not sid:
        return None
    if create:
        db.execute("UPDATE user_sessions SET totp_setup = ? WHERE id = ? AND user_id = ? "
                   "AND totp_setup IS NULL", (new_totp_secret(), sid, user_id))
    found = db.query("SELECT totp_setup FROM user_sessions WHERE id = ? AND user_id = ?",
                     (sid, user_id), one=True)
    return found["totp_setup"] if found else None


def totp_uri(secret, email):
    return (f"otpauth://totp/{quote('Nomad Life')}:{quote(email)}?secret={secret}"
            f"&issuer={quote('Nomad Life')}&period={TOTP_STEP_SECONDS}&digits=6")


def totp_qr(secret, email):
    """The setup QR code as a data URI (no inline SVG, so no markup built from user data)."""
    return segno.make(totp_uri(secret, email), error="m").svg_data_uri(
        scale=5, border=4, dark="#000000", light="#ffffff")


# ---------- passkeys (WebAuthn), the other second sign in step ----------

def passkey_site():
    """(rp_id, origin) of the passkeys: from APP_BASE_URL, the address people use, so a
    passkey made on a look-alike site never works here. Without one (a local run), the
    address the browser used."""
    parts = urlsplit(current_app.config.get("APP_BASE_URL") or request.host_url)
    return parts.hostname or "localhost", f"{parts.scheme}://{parts.netloc}"


def user_passkeys(user_id):
    return db.query("SELECT * FROM passkeys WHERE user_id = ? ORDER BY id", (user_id,))


def has_two_factor(row):
    """Two-factor sign in is on while the account has an authenticator app or a passkey."""
    return bool(row["totp_secret"]) or bool(db.query(
        "SELECT 1 FROM passkeys WHERE user_id = ? LIMIT 1", (row["id"],), one=True))


def passkey_descriptors(rows):
    """The browser's description of passkeys (to exclude at registration, allow at sign in)."""
    known = {t.value for t in AuthenticatorTransport}
    out = []
    for r in rows:
        try:
            transports = [AuthenticatorTransport(t) for t in json.loads(r["transports"])
                          if t in known]
        except (ValueError, TypeError):
            transports = []
        out.append(PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["credential_id"]),
                                                 transports=transports or None))
    return out


def new_passkey_challenge(user_id, kind, challenge):
    """Record the challenge of a ceremony (see passkey_challenges); its base64url text. Only
    the newest of an account and kind counts: asking again ends the earlier prompt, so asking
    in a loop cannot pile up rows."""
    text = bytes_to_base64url(challenge)
    with db.transaction() as conn:
        conn.execute("DELETE FROM passkey_challenges WHERE user_id = ? AND kind = ?",
                     (user_id, kind))
        conn.execute("INSERT INTO passkey_challenges (challenge, user_id, kind) VALUES (?, ?, ?)",
                     (text, user_id, kind))
    return text


def use_passkey_challenge(user_id, kind, text):
    """Spend a challenge: True only once, and only while it is recent."""
    if not isinstance(text, str) or not text:
        return False
    return db.execute_rowcount(
        "DELETE FROM passkey_challenges WHERE challenge = ? AND user_id = ? AND kind = ? "
        "AND created_at >= datetime('now', ?)",
        (text, user_id, kind, f"-{PASSKEY_CEREMONY_SECONDS} seconds")) == 1


def credential_from_form():
    """The credential the browser made or signed (JSON in the form's credential field), as a
    dict, or None when it is missing or not JSON."""
    raw = request.form.get("credential", "")
    if not raw or len(raw) > PASSKEY_CREDENTIAL_MAX:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("id"), str) else None


def default_passkey_name():
    """A name for a new passkey when none is typed: the device and browser it was made on."""
    ua = request.headers.get("User-Agent", "")
    device = next((name for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"),
                                          ("Android", "Android"), ("CrOS", "Chromebook"),
                                          ("Macintosh", "Mac"), ("Windows", "Windows"),
                                          ("Linux", "Linux")) if key in ua), "")
    browser = next((name for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"),
                                           ("Firefox/", "Firefox"), ("FxiOS", "Firefox"),
                                           ("CriOS", "Chrome"), ("Chrome/", "Chrome"),
                                           ("Safari/", "Safari")) if key in ua), "")
    if device and browser:
        return f"{device} ({browser})"
    return device or browser or "Passkey"


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
    this browser signed in, with the same session row, and forget every other one. False (and
    signed out) when the account was deleted meanwhile."""
    remember = session.get("nl_remember", False)
    row = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if row is None:
        sign_out()
        return False
    end_other_sessions(user_id, sign_in_user(row, remember, keep_current=True))
    return True


def account_gone():
    """The signed in account was deleted while this request ran: sign in again (it fails)."""
    sign_out()
    flash("This account no longer exists.", "error")
    return redirect(url_for("login"))


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


def billing_on():
    """Online payment works: config.env has every Stripe setting (billing_settings)."""
    return all(current_app.config.get(k) for k in BILLING_KEYS)


def captcha_on():
    return bool(current_app.config.get("HCAPTCHA_SITEKEY")
                and current_app.config.get("HCAPTCHA_SECRET"))


def hcaptcha_verify(token):
    """True or False as hCaptcha answers for this token, None when it cannot be reached."""
    cfg = current_app.config
    params = {"secret": cfg["HCAPTCHA_SECRET"], "response": token,
              "sitekey": cfg["HCAPTCHA_SITEKEY"]}
    try:
        params["remoteip"] = str(ipaddress.ip_address(client_ip()))
    except ValueError:
        pass  # "unknown": hCaptcha refuses even a right answer (invalid-remoteip)
    data = urlencode(params).encode()
    req = urllib.request.Request(HCAPTCHA_VERIFY_URL, data=data,
                                 headers={"User-Agent": "Nomad-Life"})
    try:
        with urllib.request.urlopen(req, timeout=HCAPTCHA_TIMEOUT_SECONDS) as resp:
            answer = json.loads(resp.read(64 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        exc.close()
        # hCaptcha answered, so it is not out of reach: a refusal such as 429 (too many checks,
        # which a bot posting made up answers can cause) must not let every form through.
        current_app.logger.warning("hCaptcha check failed with HTTP %s", exc.code)
        return None if exc.code >= 500 else False
    except Exception as exc:  # noqa: BLE001 - offline, slow or changed service
        current_app.logger.warning("hCaptcha check skipped: %s", exc)
        return None
    if answer.get("success") is True:
        return True
    codes = answer.get("error-codes") or []
    if HCAPTCHA_CONFIG_ERRORS.intersection(codes):
        current_app.logger.error("hCaptcha refuses every form: check HCAPTCHA_SITEKEY and "
                                 "HCAPTCHA_SECRET in config.env (%s)", ", ".join(codes))
    else:
        current_app.logger.warning("hCaptcha refused a form: %s", ", ".join(codes) or "no reason")
    return False


def captcha_passed():
    """Whether the form's hCaptcha answer is good, or the captcha is off. A form without an
    answer (a bot posting directly) fails without asking hCaptcha; a service that cannot be
    reached lets the form through, as the breach check does, since nobody can cause that."""
    if not captcha_on():
        return True
    token = request.form.get("h-captcha-response", "").strip()
    if not token or len(token) > 8192:
        return False
    return hcaptcha_verify(token) is not False


def dotted_gmail(email):
    """A Gmail address with more dots than people use (the part after a + does not count)."""
    local, _, domain = email.rpartition("@")
    return domain in GMAIL_DOMAINS and local.split("+", 1)[0].count(".") > GMAIL_MAX_DOTS


def honeypot_filled():
    """Only bots fill in the off screen field."""
    return bool(request.form.get(HONEYPOT_FIELD, "").strip())


def captcha_page(template, status=200):
    """Render Sign up or Forgot password, allowing the hCaptcha hosts in this page's CSP."""
    g.captcha = captcha_on()
    return render_template(template), status


CAPTCHA_MESSAGE = "Please complete the check that shows you are not a robot."


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
    "subject": "sort_fold(t.subject)", "messages": "messages",
    "user": "sort_fold(u.email)", "status": "t.status", "created": "t.created_at",
    "updated": "t.updated_at", "closed": "t.closed_at IS NULL, t.closed_at",
}
# Sortable columns of the admin accounts table. Accounts that never signed in go last.
ADMIN_USER_SORTS = {
    "email": "sort_fold(u.email)", "joined": "u.created_at",
    "last": "u.last_login_at IS NULL, u.last_login_at", "used": "used",
}
# The accounts filter of the admin table: every account, only the likely fake ones, or all but.
ADMIN_FAKE_FILTERS = ("", "only", "hide")
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


DUPLICATE_SECONDS = 60  # the same ticket or message sent again this soon: a double submit


def duplicate_message(conn, ticket_id, author_id, from_admin, body):
    """The same text by the same author on this ticket within DUPLICATE_SECONDS: one form sent
    twice (a double click, a retried request). Call inside the write transaction."""
    return conn.execute(
        "SELECT id FROM ticket_messages WHERE ticket_id = ? AND author_id = ? AND from_admin = ? "
        "AND body = ? AND created_at >= datetime('now', ?) ORDER BY id DESC LIMIT 1",
        (ticket_id, author_id, int(bool(from_admin)), body,
         f"-{DUPLICATE_SECONDS} seconds")).fetchone()


def ticket_messages(ticket_id):
    return db.query("SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id",
                    (ticket_id,))


# ---------- security history and alerts ----------

AUDIT_LABELS = {
    "sign_in": "Signed in",
    "sign_in_failed": "Wrong password at sign in",
    "sign_in_code_failed": "Wrong two-factor code at sign in",
    "sign_in_passkey_failed": "Passkey not accepted at sign in",
    "code_failed": "Wrong two-factor code on the Settings page",
    "password_check_failed": "Wrong current password on the Settings page",
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
    "plan_upgraded": "Plan bought",
    "plan_changed": "Plan switched",
    "plan_renewed": "Plan renewed",
    "plan_downgraded": "Plan ended",
    "payment_failed": "Payment failed",
    "passkey_added": "Passkey added",
    "passkey_renamed": "Passkey renamed",
    "passkey_removed": "Passkey removed",
    "account_deleted": "Account deleted",
    "admin_plan": "Plan changed by an admin",
    "admin_quota": "Storage quota changed by an admin",
    "admin_disable": "Account disabled by an admin",
    "admin_enable": "Account enabled by an admin",
    "admin_delete": "Account deleted by an admin",
    "admin_delete_fake": "Suspected fake account deleted",
    "admin_fake_cleanup": "Suspected fake accounts deleted (bulk)",
    "admin_price": "Plan prices changed",
}


def _sql_text(value):
    return "'" + value.replace("'", "''") + "'"


# Sortable columns of the admin Security activity table: (kept first in both directions, key).
# Ids grow with time, so Time sorts by id. Event sorts by the label shown; events without an
# account email or an IP (command line events) go last either way.
AUDIT_SORTS = {
    "time": ("", "a.id"),
    "event": ("", "CASE a.event " + " ".join(
        f"WHEN {_sql_text(k)} THEN {_sql_text(v)}" for k, v in AUDIT_LABELS.items())
        + " ELSE a.event END COLLATE NOCASE"),
    "account": ("a.email = ''", "sort_fold(a.email)"),
    "ip": ("ip_sort_key(a.ip) IS NULL", "ip_sort_key(a.ip)"),  # 9.x before 10.x
}


def audit(event, user_id=None, email="", detail="", actor="", conn=None):
    """Record a security event. Never fails the request it belongs to. With conn (inside
    db.transaction()) the row is part of that transaction: it is saved with the change it
    records, or not at all, and an error is the caller's."""
    ip = client_ip() if has_request_context() else ""  # none on the command line
    sql = ("INSERT INTO audit_log (user_id, email, actor, event, detail, ip) "
           "VALUES (?, ?, ?, ?, ?, ?)")
    args = (user_id, email or "", actor or "", event, detail or "", ip)
    if conn is not None:
        conn.execute(sql, args)
        return
    try:
        db.execute(sql, args)
    except Exception as exc:  # noqa: BLE001 - the history must never break sign in
        current_app.logger.error("Could not record %s for %s: %s", event, email, exc)


AUDIT_CSV_HEADER = ["Time (UTC)", "Event", "Event key", "Email", "Actor", "Detail", "IP",
                    "Account id"]


def audit_range_sql(range_key):
    """(where, params) of the events an export or purge of this range covers. The cutoff is
    a fixed time, worked out once: a purge reads its rows and deletes them a moment later,
    and both must mean the same rows."""
    if range_key == "30":
        cutoff = datetime.now(timezone.utc) - timedelta(days=AUDIT_EXPORT_DAYS)
        return "created_at >= ?", [cutoff.strftime("%Y-%m-%d %H:%M:%S")]
    return "1 = 1", []


def audit_csv_lines(rows, header=False):
    """CSV text for audit rows (cells through csv_safe: a spreadsheet must not run them)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    if header:
        writer.writerow(AUDIT_CSV_HEADER)
    for r in rows:
        writer.writerow([package.csv_safe(v) for v in (
            r["created_at"], AUDIT_LABELS.get(r["event"], r["event"]), r["event"], r["email"],
            r["actor"], r["detail"], r["ip"], "" if r["user_id"] is None else r["user_id"])])
    return buf.getvalue()


def audit_csv_name(range_key):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"nomadlife-security-activity-{AUDIT_RANGES[range_key]}-{day}.csv"


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
    # The 429 answer also says when to try again, in a Retry-After header (security_headers).
    if has_request_context():
        g.retry_after = max(1, int(wait)) * 60
    who = {"ip": "from this network", "device": "on this device"}.get(scope, "for this account")
    return f"Too many {what} {who}. Please wait {plural(wait, 'minute')} and try again."


def utc_now_text():
    """Now in UTC, written like SQLite's CURRENT_TIMESTAMP ("2026-10-01 12:00:00")."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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


def expired_payload(ser, exc):
    """What an expired (but genuine) link carried, as a dict ({} when unreadable): to tell a link
    that already did its job from one that came too late."""
    try:
        data = ser.load_payload(exc.payload)
    except Exception:  # noqa: BLE001 - any unreadable payload is simply unknown
        return {}
    return data if isinstance(data, dict) else {}


def undo_status(conn, data, user):
    """Where an email change undo link (its payload) stands for the account (a users row or
    None), read through conn (a db.transaction() connection, or db.get_db()): "back" when the
    account uses the old address again, "ok" while the change can be undone, else "stale". A
    numbered change ("n", a row of email_changes) can be undone while it stands, whatever
    address the account moved on to since. Links from before the numbering only while the
    account still uses the new address."""
    if user is None:
        return "stale"
    if user["email"] == data.get("old"):
        return "back"
    if "n" not in data:
        return "ok" if user["email"] == data.get("new") else "stale"
    standing = conn.execute("SELECT 1 FROM email_changes WHERE id = ? AND user_id = ? AND "
                            "old_email = ? AND undone_at IS NULL",
                            (data["n"], user["id"], data.get("old"))).fetchone()
    return "ok" if standing else "stale"


def undo_already_done(data, user):
    """The answer of an undo link when the account uses the old address again: an undo did it
    (this link pressed twice, or an earlier change's) and emailed a password link, or the
    address was changed back from Settings, and nothing was sent."""
    if "n" in data:
        undone = db.query("SELECT 1 FROM email_changes WHERE id = ? AND undone_at IS NOT NULL",
                          (data["n"],), one=True)
    else:
        undone = db.query("SELECT 1 FROM audit_log WHERE user_id = ? AND event = "
                          "'email_reverted' AND detail = ?",
                          (user["id"], f"{data['new']} to {data['old']}"), one=True)
    flash(f"The account already uses {data['old']} again."
          + (" Use the link we emailed there to choose a new password." if undone else ""),
          "info")
    return redirect(url_for("login"))


def send_template_email(to, subject, name, *, background=False, on_failure=None, **context):
    """Render templates/email/<name>.txt and .html and send them as one email. background
    sends it from a thread once rendered (the answer then does not wait for Gmail) and returns
    True; on_failure runs there, inside the app context, if sending fails."""
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
    if background and current_app.config.get("EMAIL_IN_BACKGROUND", True):
        app = current_app._get_current_object()

        def deliver():
            with app.app_context():
                if not send_email(to, subject, text, html) and on_failure:
                    on_failure()

        # Not a daemon: a server restart waits for the email instead of dropping it.
        threading.Thread(target=deliver, name="email").start()
        return True
    sent = send_email(to, subject, text, html)
    if not sent and on_failure:
        on_failure()
    return sent


def after_answer(work):
    """Run work() once the answer is sent (the server closes the response), from a thread
    with a request context of its own (emails build their links with url_for); at once when
    EMAIL_IN_BACKGROUND is off (tests read the outbox right after the request). For what a
    public form does only for a registered address: run for every address, the answer takes
    as long for both (a thread started during the request still slowed it down)."""
    app = current_app._get_current_object()
    if not app.config.get("EMAIL_IN_BACKGROUND", True):
        work()
        return
    ip = client_ip()

    def run():
        with app.test_request_context(environ_base={"REMOTE_ADDR": ip}):
            work()

    @after_this_request
    def start_when_sent(resp):
        # Not a daemon thread: a server restart waits for the email instead of dropping it.
        resp.call_on_close(lambda: threading.Thread(target=run, name="email").start())
        return resp


def release_email_wait(user_id):
    """Nothing was sent, so do not make the user wait a minute before trying again."""
    db.execute("UPDATE users SET email_sent_at = NULL WHERE id = ?", (user_id,))


def send_password_link(row, template, subject, background=False, **context):
    """Email a link to choose a new password (it also confirms a pending account). context:
    more template values (first_name="" leaves out the name the account holder typed)."""
    token = serializer().dumps({"uid": row["id"], "h": password_fingerprint(row["password_hash"]),
                                "e": row["email"]})
    # An account still waiting for confirmation is deleted VERIFY_MINUTES after its sign up
    # (unless it holds data), so its link works only until then, not RESET_TOKEN_MAX_AGE.
    minutes = None
    if row["verified_at"] is None and not db.query(
            "SELECT 1 FROM years WHERE user_id = ? LIMIT 1", (row["id"],), one=True):
        minutes = min(minutes_left(row), RESET_TOKEN_MAX_AGE // 60)
    user_id = row["id"]
    return send_template_email(row["email"], subject, template, background=background,
                               on_failure=lambda: release_email_wait(user_id),
                               link=email_link("reset", token=token),
                               hours=RESET_TOKEN_MAX_AGE // 3600, minutes=minutes, **context)


def send_verification(row, background=False, on_failure=None):
    # "c" lets a link for an account that is gone say whether it expired or was replaced.
    token = serializer("email-verify").dumps(
        {"uid": row["id"], "h": password_fingerprint(row["password_hash"]),
         "v": row["session_version"],
         # "c" lets a link for an account that is gone say whether it expired.
         "c": row["created_at"]})
    return send_template_email(row["email"], "Confirm your Nomad Life account", "verify",
                               background=background, on_failure=on_failure,
                               link=email_link("verify", token=token), minutes=minutes_left(row))


def parse_date(value):
    """A "YYYY-MM-DD" date, or None. The day math reads every stay's dates many times (a year
    of 1,000 stays: thousands of calls, each a slow strptime), and a year has 366 of them, so
    stored dates are remembered. Only text of a date's length is: a form field can hold
    megabytes, which must not stay in memory. Same answers as before for any value."""
    if isinstance(value, str) and len(value) == 10:
        return _cached_date(value)
    return _read_date(value)


def _read_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


_cached_date = functools.lru_cache(maxsize=4096)(_read_date)


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
    organization = {"@type": "Organization", "@id": url + "#organization", "name": "Nomad Life",
                    "url": url, "email": CONTACT_EMAIL,
                    "parentOrganization": {"@type": "Organization", "name": COMPANY["name"],
                                           "url": COMPANY["url"], "vatID": COMPANY["vat"],
                                           "address": {"@type": "PostalAddress",
                                                       "addressLocality": COMPANY["city"],
                                                       "addressCountry": COMPANY["country_code"]}},
                    "logo": {"@type": "ImageObject",
                             "url": base + url_for("static", filename="img/icon-512.png"),
                             "width": 512, "height": 512}}
    structured_data = {
        "@context": "https://schema.org",
        "@graph": [
            organization,
            {"@type": "WebSite", "@id": url + "#website", "url": url, "name": "Nomad Life",
             "description": SITE_DESCRIPTION, "inLanguage": "en",
             "publisher": {"@id": url + "#organization"}},
            {"@type": "WebApplication", "@id": url + "#app", "name": "Nomad Life", "url": url,
             "description": SITE_DESCRIPTION, "image": image,
             "applicationCategory": "TravelApplication", "operatingSystem": "Any",
             "browserRequirements": "Requires a modern web browser",
             "isAccessibleForFree": True,
             "offers": [dict({"@type": "Offer", "name": p["name"], "price": p["price"],
                              "priceCurrency": PLAN_CURRENCY, "url": url + "#pricing"},
                             **({"priceSpecification": {
                                 "@type": "UnitPriceSpecification", "price": p["price"],
                                 "priceCurrency": PLAN_CURRENCY, "unitText": "YEAR",
                                 "valueAddedTaxIncluded": False}} if p["price_cents"] else {}))
                        for p in plan_catalog()],
             "featureList": ["Days per country for each solar year",
                             "183 day indicator for your base country",
                             "Receipt storage for rental contracts, hotel bills and flight tickets",
                             "Private workspace for each user"],
             "author": {"@type": "Person", "name": "Paolo Diomede", "url": "https://x.com/pdiomede"},
             "isPartOf": {"@id": url + "#website"}},
        ],
    }
    docs_url = base + url_for("docs")
    docs_data = {
        "@context": "https://schema.org",
        "@graph": [
            organization,
            {"@type": "WebPage", "@id": docs_url + "#page", "url": docs_url,
             "name": DOCS_TITLE, "description": DOCS_DESCRIPTION, "inLanguage": "en",
             "isPartOf": {"@id": url + "#website"}, "publisher": {"@id": url + "#organization"},
             "breadcrumb": {"@id": docs_url + "#breadcrumb"}},
            {"@type": "BreadcrumbList", "@id": docs_url + "#breadcrumb", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Nomad Life", "item": url},
                {"@type": "ListItem", "position": 2, "name": "Documentation", "item": docs_url}]},
        ],
    }
    return {"title": SITE_TITLE, "description": SITE_DESCRIPTION, "share_text": SHARE_TEXT,
            "docs_title": DOCS_TITLE, "docs_description": DOCS_DESCRIPTION, "docs_data": docs_data,
            "guides_title": GUIDES_TITLE, "guides_description": GUIDES_DESCRIPTION,
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
    """Yearly prices set on the admin page, {plan: year_cents}, read once a request."""
    from flask import g, has_app_context
    if not has_app_context():
        return {}
    if "plan_prices" not in g:
        g.plan_prices = {r["plan"]: r["year_cents"] for r in db.query("SELECT * FROM plan_prices")}
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
        year_cents = p["year_cents"] if key == "free" else overrides.get(key, p["year_cents"])
        # price/price_cents: the yearly price (the only one); month: a twelfth of it, rounded
        # down, shown as "about $0.83 a month".
        plans.append({"key": key, "name": p["name"], "price_cents": year_cents,
                      "year_cents": year_cents, "price": money(year_cents), "year": money(year_cents),
                      "month": money(year_cents // 12),
                      "default_price": money(p["year_cents"]), "default_year": money(p["year_cents"]),
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
    except package.RECEIPT_ERRORS:  # a damaged header too: listed as missing, never a 500
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


def delete_account(user_id, password_hash=None):
    """Delete an account with all its years, movements and documents, then its files.
    Returns False when there was no such account any more (deleted meanwhile), or, with
    password_hash, when its password is no longer that one (reset by email meanwhile)."""
    from flask import current_app
    sql, args = "DELETE FROM users WHERE id = ?", (user_id,)
    if password_hash is not None:
        sql, args = sql + " AND password_hash = ?", (user_id, password_hash)
    with db.transaction() as conn:  # list and delete together, see the year delete
        docs = conn.execute("SELECT * FROM documents WHERE user_id = ?", (user_id,)).fetchall()
        deleted = conn.execute(sql, args).rowcount
    if not deleted:
        return False
    remove_files(docs)
    try:
        os.rmdir(os.path.join(current_app.config["UPLOAD_DIR"], str(user_id)))
    except OSError:
        pass  # missing, or holds files no row points to: leave them for the operator
    return True


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


def assign_days(movements):
    """{date: movement id} for every day some movement covers: the one place each day counts.
    A shared day belongs to the stay that started later (the arrival); on the same start day
    the shorter stay wins, so a side trip is never hidden by the longer stay around it; with
    identical dates, the newest entry. The single source for compute_stats and the overlap
    report."""
    owner = {}
    for m in stay_order(movements):
        d, end = parse_date(m["start_date"]), parse_date(m["end_date"])
        while d <= end:
            owner[d] = m["id"]
            d += timedelta(days=1)
    return owner


# The dashboard report details at most this many overlapping pairs (and counts the rest), so a
# year of many overlapping stays cannot make the page slow.
OVERLAP_REPORT_MAX = 50


def shares_days(a_start, a_end, b_start, b_end):
    """Days two stays share, when that is an overlap to report, else 0. A travel day (the
    first stay ends the day the other begins: leave one place, arrive in the next) is normal
    and not an overlap. The one rule for the warning on save and the dashboard alert."""
    if (b_start, -b_end.toordinal()) < (a_start, -a_end.toordinal()):
        a_start, a_end, b_start, b_end = b_start, b_end, a_start, a_end  # a starts first
    days = (min(a_end, b_end) - b_start).days + 1
    if days <= 0 or (days == 1 and a_start < b_start and a_end == b_start):
        return 0
    return days


def movement_overlaps(movements, owner=None, limit=OVERLAP_REPORT_MAX):
    """(pairs, total): the pairs of movements that overlap (shares_days), in date order, at
    most `limit` of them with details, and how many there are in all. Each pair has both
    movements (a started first), the shared first and last day, the number of shared days, and
    which movements those days really count for ({id: days}, from assign_days, so a third
    stay covering the same days is named)."""
    owner = assign_days(movements) if owner is None else owner
    spans = sorted(((parse_date(m["start_date"]), parse_date(m["end_date"]), m)
                    for m in movements), key=lambda t: (t[0], -t[1].toordinal(), t[2]["id"]))
    pairs, total = [], 0
    for i, (a_start, a_end, a) in enumerate(spans):
        for b_start, b_end, b in spans[i + 1:]:
            if b_start > a_end:
                break  # sorted by start: no later movement reaches back into a
            days = shares_days(a_start, a_end, b_start, b_end)
            if not days:
                continue
            total += 1
            if len(pairs) >= limit:
                continue
            first, last = b_start, min(a_end, b_end)
            counts = {}
            d = first
            while d <= last:
                counts[owner[d]] = counts.get(owner[d], 0) + 1
                d += timedelta(days=1)
            pairs.append({"a": a, "b": b, "first": first, "last": last, "days": days,
                          "owners": counts})
    return pairs, total


def overlap_report(year, pairs, movements, counted, total=None):
    """The overlap alert's text: one numbered paragraph per pair, then the rule."""
    total = len(pairs) if total is None else total
    by_id = {m["id"]: m for m in movements}

    def place(m):
        return f"{m['city']}, {m['country']}"

    def span(m):
        return f"{m['start_date']} to {m['end_date']}"

    lines = [f"Overlapping movements in {year} ({total})"]
    for n, p in enumerate(pairs, 1):
        a, b = p["a"], p["b"]
        shared = (p["first"].isoformat() if p["days"] == 1
                  else f"{p['first'].isoformat()} to {p['last'].isoformat()}")
        owners = sorted(p["owners"].items(), key=lambda kv: (-kv[1], kv[0]))
        if len(owners) == 1:
            goes = place(by_id[owners[0][0]])
        else:
            goes = " and ".join(f"{place(by_id[mid])} ({plural(days, 'day')})"
                                for mid, days in owners)
        lines += ["", f"{n}. {place(a)}, {span(a)}", f"   and {place(b)}, {span(b)}",
                  f"   Shared: {plural(p['days'], 'day')} ({shared}), counted for {goes}."]
        # Only a stay that gives up some of this pair's shared days lost them here (a travel day
        # it hands to a third stay is not this pair's doing).
        lines += [f"   {place(m)} counts {counted.get(m['id'], 0)} of its {stay_length(m)} days."
                  for m in (a, b) if p["owners"].get(m["id"], 0) < p["days"]]
    if total > len(pairs):
        lines += ["", f"... and {total - len(pairs)} more."]
    lines += ["", "How shared days count:",
              "- Each day counts once, for the stay that started later.",
              "- Same start day: the shorter stay. Same dates: the newest.",
              "- A travel day (one stay ends, the next begins) is normal and not listed.",
              "If you were not in both places, fix the dates."]
    return "\n".join(lines)


# The warning on save names at most this many overlapping stays: flash messages travel in the
# session cookie, which browsers drop above 4 KB (signing the person out).
OVERLAP_NOTES_MAX = 5


def overlap_notes(year_id, data, exclude_id=None):
    """(notes, same start): the stays that overlap the given one (shares_days: a travel day is
    not one), in date order, at most OVERLAP_NOTES_MAX, then how many more; and whether one of
    them starts the same day (then "started later" decides nothing, so the message says more)."""
    rows = db.query("SELECT * FROM movements WHERE year_id = ? AND id IS NOT ? "
                    "AND start_date <= ? AND end_date >= ? ORDER BY start_date, end_date, id",
                    (year_id, exclude_id, data["end_date"], data["start_date"]))
    notes, same_start = [], False
    for r in rows:
        shared = shares_days(parse_date(r["start_date"]), parse_date(r["end_date"]),
                             parse_date(data["start_date"]), parse_date(data["end_date"]))
        if shared:
            notes.append(f"{r['city']} ({plural(shared, 'day')})")
            same_start = same_start or r["start_date"] == data["start_date"]
    if len(notes) > OVERLAP_NOTES_MAX:
        notes = notes[:OVERLAP_NOTES_MAX] + [f"{len(notes) - OVERLAP_NOTES_MAX} more"]
    return notes, same_start


def flash_overlaps(year_id, data, movement_id):
    notes, same_start = overlap_notes(year_id, data, movement_id)
    if notes:
        listed = notes[0] if len(notes) == 1 else ", ".join(notes[:-1]) + " and " + notes[-1]
        tie = (" (starting the same day: the shorter one; same dates: the newest)"
               if same_start else "")
        flash(f"This stay shares days with {listed}. Each shared day counts once, for the stay "
              f"that started later{tie}. If you were not in both places, check the dates.",
              "info")


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
    pager = paged(len(items), page, per_page, url)
    start = (pager["page"] - 1) * per_page
    pager["rows"] = items[start:start + per_page]
    return pager


def paged(total, page, per_page, url):
    """The pager of paginate() for `total` items whose rows the caller loads itself (with
    LIMIT per_page OFFSET (page - 1) * per_page), for tables too big to load whole."""
    pages = max(1, -(-total // per_page))
    page = min(max(to_int(page, 1), 1), pages)
    start = (page - 1) * per_page
    return {
        "rows": [], "page": page, "pages": pages, "total": total,
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


# ---------- billing (Stripe) ----------
# Stripe's hosted Checkout takes the payment and its customer portal handles cancelling,
# switching plans and cards; the app only redirects there. Stripe's signed webhook is the one
# source of truth for users.plan: nothing a browser sends ever sets a plan.

def stripe_form(data, prefix=""):
    """Stripe's form encoding: {"a": {"b": 1}, "c": [{"d": 2}]} -> a[b]=1, c[0][d]=2."""
    pairs = []
    items = data.items() if isinstance(data, dict) else enumerate(data)
    for key, value in items:
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, (dict, list)):
            pairs += stripe_form(value, name)
        elif value is not None:
            pairs.append((name, "true" if value is True else "false" if value is False
                          else str(value)))
    return pairs


def stripe_api(method, path, data=None, idempotency_key=None):
    """Call Stripe's API (form encoded, Bearer secret key). The JSON answer, or None when
    Stripe refused or could not be reached (logged)."""
    cfg = current_app.config
    body = urlencode(stripe_form(data)).encode() if data is not None else None
    headers = {"Authorization": f"Bearer {cfg['STRIPE_SECRET_KEY']}",
               "Stripe-Version": STRIPE_API_VERSION, "User-Agent": "Nomad-Life"}
    if has_request_context():
        g.stripe_error = ""
    if idempotency_key:  # a retried request never makes two customers or sessions
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(STRIPE_API_URL + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=STRIPE_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read(1024 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(64 * 1024).decode("utf-8", "replace"))["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = ""
        exc.close()
        if has_request_context():
            g.stripe_error = detail  # the caller may react to a known refusal
        current_app.logger.error("Stripe %s %s refused with HTTP %s: %s", method, path,
                                 exc.code, detail)
    except Exception as exc:  # noqa: BLE001 - offline, slow or changed service
        current_app.logger.error("Stripe %s %s failed: %s", method, path, exc)
    return None


def stripe_signature_ok(payload, header, secret, now=None):
    """Stripe's webhook signature: t=<time>,v1=<HMAC SHA-256 of "t.payload">, recent."""
    fields = [part.split("=", 1) for part in (header or "").split(",") if "=" in part]
    times = [v for k, v in fields if k == "t"]
    signatures = [v for k, v in fields if k == "v1"]
    if len(times) != 1 or not times[0].isdigit() or not signatures or not secret:
        return False
    if abs((now or time.time()) - int(times[0])) > STRIPE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), times[0].encode() + b"." + payload,
                        hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)


def new_stripe_customer(row, retry=False):
    """Make the account's Stripe customer and store it (a parallel request's wins). The
    customer id, or None when Stripe could not be reached. retry: the stored one was gone, so
    the idempotency key must be new (the old key would hand back the customer that is gone)."""
    key = f"nl-customer-{row['id']}" + (f"-{uuid.uuid4().hex}" if retry else "")
    made = stripe_api("POST", "customers", {
        "email": row["email"], "name": full_name(row) or None,
        "metadata": {"user_id": row["id"]}}, idempotency_key=key)
    if not made or not isinstance(made.get("id"), str):
        return None
    db.execute("UPDATE users SET stripe_customer_id = ? WHERE id = ? AND "
               "stripe_customer_id IS NULL", (made["id"], row["id"]))
    stored = db.query("SELECT stripe_customer_id FROM users WHERE id = ?", (row["id"],), one=True)
    return stored["stripe_customer_id"] if stored else None


def stripe_customer_gone(user_id, customer):
    """After a refused call: Stripe has no such customer (one made with the test keys, now
    asked with the live ones, or deleted in the dashboard). Forgets it and returns True, so
    the next checkout makes a new one instead of failing forever."""
    if "no such customer" not in g.get("stripe_error", "").lower():
        return False
    db.execute("UPDATE users SET stripe_customer_id = NULL WHERE id = ? AND "
               "stripe_customer_id = ?", (user_id, customer))
    current_app.logger.warning("Stripe has no customer %s: forgotten for account %s",
                               customer, user_id)
    return True


def stripe_sync_email(user_id):
    """Give the account's Stripe customer its new email address, so Stripe's receipts,
    invoices and renewal reminders follow an email change. Best effort: logged on failure."""
    if not billing_on():
        return
    row = db.query("SELECT email, stripe_customer_id FROM users WHERE id = ?", (user_id,),
                   one=True)
    if row and row["stripe_customer_id"]:
        stripe_api("POST", f"customers/{row['stripe_customer_id']}", {"email": row["email"]})


def price_plans():
    """{Stripe price id: plan key}, from config.env."""
    cfg = current_app.config
    return {cfg["STRIPE_PRICE_PRO"]: "pro", cfg["STRIPE_PRICE_PLUS"]: "plus"}


def plan_rank(key):
    keys = list(PLANS)
    return keys.index(key) if key in keys else 0


def active_subscription(user_id, conn=None, statuses=PAID_STATUSES):
    """The account's subscription that still pays for a plan, or None. With
    statuses=OPEN_STATUSES (open_subscription), also one that is unpaid or paused."""
    marks = ", ".join("?" * len(statuses))
    sql = (f"SELECT * FROM subscriptions WHERE user_id = ? AND status IN ({marks}) "
           "ORDER BY id DESC LIMIT 1")
    if conn is not None:
        return conn.execute(sql, (user_id, *statuses)).fetchone()
    return db.query(sql, (user_id, *statuses), one=True)


def open_subscription(user_id):
    """A subscription Stripe may still charge (OPEN_STATUSES): blocks a second checkout and
    deleting the account."""
    return active_subscription(user_id, statuses=OPEN_STATUSES)


def stripe_period_end(sub):
    """When the paid year ends: on the subscription, or (API 2025-03 on) on its item."""
    end = sub.get("current_period_end")
    if not end:
        items = (sub.get("items") or {}).get("data") or [{}]
        end = items[0].get("current_period_end")
    if not isinstance(end, int):
        return None
    return datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def paid_plan(conn, user_id):
    """The best plan any of the account's subscriptions still pays for; none: Free."""
    marks = ", ".join("?" * len(PAID_STATUSES))
    paid = [r["plan"] for r in conn.execute(
        f"SELECT plan FROM subscriptions WHERE user_id = ? AND status IN ({marks})",
        (user_id, *PAID_STATUSES)) if r["plan"] in PLANS]
    return max(paid, key=plan_rank) if paid else "free"


def apply_subscription(conn, sub, created):
    """Store a subscription Stripe sent (in a write transaction) and set the account's plan
    from all its subscriptions. Returns (user_id, plan before, plan after), or None when the
    event changes nothing (older than one already applied, or no account to apply it to)."""
    sub_id, customer = sub.get("id"), sub.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    if not isinstance(sub_id, str) or not isinstance(customer, str):
        return None
    items = (sub.get("items") or {}).get("data") or [{}]
    price_id = ((items[0] or {}).get("price") or {}).get("id") or ""
    plan = price_plans().get(price_id)
    status = str(sub.get("status") or "")
    row = conn.execute("SELECT * FROM subscriptions WHERE subscription_id = ?",
                       (sub_id,)).fetchone()
    if row and created < row["last_event_at"]:
        return None  # Stripe sends events out of order: a newer one was applied already
    if row:
        # Once stored, a subscription stays with its account: nothing in a later event can
        # move it (and the plan it pays for) to another one.
        user_id = row["user_id"]
    else:
        user_id = None
        wanted = str((sub.get("metadata") or {}).get("user_id") or "")
        if wanted.isdigit():
            found = conn.execute("SELECT id, stripe_customer_id FROM users WHERE id = ?",
                                 (int(wanted),)).fetchone()
            # The customer the app made for that account, or one it has none of yet.
            if found and found["stripe_customer_id"] in (None, customer):
                user_id = found["id"]
        if user_id is None:
            found = conn.execute("SELECT id FROM users WHERE stripe_customer_id = ?",
                                 (customer,)).fetchone()
            user_id = found["id"] if found else None
    if plan is None:
        current_app.logger.error("Stripe subscription %s has price %s, which is neither "
                                 "STRIPE_PRICE_PRO nor STRIPE_PRICE_PLUS", sub_id, price_id)
    # Cancelled from the portal: classic billing sets cancel_at_period_end, flexible billing
    # (the default for new subscriptions) sets only cancel_at, the day the plan ends.
    cancel_at = sub.get("cancel_at")
    cancelling = bool(sub.get("cancel_at_period_end")) or isinstance(cancel_at, int)
    ends = (datetime.fromtimestamp(cancel_at, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(cancel_at, int) else stripe_period_end(sub))
    values = (user_id, customer, price_id, plan or "free", status, ends,
              1 if cancelling else 0, created)
    if row:
        conn.execute("UPDATE subscriptions SET user_id = ?, customer_id = ?, price_id = ?, "
                     "plan = ?, status = ?, current_period_end = ?, cancel_at_period_end = ?, "
                     "last_event_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*values, row["id"]))
    else:
        conn.execute("INSERT INTO subscriptions (user_id, customer_id, price_id, plan, status, "
                     "current_period_end, cancel_at_period_end, last_event_at, subscription_id) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (*values, sub_id))
    if user_id is None:
        current_app.logger.error("Stripe subscription %s belongs to no account", sub_id)
        return None
    user = conn.execute("SELECT plan FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return None
    conn.execute("UPDATE users SET stripe_customer_id = COALESCE(stripe_customer_id, ?) "
                 "WHERE id = ?", (customer, user_id))
    new_plan = paid_plan(conn, user_id)
    if new_plan != user["plan"]:
        conn.execute("UPDATE users SET plan = ? WHERE id = ?", (new_plan, user_id))
    return user_id, user["plan"], new_plan


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
    keys = {}
    for m in stay_order(movements):
        name = normalize_country(m["country"])
        keys[m["id"]] = fold(name)
        names.setdefault(keys[m["id"]], name)
    assigned_mv = assign_days(movements)
    assigned = {d: keys[mid] for d, mid in assigned_mv.items()}

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
            # Who gets each day ({date: movement id}), for movement_overlaps: worked out once.
            "day_owner": assigned_mv,
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
            # The same answer whether or not the address has an account, so sign up cannot
            # be used to find out who uses Nomad Life. The mailbox learns the rest.
            sent = (f"We sent an email to {email}. Open the link in it within "
                    f"{VERIFY_MINUTES} minutes to continue.")
            if honeypot_filled():
                # A bot: the usual answer, so it learns nothing, but no account and no email.
                current_app.logger.warning("Sign up refused by the honeypot from %s", client_ip())
                flash(sent, "info")
                return redirect(url_for("login"))
            if not valid_email(email):
                flash("Please enter a valid email address.", "error")
            elif problem := password_problem(password, confirm, breaches=False):
                flash(problem, "error")
            elif not captcha_passed():
                # Before the limit, so a person who missed the box does not use up attempts.
                flash(CAPTCHA_MESSAGE, "error")
                return captcha_page("auth/signup.html", 400)
            elif dotted_gmail(email):
                # A bot's dot trick address: the usual answer, but no account and no email.
                current_app.logger.warning("Sign up refused for a dotted Gmail address from %s",
                                           client_ip())
                flash(sent, "info")
                return redirect(url_for("login"))
            else:
                wait, scope, _attempt = take_attempt("signup")
                if wait:
                    flash(too_many_message(wait, scope, "sign ups"), "error")
                    return captcha_page("auth/signup.html", 429)
                # Checked for every address, known or not, so the answer reveals nothing.
                problem = password_problem(password, confirm)
                if problem:
                    flash(problem, "error")
                    return captcha_page("auth/signup.html")
                old = db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)
                # Hashed for every address, and every email sent after answering (as for Forgot
                # password): a known address must not answer sooner or later than a new one.
                pw_hash = hash_password(password)
                if not old:
                    with db.transaction() as conn:
                        old = conn.execute("SELECT * FROM users WHERE email = ?",
                                           (email,)).fetchone()
                        if not old:
                            uid = conn.execute(
                                "INSERT INTO users (email, password_hash, email_sent_at, "
                                "signup_ip) VALUES (?, ?, CURRENT_TIMESTAMP, ?)",
                                (email, pw_hash, client_ip())).lastrowid
                if old and (old["verified_at"] is not None or old["disabled"]):
                    if not old["disabled"] and email_allowed(old["id"]):
                        old_id = old["id"]
                        send_template_email(email, "You already have a Nomad Life account",
                                            "account_exists", background=True,
                                            on_failure=lambda: release_email_wait(old_id),
                                            email=email, link=email_link("login"),
                                            forgot_link=email_link("forgot"))
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
                    # the link below, decides the password now. Only while it still waits: it
                    # may have been confirmed while the password was hashed, and a confirmed
                    # account must not be signed out everywhere and asked to finish signing up.
                    if not db.execute_rowcount("UPDATE users SET session_version = "
                                               "session_version + 1 WHERE id = ? AND "
                                               "verified_at IS NULL", (old["id"],)):
                        release_email_wait(old["id"])
                        flash(sent, "info")
                        return redirect(url_for("login"))
                    old = db.query("SELECT * FROM users WHERE id = ?", (old["id"],), one=True)
                    if not send_password_link(old, "finish_signup",
                                              "Finish setting up your Nomad Life account",
                                              background=True):
                        flash("We could not send the email. Please try again in a few minutes.",
                              "error")
                        return captcha_page("auth/signup.html")
                    flash(sent, "info")
                    return redirect(url_for("login"))
                row = db.query("SELECT * FROM users WHERE id = ?", (uid,), one=True)
                # Nothing sent: the account cannot be confirmed, so it gives way at once.
                if not send_verification(row, background=True, on_failure=lambda: db.execute(
                        "DELETE FROM users WHERE id = ? AND verified_at IS NULL", (uid,))):
                    db.execute("DELETE FROM users WHERE id = ? AND verified_at IS NULL", (uid,))
                    flash("We could not send the confirmation email. Please try again in a few "
                          "minutes.", "error")
                    return captcha_page("auth/signup.html")
                flash(sent, "info")
                return redirect(url_for("login"))
        return captcha_page("auth/signup.html")

    @app.route("/verify/<token>", methods=["GET", "POST"])
    def verify(token):
        purge_unverified()
        try:
            data = serializer("email-verify").loads(token, max_age=VERIFY_MINUTES * 60)
        except SignatureExpired as exc:
            uid = expired_payload(serializer("email-verify"), exc).get("uid")
            done = db.query("SELECT email FROM users WHERE id = ? AND verified_at IS NOT NULL",
                            (uid,), one=True) if uid else None
            if done:  # the link worked earlier and was opened again (the address may have
                # changed since: never show the current one to whoever reads the old mailbox)
                flash("This account is already confirmed. Please sign in.", "info")
                return redirect(url_for("index" if current_user.is_authenticated else "login"))
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
            flash("This account is already confirmed. "
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
        if request.method == "GET":
            # Company mail scanners open every link in incoming mail. Opening the link must not
            # confirm, or accounts a bot signed up with other people's addresses would stay:
            # only the button does, and unconfirmed accounts are removed after VERIFY_MINUTES.
            return render_template("auth/verify.html", email=row["email"],
                                   minutes=minutes_left(row))
        # The checks above again in the write: the account may have been removed (20 minutes
        # passed) or signed up again meanwhile, and the same link pressed twice confirms once.
        if not db.execute_rowcount(
                "UPDATE users SET verified_at = CURRENT_TIMESTAMP WHERE id = ? AND verified_at IS "
                "NULL AND session_version = ? AND password_hash = ?",
                (row["id"], row["session_version"], row["password_hash"])):
            again = db.query("SELECT verified_at FROM users WHERE id = ?", (row["id"],), one=True)
            if again and again["verified_at"]:
                flash("This account is already confirmed. Please sign in.", "info")
                return redirect(url_for("index" if signed_in else "login"))
            flash("This confirmation link is no longer valid. If you signed up more than once, "
                  "use the link in the newest email; otherwise, sign up again.", "error")
            return redirect(url_for("signup"))
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
            # Every failure is recorded under the typed address, so a longer one than any
            # account can have (valid_email: 254) is cut: a form field can hold 500 KB.
            email = request.form.get("email", "").strip().lower()[:255]
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
                if has_two_factor(row):
                    # The password was right; the code from the authenticator app or a passkey
                    # comes next. Nothing is signed in until then.
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
        # The row was read before the password check (slow): the account may have been deleted
        # (the fake account cleanup takes exactly the never signed in ones) or disabled since.
        gone = not db.execute_rowcount(
            "UPDATE users SET last_login_at = CURRENT_TIMESTAMP, last_login_ip = ? "
            "WHERE id = ? AND disabled = 0", (client_ip(), row["id"]))
        if not gone:
            try:
                sign_in_user(row, remember)
            except db.IntegrityError:  # deleted between the two writes
                gone = True
        if gone:
            session.pop("tfa", None)
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))
        audit("sign_in", row["id"], row["email"], detail)
        return remember_device(redirect(next_url), row["email"])

    def pending_two_factor():
        """(pending, row) of the sign in waiting for its second step, or (None, None) when
        there is none, or it is stale: too old, or the password, sessions or two-factor
        changed meanwhile."""
        pending = session.get("tfa")
        if not isinstance(pending, dict):
            return None, None
        row = db.query("SELECT * FROM users WHERE id = ?", (pending.get("uid"),), one=True)
        if (not row or row["disabled"] or not has_two_factor(row)
                or time.time() - pending.get("t", 0) > TOTP_PENDING_SECONDS
                or password_fingerprint(row["password_hash"]) != pending.get("h")
                or row["session_version"] != pending.get("v")):
            session.pop("tfa", None)
            return None, None
        return pending, row

    def login_code_page(row, status=200):
        return render_template("auth/login_code.html", has_totp=bool(row["totp_secret"]),
                               has_passkeys=bool(user_passkeys(row["id"]))), status

    @app.route("/login/code", methods=["GET", "POST"])
    def login_code():
        """Second step of signing in to an account with two-factor sign in turned on: a code
        from the authenticator app, or a passkey (login_passkey)."""
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        pending, row = pending_two_factor()
        if row is None:
            flash("Please sign in again.", "info")
            return redirect(url_for("login"))
        if request.method == "POST":
            wait, scope, attempt = take_attempt("code", row["email"])
            if wait:
                flash(too_many_message(wait, scope, "wrong codes"), "error")
                return login_code_page(row, 429)
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
        return login_code_page(row)

    def json_answer(data, status=200):
        resp = Response(data if isinstance(data, str) else json.dumps(data), status=status,
                        mimetype="application/json")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/login/passkey/options", methods=["POST"])
    def login_passkey_options():
        """What the browser needs to sign with one of the account's passkeys (JSON)."""
        if current_user.is_authenticated:
            return json_answer({"error": "You are already signed in.", "reload": True}, 400)
        pending, row = pending_two_factor()
        if row is None:
            return json_answer({"error": "Please sign in again.", "reload": True}, 400)
        keys = user_passkeys(row["id"])
        if not keys:
            return json_answer({"error": "This account has no passkey. Use the code from your "
                                         "authenticator app."}, 400)
        rp_id, _origin = passkey_site()
        options = webauthn.generate_authentication_options(
            rp_id=rp_id, allow_credentials=passkey_descriptors(keys),
            user_verification=UserVerificationRequirement.PREFERRED,
            timeout=PASSKEY_CEREMONY_SECONDS * 1000)
        pending["pk"] = new_passkey_challenge(row["id"], "sign_in", options.challenge)
        session["tfa"] = pending
        return json_answer(options_to_json(options))

    @app.route("/login/passkey", methods=["POST"])
    def login_passkey():
        """Second step of signing in with a passkey: the browser's signature, checked against
        the passkey's public key and the challenge of login_passkey_options."""
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        pending, row = pending_two_factor()
        if row is None:
            flash("Please sign in again.", "info")
            return redirect(url_for("login"))
        wait, scope, attempt = take_attempt("code", row["email"])
        if wait:
            flash(too_many_message(wait, scope, "failed second steps"), "error")
            return login_code_page(row, 429)
        challenge = pending.pop("pk", None)
        session["tfa"] = pending
        credential = credential_from_form()
        key = credential and db.query(
            "SELECT * FROM passkeys WHERE credential_id = ? AND user_id = ?",
            (credential["id"], row["id"]), one=True)
        verified = None
        # The challenge is spent first, whatever follows: a signature works at most once.
        if key and use_passkey_challenge(row["id"], "sign_in", challenge):
            rp_id, origin = passkey_site()
            try:
                verified = webauthn.verify_authentication_response(
                    credential=credential, expected_challenge=base64url_to_bytes(challenge),
                    expected_rp_id=rp_id, expected_origin=origin,
                    credential_public_key=key["public_key"],
                    credential_current_sign_count=key["sign_count"])
            except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
                current_app.logger.warning("Passkey sign in refused: %s", exc)
        # The counter moves only from the value checked: two uses of one signature (or of a
        # copied authenticator) at once cannot both pass.
        if verified is not None and db.execute_rowcount(
                "UPDATE passkeys SET sign_count = ?, last_used_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND sign_count = ?",
                (verified.new_sign_count, key["id"], key["sign_count"])):
            clear_code_failures(row["email"], attempt)
            session.pop("tfa", None)
            return finish_sign_in(row, pending.get("remember", False),
                                  safe_next(pending.get("next", "")) or url_for("index"),
                                  "with passkey")
        audit("sign_in_passkey_failed", row["id"], row["email"])
        flash("That passkey was not accepted. Please try again"
              + (", or use the code from your authenticator app." if row["totp_secret"] else "."),
              "error")
        return login_code_page(row)

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
            answer = "If that email is registered, a reset link is on its way."
            if honeypot_filled():
                current_app.logger.warning("Reset request refused by the honeypot from %s",
                                           client_ip())
                flash(answer, "info")
                return redirect(url_for("login"))
            if not captcha_passed():
                flash(CAPTCHA_MESSAGE, "error")
                return captcha_page("auth/forgot.html", 400)
            wait, scope, _attempt = take_attempt("forgot")
            if wait:
                flash(too_many_message(wait, scope, "reset requests"), "error")
                return captcha_page("auth/forgot.html", 429)
            def send_link():
                row = db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)
                if row and not row["disabled"] and email_allowed(row["id"]):
                    send_password_link(row, "reset", "Reset your Nomad Life password")

            # Looked up and sent after answering, for every address: an unknown one, which
            # sends nothing, must not answer sooner than a registered one (seconds when Gmail
            # was awaited, still half a millisecond while the email was claimed and rendered).
            after_answer(send_link)
            flash(answer, "info")
            return redirect(url_for("login"))
        return captcha_page("auth/forgot.html")

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
                    # submits at once (two tabs) must not both succeed. And only while the
                    # email is the one checked above: once it changed, the old mailbox has no say.
                    used = conn.execute(
                        "UPDATE users SET password_hash = ?, verified_at = COALESCE(verified_at, "
                        "CURRENT_TIMESTAMP), pending_email = NULL "
                        "WHERE id = ? AND password_hash = ? AND email = ?",
                        (new_hash, row["id"], row["password_hash"], row["email"])).rowcount
                    if used:
                        # Proving the mailbox ends a lock from wrong guesses (by anyone),
                        # also the one of each browser the account signed in from.
                        device_prefix = f"device:{row['email']}:"
                        conn.execute("DELETE FROM auth_events WHERE kind = 'fail' AND (key = ? "
                                     "OR substr(key, 1, ?) = ?)",
                                     (f"email:{row['email']}", len(device_prefix), device_prefix))
                        # The new password already ends every session; forget their rows too.
                        conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (row["id"],))
                if not used:
                    # What changed while the new password was hashed, in the words the checks
                    # above use: the account removed (a sign up whose 20 minutes ran out), the
                    # same link used in another tab, or the email changed.
                    now = db.query("SELECT password_hash FROM users WHERE id = ?", (row["id"],),
                                   one=True)
                    flash("This reset link is no longer valid." if now is None
                          else "This reset link has already been used."
                          if now["password_hash"] != row["password_hash"]
                          else "This reset link is no longer valid. Please request a new one.",
                          "error")
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
        return render_template("landing.html", plans=plan_catalog(), guides=GUIDES)

    def guide_page_data(page_url, guide=None):
        """JSON-LD of /guides (a collection) or of one guide (an article), with a breadcrumb
        and the Organization of every public page."""
        meta = site_meta()
        home = meta["url"]
        org = meta["structured_data"]["@graph"][0]
        index_url = meta["base"] + url_for("guides")
        crumbs = [{"@type": "ListItem", "position": 1, "name": "Nomad Life", "item": home},
                  {"@type": "ListItem", "position": 2, "name": "Guides", "item": index_url}]
        if guide:
            crumbs.append({"@type": "ListItem", "position": 3, "name": guide["title"],
                           "item": page_url})
            page = {"@type": "Article", "@id": page_url + "#article", "url": page_url,
                    "headline": guide["title"], "description": guide["description"],
                    "datePublished": guide["updated"], "dateModified": guide["updated"],
                    "inLanguage": "en", "image": meta["image"],
                    "author": {"@type": "Person", "name": "Paolo Diomede",
                               "url": "https://x.com/pdiomede"},
                    "publisher": {"@id": home + "#organization"},
                    "mainEntityOfPage": page_url, "breadcrumb": {"@id": page_url + "#breadcrumb"}}
        else:
            page = {"@type": "CollectionPage", "@id": page_url + "#page", "url": page_url,
                    "name": "Guides for digital nomads", "description": GUIDES_DESCRIPTION,
                    "inLanguage": "en", "publisher": {"@id": home + "#organization"},
                    "breadcrumb": {"@id": page_url + "#breadcrumb"},
                    "hasPart": [{"@type": "Article", "headline": g["title"],
                                 "url": meta["base"] + url_for("guide", slug=g["slug"])}
                                for g in GUIDES]}
        return {"@context": "https://schema.org",
                "@graph": [org, page, {"@type": "BreadcrumbList", "@id": page_url + "#breadcrumb",
                                       "itemListElement": crumbs}]}

    def legal_page(endpoint):
        page = LEGAL_PAGES[endpoint]
        page_url = app.config["APP_BASE_URL"].rstrip("/") + url_for(endpoint)
        return render_template(f"legal/{endpoint}.html", page=page, page_url=page_url,
                               refund_days=REFUND_DAYS, audit_days=AUDIT_DAYS,
                               verify_minutes=VERIFY_MINUTES, idle_days=SESSION_IDLE_DAYS,
                               support_email=SUPPORT_EMAIL)

    @app.route("/terms")
    def terms():
        return legal_page("terms")

    @app.route("/privacy")
    def privacy():
        return legal_page("privacy")

    @app.route("/refunds")
    def refunds():
        return legal_page("refunds")

    @app.route("/guides")
    def guides():
        """Public guides on day counting and tax residency, indexable like /docs."""
        page_url = app.config["APP_BASE_URL"].rstrip("/") + url_for("guides")
        return render_template("guides/index.html", guides=GUIDES, page_url=page_url,
                               page_data=guide_page_data(page_url), threshold=RESIDENCE_THRESHOLD)

    @app.route("/guides/<slug>")
    def guide(slug):
        found = GUIDES_BY_SLUG.get(slug)
        if not found:
            abort(404)
        page_url = app.config["APP_BASE_URL"].rstrip("/") + url_for("guide", slug=slug)
        return render_template(f"guides/{slug}.html", guide=found, guides=GUIDES,
                               page_url=page_url, page_data=guide_page_data(page_url, found),
                               threshold=RESIDENCE_THRESHOLD)

    docs_shots_file = os.path.join(app.static_folder, "img", "docs", "shots.json")
    try:
        with open(docs_shots_file, encoding="utf-8") as fh:
            docs_shots = json.load(fh)  # {name: [width, height]}, from scripts/docs_screenshots.py
    except (OSError, ValueError):
        docs_shots = {}

    @app.route("/docs")
    def docs():
        """How to use the app, public and indexable like the landing page. Every number on it
        comes from the settings below, so it never contradicts the app."""
        return render_template(
            "docs.html", toc=DOCS_TOC, shots=docs_shots, support_email=SUPPORT_EMAIL,
            docs_url=app.config["APP_BASE_URL"].rstrip("/") + url_for("docs"),
            plans=plan_catalog(), threshold=RESIDENCE_THRESHOLD,
            verify_minutes=VERIFY_MINUTES, notes_max=NOTES_MAX, place_max=PLACE_MAX,
            movements_max=MOVEMENTS_MAX, per_page_options=PER_PAGE_OPTIONS,
            fail_limit=LIMITS[("fail", "email")], limit_minutes=LIMIT_WINDOW_MINUTES,
            code_limit=LIMITS[("code", "email")],
            passkeys_max=PASSKEYS_MAX, refund_days=REFUND_DAYS,
            remember_days=app.config["REMEMBER_COOKIE_DURATION"].days,
            reset_minutes=RESET_TOKEN_MAX_AGE // 60, email_change_minutes=EMAIL_CHANGE_MAX_AGE // 60,
            revert_days=EMAIL_REVERT_MAX_AGE // 86400, idle_days=SESSION_IDLE_DAYS,
            photo_megapixels=PHOTO_MAX_AREA // 1_000_000, photo_min_side=PHOTO_MIN_SIDE,
            photo_shrink_mb=PHOTO_SHRINK_BYTES // MB, photo_max_megapixels=PHOTO_MAX_PIXELS // 1_000_000,
            document_kinds=DOCUMENT_KINDS,
            open_tickets_max=OPEN_TICKETS_MAX, ticket_body_max=TICKET_BODY_MAX,
            ticket_kinds=TICKET_KINDS, audit_days=AUDIT_DAYS)

    @app.route("/robots.txt")
    def robots_txt():
        base = app.config["APP_BASE_URL"].rstrip("/")
        lines = ["User-agent: *", "Allow: /"] + [f"Disallow: {p}" for p in PRIVATE_PATHS]
        lines += ["", f"Sitemap: {base}{url_for('sitemap_xml')}", ""]
        return Response("\n".join(lines), mimetype="text/plain")

    @app.route("/.well-known/security.txt")
    def security_txt():
        """How to report a vulnerability (RFC 9116); Expires always lies ahead."""
        base = app.config["APP_BASE_URL"].rstrip("/")
        expires = (datetime.now(timezone.utc) + timedelta(days=SECURITY_TXT_DAYS)).strftime(
            "%Y-%m-%dT00:00:00Z")
        lines = [f"Contact: mailto:{SUPPORT_EMAIL}", f"Expires: {expires}",
                 "Preferred-Languages: en",
                 f"Canonical: {base}{url_for('security_txt')}", ""]
        return Response("\n".join(lines), mimetype="text/plain")

    @app.route("/sitemap.xml")
    def sitemap_xml():
        base = app.config["APP_BASE_URL"].rstrip("/")
        # The public pages: the landing page, the documentation and the guides.
        urls = []
        for endpoint, template, priority in (("landing", "landing.html", "1.0"),
                                             ("docs", "docs.html", "0.8")):
            path = os.path.join(app.root_path, app.template_folder, template)
            lastmod = date.fromtimestamp(os.path.getmtime(path)).isoformat()
            urls.append(f"  <url><loc>{escape(base + url_for(endpoint))}</loc>"
                        f"<lastmod>{lastmod}</lastmod><changefreq>monthly</changefreq>"
                        f"<priority>{priority}</priority></url>\n")
        # The guides: their index (as new as the newest guide) and each one.
        newest = max(g["updated"] for g in GUIDES)
        urls.append(f"  <url><loc>{escape(base + url_for('guides'))}</loc>"
                    f"<lastmod>{newest}</lastmod><changefreq>monthly</changefreq>"
                    f"<priority>0.7</priority></url>\n")
        for g in GUIDES:
            urls.append(f"  <url><loc>{escape(base + url_for('guide', slug=g['slug']))}</loc>"
                        f"<lastmod>{g['updated']}</lastmod><changefreq>monthly</changefreq>"
                        f"<priority>0.7</priority></url>\n")
        for endpoint, page in LEGAL_PAGES.items():
            urls.append(f"  <url><loc>{escape(base + url_for(endpoint))}</loc>"
                        f"<lastmod>{page['updated']}</lastmod><changefreq>yearly</changefreq>"
                        f"<priority>0.3</priority></url>\n")
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "".join(urls) + "</urlset>\n")
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
        row = db.query("SELECT stripe_customer_id FROM users WHERE id = ?", (current_user.id,),
                       one=True)
        sub = db.query("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                       (current_user.id,), one=True)
        return render_template("plan.html", plan=mine, upgrade=next_plan(mine["key"]),
                               subscription=sub, paying=open_subscription(current_user.id),
                               has_customer=bool(row and row["stripe_customer_id"]),
                               paid=request.args.get("paid") == "1")

    def site_base():
        return (app.config["APP_BASE_URL"] or request.host_url).rstrip("/")

    @app.route("/billing/checkout", methods=["POST"])
    @login_required
    def billing_checkout():
        """Start paying for a plan: Stripe's hosted Checkout, for this account (set here, never
        by the form), yearly, with tax worked out by Stripe Tax."""
        if not billing_on():
            flash("Online payment is not available yet.", "info")
            return redirect(url_for("plan"))
        key = request.form.get("plan", "")
        if key not in PLANS or key == "free":
            abort(400)
        row = db.query("SELECT * FROM users WHERE id = ?", (current_user.id,), one=True)
        if plan_rank(row["plan"]) >= plan_rank(key):
            flash(f"You are already on {plan_named(row['plan'])['name']}.", "info")
            return redirect(url_for("plan"))
        if open_subscription(row["id"]):
            flash("You already have a plan with us. Switch or renew it with Manage billing.",
                  "info")
            return redirect(url_for("plan"))
        customer = row["stripe_customer_id"] or new_stripe_customer(row)
        if not customer:
            flash("The payment page could not be opened. Please try again in a few minutes.",
                  "error")
            return redirect(url_for("plan"))
        base = site_base()
        price = {v: k for k, v in price_plans().items()}[key]
        terms, refunds_url = base + url_for("terms"), base + url_for("refunds")
        session_args = {
            "mode": "subscription", "customer": customer,
            "line_items": [{"price": price, "quantity": 1}],
            "client_reference_id": row["id"],
            "metadata": {"user_id": row["id"]},
            "subscription_data": {"metadata": {"user_id": row["id"]}},
            "automatic_tax": {"enabled": True},
            "tax_id_collection": {"enabled": True},
            "customer_update": {"address": "auto", "name": "auto"},
            "billing_address_collection": "required",
            # A checkbox the customer must tick, recorded on the session (consent), rather than
            # a line of text: the terms, the refund policy and the plan starting at once (EU
            # buyers give up the withdrawal period of a service already started).
            "consent_collection": {"terms_of_service": "required"},
            "custom_text": {
                "terms_of_service_acceptance": {"message": (
                    f"I agree to the [Terms]({terms}) and the [Refund Policy]({refunds_url}), "
                    "and want my plan to start at once.")},
                "submit": {"message": "Your plan renews every year until you cancel it with "
                                      "Manage billing on your plan page."}},
            "success_url": base + url_for("plan") + "?paid=1",
            "cancel_url": base + url_for("plan"),
        }
        # Up to two known refusals are mended, in whichever order Stripe reports them.
        customer_renewed = False
        for _ in range(3):
            session_obj = stripe_api("POST", "checkout/sessions", session_args,
                                     idempotency_key=f"nl-checkout-{row['id']}-{uuid.uuid4().hex}")
            if session_obj is not None:
                break
            if not customer_renewed and stripe_customer_gone(row["id"], customer):
                # Made in test mode before the live keys, or deleted in Stripe's dashboard.
                customer_renewed = True
                customer = new_stripe_customer(row, retry=True)
                if not customer:
                    break
                session_args["customer"] = customer
            elif ("consent_collection" in session_args
                  and "terms of service" in g.get("stripe_error", "").lower()):
                # Stripe asks for the agreement only once the account's public details name a
                # terms of service URL: until someone sets it, take the payment with the text
                # alone.
                current_app.logger.error("Stripe checkout without the terms checkbox: set the "
                                         "Terms of service URL in Stripe's public details")
                session_args.pop("consent_collection")
                session_args["custom_text"] = {"submit": {"message": (
                    "Your plan starts at once and renews every year until you cancel. By paying "
                    f"you agree to the Terms and Refund Policy at {terms} and {refunds_url}.")}}
            else:
                break
        if not session_obj or not str(session_obj.get("url", "")).startswith("https://"):
            flash("The payment page could not be opened. Please try again in a few minutes.",
                  "error")
            return redirect(url_for("plan"))
        return redirect(session_obj["url"], 303)

    @app.route("/billing/status")
    @login_required
    def billing_status():
        """The account's plan now (the plan page waits for the webhook after paying)."""
        row = db.query("SELECT plan FROM users WHERE id = ?", (current_user.id,), one=True)
        return json_answer({"plan": row["plan"] if row else "free",
                            "paying": bool(open_subscription(current_user.id))})

    @app.route("/billing/portal", methods=["POST"])
    @login_required
    def billing_portal():
        """Stripe's customer portal: cancel, switch between Pro and Nomad+, card, invoices."""
        row = db.query("SELECT stripe_customer_id FROM users WHERE id = ?", (current_user.id,),
                       one=True)
        if not billing_on() or not row or not row["stripe_customer_id"]:
            flash("There is no billing account to manage yet.", "info")
            return redirect(url_for("plan"))
        portal = stripe_api("POST", "billing_portal/sessions", {
            "customer": row["stripe_customer_id"], "return_url": site_base() + url_for("plan")})
        if portal is None and stripe_customer_gone(current_user.id, row["stripe_customer_id"]):
            flash("There is no billing account to manage yet.", "info")
            return redirect(url_for("plan"))
        if not portal or not str(portal.get("url", "")).startswith("https://"):
            flash("Billing could not be opened. Please try again in a few minutes.", "error")
            return redirect(url_for("plan"))
        return redirect(portal["url"], 303)

    @app.route("/billing/webhook", methods=["POST"])
    def billing_webhook():
        """Stripe's events, signed with STRIPE_WEBHOOK_SECRET. Each event id is applied once
        (a retried delivery answers 200 and does nothing); an error answers 500 and rolls
        everything back, so Stripe sends it again."""
        if not billing_on():
            abort(404)
        if (request.content_length or 0) > BILLING_WEBHOOK_MAX:
            return Response("too large", 413)
        payload = request.get_data(cache=False)
        if len(payload) > BILLING_WEBHOOK_MAX:
            return Response("too large", 413)
        if not stripe_signature_ok(payload, request.headers.get("Stripe-Signature"),
                                   app.config["STRIPE_WEBHOOK_SECRET"]):
            return Response("bad signature", 400)
        try:
            event = json.loads(payload)
            event_id, kind = event["id"], event["type"]
            obj, created = event["data"]["object"], int(event.get("created") or 0)
        except (ValueError, KeyError, TypeError):
            return Response("bad event", 400)
        if kind.startswith("customer.subscription."):
            # Stripe does not promise the order of events (a created and an updated of the same
            # second can arrive swapped): apply the subscription as it is now, read from Stripe
            # before taking the write lock. Unreachable: 500, and Stripe sends the event again.
            sub_id = obj.get("id") if isinstance(obj, dict) else None
            if not isinstance(sub_id, str) or not re.fullmatch(r"sub_[A-Za-z0-9]+", sub_id):
                return Response("bad event", 400)
            latest = stripe_api("GET", f"subscriptions/{sub_id}")
            if not isinstance(latest, dict) or latest.get("id") != sub_id:
                return Response("try again", 500)
            obj = latest
        notices = []
        with db.transaction() as conn:
            if not conn.execute("INSERT OR IGNORE INTO billing_events (event_id, type) "
                                "VALUES (?, ?)", (str(event_id), str(kind))).rowcount:
                return Response("already handled", 200)
            if kind in ("customer.subscription.created", "customer.subscription.updated",
                        "customer.subscription.deleted"):
                changed = apply_subscription(conn, obj, created)
                if changed and changed[1] != changed[2]:
                    user_id, before, after = changed
                    user = conn.execute("SELECT email FROM users WHERE id = ?",
                                        (user_id,)).fetchone()
                    if plan_rank(after) == 0:
                        event_name = "plan_downgraded"
                    elif plan_rank(before) == 0:
                        event_name = "plan_upgraded"
                    else:
                        event_name = "plan_changed"
                    audit(event_name, user_id, user["email"],
                          f"{plan_named(before)['name']} to {plan_named(after)['name']}",
                          "Stripe", conn=conn)
                    notices.append((event_name, user["email"], before, after))
            elif kind == "checkout.session.completed":
                customer, ref = obj.get("customer"), str(obj.get("client_reference_id") or "")
                if isinstance(customer, str) and ref.isdigit():
                    conn.execute("UPDATE users SET stripe_customer_id = ? WHERE id = ? AND "
                                 "stripe_customer_id IS NULL", (customer, int(ref)))
            elif kind in ("invoice.paid", "invoice.payment_failed"):
                user = conn.execute("SELECT id, email FROM users WHERE stripe_customer_id = ?",
                                    (str(obj.get("customer") or ""),)).fetchone()
                if user and kind == "invoice.payment_failed":
                    audit("payment_failed", user["id"], user["email"], "", "Stripe", conn=conn)
                    notices.append(("payment_failed", user["email"], None, None))
                elif user and obj.get("billing_reason") == "subscription_cycle":
                    audit("plan_renewed", user["id"], user["email"], "", "Stripe", conn=conn)
        for event_name, email, before, after in notices:
            billing_notice(event_name, email, before, after)
        return Response("ok", 200)

    # Stripe signs it instead (stripe_signature_ok); CSRFProtect registers itself here.
    app.extensions["csrf"].exempt(billing_webhook)

    def billing_notice(event_name, email, before, after):
        """Tell the account's mailbox about its plan (never for a renewal: Stripe emails the
        receipt)."""
        link = site_base() + url_for("plan")
        if event_name == "payment_failed":
            subject, what = ("Your Nomad Life payment did not go through",
                             "We could not take the payment for your Nomad Life plan. Stripe "
                             "will try again over the next days; update your card with Manage "
                             "billing on your plan page so the plan stays on.")
        elif event_name == "plan_downgraded":
            subject, what = ("Your Nomad Life plan has ended",
                             f"Your {plan_named(before)['name']} plan has ended and your "
                             "account is on Free again. Your stays and receipts are kept; over "
                             "the Free storage, new uploads wait until you free some space.")
        elif plan_rank(after) < plan_rank(before):  # Nomad+ to Pro: no "welcome" for less
            subject, what = (f"Your Nomad Life plan is now {plan_named(after)['name']}",
                             f"Your account moved from {plan_named(before)['name']} to "
                             f"{plan_named(after)['name']}. Your stays and receipts are kept; "
                             "over the new storage, new uploads wait until you free some space.")
        else:
            subject, what = (f"Welcome to Nomad Life {plan_named(after)['name']}",
                             f"Your account is now on {plan_named(after)['name']}. Thank you "
                             "for your support. Stripe emails the receipt and invoice.")
        send_template_email(email, subject, "billing_notice", background=True, what=what,
                            link=link, company=COMPANY)

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
                    same = db.query("SELECT 1 FROM years WHERE user_id = ? AND year = ? AND "
                                    "base_city = ? AND base_country = ?",
                                    (current_user.id, year, city, country), one=True)
                    if same:  # the form sent twice: the first one created it
                        flash(f"Year {year} is already created.", "info")
                        return redirect(url_for("dashboard", year=year))
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
        # Every movement of the year, not only this page of the table.
        overlaps, overlap_total = movement_overlaps(movements, stats["day_owner"])
        overlap_text = (overlap_report(year, overlaps, movements, stats["counted"], overlap_total)
                        if overlaps else "")
        return render_template("dashboard.html", y=year_row, movements=movements, pager=pager,
                               overlaps=overlaps, overlap_total=overlap_total,
                               overlap_text=overlap_text,
                               # Changes with the overlaps: the triangle blinks again for new ones.
                               overlap_key=f"{year}:" + hashlib.sha256(
                                   overlap_text.encode()).hexdigest()[:16] if overlaps else "",
                               per_page_options=PER_PAGE_OPTIONS,
                               years=years, stats=stats,
                               base_docs=base_docs, map=map_pins(year_row, movements, stats),
                               today=date.today().isoformat())

    @app.route("/year/<int:year>/base", methods=["GET", "POST"])
    @login_required
    def base(year):
        if request.method == "POST" and not db.query(
                "SELECT 1 FROM years WHERE user_id = ? AND year = ?", (current_user.id, year),
                one=True):
            # Deleted in another tab while this page was open (see movement_edit).
            action = request.form.get("action")
            flash({"delete_year": f"Year {year} was already deleted.",
                   "upload": UPLOAD_GONE}.get(action, f"The year {year} no longer exists, so "
                                                      "the changes were not saved."),
                  "info" if action == "delete_year" else "error")
            return redirect(url_for("index"))
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
                elif not db.execute_rowcount("UPDATE years SET base_city = ?, base_country = ? "
                                             "WHERE id = ?", (city, country, year_row["id"])):
                    flash(f"The year {year} no longer exists, so the changes were not saved.",
                          "error")
                    return redirect(url_for("index"))
                else:
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
        if request.method == "POST" and not db.query(
                "SELECT 1 FROM years WHERE user_id = ? AND year = ?", (current_user.id, year),
                one=True):
            # The year was deleted in another tab while this form was open.
            flash(f"The year {year} no longer exists, so the movement was not saved.", "error")
            return redirect(url_for("index"))
        year_row = get_year_or_404(year)
        form = request.form if request.method == "POST" else {}
        if request.method == "POST":
            data, err = validate_movement(request.form, year)
            mid = duplicate = None
            if not err:
                try:
                    # Count and insert under one write lock, so parallel posts cannot pass the
                    # limit together, nor a form sent twice (a double click) save two copies.
                    with db.transaction() as conn:
                        duplicate = conn.execute(
                            "SELECT id, notes FROM movements WHERE year_id = ? AND city = ? AND "
                            "country = ? AND start_date = ? AND end_date = ? ORDER BY id LIMIT 1",
                            (year_row["id"], data["city"], data["country"], data["start_date"],
                             data["end_date"])).fetchone()
                        count = conn.execute("SELECT COUNT(*) FROM movements WHERE year_id = ?",
                                             (year_row["id"],)).fetchone()[0]
                        if duplicate:
                            pass
                        elif count >= MOVEMENTS_MAX:
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
            elif duplicate:
                # The same place and dates are already saved (the form sent twice, or typed
                # again): a second copy would only count 0 days and trigger the overlap alert.
                flash(f"This movement to {data['city']} is already saved.", "info")
                # A receipt or notes sent with it are not added to the saved one (a form sent
                # twice would store the receipt twice), so say so instead of dropping them.
                upload = request.files.get("file")
                saved = {stem(d["original_name"])
                         for d in documents_for(year_row["id"], duplicate["id"])}
                lost_file = bool(upload and upload.filename) and stem(
                    display_name(upload.filename)) not in saved
                lost_notes = bool(data["notes"]) and data["notes"] != duplicate["notes"]
                if lost_file or lost_notes:
                    what = ("The receipt you chose and your notes were" if lost_file and lost_notes
                            else "The receipt you chose was" if lost_file else "Your notes were")
                    flash(f"{what} not added: add {'it' if what.endswith(' was') else 'them'} "
                          "on this page.", "error")
                return redirect(url_for("movement_edit", movement_id=duplicate["id"]))
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
        if request.method == "POST" and not db.query(
                "SELECT 1 FROM movements m JOIN years y ON y.id = m.year_id "
                "WHERE m.id = ? AND y.user_id = ?", (movement_id, current_user.id), one=True):
            # Deleted in another tab (or with its year) while this page was open: say what
            # happened instead of a bare "Page not found". Another user's movement is still 404:
            # this only tells "it is not there", which the 404 says too.
            action = request.form.get("action")
            flash({"delete": "This movement was already deleted.",
                   "upload": UPLOAD_GONE}.get(action, "This movement no longer exists, so the "
                                                        "changes were not saved."),
                  "info" if action == "delete" else "error")
            return redirect(url_for("index"))
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
            audit("password_check_failed", row["id"], row["email"])
            return "Your current password is not correct."
        forgive(attempt, row["email"], device)
        return None

    @app.route("/settings/passkeys/options", methods=["POST"])
    @login_required
    def passkey_register_options():
        """First half of adding a passkey: the current password is checked here, before the
        browser makes a passkey that could then not be saved; JSON for the browser's prompt."""
        row = db.query("SELECT * FROM users WHERE id = ?", (current_user.id,), one=True)
        if not row:
            return json_answer({"error": "Please sign in again.", "reload": True}, 400)
        err = check_current_password(row)
        if err:
            return json_answer({"error": err}, 400)
        keys = user_passkeys(row["id"])
        if len(keys) >= PASSKEYS_MAX:
            return json_answer({"error": f"An account can have at most {PASSKEYS_MAX} passkeys. "
                                         "Remove one first."}, 400)
        rp_id, _origin = passkey_site()
        options = webauthn.generate_registration_options(
            rp_id=rp_id, rp_name="Nomad Life", user_id=str(row["id"]).encode(),
            user_name=row["email"], user_display_name=row["email"],
            exclude_credentials=passkey_descriptors(keys),
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED),
            timeout=PASSKEY_CEREMONY_SECONDS * 1000)
        session["passkey_reg"] = {
            "uid": row["id"], "h": password_fingerprint(row["password_hash"]),
            "c": new_passkey_challenge(row["id"], "register", options.challenge)}
        return json_answer(options_to_json(options))

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
                else:  # a page left open: confirmed or cancelled meanwhile (the link, a tab)
                    flash(f"No email change is waiting any more. Your email address is "
                          f"{row['email']}.", "info")
                return redirect(url_for("settings"))
            if action == "sessions":  # ending other sessions only takes access away
                if not db.execute_rowcount("UPDATE users SET session_version = session_version "
                                           "+ 1 WHERE id = ?", (row["id"],)) \
                        or not stay_signed_in_here(row["id"]):
                    return account_gone()
                audit("sessions_revoked", row["id"], row["email"])
                flash("Every other browser and device has been signed out.", "success")
                return redirect(url_for("settings"))
            if action == "passkey_rename":  # only a label: no password needed
                name = clean_name(request.form.get("name", ""))
                if not visible(name):
                    flash("Type a name for the passkey.", "error")
                elif len(name) > NAME_MAX:
                    flash(f"A passkey name can be at most {NAME_MAX} characters.", "error")
                elif not db.execute_rowcount(
                        "UPDATE passkeys SET name = ? WHERE id = ? AND user_id = ?",
                        (name, request.form.get("passkey_id", type=int), row["id"])):
                    flash("This passkey was removed in the meantime.", "info")
                else:
                    audit("passkey_renamed", row["id"], row["email"], name)
                    flash("The passkey was renamed.", "success")
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
                    # A pending email change ends too: its link carries the old password, so
                    # Settings would wait for a confirmation that can no longer come.
                    # Only over the password checked above: a reset by email that finished
                    # meanwhile (the owner taking the account back) must not be overwritten.
                    if not db.execute_rowcount(
                            "UPDATE users SET password_hash = ?, pending_email = NULL "
                            "WHERE id = ? AND password_hash = ?",
                            (hash_password(password), row["id"], row["password_hash"])):
                        flash("The password of this account was changed elsewhere in the "
                              "meantime, so yours was not saved.", "error")
                        return redirect(url_for("settings"))
                    # The new password changes the session fingerprint: stay signed in here,
                    # every other browser and remember cookie is signed out.
                    if not stay_signed_in_here(row["id"]):
                        return account_gone()
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
                    # say) can never take the account. Only over the password checked above:
                    # after a reset by email meanwhile its link could never work.
                    if not db.execute_rowcount("UPDATE users SET pending_email = ? WHERE id = ? "
                                               "AND password_hash = ?",
                                               (new, row["id"], row["password_hash"])):
                        release_email_wait(row["id"])
                        if not db.query("SELECT 1 FROM users WHERE id = ?", (row["id"],),
                                        one=True):
                            return account_gone()
                        flash("The password of this account was changed elsewhere in the "
                              "meantime, so no confirmation link was sent.", "error")
                        return redirect(url_for("settings"))
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
                              "press Confirm my new email on the page it opens.", "info")
                    else:
                        # Nothing went out, so do not make the user wait for a minute.
                        db.execute("UPDATE users SET email_sent_at = NULL, pending_email = NULL "
                                   "WHERE id = ?", (row["id"],))
                        flash("We could not send the confirmation email. Please try again later.",
                              "error")
            elif action == "2fa_enable":
                session.pop("totp_setup", None)  # where pages before 1.11.1 kept it
                secret = totp_setup_secret(row["id"], create=False)
                step = totp_match(secret, request.form.get("code"))
                if err:
                    flash(err, "error")
                elif row["totp_secret"]:
                    flash("Two-factor sign in is already on.", "info")
                elif step is None:
                    flash("That code is not correct. Scan the QR code again, or check that the "
                          "time on your phone is right, and type the current code.", "error")
                else:
                    # Sessions from before were signed in without a code: end them. Only if
                    # the password, sessions and two-factor are as checked above: a reset by
                    # email or another browser turning it on meanwhile wins.
                    if not db.execute_rowcount(
                            "UPDATE users SET totp_secret = ?, totp_step = ?, session_version = "
                            "session_version + 1 WHERE id = ? AND password_hash = ? AND "
                            "totp_secret IS NULL AND session_version = ?",
                            (secret, step, row["id"], row["password_hash"],
                             row["session_version"])):
                        flash("This account was changed elsewhere in the meantime, so two-factor "
                              "sign in was not turned on. Please try again.", "error")
                        return redirect(url_for("settings"))
                    db.execute("UPDATE user_sessions SET totp_setup = NULL WHERE user_id = ?",
                               (row["id"],))
                    if not stay_signed_in_here(row["id"]):
                        return account_gone()
                    audit("2fa_enabled", row["id"], row["email"])
                    had_passkeys = bool(user_passkeys(row["id"]))  # then it was on already
                    security_alert(row["email"], "An authenticator app was added to two-factor "
                                   "sign in for your Nomad Life account." if had_passkeys else
                                   "Two-factor sign in was turned on for your Nomad Life account.",
                                   advice=("If this was you, there is nothing to do. If it was "
                                           "not you, a new password alone does not remove "
                                           "someone else's two-factor sign in: write to "
                                           f"{SUPPORT_EMAIL} from this address, and reset your "
                                           "password with the link below."))
                    flash("The authenticator app was added: signing in can now also take a code "
                          "from it. Other devices have been signed out." if had_passkeys else
                          "Two-factor sign in is on. From now on, signing in also asks for a code "
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
                    if user_passkeys(row["id"]):  # the passkeys still ask for a second step
                        security_alert(row["email"], "The authenticator app was removed from "
                                       "two-factor sign in for your Nomad Life account. Your "
                                       "passkeys still protect it.")
                        flash("The authenticator app was removed. Signing in still asks for "
                              "one of your passkeys.", "success")
                    else:
                        security_alert(row["email"], "Two-factor sign in was turned off for "
                                       "your Nomad Life account.")
                        note = (" The admin page needs it: turn it on again to use it."
                                if current_user.is_admin else "")
                        flash("Two-factor sign in is off." + note, "success")
            elif action == "passkey_add":
                reg = session.pop("passkey_reg", None)
                credential = credential_from_form()
                name = clean_name(request.form.get("name", ""))
                name = name if visible(name) else default_passkey_name()
                verified = None
                if err:
                    flash(err, "error")
                elif len(name) > NAME_MAX:
                    flash(f"A passkey name can be at most {NAME_MAX} characters.", "error")
                # The request must be the one this browser started, for this account, over
                # the password checked then, and its challenge unused.
                elif not (isinstance(reg, dict) and reg.get("uid") == row["id"] and credential
                          and reg.get("h") == password_fingerprint(row["password_hash"])
                          and use_passkey_challenge(row["id"], "register", reg.get("c"))):
                    flash("The passkey could not be added: the request expired or was already "
                          "used. Please try again.", "error")
                else:
                    rp_id, origin = passkey_site()
                    try:
                        verified = webauthn.verify_registration_response(
                            credential=credential, expected_challenge=base64url_to_bytes(reg["c"]),
                            expected_rp_id=rp_id, expected_origin=origin)
                    except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
                        current_app.logger.warning("Passkey registration refused: %s", exc)
                        flash("The passkey could not be checked. Please try again.", "error")
                if verified is not None:
                    cid = bytes_to_base64url(verified.credential_id)
                    response = credential.get("response")
                    transports = response.get("transports") if isinstance(response, dict) else None
                    transports = [t for t in transports if isinstance(t, str)][:10] \
                        if isinstance(transports, list) else []
                    with db.transaction() as conn:
                        # Checked again under the write lock: the limit, a passkey already
                        # added (another tab), and the account unchanged since the password
                        # check (a reset by email, other sessions signed out).
                        if conn.execute("SELECT COUNT(*) FROM passkeys WHERE user_id = ?",
                                        (row["id"],)).fetchone()[0] >= PASSKEYS_MAX:
                            result = "full"
                        elif conn.execute("SELECT 1 FROM passkeys WHERE credential_id = ?",
                                          (cid,)).fetchone():
                            result = "exists"
                        elif not conn.execute(
                                "UPDATE users SET session_version = session_version + 1 "
                                "WHERE id = ? AND password_hash = ? AND session_version = ?",
                                (row["id"], row["password_hash"],
                                 row["session_version"])).rowcount:
                            result = "changed"
                        else:
                            conn.execute(
                                "INSERT INTO passkeys (user_id, credential_id, public_key, "
                                "sign_count, transports, name) VALUES (?, ?, ?, ?, ?, ?)",
                                (row["id"], cid, verified.credential_public_key,
                                 verified.sign_count, json.dumps(transports), name))
                            result = "ok"
                    if result == "full":
                        flash(f"An account can have at most {PASSKEYS_MAX} passkeys. Remove one "
                              "first.", "error")
                    elif result == "exists":
                        flash("This passkey is already added.", "info")
                    elif result == "changed":
                        flash("This account was changed elsewhere in the meantime, so the "
                              "passkey was not added. Please try again.", "error")
                    else:
                        if not stay_signed_in_here(row["id"]):
                            return account_gone()
                        audit("passkey_added", row["id"], row["email"], name)
                        # Never the passkey's name: whoever added it chose it.
                        security_alert(row["email"], "A passkey was added to two-factor sign in "
                                       "for your Nomad Life account.",
                                       advice=("If this was you, there is nothing to do. If it "
                                               "was not you, a new password alone does not "
                                               "remove someone else's passkey: write to "
                                               f"{SUPPORT_EMAIL} from this address, and reset "
                                               "your password with the link below."))
                        flash(f"The passkey \u201c{name}\u201d was added. Signing in now asks for "
                              "it (or another two-factor method) after your password. Other "
                              "devices have been signed out.", "success")
            elif action == "passkey_remove":
                key = None
                if not delete_confirmed():
                    pass
                elif err:
                    flash(err, "error")
                else:
                    with db.transaction() as conn:
                        key = conn.execute("SELECT * FROM passkeys WHERE id = ? AND user_id = ?",
                                           (request.form.get("passkey_id", type=int),
                                            row["id"])).fetchone()
                        if key:
                            conn.execute("DELETE FROM passkeys WHERE id = ?", (key["id"],))
                    if not key:
                        flash("This passkey was already removed.", "info")
                if key:
                    audit("passkey_removed", row["id"], row["email"], key["name"])
                    fresh = db.query("SELECT * FROM users WHERE id = ?", (row["id"],), one=True)
                    off = not fresh or not has_two_factor(fresh)
                    security_alert(row["email"], "A passkey was removed from two-factor sign in "
                                   "for your Nomad Life account."
                                   + (" Two-factor sign in is now off." if off else ""))
                    note = (" The admin page needs it: add a passkey or an authenticator app to "
                            "use it." if off and current_user.is_admin else "")
                    flash(f"The passkey \u201c{key['name']}\u201d was removed."
                          + (" Two-factor sign in is off." if off else "") + note, "success")
            elif action == "delete":
                if not delete_confirmed():
                    pass
                elif request.form.get("confirm_email", "").strip().lower() != row["email"]:
                    flash("Type your email address to confirm the deletion.", "error")
                elif err:
                    flash(err, "error")
                elif open_subscription(row["id"]):
                    # Stripe would keep charging a deleted account every year.
                    flash("Your plan is still paid for: cancel it with Manage billing on your "
                          "plan page first, then delete the account.", "error")
                # Only over the password checked above, as for a password change: a reset by
                # email that finished meanwhile (the owner taking the account back) wins.
                elif not delete_account(row["id"], row["password_hash"]):
                    if not db.query("SELECT 1 FROM users WHERE id = ?", (row["id"],), one=True):
                        return account_gone()  # deleted meanwhile (an admin): nothing to record
                    flash("The password of this account was changed elsewhere in the meantime, "
                          "so the account was not deleted.", "error")
                else:
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
        session.pop("totp_setup", None)  # where pages before 1.11.1 kept it
        secret = None if row["totp_secret"] else totp_setup_secret(row["id"])
        if secret:
            # A new key for this setup, for this browser only; it counts once a code from it
            # is confirmed.
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
        passkeys = user_passkeys(row["id"])
        return render_template("settings.html", row=row, counts=counts, account_storage=storage,
                               totp=totp, activity=activity, devices=max(devices, 1),
                               passkeys=passkeys, has_2fa=bool(row["totp_secret"] or passkeys),
                               passkeys_max=PASSKEYS_MAX, passkey_name=default_passkey_name(),
                               idle_days=SESSION_IDLE_DAYS, name_max=NAME_MAX)

    @app.route("/account/email/<token>", methods=["GET", "POST"])
    def confirm_email(token):
        try:
            data = serializer("email-change").loads(token, max_age=EMAIL_CHANGE_MAX_AGE)
        except SignatureExpired as exc:
            old = expired_payload(serializer("email-change"), exc)
            done = db.query("SELECT 1 FROM users WHERE id = ? AND email = ?",
                            (old.get("uid"), old.get("email")), one=True)
            if done:  # opened again later, after it worked
                flash("This email address is already confirmed.", "info")
            else:
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
        if request.method == "GET":
            # Company mail scanners open every link in incoming mail: only the button confirms,
            # or a mistyped address whose mailbox is scanned would take the account.
            return render_template("auth/confirm_email.html", old=row["email"], new=new)
        try:
            with db.transaction() as conn:
                # Pressing the button proves this mailbox is theirs: an unconfirmed sign up with
                # the same address (anyone can start one) gives way, as it does on sign up.
                conn.execute("DELETE FROM users WHERE email = ? AND verified_at IS NULL AND "
                             "disabled = 0 AND NOT EXISTS (SELECT 1 FROM years WHERE "
                             "years.user_id = users.id)", (new,))
                # The checks above hold only if nothing changed meanwhile (the same link opened
                # twice at once, by the user and a mail scanner, would apply and alert twice).
                if not conn.execute("UPDATE users SET email = ?, pending_email = NULL WHERE id = ? "
                                    "AND email = ? AND pending_email = ? AND password_hash = ?",
                                    (new, row["id"], row["email"], new,
                                     row["password_hash"])).rowcount:
                    raise LinkUsed
                # The change's number, for its undo link (see email_changes in db.py).
                change = conn.execute("INSERT INTO email_changes (user_id, old_email, new_email) "
                                      "VALUES (?, ?, ?)", (row["id"], row["email"], new)).lastrowid
        except LinkUsed:
            flash("This link is no longer valid. Please ask for the email change again.", "error")
            return redirect(url_for("settings") if current_user.is_authenticated else url_for("login"))
        except db.IntegrityError:
            flash("Another account started using this email address in the meantime.", "error")
            return redirect(url_for("settings") if current_user.is_authenticated else url_for("login"))
        audit("email_changed", row["id"], new, f"{row['email']} to {new}")
        stripe_sync_email(row["id"])
        # The old mailbox hears about it too: if the account was taken over, that is where the
        # owner still reads.
        # This address has no account any more, so a password reset link would not work here:
        # it gets a link that puts itself back instead.
        revert = serializer("email-revert").dumps({"uid": row["id"], "old": row["email"],
                                                    "new": new, "n": change})
        security_alert(row["email"], f"The email address of your Nomad Life account was changed "
                       f"from {row['email']} to {new}. This address can no longer sign in.",
                       account=new, link=email_link("revert_email", token=revert),
                       advice=("If this was you, there is nothing to do. If it was not you, undo "
                               f"the change with the link below within "
                               f"{EMAIL_REVERT_MAX_AGE // 86400} days, also if the address is "
                               "changed again meanwhile: it puts this address "
                               "back, signs out every browser and device, turns off two-factor "
                               "sign in, and sends you a link to choose a new password."),
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
        status = undo_status(db.get_db(), data, row)
        if status == "back":  # pressed twice, or changed back from Settings
            return undo_already_done(data, row)
        if status == "stale":  # this change, or an earlier one, was undone since
            flash("This link is no longer valid.", "error")
            return redirect(url_for("login"))
        if request.method == "GET":  # a mail scanner opening the link must not act on it
            # The address it moved on to since is not named: this mailbox may not be the owner's.
            return render_template("auth/revert_email.html", old=data["old"],
                                   new=data["new"] if row["email"] == data["new"] else None)
        # Whoever changed the address knew the password: it stops working, and only the link
        # emailed below can set a new one (hashed before the write lock: it is slow).
        locked_hash = hash_password(secrets.token_hex(32))
        try:
            with db.transaction() as conn:
                # Checked again under the write lock: the same link pressed twice at once
                # undoes once (one reset email, one record), and an undo or a change meanwhile
                # is seen.
                current = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
                if undo_status(conn, data, current) != "ok":
                    raise LinkUsed
                conn.execute("DELETE FROM users WHERE email = ? AND verified_at IS NULL AND "
                             "disabled = 0 AND NOT EXISTS (SELECT 1 FROM years WHERE "
                             "years.user_id = users.id)",
                             (data["old"],))
                # From whatever address the account uses now. Two-factor sign in goes too:
                # whoever made the change may have turned it on with their own phone, which
                # would keep the owner out after the reset.
                conn.execute(
                    "UPDATE users SET email = ?, pending_email = NULL, password_hash = ?, "
                    "totp_secret = NULL, totp_step = 0, session_version = session_version + 1 "
                    "WHERE id = ? AND email = ?",
                    (data["old"], locked_hash, row["id"], current["email"]))
                # The same for passkeys: theirs would let them past the owner's new password.
                conn.execute("DELETE FROM passkeys WHERE user_id = ?", (row["id"],))
                # This change and every later one are undone: their undo links stop working,
                # so whoever made them cannot move the account away from the owner again.
                conn.execute("UPDATE email_changes SET undone_at = CURRENT_TIMESTAMP WHERE "
                             "user_id = ? AND id >= ? AND undone_at IS NULL",
                             (row["id"], data.get("n", 0)))
                conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (row["id"],))
        except LinkUsed:  # another press, undo or change got there first
            row = db.query("SELECT * FROM users WHERE id = ?", (row["id"],), one=True)
            if undo_status(db.get_db(), data, row) == "back":
                return undo_already_done(data, row)
            flash("This link is no longer valid.", "error")
            return redirect(url_for("login"))
        except db.IntegrityError:
            flash(f"{data['old']} is used by another account now. Please contact the person who "
                  "runs this Nomad Life server.", "error")
            return redirect(url_for("login"))
        # This account's sessions ended above; a browser signed in to another account (the
        # link opened where someone else is signed in) stays signed in.
        if current_user.is_authenticated and current_user.id == row["id"]:
            sign_out()
        audit("email_reverted", row["id"], data["old"], f"{current['email']} to {data['old']}")
        stripe_sync_email(row["id"])
        fresh = db.query("SELECT * FROM users WHERE id = ?", (row["id"],), one=True)
        # Without the name, as the security alerts: whoever changed the address may have set
        # it ("Hi Ignore this email,") in the email the owner takes the account back with.
        if send_password_link(fresh, "reset", "Reset your Nomad Life password", first_name=""):
            flash(f"The account uses {data['old']} again and every device was signed out. We "
                  "sent you a link to choose a new password.", "success")
        else:
            flash(f"The account uses {data['old']} again and every device was signed out. We "
                  "could not send the link to choose a new password: use Forgot password in a "
                  "few minutes.", "success")
        return redirect(url_for("login"))

    # --- admin ---

    def require_admin():
        # Same answer as for any page that is not yours: nothing to see here.
        if not current_user.is_admin:
            abort(404)
        # Admins act on everyone's accounts, so a password alone is not enough.
        if app.config["ADMIN_REQUIRE_2FA"] and not has_two_factor(db.query(
                "SELECT id, totp_secret FROM users WHERE id = ?", (current_user.id,), one=True)):
            flash("Turn on two-factor sign in to use the admin page.", "info")
            abort(redirect(url_for("settings")))  # no anchor: the message stays in view

    # Everything the admin page keeps in its address: the accounts table (search, sort, dir,
    # page) and the Security activity table (q, the account filter, and asort, adir, apage).
    admin_defaults = {"search": "", "sort": "joined", "dir": "desc", "page": 1, "fake": "",
                      "q": "", "asort": "time", "adir": "desc", "apage": 1}

    def admin_args(values):
        """URL arguments without the defaults, so plain /admin stays plain."""
        return {k: v for k, v in values.items() if v not in (None, "")
                and v != admin_defaults.get(k)}

    def admin_url(**values):
        # Both tables' state stays while paging, sorting or filtering the other one, on the
        # account page and after saving an account's settings.
        return url_for("admin", **admin_args(values))

    def admin_account_url(user_id, state):
        return url_for("admin_account", user_id=user_id, **admin_args(state))

    def admin_hidden(state, *skip):
        """(name, value) of the state a form must carry as hidden fields, defaults left out."""
        return [(k, v) for k, v in admin_args(state).items() if k not in skip]

    def admin_list_state(source):
        """State of both admin tables, from the query string or a form."""
        sort = source.get("sort", "joined")
        asort = source.get("asort", "time")
        fake = source.get("fake", "")
        return {"search": source.get("search", "").strip()[:254],
                "sort": sort if sort in ADMIN_USER_SORTS else "joined",
                "dir": "asc" if source.get("dir") == "asc" else "desc",
                "page": max(to_int(source.get("page"), 1), 1),
                "fake": fake if fake in ADMIN_FAKE_FILTERS else "",
                "q": source.get("q", "").strip().lower()[:254],
                "asort": asort if asort in AUDIT_SORTS else "time",
                "adir": "asc" if source.get("adir") == "asc" else "desc",
                "apage": max(to_int(source.get("apage"), 1), 1)}

    def admin_accounts(where="1 = 1", params=(), order="u.created_at DESC", asof=None):
        """Accounts with their plan, quota, storage and years, ready for the admin pages, and
        whether they look fake (suspect, with the reason for the chip)."""
        fake_sql, fake_params = fake_user_sql(app.config["ADMIN_EMAILS"], asof=asof)
        rows = db.query(
            "SELECT u.id, u.email, u.created_at, u.last_login_at, u.disabled, u.quota_bytes, "
            "u.verified_at, u.plan, u.totp_secret IS NOT NULL AS has_totp, "
            "(SELECT COUNT(*) FROM passkeys p WHERE p.user_id = u.id) AS passkeys, "
            "(u.totp_secret IS NOT NULL OR EXISTS (SELECT 1 FROM passkeys p "
            "WHERE p.user_id = u.id)) AS has_2fa, u.first_name, u.last_name, "
            "u.signup_ip, u.last_login_ip, "
            "(SELECT COUNT(*) FROM years y WHERE y.user_id = u.id) AS years, "
            "(SELECT COALESCE(SUM(size), 0) FROM documents d WHERE d.user_id = u.id) AS used, "
            f"CASE WHEN {fake_sql} THEN 1 ELSE 0 END AS suspect, "
            "(SELECT a.created_at FROM audit_log a WHERE a.user_id = u.id "
            " AND a.event = 'account_confirmed' ORDER BY a.id LIMIT 1) AS confirmed_at "
            f"FROM users u WHERE {where} ORDER BY {order}", (*fake_params, *params))
        plans = {p["key"]: p for p in plan_catalog()}
        # Without a custom quota an account gets the quota of its plan.
        return [dict(u, plan_name=plans.get(u["plan"], plans["free"])["name"],
                     plan_key=u["plan"] if u["plan"] in plans else "free",
                     quota=u["quota_bytes"] if u["quota_bytes"] is not None
                     else plans.get(u["plan"], plans["free"])["quota_bytes"],
                     quota_mb=quota_mb_text(u["quota_bytes"])
                     if u["quota_bytes"] is not None else "",
                     is_admin=u["email"] in app.config["ADMIN_EMAILS"], full_name=full_name(u),
                     fake_reason=fake_reason(u["created_at"], u["confirmed_at"])
                     if u["suspect"] else "")
                for u in rows]

    def activity_where(q):
        """The Security activity filter. An account's email: that account's whole history
        (by its id, so also from before an email change), events under that address that no
        other live account owns, and what it did as an admin. Anything else: a search in the
        event's email, actor, IP and detail, and in the event names ("fake", "password
        changed"). Empty: every event."""
        if not q:
            return "1 = 1", ()
        owner = db.query("SELECT id FROM users WHERE email = ?", (q,), one=True)
        if owner:
            # Events under the address without an account (failed sign ins for it before it
            # existed); never those of another account that used it, live or deleted.
            return ("(a.user_id = ? OR a.actor = ? OR (a.user_id IS NULL AND a.email = ?))",
                    (owner["id"], q, q))
        like = db.search_like(q)
        # The event as shown ("Password changed", "fake") or its key, and its detail ("Pro",
        # "#2026-3", "7 accounts"), besides the email, actor and IP.
        folded = db.search_fold(q)
        events = [k for k, label in AUDIT_LABELS.items()
                  if folded in db.search_fold(label) or folded == k]
        event_sql = f" OR a.event IN ({', '.join('?' * len(events))})" if events else ""
        return ("(casefold(a.email) LIKE ? ESCAPE '\\' OR casefold(a.actor) LIKE ? ESCAPE '\\' "
                "OR a.ip LIKE ? ESCAPE '\\' OR casefold(a.detail) LIKE ? ESCAPE '\\'"
                f"{event_sql})", (like, like, like, like, *events))

    def account_activity(email, limit):
        where, params = activity_where(email)
        return audit_rows(db.query(f"SELECT a.* FROM audit_log a WHERE {where} "
                                   "ORDER BY a.id DESC LIMIT ?", (*params, limit)))

    def activity_page(state):
        """One page of the Security activity table, sorted and paged in SQL: a year of failed
        sign ins by bots is far too much to load for 20 rows."""
        where, params = activity_where(state["q"])
        total = db.query(f"SELECT COUNT(*) AS n FROM audit_log a WHERE {where}", params,
                         one=True)["n"]
        pager = paged(total, state["apage"], ADMIN_AUDIT_PER_PAGE,
                      lambda n, _size: admin_url(**dict(state, apage=n)) + "#activity")
        fixed, key = AUDIT_SORTS[state["asort"]]
        order = ", ".join(filter(None, [fixed, f"{key} {state['adir'].upper()}", "a.id DESC"]))
        # The page is picked first and only its 20 rows are joined to users: joining before the
        # sort looked up an account for every event of the year. current_email: the account's
        # address now, so its link shows the whole history also for an event recorded before an
        # email change. a.id ends the order, so the outer sort keeps the page's order exactly.
        pager["rows"] = audit_rows(db.query(
            "SELECT a.*, u.email AS current_email FROM (SELECT a.* FROM audit_log a "
            f"WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?) a "
            f"LEFT JOIN users u ON u.id = a.user_id ORDER BY {order}",
            (*params, ADMIN_AUDIT_PER_PAGE, (pager["page"] - 1) * ADMIN_AUDIT_PER_PAGE)))
        return pager

    def admin_where(state, asof=None):
        """(where, params) on `users u` for the accounts table's search and fake filter: the
        rows the table lists, on every page, and the ones "Delete these N accounts" removes.
        asof: the moment the list was made (see fake_user_sql)."""
        where, params = "1 = 1", []
        if state["search"]:
            # casefold (registered in db.get_db) folds every script and composes accents.
            # Part of an IP finds every account that signed up or last signed in from it.
            like = db.search_like(state["search"])
            try:  # a whole IP address (the account page links to it) matches only itself
                ip = ipaddress.ip_address(state["search"])
            except ValueError:
                ip = None
            if ip is not None:
                where = ("(casefold(u.email) LIKE ? ESCAPE '\\' OR casefold(u.first_name || ' ' "
                         "|| u.last_name) LIKE ? ESCAPE '\\' OR u.signup_ip IN (?, ?) "
                         "OR u.last_login_ip IN (?, ?))")
                params = [like, like] + [state["search"], str(ip)] * 2
            else:
                where = ("(casefold(u.email) LIKE ? ESCAPE '\\' OR casefold(u.first_name || ' ' "
                         "|| u.last_name) LIKE ? ESCAPE '\\' OR u.signup_ip LIKE ? ESCAPE '\\' "
                         "OR u.last_login_ip LIKE ? ESCAPE '\\')")
                params = [like] * 4
        if state["fake"]:
            fake_sql, fake_params = fake_user_sql(app.config["ADMIN_EMAILS"], asof=asof)
            where = f"{where} AND {'NOT ' if state['fake'] == 'hide' else ''}{fake_sql}"
            params = [*params, *fake_params]
        return where, params

    @app.route("/admin")
    @login_required
    def admin():
        require_admin()
        state = admin_list_state(request.args)
        # One moment for the whole page: "Delete these N accounts" sends it back, so accounts
        # that pass the 24 hour line meanwhile are neither listed nor deleted.
        asof = utc_now_text()
        where, params = admin_where(state, asof)
        fake_sql, fake_params = fake_user_sql(app.config["ADMIN_EMAILS"], asof=asof)
        direction = state["dir"].upper()
        order = ", ".join(f"{part} {direction}" if not part.endswith("IS NULL") else part
                          for part in ADMIN_USER_SORTS[state["sort"]].split(", "))
        users = admin_accounts(where, params, f"{order}, u.id {direction}", asof)
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
            # Likely fake accounts, whatever the search: the count on the filter.
            "suspects": db.query(f"SELECT COUNT(*) AS n FROM users u WHERE {fake_sql}",
                                 fake_params, one=True)["n"],
        }
        pager = paginate(users, state["page"], ADMIN_USERS_PER_PAGE,
                         lambda n, _size: admin_url(**dict(state, page=n)) + "#accounts")
        state["page"] = pager["page"]
        activity = activity_page(state)
        state["apage"] = activity["page"]
        return render_template("admin.html", pager=pager, totals=totals, state=state, asof=asof,
                               admin_url=admin_url, admin_hidden=admin_hidden,
                               account_url=admin_account_url,
                               default_quota=app.config["USER_QUOTA_BYTES"],
                               plans=plan_catalog(), activity=activity, audit_days=AUDIT_DAYS,
                               audit_export_days=AUDIT_EXPORT_DAYS)

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
        subscription = db.query("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC "
                                "LIMIT 1", (user_id,), one=True)
        return render_template("admin_account.html", u=u, counts=counts, state=state,
                               subscription=subscription,
                               paying=bool(open_subscription(user_id)),
                               stripe_dashboard="https://dashboard.stripe.com/" + (
                                   "test/" if "_test_" in app.config["STRIPE_SECRET_KEY"]
                                   else ""),  # sk_test_ and restricted rk_test_ keys
                               admin_url=admin_url, admin_hidden=admin_hidden,
                               back=admin_url(**state), plans=plan_catalog(),
                               activity=account_activity(u["email"], ADMIN_ACCOUNT_AUDIT_ROWS),
                               activity_limit=ADMIN_ACCOUNT_AUDIT_ROWS,
                               history=admin_url(**dict(state, q=u["email"], apage=1)) + "#activity",
                               tickets_url=url_for("admin_support", q=u["email"]))

    @app.route("/admin/plans/<key>", methods=["POST"])
    @login_required
    def admin_plan_price(key):
        """The yearly price of a paid plan, as shown on the site. It must match the plan's
        Stripe price (what people are charged). Empty: back to the price in PLANS."""
        require_admin()
        if key not in PLANS or key == "free":
            abort(404)
        name = PLANS[key]["name"]
        default = PLANS[key]["year_cents"]
        # Back to the same view of both tables, without an anchor: the message at the top of
        # the page stays in view.
        back = admin_url(**admin_list_state(request.form))
        raw_year = request.form.get("year", "").strip()
        year = parse_price(raw_year)
        # Empty, or equal to the default: no custom price is kept, so the plan follows the
        # default in PLANS from now on.
        if not raw_year or year == default:
            if db.execute_rowcount("DELETE FROM plan_prices WHERE plan = ?", (key,)):
                audit("admin_price", None, "", f"{name}: default price", current_user.email)
                flash(f"{name} is back to its default price (${money(default)} / year).",
                      "success")
            else:
                flash(f"{name} already uses its default price (${money(default)} / year).",
                      "info")
            return redirect(back)
        if not year or year > MAX_PRICE_CENTS:
            flash(f"Enter the yearly price of {name} as an amount in dollars, for example 9 or "
                  "9.99 (up to 10000), or leave it empty for the default.", "error")
            return redirect(back)
        # month_cents is kept (a twelfth) for databases from before yearly only plans.
        db.execute("INSERT INTO plan_prices (plan, month_cents, year_cents) VALUES (?, ?, ?) "
                   "ON CONFLICT(plan) DO UPDATE SET month_cents = excluded.month_cents, "
                   "year_cents = excluded.year_cents, updated_at = CURRENT_TIMESTAMP",
                   (key, year // 12, year))
        audit("admin_price", None, "", f"{name}: ${money(year)} / year", current_user.email)
        flash(f"{name} now shows ${money(year)} / year. Set the same price on its Stripe "
              "product, which is what people are charged.", "success")
        return redirect(back)

    def csv_download(body, filename):
        """A CSV attachment (BOM, so Excel reads accents), never cached; sets the nl_download
        cookie when the page sent a dl token, so it knows the download started."""
        resp = Response(body, mimetype="text/csv")
        resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        resp.headers["Cache-Control"] = "private, no-store"
        token = request.values.get("dl", "")
        if re.fullmatch(r"[A-Za-z0-9]{8,40}", token):
            resp.set_cookie("nl_download", token, max_age=120, samesite="Lax", path="/")
        return resp

    @app.route("/admin/activity/export")
    @login_required
    def admin_activity_export():
        """The Security Activity of the last AUDIT_EXPORT_DAYS days, or all of it, as CSV,
        oldest first, read in chunks of AUDIT_EXPORT_CHUNK rows while it streams."""
        require_admin()
        range_key = request.args.get("range", "")
        if range_key not in AUDIT_RANGES:
            abort(404)
        where, params = audit_range_sql(range_key)
        last = db.query("SELECT COALESCE(MAX(id), 0) AS n FROM audit_log", one=True)["n"]

        def chunks():
            yield "\ufeff" + audit_csv_lines([], header=True)
            after = 0
            while True:
                rows = db.query(f"SELECT * FROM audit_log WHERE id > ? AND id <= ? AND {where} "
                                "ORDER BY id LIMIT ?", (after, last, *params, AUDIT_EXPORT_CHUNK))
                if not rows:
                    return
                yield audit_csv_lines(rows)
                after = rows[-1]["id"]

        return csv_download(stream_with_context(chunks()), audit_csv_name(range_key))

    @app.route("/admin/activity/purge", methods=["POST"])
    @login_required
    def admin_activity_purge():
        """Delete the Security Activity of the last AUDIT_EXPORT_DAYS days, or all of it, after
        the two step dialog and PURGE typed, and answer with the CSV of what was deleted. The
        rows are read before the write lock (no slow work while holding it) up to the newest
        id at that moment; the delete takes only those, so an event written meanwhile (a sign
        in) stays and is not in the file."""
        require_admin()
        state = admin_list_state(request.form)
        back = admin_url(**dict(state, apage=1))  # no anchor: the message stays in view
        range_key = request.form.get("range", "")
        if range_key not in AUDIT_RANGES:
            abort(404)
        if not delete_confirmed():
            return redirect(back)
        if "".join(request.form.get("confirm_word", "").split()).upper() != "PURGE":
            flash("Type PURGE to confirm.", "error")
            return redirect(back)
        where, params = audit_range_sql(range_key)
        last = db.query("SELECT COALESCE(MAX(id), 0) AS n FROM audit_log", one=True)["n"]
        rows = db.query(f"SELECT * FROM audit_log WHERE id <= ? AND {where} ORDER BY id",
                        (last, *params))
        if not rows:
            flash("There was no security activity to purge.", "info")
            return redirect(back)
        with db.transaction() as conn:
            # The ids read under the same write lock as the delete are exactly what goes, so
            # the file lists only those (another admin may have purged some meanwhile).
            gone = {r[0] for r in conn.execute(
                f"SELECT id FROM audit_log WHERE id <= ? AND {where}", (last, *params))}
            conn.execute(f"DELETE FROM audit_log WHERE id <= ? AND {where}", (last, *params))
        rows = [r for r in rows if r["id"] in gone]
        if not rows:
            flash("This security activity was already purged meanwhile.", "info")
            return redirect(back)
        body = "\ufeff" + audit_csv_lines(rows, header=True)
        what = (f"of the last {AUDIT_EXPORT_DAYS} days" if range_key == "30" else "")
        flash(f"Purged {plural(len(rows), 'event')} of security activity {what}".rstrip()
              + ". The CSV of what was deleted is in your downloads.", "success")
        return csv_download(body, audit_csv_name(range_key))

    @app.route("/admin/suspects/delete", methods=["POST"])
    @login_required
    def admin_delete_suspects():
        """Delete the suspected fake accounts the "Suspected fake" view lists (every page,
        within its search), after the two step dialog and the number of accounts typed. The
        list is read again, counted and deleted under one write lock, with a Security activity
        row for each account and one for the whole deletion, so what goes is what was
        confirmed, and nothing goes unrecorded."""
        require_admin()
        state = admin_list_state(request.form)
        back = admin_url(**dict(state, page=1))  # no anchor: the message at the top stays in view
        if state["fake"] != "only":
            flash("Show the suspected fake accounts first, then delete them from that list.",
                  "error")
            return redirect(back)
        if not delete_confirmed():
            return redirect(back)
        typed = request.form.get("confirm_count", "").strip()
        if not (typed.isascii() and typed.isdigit() and len(typed) <= 9):
            flash("Type the number of accounts to confirm the deletion.", "error")
            return redirect(back)
        # The list as it was when the page was made: an account passing the 24 hour line since
        # was not shown, so it is not deleted (never later than now).
        asof = request.form.get("asof", "")
        try:
            datetime.strptime(asof, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            flash("This list is out of date. Reload the page and delete again.", "error")
            return redirect(back)
        asof = min(asof, utc_now_text())
        where, params = admin_where(state, asof)
        matching = f' matching "{state["search"]}"' if state["search"] else ""
        who = current_user.email
        with db.transaction() as conn:
            rows = conn.execute(
                "SELECT u.id, u.email, u.created_at, (SELECT a.created_at FROM audit_log a "
                "WHERE a.user_id = u.id AND a.event = 'account_confirmed' ORDER BY a.id "
                f"LIMIT 1) AS confirmed_at FROM users u WHERE {where} ORDER BY u.id",
                params).fetchall()
            if rows and len(rows) == int(typed):
                # The same rule at the same moment again in the DELETE, under the same lock:
                # nothing can sign in or add data between the count and the deletion, so it
                # removes exactly the rows counted and recorded below.
                if conn.execute(f"DELETE FROM users WHERE id IN (SELECT u.id FROM users u "
                                f"WHERE {where})", params).rowcount != len(rows):
                    raise RuntimeError("bulk delete removed other rows than it counted")
                for r in rows:
                    audit("admin_delete_fake", r["id"], r["email"],
                          fake_reason(r["created_at"], r["confirmed_at"]), who, conn=conn)
                audit("admin_fake_cleanup", None, "", plural(len(rows), "account") + matching,
                      who, conn=conn)
        if not rows:
            flash(f"No suspected fake accounts{matching} to delete.", "info")
            return redirect(back)
        if len(rows) != int(typed):
            flash(f"The list changed: {plural(len(rows), 'account')} "
                  f"{'is' if len(rows) == 1 else 'are'} suspected now{matching}. Nothing was "
                  "deleted: check them and delete again.", "error")
            return redirect(back)
        for r in rows:  # an account without receipts has at most an empty folder
            try:
                os.rmdir(os.path.join(app.config["UPLOAD_DIR"], str(r["id"])))
            except OSError:
                pass
        flash(f"{plural(len(rows), 'suspected fake account')}{matching} "
              f"{'was' if len(rows) == 1 else 'were'} deleted.", "info")
        return redirect(back)

    @app.route("/admin/users/<int:user_id>", methods=["POST"])
    @login_required
    def admin_user(user_id):
        require_admin()
        state = admin_list_state(request.form)
        row = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
        if row is None:
            # Deleted meanwhile (another admin, the cleanup of unconfirmed accounts) while its
            # page was open: say so and go back to the same list, not to a bare 404.
            flash("This account no longer exists.", "error")
            return redirect(admin_url(**state))
        back = admin_account_url(user_id, state)

        def gone():
            flash(f"The account {row['email']} no longer exists, so nothing was changed.", "error")
            return redirect(admin_url(**state))
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
                if not db.execute_rowcount("UPDATE users SET quota_bytes = NULL WHERE id = ?",
                                           (user_id,)):
                    return gone()
                audit("admin_quota", user_id, email, "plan default", who)
                flash(f"{email} now uses the storage quota of the {plan_named(row['plan'])['name']} "
                      f"plan ({format_size(user_quota(user_id), 'down')}).", "success")
            else:
                try:
                    mb = float(raw)
                except ValueError:
                    mb = -1
                mb = round(mb, 2)  # what the field shows again, so saving it again changes nothing
                if not 1 <= mb <= MAX_QUOTA_MB:
                    flash(f"Enter a storage quota between 1 and {MAX_QUOTA_MB} MB, or leave it "
                          "empty for the default.", "error")
                else:
                    quota = math.ceil(mb * MB)
                    if not db.execute_rowcount("UPDATE users SET quota_bytes = ? WHERE id = ?",
                                               (quota, user_id)):
                        return gone()
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
                if not db.execute_rowcount("UPDATE users SET plan = ? WHERE id = ?", (key, user_id)):
                    return gone()
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
                changed = conn.execute("UPDATE users SET disabled = 1, session_version = "
                                       "session_version + 1 WHERE id = ?", (user_id,)).rowcount
                conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            if not changed:
                return gone()
            audit("admin_disable", user_id, email, "", who)
            flash(f"{email} is disabled and signed out everywhere.", "info")
        elif action == "enable":
            if not db.execute_rowcount("UPDATE users SET disabled = 0 WHERE id = ?", (user_id,)):
                return gone()
            audit("admin_enable", user_id, email, "", who)
            if row["verified_at"] is None:
                # A blocked sign up that was never confirmed: enabled, it is removed like any
                # other once its 20 minutes are over, which frees the address (and its page).
                purge_unverified()
                if not db.query("SELECT 1 FROM users WHERE id = ?", (user_id,), one=True):
                    flash(f"{email} is no longer blocked. The account was never confirmed, so it "
                          "was removed: the address can sign up again.", "success")
                    return redirect(admin_url(**state))
            flash(f"{email} can sign in again.", "success")
        elif action == "delete":
            if not delete_confirmed():
                pass
            elif request.form.get("confirm_email", "").strip().lower() != email:
                flash("Type the email address to confirm the deletion.", "error")
            elif open_subscription(user_id):
                flash(f"{email} still pays for a plan: cancel the subscription in Stripe first, "
                      "or Stripe keeps charging it.", "error")
            elif not delete_account(user_id):  # another admin was quicker: report it once
                flash(f"The account {email} was already deleted.", "info")
                back = admin_url(**state)
            else:
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

    def ticket_url(endpoint, ticket, **list_args):
        return url_for(endpoint, **ticket_args(ticket), **list_args)

    def support_list_args(source):
        """The tab and page of /support, carried to a ticket and back."""
        status = source.get("status")
        page = to_int(source.get("page"), 1)
        return {k: v for k, v in (("status", status if status in ("open", "closed") else None),
                                  ("page", page if page > 1 else None)) if v is not None}

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
        list_args = support_list_args({"status": status, "page": pager["page"]})
        return render_template("support.html", pager=pager, status=status, counts=counts,
                               kinds=TICKET_KINDS, list_args=list_args)

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
                try:
                    with db.transaction() as conn:
                        # The same ticket sent twice (a double click): the second one finds the
                        # first under the write lock and opens it instead of making another.
                        ticket = conn.execute(
                            "SELECT t.* FROM tickets t JOIN ticket_messages m ON m.ticket_id = t.id "
                            "WHERE t.user_id = ? AND t.kind = ? AND t.subject = ? AND m.body = ? "
                            "AND t.created_at >= datetime('now', ?) ORDER BY t.id DESC LIMIT 1",
                            (current_user.id, kind, subject, body,
                             f"-{DUPLICATE_SECONDS} seconds")).fetchone()
                        duplicate = ticket is not None
                        # Counted again under the write lock: parallel posts cannot pass the cap.
                        if duplicate:
                            pass
                        elif conn.execute("SELECT COUNT(*) FROM tickets WHERE user_id = ? AND "
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
                except db.IntegrityError:  # the account was deleted while this was sent
                    return account_gone()
            if err:
                flash(err, "error")
                return render_template("support_new.html", form=form, kinds=TICKET_KINDS,
                                       subject_max=TICKET_SUBJECT_MAX,
                                       body_max=TICKET_BODY_MAX), 400
            ref = ticket_ref(ticket)
            if not duplicate:
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
        list_args = support_list_args(request.args)  # back to the same tab and page
        back_url = url_for("support", **list_args)
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
                    try:
                        with db.transaction() as conn:
                            duplicate = duplicate_message(conn, ticket_id, current_user.id, False, body)
                            if not duplicate:
                                message_id = conn.execute(
                                    "INSERT INTO ticket_messages (ticket_id, author_id, body) "
                                    "VALUES (?, ?, ?)", (ticket_id, current_user.id, body)).lastrowid
                                # Writing on a closed ticket opens it again, unless the account is
                                # already at its limit of open tickets (the message is kept).
                                open_now = conn.execute("SELECT COUNT(*) FROM tickets WHERE "
                                                        "user_id = ? AND status = 'open'",
                                                        (current_user.id,)).fetchone()[0]
                                reopened = conn.execute(
                                    "UPDATE tickets SET status = 'open', closed_at = NULL, "
                                    "closed_by = '' WHERE id = ? AND status = 'closed' AND ? < ?",
                                    (ticket_id, open_now, OPEN_TICKETS_MAX)).rowcount
                                still_closed = conn.execute(
                                    "SELECT status FROM tickets WHERE id = ?",
                                    (ticket_id,)).fetchone()[0] == "closed"
                                conn.execute("UPDATE tickets SET updated_at = CURRENT_TIMESTAMP, "
                                             "user_seen_id = MAX(user_seen_id, ?) WHERE id = ?",
                                             (message_id, ticket_id))
                    except db.IntegrityError:  # the account was deleted while this was sent
                        return account_gone()
                    if duplicate:  # sent twice: the first one already did everything
                        flash("Your message was sent.", "success")
                        return redirect(ticket_url("support_ticket", ticket, **list_args))
                    audit("ticket_reopened" if reopened else "ticket_message", current_user.id,
                          current_user.email, f"#{ref}")
                    notify_admins(ticket, "reopened" if reopened else "reply",
                                  current_user.email)
                    flash("Your message was sent." + (
                        " The ticket is open again." if reopened else
                        f" The ticket stays closed: you already have {OPEN_TICKETS_MAX} open "
                        "tickets. Close the solved ones to open it again." if still_closed
                        else ""), "success")
                    return redirect(ticket_url("support_ticket", ticket, **list_args))
                return render_template("support_ticket.html", ticket=ticket, back_url=back_url,
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
            return redirect(ticket_url("support_ticket", ticket, **list_args))
        messages = ticket_messages(ticket_id)
        if messages and messages[-1]["id"] > ticket["user_seen_id"]:
            # Housekeeping: skipped when the database is busy (the dot stays until next time),
            # so reading a ticket never fails behind another request's write.
            db.try_execute("UPDATE tickets SET user_seen_id = MAX(user_seen_id, ?) WHERE id = ?",
                           (messages[-1]["id"], ticket_id))
        return render_template("support_ticket.html", ticket=ticket, back_url=back_url, messages=messages,
                               admin_view=False, kinds=TICKET_KINDS, body_max=TICKET_BODY_MAX,
                               draft="", at_cap=open_ticket_cap_reached(ticket))

    def admin_support_args(**values):
        # "all" is the default of the filters only: a search for the word "all" is kept.
        return {k: v for k, v in values.items() if v not in (None, "")
                and not (k == "page" and v in (1, "1"))
                and not (k in ("status", "kind") and v == "all")}

    def admin_support_url(**values):
        return url_for("admin_support", **admin_support_args(**values))

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
        # Filters, search, sort and page go to each ticket, so its Back returns here.
        list_args = admin_support_args(**state, page=pager["page"])
        return render_template("admin_support.html", pager=pager, state=state, counts=counts,
                               kinds=TICKET_KINDS, sorts=list(TICKET_SORTS),
                               support_url=admin_support_url, list_args=list_args)

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
        # The list's filters, search, sort and page, for Back and after each action. Only
        # carried in links: /admin/support checks them again.
        list_args = admin_support_args(**{k: request.args.get(k, "")[:254] for k in
                                          ("status", "kind", "q", "sort", "dir", "page")})

        def ticket_gone():
            # Deleted with its account (by the user, an admin or the fake account cleanup)
            # while this page was open: say so instead of a bare "Page not found" or an error.
            flash(f"Ticket #{year}-{seq} no longer exists (its account was deleted), so nothing "
                  "was sent or changed.", "error")
            return redirect(admin_support_url(**list_args))
        if ticket is None:
            if request.method == "POST":
                return ticket_gone()
            abort(404)
        ticket_id, ref = ticket["id"], ticket_ref(ticket)
        back = ticket_url("admin_ticket", ticket, **list_args)
        back_url = admin_support_url(**list_args)
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
                        current = conn.execute("SELECT status FROM tickets WHERE id = ?",
                                               (ticket_id,)).fetchone()
                        if current is None:
                            return ticket_gone()
                        still_open = current[0] == "open"
                        duplicate = still_open and duplicate_message(
                            conn, ticket_id, current_user.id, True, body)
                        if still_open and not duplicate:
                            message_id = conn.execute(
                                "INSERT INTO ticket_messages (ticket_id, author_id, from_admin, "
                                "body) VALUES (?, ?, 1, ?)",
                                (ticket_id, current_user.id, body)).lastrowid
                            conn.execute("UPDATE tickets SET updated_at = CURRENT_TIMESTAMP, "
                                         "admin_seen_id = MAX(admin_seen_id, ?) WHERE id = ?",
                                         (message_id, ticket_id))
                    if duplicate:  # sent twice: the first one already did everything
                        flash("Your answer was sent. The user gets an email.", "success")
                        return redirect(back)
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
                return render_template("support_ticket.html", ticket=ticket, back_url=back_url,
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
                elif not db.query("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,), one=True):
                    return ticket_gone()
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
                elif not db.query("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,), one=True):
                    return ticket_gone()
                else:
                    flash("This ticket is already open.", "info")
            return redirect(back)
        messages = ticket_messages(ticket_id)
        if messages and messages[-1]["id"] > ticket["admin_seen_id"]:
            db.try_execute("UPDATE tickets SET admin_seen_id = MAX(admin_seen_id, ?) WHERE id = ?",
                           (messages[-1]["id"], ticket_id))
        return render_template("support_ticket.html", ticket=ticket, back_url=back_url, messages=messages,
                               admin_view=True, kinds=TICKET_KINDS, body_max=TICKET_BODY_MAX,
                               draft="", owner_name=full_name(ticket))

    # --- documents ---

    def get_document_or_404(doc_id):
        row = db.query("SELECT * FROM documents WHERE id = ? AND user_id = ?",
                       (doc_id, current_user.id), one=True)
        if row is None:
            abort(404)
        return row

    def document_gone(message):
        """A receipt form left open after the receipt (or its stay or year) was deleted in
        another tab: say so instead of a bare "Page not found". Another user's receipt gets the
        same answer, so nothing tells the two apart. The page it came from may be gone too."""
        flash(message, "info" if "already deleted" in message else "error")
        return redirect(url_for("index"))

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
        if not db.query("SELECT 1 FROM documents WHERE id = ? AND user_id = ?",
                        (doc_id, current_user.id), one=True):
            return document_gone("This document was already deleted.")
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
        doc = db.query("SELECT * FROM documents WHERE id = ? AND user_id = ?",
                       (doc_id, current_user.id), one=True)
        if doc is None:
            return document_gone("This document no longer exists, so the changes were not "
                                 "saved.")
        # A typed name is not a path: keep "Rent 03/2026" instead of dropping "Rent 03/".
        # Trailing dots and spaces would leave "Hotel..pdf" (Windows drops them anyway).
        typed = request.form.get("name", "").replace("/", "-").replace("\\", "-")
        typed = typed.strip().rstrip(". ")
        # A name of invisible characters only (zero width spaces, blank letters) would show
        # as an empty name, or as the extension alone.
        name = display_name(typed) if visible(stem(typed)) else ""
        kind = request.form.get("kind", "")
        ext = doc_format(doc)
        if not name:
            flash("Please enter a name for the document.", "error")
        elif kind not in DOCUMENT_KINDS:
            flash("Please choose a document type.", "error")
        else:
            if {file_ext(name), ext} == {"jpg", "jpeg"}:
                name = f"{stem(name)}.{ext}"  # the same type: no "photo.jpg.jpeg"
            elif file_ext(name) != ext:
                name = f"{name}.{ext}"  # keep the real extension so downloads still open
            # The file inside the ZIP carries the name too. It is rewritten into a temporary file
            # outside the database write lock (a big receipt takes seconds and would stall every
            # other request), then moved into place under the lock only if nobody changed the
            # receipt meanwhile; two renames at once (a double click, two tabs) simply retry.
            folder = os.path.join(app.config["UPLOAD_DIR"], str(current_user.id))
            changed = 0
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
                    except package.RECEIPT_ERRORS as exc:
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
            if current is None:  # deleted in another tab while the file was rewritten
                return document_gone("This document no longer exists, so the changes were not "
                                     "saved.")
            flash("Document updated." if changed else "The document was changed elsewhere "
                  "at the same time. Please try again.", "success" if changed else "error")
        return redirect(request.referrer or url_for("index"))


app = create_app()

if __name__ == "__main__":
    os.umask(0o077)  # the database and receipts are private to the account running the app
    app.run(host="127.0.0.1", port=app.config["APP_PORT"],
            debug=os.getenv("FLASK_DEBUG") == "1")
