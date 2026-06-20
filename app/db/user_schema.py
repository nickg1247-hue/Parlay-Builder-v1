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
    created_at TEXT NOT NULL
)
"""

_CREATE_USERS_EMAIL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)
"""


def ensure_users_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_USERS)
    conn.execute(_CREATE_USERS_EMAIL_INDEX)
    conn.commit()
