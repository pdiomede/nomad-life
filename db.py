"""SQLite storage for Nomad Life."""
import os
import sqlite3
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
    totp_step INTEGER NOT NULL DEFAULT 0
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
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

-- One row per signed in browser. Sign out deletes its row, so a copied session or remember
-- cookie stops working at once. Rows unused for a while are pruned.
CREATE TABLE IF NOT EXISTS user_sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id);

CREATE INDEX IF NOT EXISTS idx_movements_year ON movements(year_id);
CREATE INDEX IF NOT EXISTS idx_documents_year ON documents(year_id);
CREATE INDEX IF NOT EXISTS idx_documents_movement ON documents(movement_id);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
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
        # gives its journal files the same permissions.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        conn.executescript(SCHEMA)
        conn.commit()
        migrate(conn)
    finally:
        conn.close()


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
]


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
