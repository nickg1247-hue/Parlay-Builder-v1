"""Saved player props / games. Original lines are never overwritten.

Alert preference columns are stored for a future notifier — no delivery here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

WATCHLIST_PROPS_TABLE = """
CREATE TABLE IF NOT EXISTS user_watchlist_props (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    sport TEXT NOT NULL,
    event_id TEXT,
    game_id TEXT,
    player_id TEXT,
    player_name TEXT NOT NULL,
    market_type TEXT NOT NULL,
    market_label TEXT,
    side TEXT NOT NULL,
    line REAL NOT NULL,
    sportsbook TEXT,
    odds INTEGER,
    prediction_id TEXT,
    matchup TEXT,
    saved_at TEXT NOT NULL,
    notify_line_change INTEGER NOT NULL DEFAULT 0,
    notify_score_threshold REAL,
    notify_new_book INTEGER NOT NULL DEFAULT 0,
    notify_side_change INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, sport, game_id, player_name, market_type, side, line, sportsbook),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
"""

WATCHLIST_GAMES_TABLE = """
CREATE TABLE IF NOT EXISTS user_watchlist_games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    sport TEXT NOT NULL,
    game_id TEXT NOT NULL,
    matchup TEXT,
    saved_at TEXT NOT NULL,
    UNIQUE(user_id, sport, game_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
"""


def ensure_watchlist_tables(conn: sqlite3.Connection) -> None:
    conn.execute(WATCHLIST_PROPS_TABLE)
    conn.execute(WATCHLIST_GAMES_TABLE)
    conn.commit()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_prop(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sport": row["sport"],
        "event_id": row["event_id"],
        "game_id": row["game_id"],
        "player_id": row["player_id"],
        "player": row["player_name"],
        "market_type": row["market_type"],
        "market_label": row["market_label"],
        "side": row["side"],
        "saved_line": row["line"],
        "line": row["line"],
        "sportsbook": row["sportsbook"],
        "saved_odds": row["odds"],
        "odds": row["odds"],
        "prediction_id": row["prediction_id"],
        "matchup": row["matchup"],
        "saved_at": row["saved_at"],
        "current_line": None,
        "current_odds": None,
        "movement": None,
        "status": "saved",
        "result": None,
        "alerts": {
            "line_change": bool(row["notify_line_change"]),
            "score_threshold": row["notify_score_threshold"],
            "new_book": bool(row["notify_new_book"]),
            "side_change": bool(row["notify_side_change"]),
        },
    }


def save_watchlist_prop(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    sport: str,
    player_name: str,
    market_type: str,
    side: str,
    line: float,
    sportsbook: str | None = None,
    odds: int | None = None,
    game_id: str | None = None,
    event_id: str | None = None,
    player_id: str | None = None,
    market_label: str | None = None,
    prediction_id: str | None = None,
    matchup: str | None = None,
) -> dict[str, Any]:
    ensure_watchlist_tables(conn)
    now = _utc_now()
    side_key = "under" if str(side).lower() == "under" else "over"
    sport_key = str(sport or "mlb").lower()
    conn.execute(
        """
        INSERT INTO user_watchlist_props (
            user_id, sport, event_id, game_id, player_id, player_name,
            market_type, market_label, side, line, sportsbook, odds,
            prediction_id, matchup, saved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, sport, game_id, player_name, market_type, side, line, sportsbook)
        DO UPDATE SET
            odds = COALESCE(user_watchlist_props.odds, excluded.odds),
            market_label = COALESCE(excluded.market_label, user_watchlist_props.market_label),
            matchup = COALESCE(excluded.matchup, user_watchlist_props.matchup)
        """,
        (
            user_id,
            sport_key,
            event_id,
            game_id,
            player_id,
            player_name.strip(),
            market_type,
            market_label,
            side_key,
            float(line),
            sportsbook,
            odds,
            prediction_id,
            matchup,
            now,
        ),
    )
    conn.commit()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT * FROM user_watchlist_props
        WHERE user_id = ? AND sport = ? AND ifnull(game_id, '') = ifnull(?, '')
          AND player_name = ? AND market_type = ? AND side = ? AND line = ?
          AND ifnull(sportsbook, '') = ifnull(?, '')
        ORDER BY id DESC LIMIT 1
        """,
        (
            user_id,
            sport_key,
            game_id,
            player_name.strip(),
            market_type,
            side_key,
            float(line),
            sportsbook,
        ),
    ).fetchone()
    return {"ok": True, "item": _row_to_prop(row) if row else None}


def list_watchlist_props(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    ensure_watchlist_tables(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM user_watchlist_props
        WHERE user_id = ?
        ORDER BY saved_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [_row_to_prop(row) for row in rows]


def delete_watchlist_prop(conn: sqlite3.Connection, user_id: int, item_id: int) -> dict[str, Any]:
    ensure_watchlist_tables(conn)
    cur = conn.execute(
        "DELETE FROM user_watchlist_props WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    )
    conn.commit()
    return {"ok": True, "deleted": cur.rowcount > 0}


def save_watchlist_game(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    sport: str,
    game_id: str,
    matchup: str | None = None,
) -> dict[str, Any]:
    ensure_watchlist_tables(conn)
    conn.execute(
        """
        INSERT INTO user_watchlist_games (user_id, sport, game_id, matchup, saved_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, sport, game_id) DO UPDATE SET
            matchup = COALESCE(excluded.matchup, user_watchlist_games.matchup)
        """,
        (user_id, str(sport).lower(), str(game_id), matchup, _utc_now()),
    )
    conn.commit()
    return {"ok": True, "sport": str(sport).lower(), "game_id": str(game_id)}


def list_watchlist_games(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    ensure_watchlist_tables(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, sport, game_id, matchup, saved_at
        FROM user_watchlist_games
        WHERE user_id = ?
        ORDER BY saved_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_watchlist_game(conn: sqlite3.Connection, user_id: int, item_id: int) -> dict[str, Any]:
    ensure_watchlist_tables(conn)
    cur = conn.execute(
        "DELETE FROM user_watchlist_games WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    )
    conn.commit()
    return {"ok": True, "deleted": cur.rowcount > 0}
