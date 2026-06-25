"""SQLite schema for end-user accounts (email + password)."""

from __future__ import annotations

import sqlite3

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    email_verified_at TEXT,
    verification_token TEXT,
    verification_token_expires_at TEXT,
    created_at TEXT NOT NULL,
    terms_accepted_at TEXT
)
"""

_CREATE_USERS_EMAIL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)
"""


def _migrate_user_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "terms_accepted_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN terms_accepted_at TEXT")


def ensure_users_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_USERS)
    conn.execute(_CREATE_USERS_EMAIL_INDEX)
    _migrate_user_columns(conn)
    conn.commit()
