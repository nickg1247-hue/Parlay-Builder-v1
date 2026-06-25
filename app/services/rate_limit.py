"""SQLite-backed rate limits for auth and public APIs."""

from __future__ import annotations

import time

from app.db.database import get_connection

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    bucket_key TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL
)
"""


def ensure_rate_limit_table(conn) -> None:
    conn.execute(_CREATE_TABLE)


def check_rate_limit(bucket_key: str, max_count: int, window_seconds: int) -> bool:
    """Return True if the request is allowed, False if rate limited."""
    now = int(time.time())
    window_start = now - (now % window_seconds)
    conn = get_connection()
    try:
        ensure_rate_limit_table(conn)
        row = conn.execute(
            "SELECT window_start, count FROM rate_limit_buckets WHERE bucket_key = ?",
            (bucket_key,),
        ).fetchone()
        if row is None or row[0] != window_start:
            conn.execute(
                """
                INSERT INTO rate_limit_buckets (bucket_key, window_start, count)
                VALUES (?, ?, 1)
                ON CONFLICT(bucket_key) DO UPDATE SET
                    window_start = excluded.window_start,
                    count = 1
                """,
                (bucket_key, window_start),
            )
            conn.commit()
            return True
        if row[1] >= max_count:
            return False
        conn.execute(
            "UPDATE rate_limit_buckets SET count = count + 1 WHERE bucket_key = ?",
            (bucket_key,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
