"""SQLite storage for Nomad Life."""
import functools
import ipaddress
import logging
import os
import sqlite3
import unicodedata
from contextlib import contextmanager

from flask import current_app, g

IntegrityError = sqlite3.IntegrityError

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified_at TEXT,
    email_sent_at TEXT,
    quota_bytes INTEGER,
    disabled INTEGER NOT NULL DEFAULT 0,
    last_login_at TEXT,
    pending_email TEXT,
    session_version INTEGER NOT NULL DEFAULT 0,
    plan TEXT NOT NULL DEFAULT 'free',
    totp_secret TEXT,
    totp_step INTEGER NOT NULL DEFAULT 0,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    signup_ip TEXT NOT NULL DEFAULT '',
    last_login_ip TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS years (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year INTEGER NOT NULL,
    base_city TEXT NOT NULL,
    base_country TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, year)
);

CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year_id INTEGER NOT NULL REFERENCES years(id) ON DELETE CASCADE,
    city TEXT NOT NULL,
    country TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year_id INTEGER NOT NULL REFERENCES years(id) ON DELETE CASCADE,
    movement_id INTEGER REFERENCES movements(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    format TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0
);

-- Failed sign ins and password reset requests, to slow down password guessing and email
-- flooding. key is "email:<address>" or "ip:<address>". Old rows are pruned as they expire.
CREATE TABLE IF NOT EXISTS auth_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_auth_events ON auth_events(kind, key, created_at);

-- Prices set on the admin page, in cents. A plan without a row uses PLANS in app.py.
CREATE TABLE IF NOT EXISTS plan_prices (
    plan TEXT PRIMARY KEY,
    month_cents INTEGER NOT NULL,
    year_cents INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Security relevant events (sign ins, password and email changes, two-factor, admin actions).
-- email is kept as text so the history survives a deleted account; actor is the admin who
-- acted on someone else's account. Rows older than AUDIT_DAYS are pruned.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id INTEGER,
    email TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(created_at);
-- When each account was confirmed (the admin accounts table, its Likely fake chip and
-- check-fake-users): one entry per account, instead of reading every event of each account.
-- Queries must name the event as this literal for SQLite to use it.
CREATE INDEX IF NOT EXISTS idx_audit_confirmed ON audit_log(user_id, id)
    WHERE event = 'account_confirmed';

-- One row per signed in browser. Sign out deletes its row, so a copied session or remember
-- cookie stops working at once. Rows unused for a while are pruned.
CREATE TABLE IF NOT EXISTS user_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id);

-- Every confirmed email change, numbered by its id, which the undo link sent to the old
-- address carries. The link works while its change stands (undone_at NULL), also after later
-- changes, and undoing it undoes every later change of the account too: changing the address
-- once more cannot take the owner's way back, and the undo of a later change cannot take the
-- account from the owner again. Rows are pruned once the links have expired.
CREATE TABLE IF NOT EXISTS email_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    old_email TEXT NOT NULL,
    new_email TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    undone_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_email_changes_user ON email_changes(user_id, id);

-- Support tickets: one row per ticket, its messages apart. updated_at moves with every message
-- or status change; closed_at is NULL while open. The *_seen_id columns hold the last message
-- the user (or any admin) has seen, for the "new reply" badges. ref_year and ref_seq make the
-- number people see, 2026-1: set by the tickets_ref trigger, unique (see AFTER_MIGRATIONS).
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'question',
    subject TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT,
    closed_by TEXT NOT NULL DEFAULT '',
    user_seen_id INTEGER NOT NULL DEFAULT 0,
    admin_seen_id INTEGER NOT NULL DEFAULT 0,
    ref_year INTEGER NOT NULL DEFAULT 0,
    ref_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id, updated_at);
-- The last ticket number given in each year. Kept apart from the tickets, so the number of a
-- ticket deleted with its account is never given again.
CREATE TABLE IF NOT EXISTS ticket_counters (
    year INTEGER PRIMARY KEY,
    last_seq INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, updated_at);

