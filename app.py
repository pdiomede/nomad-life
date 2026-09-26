"""Nomad Life: track your yearly movements and keep your receipts in one place."""
import hashlib
import math
import mimetypes
import os
import re
import secrets
from urllib.parse import quote, urlencode
import uuid
from collections import OrderedDict
import unicodedata
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, redirect, render_template, request,
                   send_from_directory, url_for)
from markupsafe import escape
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_wtf.csrf import CSRFError, CSRFProtect
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

import db
import package
from countries import COUNTRIES, COUNTRY_CODES, COUNTRY_DATA, flag_emoji
from mailer import send_email

APP_VERSION = "0.0.8"
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
SITE_TITLE = "Nomad Life | Every day. Every place."
SITE_DESCRIPTION = ("Track the days you spend in each country and keep your travel receipts safe, "
                    "all in one place. Made for digital nomads.")
SHARE_TEXT = "Track the days you spend in each country and keep your travel receipts safe."
# Search result snippet for the landing page (title under 60 and description under 160 characters).
SEO_TITLE = "Digital nomad day tracker and receipt vault | Nomad Life"
SEO_DESCRIPTION = ("Count your days in each country, watch the 183 day line and keep rental contracts, "
                   "hotel bills and flight tickets in one place. Free for digital nomads.")
# Paths that need an account. Kept out of search engines via robots.txt.
PRIVATE_PATHS = ["/app", "/year/", "/movements/", "/documents/", "/reset/"]
RESET_TOKEN_MAX_AGE = 3600

load_dotenv(os.path.join(BASE_DIR, "config.env"))
mimetypes.add_type("application/manifest+json", ".webmanifest")


def _abs(path):
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

    def get_id(self):
        # Binding the session to the password means a reset signs out every other session.
        return f"{self.id}:{self.fingerprint}"


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
    return int(value * MB)


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "").strip(),
        APP_PORT=int(os.getenv("APP_PORT", "5050")),
        APP_BASE_URL=os.getenv("APP_BASE_URL", ""),
        GMAIL_USER=os.getenv("GMAIL_USER", ""),
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
    )
    if overrides:
        app.config.update(overrides)
    # Reject oversized request bodies before reading them. The extra megabyte leaves room for
    # the other form fields; the exact per receipt limit is checked in save_upload.
    app.config["MAX_CONTENT_LENGTH"] = app.config["MAX_RECEIPT_BYTES"] + MB

    db.init_db(app.config["DATABASE_PATH"])
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    app.teardown_appcontext(db.close_db)

    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = load_or_create_secret(
            os.path.join(os.path.dirname(app.config["DATABASE_PATH"]), ".secret_key"))
    if not app.config["APP_BASE_URL"]:
        # Never build reset links from the request Host header.
        app.config["APP_BASE_URL"] = f"http://localhost:{app.config['APP_PORT']}"

    CSRFProtect(app)
    login_manager = LoginManager(app)
    login_manager.login_view = "login"
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(session_id):
        uid, _, fingerprint = session_id.partition(":")
        row = db.query("SELECT * FROM users WHERE id = ?", (uid,), one=True)
        if row is None or password_fingerprint(row["password_hash"]) != fingerprint:
            return None
        return User(row)

    @app.context_processor
    def inject_globals():
        return {"app_version": APP_VERSION, "countries": COUNTRIES, "country_data": COUNTRY_DATA,
                "document_kinds": DOCUMENT_KINDS,
                "max_receipt_bytes": app.config["MAX_RECEIPT_BYTES"],
                "max_receipt_label": format_size(app.config["MAX_RECEIPT_BYTES"], "down"),
                "storage": storage_summary(current_user.id)
                if current_user.is_authenticated else None,
                "site": site_meta()}

    app.add_template_filter(parse_date, "todate")
    app.add_template_filter(format_size, "filesize")
    app.add_template_filter(local_date, "localdate")
    app.add_template_filter(country_flag, "flag")

    @app.errorhandler(413)
    def too_large(_e):
        flash(f"The file is larger than the {format_size(app.config['MAX_RECEIPT_BYTES'], 'down')} "
              "limit per receipt.", "error")
        return redirect(request.referrer or url_for("index"))

    @app.errorhandler(CSRFError)
    def csrf_error(_e):
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

def serializer():
    from flask import current_app
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


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
    if size < 1024 * 1024:
        return f"{step(size / 1024)} KB"
    return f"{step(size / MB * 10) / 10:.1f}".rstrip("0").rstrip(".") + " MB"


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
        ext = file_ext(name)
        name = name[:limit - len(ext) - 1].rstrip() + "." + ext if ext else name[:limit]
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
             "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
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


