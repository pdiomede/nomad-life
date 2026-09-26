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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


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
