"""User team follows and alert preferences."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def ensure_user_team_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_team_follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            sport TEXT NOT NULL,
            team_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, sport, team_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_alert_prefs (
            user_id INTEGER PRIMARY KEY,
            daily_digest INTEGER NOT NULL DEFAULT 0,
            digest_hour_et INTEGER NOT NULL DEFAULT 8,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()


def follow_team(conn: sqlite3.Connection, user_id: int, sport: str, team_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO user_team_follows (user_id, sport, team_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, sport.lower(), str(team_id), now),
    )
    conn.commit()
    return {"ok": True, "sport": sport.lower(), "team_id": str(team_id)}


def unfollow_team(conn: sqlite3.Connection, user_id: int, sport: str, team_id: str) -> dict[str, Any]:
    conn.execute(
        """
        DELETE FROM user_team_follows
        WHERE user_id = ? AND sport = ? AND team_id = ?
        """,
        (user_id, sport.lower(), str(team_id)),
    )
    conn.commit()
    return {"ok": True}


def list_follows(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT sport, team_id, created_at
        FROM user_team_follows
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    )
    return [
        {"sport": row[0], "team_id": row[1], "created_at": row[2]}
        for row in cur.fetchall()
    ]


def get_alert_prefs(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT daily_digest, digest_hour_et, updated_at FROM user_alert_prefs WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return {"daily_digest": False, "digest_hour_et": 8, "updated_at": None}
    return {
        "daily_digest": bool(row[0]),
        "digest_hour_et": int(row[1]),
        "updated_at": row[2],
    }


def set_alert_prefs(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    daily_digest: bool,
    digest_hour_et: int = 8,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO user_alert_prefs (user_id, daily_digest, digest_hour_et, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            daily_digest = excluded.daily_digest,
            digest_hour_et = excluded.digest_hour_et,
            updated_at = excluded.updated_at
        """,
        (user_id, int(daily_digest), digest_hour_et, now),
    )
    conn.commit()
    return get_alert_prefs(conn, user_id)