def storage_summary(user_id):
    from flask import current_app
    quota = current_app.config["USER_QUOTA_BYTES"]
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
    limit = current_app.config["MAX_RECEIPT_BYTES"]
    quota = current_app.config["USER_QUOTA_BYTES"]
    if size == 0:
        return "The file is empty. Please choose another file."
    if size > limit:
        return (f"{name} is {format_size(size, 'up')}, larger than the "
                f"{format_size(limit, 'down')} limit per receipt.")
    left = max(quota - storage_used(current_user.id), 0)
    if size > left:
        return quota_message(name, size, left, quota)

    user_dir = os.path.join(current_app.config["UPLOAD_DIR"], str(current_user.id))
    os.makedirs(user_dir, exist_ok=True)
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
    except BaseException:
        os.remove(path)  # no orphan file when the row could not be saved
        raise
    return None


class QuotaExceeded(Exception):
    def __init__(self, left):
        super().__init__(left)
        self.left = left


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


def delete_confirmed():
    """Deletes must come from the two step confirmation dialog, which sets confirm_delete=2."""
    if request.form.get("confirm_delete") == "2":
        return True
    flash("Please confirm the deletion twice.", "error")
    return False


def validate_movement(form, year):
    city = " ".join(form.get("city", "").split())
    country = normalize_country(form.get("country", ""))
    start = parse_date(form.get("start_date"))
    end = parse_date(form.get("end_date"))
    if not city or not country:
        return None, "City and country are required."
    if not start or not end:
        return None, "Please provide valid start and end dates."
    if end < start:
        return None, "The end date must be on or after the start date."
    if start.year != year or end.year != year:
        return None, f"Dates must fall within {year}."
    return {"city": city, "country": country, "start_date": start.isoformat(),
            "end_date": end.isoformat(), "notes": form.get("notes", "").strip()}, None


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
        flash("This stay overlaps " + "; ".join(notes) + ". Shared days count toward the stay "
              "that started later, so check the dates if that is not intended.", "info")


def can_download_package(user):
    """Who may download the annual accountant package. Everyone for now; a future Pro plan
    only needs to change this function."""
    return True


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
        include_notes=include_notes)


