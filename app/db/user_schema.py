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

_CREATE_STRIPE_WEBHOOK_EVENTS = """
CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    event_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
)
"""

_CREATE_RATE_LIMIT_BUCKETS = """
CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    bucket_key TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL
)
"""


def _migrate_user_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "terms_accepted_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN terms_accepted_at TEXT")
    if "stripe_customer_id" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT")
    if "subscription_status" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'none'"
        )
    if "subscription_current_period_end" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN subscription_current_period_end TEXT")


def ensure_users_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_USERS)
    conn.execute(_CREATE_USERS_EMAIL_INDEX)
    _migrate_user_columns(conn)
    conn.execute(_CREATE_STRIPE_WEBHOOK_EVENTS)
    conn.execute(_CREATE_RATE_LIMIT_BUCKETS)
    conn.commit()