CREATE TABLE IF NOT EXISTS ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    from_admin INTEGER NOT NULL DEFAULT 0,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ticket_messages ON ticket_messages(ticket_id, id);
-- Deleting an account sets author_id to NULL in its messages (ON DELETE SET NULL): without it
-- every deleted account read every message, holding the write lock (seconds for a bulk delete).
CREATE INDEX IF NOT EXISTS idx_ticket_messages_author ON ticket_messages(author_id);

CREATE INDEX IF NOT EXISTS idx_movements_year ON movements(year_id);
CREATE INDEX IF NOT EXISTS idx_documents_year ON documents(year_id);
CREATE INDEX IF NOT EXISTS idx_documents_movement ON documents(movement_id);
-- Storage used per account (every page's storage card, quotas, the admin accounts table):
-- without it each sum read every receipt of every account.
CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
"""


# The SQL functions below run once per row of a sort or search (every event of a year in the
# Security activity table), and their values repeat a lot (a few thousand emails and IPs), so
# their results are cached per process. They are pure: same text in, same key out.
KEY_CACHE = 16384


@functools.lru_cache(maxsize=KEY_CACHE)
def search_fold(text):
    """Lower case in every script, composed (NFC) before and after, so "Élodie" typed as one
    character or as E plus an accent (common when pasted from a Mac) finds the other."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def search_like(text):
    """A LIKE pattern (with ESCAPE '\\') that finds the folded text anywhere, so % and _ typed in
    a search box are matched as they are."""
    return ("%" + search_fold(text).replace("\\", "\\\\").replace("%", "\\%")
            .replace("_", "\\_") + "%")


@functools.lru_cache(maxsize=KEY_CACHE)
def sort_fold(value):
    """A text key for alphabetical order: case and accents ignored, so "élodie@" sorts with the
    e's instead of after z (COLLATE NOCASE only folds A to Z)."""
    if not isinstance(value, str):
        return value
    text = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


@functools.lru_cache(maxsize=KEY_CACHE)
def ip_sort_key(value):
    """A text key that sorts IP addresses by number (9.1.1.1 before 10.0.0.1), IPv4 before
    IPv6 (an IPv4 address written as ::ffff:1.2.3.4 counts as IPv4), anything else after
    them as typed. Empty stays NULL, for tables that put it last."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return "9:" + value
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return f"4:{int(ip):08x}" if ip.version == 4 else f"6:{int(ip):032x}"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        # In WAL mode (set by init_db) a commit is safe after an app crash with NORMAL, and
        # skips the fsync of every commit; a power cut can lose the last commits, never the file.
        g.db.execute("PRAGMA synchronous = NORMAL")
        # Unicode aware lower case for searches (SQLite's lower() only knows A to Z).
        g.db.create_function("casefold", 1,
                             lambda v: search_fold(v) if isinstance(v, str) else v,
                             deterministic=True)
        g.db.create_function("ip_sort_key", 1, ip_sort_key, deterministic=True)
        g.db.create_function("sort_fold", 1, sort_fold, deterministic=True)
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        # Password hashes and emails: readable by the account running the app only. SQLite
        # gives its journal files (nomad.db-wal and -shm) the same permissions.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        use_wal(conn)
        conn.executescript(SCHEMA)
        conn.commit()
        migrate(conn)
    finally:
        conn.close()


def use_wal(conn):
    """Write-ahead logging: readers and the writer no longer wait for each other (in the
    default rollback journal a write waits for every read, and new reads wait for it). The
    mode is stored in the database file, so this only changes it once. Copy the database only
    with scripts/backup.py: nomad.db alone misses what is still in nomad.db-wal."""
    try:
        mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    except sqlite3.OperationalError as exc:  # busy (a backup reading it): next start
        logging.getLogger(__name__).warning("Database kept in its journal mode: %s", exc)
        return None
    return mode


def _has_column(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


# (table, new column, statements that add it). Columns are only ever added, in this order.
MIGRATIONS = [
    # New accounts must confirm their email. Accounts made before that count as confirmed,
    # so the cleanup of unconfirmed accounts never removes them.
    ("users", "verified_at", ["ALTER TABLE users ADD COLUMN verified_at TEXT",
                              "UPDATE users SET verified_at = created_at"]),
    # When the last email went to the account, to limit how often one can be sent.
    ("users", "email_sent_at", ["ALTER TABLE users ADD COLUMN email_sent_at TEXT"]),
    # Admin page: a storage quota per account (NULL means USER_QUOTA_MB), disabled accounts
    # (sign in refused) and the last sign in.
    ("users", "quota_bytes", ["ALTER TABLE users ADD COLUMN quota_bytes INTEGER"]),
    ("users", "disabled", ["ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0"]),
    ("users", "last_login_at", ["ALTER TABLE users ADD COLUMN last_login_at TEXT"]),
    # The address an email change was last requested for: only its link can confirm it.
    ("users", "pending_email", ["ALTER TABLE users ADD COLUMN pending_email TEXT"]),
    # Raised to sign an account out everywhere for good (disabling it), without a new password.
    ("users", "session_version", ["ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL "
                                  "DEFAULT 0"]),
    # The user's plan (see PLANS in app.py). Everyone starts on Free.
    ("users", "plan", ["ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'"]),
    # Two-factor sign in: the TOTP secret (NULL means off) and the last time step used, so a
    # code can never be used twice.
    ("users", "totp_secret", ["ALTER TABLE users ADD COLUMN totp_secret TEXT"]),
    ("users", "totp_step", ["ALTER TABLE users ADD COLUMN totp_step INTEGER NOT NULL DEFAULT 0"]),
    # The name set on the Settings page (optional; empty means not given).
    ("users", "first_name", ["ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''"]),
    ("users", "last_name", ["ALTER TABLE users ADD COLUMN last_name TEXT NOT NULL DEFAULT ''"]),
    # Where an account signed up from (unknown before 1.4.0) and last signed in from, for the
    # admin page. The last sign in comes from the security history where it has one.
    ("users", "signup_ip", ["ALTER TABLE users ADD COLUMN signup_ip TEXT NOT NULL DEFAULT ''"]),
    ("users", "last_login_ip", [
        "ALTER TABLE users ADD COLUMN last_login_ip TEXT NOT NULL DEFAULT ''",
        "UPDATE users SET last_login_ip = COALESCE((SELECT a.ip FROM audit_log a "
        "WHERE a.user_id = users.id AND a.event = 'sign_in' ORDER BY a.id DESC LIMIT 1), '')",
    ]),
    # Receipts are stored as ZIP files since 1.2.12: size is the ZIP on disk (what quotas
    # count), format and file_size describe the file inside. Older rows point to plain files.
    ("documents", "format", ["ALTER TABLE documents ADD COLUMN format TEXT NOT NULL DEFAULT ''",
                             "UPDATE documents SET format = lower(substr(stored_name, "
                             "instr(stored_name, '.') + 1))"]),
    ("documents", "file_size", ["ALTER TABLE documents ADD COLUMN file_size INTEGER NOT NULL "
                                "DEFAULT 0", "UPDATE documents SET file_size = size"]),
    # Tickets are numbered per year since 1.3.1 (2026-1, 2026-2, 2027-1): existing tickets get
    # the year they were opened and their rank in it, and the history names them that way too.
    ("tickets", "ref_seq", [
        "ALTER TABLE tickets ADD COLUMN ref_year INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE tickets ADD COLUMN ref_seq INTEGER NOT NULL DEFAULT 0",
        "UPDATE tickets SET ref_year = CAST(strftime('%Y', created_at) AS INTEGER)",
        "UPDATE tickets SET ref_seq = (SELECT COUNT(*) FROM tickets t2 "
        "WHERE t2.ref_year = tickets.ref_year AND t2.id <= tickets.id)",
        "UPDATE audit_log SET detail = (SELECT '#' || t.ref_year || '-' || t.ref_seq FROM tickets t "
        "WHERE '#' || t.id = audit_log.detail) WHERE event LIKE 'ticket\\_%' ESCAPE '\\' "
        "AND detail IN (SELECT '#' || id FROM tickets)",
    ]),
]

# Indexes and triggers on migrated columns: SCHEMA runs before the columns exist on an old
# database, so these are created after MIGRATIONS, on every start (name, statement).
AFTER_MIGRATIONS = [
    # Two tickets never share a number.
    ("idx_tickets_ref", "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_ref "
                        "ON tickets(ref_year, ref_seq)"),
    # Every new ticket takes the next number of the year it was opened in (UTC), counted in the
    # inserting transaction, so parallel inserts cannot get the same one. The counter only goes
    # up (and never below a number already used), so a deleted ticket's number is not reused.
    ("tickets_number", "CREATE TRIGGER IF NOT EXISTS tickets_number AFTER INSERT ON tickets "
                       "WHEN NEW.ref_seq = 0 BEGIN "
                       "INSERT INTO ticket_counters (year, last_seq) VALUES ("
                       "CAST(strftime('%Y', NEW.created_at) AS INTEGER), "
                       "(SELECT COALESCE(MAX(ref_seq), 0) + 1 FROM tickets WHERE ref_year = "
                       "CAST(strftime('%Y', NEW.created_at) AS INTEGER))) "
                       "ON CONFLICT(year) DO UPDATE SET last_seq = MAX(last_seq, "
                       "(SELECT COALESCE(MAX(ref_seq), 0) FROM tickets "
                       "WHERE ref_year = excluded.year)) + 1; "
                       "UPDATE tickets SET ref_year = CAST(strftime('%Y', NEW.created_at) AS INTEGER), "
                       "ref_seq = (SELECT last_seq FROM ticket_counters WHERE year = "
                       "CAST(strftime('%Y', NEW.created_at) AS INTEGER)) WHERE id = NEW.id; END"),
]

# Triggers of earlier development builds, dropped on start. tickets_ref counted from the highest
# number left, so it gave a deleted ticket's number again.
REPLACED_TRIGGERS = ["tickets_ref"]


def migrate(conn):
    """Bring a database made by an older version up to date. Safe to run on every start."""
    for table, column, statements in MIGRATIONS:
        if _has_column(conn, table, column):
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not _has_column(conn, table, column):  # another process may have won
                for sql in statements:
                    conn.execute(sql)
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    if any(name not in names for name, _sql in AFTER_MIGRATIONS) or names & set(REPLACED_TRIGGERS):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for name in REPLACED_TRIGGERS:
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            for _name, sql in AFTER_MIGRATIONS:
                conn.execute(sql)  # IF NOT EXISTS: another process may have made them
        except BaseException:
            conn.rollback()
            raise
        conn.commit()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


@contextmanager
def transaction():
    """Run several statements atomically. BEGIN IMMEDIATE takes the write lock up front, so a
    check followed by an insert cannot interleave with another request doing the same."""
    db = get_db()
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    else:
        db.commit()


def execute_rowcount(sql, args=()):
    """Like execute(), returning how many rows changed (for "only if still open" updates)."""
    db = get_db()
    try:
        cur = db.execute(sql, args)
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise
    return cur.rowcount


def try_execute(sql, args=()):
    """execute() for housekeeping that may be skipped: None instead of an error when another
    request holds the write lock past the busy timeout."""
    try:
        return execute_rowcount(sql, args)
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc) and "busy" not in str(exc):
            raise
        current_app.logger.warning("Skipped while the database was busy: %s", exc)
        return None


def execute(sql, args=()):
    db = get_db()
    try:
        cur = db.execute(sql, args)
        db.commit()
    except sqlite3.Error:
        # Do not leave a half-open transaction (and its write lock) on the connection.
        db.rollback()
        raise
    return cur.lastrowid