def compute_stats(year_row, movements):
    """Count days per country. Days not covered by a movement count as base."""
    y = year_row["year"]
    first, last = date(y, 1, 1), date(y, 12, 31)
    total = (last - first).days + 1
    base_name = normalize_country(year_row["base_country"])
    base = base_name.casefold()
    # Countries are compared case insensitively so "portugal" and "Portugal" are one country.
    names = {base: base_name}
    assigned = {}
    assigned_mv = {}
    # A shared day belongs to the stay that started later (the arrival). On the same start
    # day the shorter stay wins, so a side trip is never hidden by the longer stay around it.
    for m in stay_order(movements):
        name = normalize_country(m["country"])
        key = name.casefold()
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

    per_country = {}
    per_country_so_far = {}
    d = first
    while d <= last:
        c = assigned.get(d, base)
        per_country[c] = per_country.get(c, 0) + 1
        if d <= elapsed_end:
            per_country_so_far[c] = per_country_so_far.get(c, 0) + 1
        d += timedelta(days=1)

    rows = [{"country": names[c], "days": n, "so_far": per_country_so_far.get(c, 0),
             "pct": round(n * 100 / total, 1), "is_base": c == base,
             "over_threshold": n >= RESIDENCE_THRESHOLD}
            for c, n in per_country.items()]
    rows.sort(key=lambda r: (-r["days"], r["country"]))
    base_days = per_country.get(base, 0)
    return {"total": total, "rows": rows, "base_days": base_days, "counted": counted,
            "abroad_days": total - base_days, "threshold": RESIDENCE_THRESHOLD,
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
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            if not valid_email(email):
                flash("Please enter a valid email address.", "error")
            elif len(password) < 8:
                flash("Password must be at least 8 characters.", "error")
            elif password != confirm:
                flash("Passwords do not match.", "error")
            elif db.query("SELECT 1 FROM users WHERE email = ?", (email,), one=True):
                flash("An account with this email already exists.", "error")
            else:
                pw_hash = generate_password_hash(password)
                try:
                    uid = db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)",
                                     (email, pw_hash))
                except db.IntegrityError:  # same email registered concurrently
                    flash("An account with this email already exists.", "error")
                    return render_template("auth/signup.html")
                login_user(User({"id": uid, "email": email, "password_hash": pw_hash}))
                flash("Welcome to Nomad Life! Start by setting up your year.", "success")
                return redirect(url_for("index"))
        return render_template("auth/signup.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            row = db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                login_user(User(row), remember=bool(request.form.get("remember")))
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//")
                                else url_for("index"))
            flash("Invalid email or password.", "error")
        return render_template("auth/login.html")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        logout_user()
        flash("You have been signed out.", "info")
        return redirect(url_for("login"))

    @app.route("/forgot", methods=["GET", "POST"])
    def forgot():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            row = db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)
            if row:
                token = serializer().dumps(
                    {"uid": row["id"], "h": password_fingerprint(row["password_hash"])})
                link = app.config["APP_BASE_URL"].rstrip("/") + url_for("reset", token=token)
                send_email(email, "Reset your Nomad Life password",
                           "Hi,\n\nUse the link below to reset your Nomad Life password. "
                           f"It expires in 1 hour.\n\n{link}\n\n"
                           "If you did not request this, you can ignore this email.\n")
            flash("If that email is registered, a reset link is on its way.", "info")
            return redirect(url_for("login"))
        return render_template("auth/forgot.html")

    @app.route("/reset/<token>", methods=["GET", "POST"])
    def reset(token):
        try:
            data = serializer().loads(token, max_age=RESET_TOKEN_MAX_AGE)
        except SignatureExpired:
            flash("This reset link has expired. Please request a new one.", "error")
            return redirect(url_for("forgot"))
        except BadSignature:
            flash("This reset link is not valid.", "error")
            return redirect(url_for("forgot"))
        row = db.query("SELECT * FROM users WHERE id = ?", (data.get("uid"),), one=True)
        if not row or password_fingerprint(row["password_hash"]) != data.get("h"):
            flash("This reset link has already been used.", "error")
            return redirect(url_for("forgot"))
        if request.method == "POST":
            password = request.form.get("password", "")
            if len(password) < 8:
                flash("Password must be at least 8 characters.", "error")
            elif password != request.form.get("confirm", ""):
                flash("Passwords do not match.", "error")
            else:
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                           (generate_password_hash(password), row["id"]))
                flash("Your password has been updated. Please sign in.", "success")
                return redirect(url_for("login"))
        return render_template("auth/reset.html", token=token)

    # --- workspace ---

    @app.route("/")
    def landing():
        return render_template("landing.html")

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
                flash("Base city and country are required.", "error")
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
        return render_template("dashboard.html", y=year_row, movements=movements,
                               years=years, stats=compute_stats(year_row, movements),
                               base_docs=base_docs,
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
                if not city or not country:
                    flash("Base city and country are required.", "error")
                else:
                    db.execute("UPDATE years SET base_city = ?, base_country = ? WHERE id = ?",
                               (city, country, year_row["id"]))
                    flash("Base location updated.", "success")
            elif action == "upload":
                err = save_upload(request.files.get("file"), request.form.get("kind"),
                                  year_row["id"])
                flash(err or "Document uploaded.", "error" if err else "success")
            elif action == "delete_year":
                if not delete_confirmed():
                    pass
                elif request.form.get("confirm_year", "").strip() != str(year):
                    flash("Type the year to confirm deletion.", "error")
                else:
                    docs = db.query("SELECT * FROM documents WHERE year_id = ?",
                                    (year_row["id"],))
                    db.execute("DELETE FROM years WHERE id = ?", (year_row["id"],))
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
            if err:
                flash(err, "error")
            else:
                mid = db.execute(
                    "INSERT INTO movements (year_id, city, country, start_date, end_date, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (year_row["id"], data["city"], data["country"], data["start_date"],
                     data["end_date"], data["notes"]))
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
                data, err = validate_movement(request.form, m["year"])
                if err:
                    # Re-render so the user keeps what they typed.
                    flash(err, "error")
                    return render_template("movement.html", y=year_row, m=m, form=request.form,
                                           docs=documents_for(year_row["id"], movement_id))
                else:
                    db.execute("UPDATE movements SET city = ?, country = ?, start_date = ?, "
                               "end_date = ?, notes = ? WHERE id = ?",
                               (data["city"], data["country"], data["start_date"],
                                data["end_date"], data["notes"], movement_id))
                    flash("Movement updated.", "success")
                    flash_overlaps(year_row["id"], data, movement_id)
            elif action == "upload":
                err = save_upload(request.files.get("file"), request.form.get("kind"),
                                  year_row["id"], movement_id)
                flash(err or "Document uploaded.", "error" if err else "success")
            elif action == "delete":
                if not delete_confirmed():
                    return redirect(request.referrer or url_for("movement_edit", movement_id=movement_id))
                docs = documents_for(year_row["id"], movement_id)
                db.execute("DELETE FROM movements WHERE id = ?", (movement_id,))
                remove_files(docs)
                flash("Movement deleted.", "info")
                return redirect(url_for("dashboard", year=m["year"]))
            return redirect(url_for("movement_edit", movement_id=movement_id))
        return render_template("movement.html", y=year_row, m=m, form=m,
                               docs=documents_for(year_row["id"], movement_id))

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
            flash("The accountant package is not available on your plan.", "error")
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
        name = display_name(request.form.get("name", "").strip()) if request.form.get("name", "").strip() else ""
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
    app.run(host="127.0.0.1", port=app.config["APP_PORT"],
            debug=os.getenv("FLASK_DEBUG") == "1")
