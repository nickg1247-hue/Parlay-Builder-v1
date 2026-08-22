"""Saved player props / games. Original snapshot values are never overwritten.

Alert preference columns are stored for a future notifier — no delivery here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.services.slate_clock import slate_today

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
    projection REAL,
    model_probability REAL,
    market_probability REAL,
    model_edge REAL,
    model_score REAL,
    confidence TEXT,
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
    model_lean TEXT,
    model_probability REAL,
    market_probability REAL,
    model_edge REAL,
    confidence TEXT,
    UNIQUE(user_id, sport, game_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)
"""

_PROP_EXTRA_COLS = {
    "projection": "REAL",
    "model_probability": "REAL",
    "market_probability": "REAL",
    "model_edge": "REAL",
    "model_score": "REAL",
    "confidence": "TEXT",
}

_GAME_EXTRA_COLS = {
    "model_lean": "TEXT",
    "model_probability": "REAL",
    "market_probability": "REAL",
    "model_edge": "REAL",
    "confidence": "TEXT",
}


def _ensure_columns(conn: sqlite3.Connection, table: str, extras: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, typ in extras.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def ensure_watchlist_tables(conn: sqlite3.Connection) -> None:
    conn.execute(WATCHLIST_PROPS_TABLE)
    conn.execute(WATCHLIST_GAMES_TABLE)
    _ensure_columns(conn, "user_watchlist_props", _PROP_EXTRA_COLS)
    _ensure_columns(conn, "user_watchlist_games", _GAME_EXTRA_COLS)
    conn.commit()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_get(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _row_to_prop(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": "prop",
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
        "saved_projection": _row_get(row, "projection"),
        "saved_model_probability": _row_get(row, "model_probability"),
        "saved_market_probability": _row_get(row, "market_probability"),
        "saved_model_edge": _row_get(row, "model_edge"),
        "saved_model_score": _row_get(row, "model_score"),
        "saved_confidence": _row_get(row, "confidence"),
        "current_line": None,
        "current_odds": None,
        "current_model_probability": None,
        "current_model_edge": None,
        "movement": None,
        "status": "upcoming",
        "result": None,
        "alerts": {
            "line_change": bool(row["notify_line_change"]),
            "score_threshold": row["notify_score_threshold"],
            "new_book": bool(row["notify_new_book"]),
            "side_change": bool(row["notify_side_change"]),
        },
    }


def _row_to_game(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": "game",
        "sport": row["sport"],
        "game_id": row["game_id"],
        "matchup": row["matchup"],
        "saved_at": row["saved_at"],
        "saved_model_lean": _row_get(row, "model_lean"),
        "saved_model_probability": _row_get(row, "model_probability"),
        "saved_market_probability": _row_get(row, "market_probability"),
        "saved_model_edge": _row_get(row, "model_edge"),
        "saved_confidence": _row_get(row, "confidence"),
        "status": "upcoming",
        "result": None,
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
    projection: float | None = None,
    model_probability: float | None = None,
    market_probability: float | None = None,
    model_edge: float | None = None,
    model_score: float | None = None,
    confidence: str | None = None,
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
            prediction_id, matchup, saved_at,
            projection, model_probability, market_probability, model_edge, model_score, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, sport, game_id, player_name, market_type, side, line, sportsbook)
        DO UPDATE SET
            odds = COALESCE(user_watchlist_props.odds, excluded.odds),
            market_label = COALESCE(excluded.market_label, user_watchlist_props.market_label),
            matchup = COALESCE(excluded.matchup, user_watchlist_props.matchup),
            projection = COALESCE(user_watchlist_props.projection, excluded.projection),
            model_probability = COALESCE(user_watchlist_props.model_probability, excluded.model_probability),
            market_probability = COALESCE(user_watchlist_props.market_probability, excluded.market_probability),
            model_edge = COALESCE(user_watchlist_props.model_edge, excluded.model_edge),
            model_score = COALESCE(user_watchlist_props.model_score, excluded.model_score),
            confidence = COALESCE(user_watchlist_props.confidence, excluded.confidence)
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
            projection,
            model_probability,
            market_probability,
            model_edge,
            model_score,
            confidence,
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
    model_lean: str | None = None,
    model_probability: float | None = None,
    market_probability: float | None = None,
    model_edge: float | None = None,
    confidence: str | None = None,
) -> dict[str, Any]:
    ensure_watchlist_tables(conn)
    conn.execute(
        """
        INSERT INTO user_watchlist_games (
            user_id, sport, game_id, matchup, saved_at,
            model_lean, model_probability, market_probability, model_edge, confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, sport, game_id) DO UPDATE SET
            matchup = COALESCE(excluded.matchup, user_watchlist_games.matchup),
            model_lean = COALESCE(user_watchlist_games.model_lean, excluded.model_lean),
            model_probability = COALESCE(user_watchlist_games.model_probability, excluded.model_probability),
            market_probability = COALESCE(user_watchlist_games.market_probability, excluded.market_probability),
            model_edge = COALESCE(user_watchlist_games.model_edge, excluded.model_edge),
            confidence = COALESCE(user_watchlist_games.confidence, excluded.confidence)
        """,
        (
            user_id,
            str(sport).lower(),
            str(game_id),
            matchup,
            _utc_now(),
            model_lean,
            model_probability,
            market_probability,
            model_edge,
            confidence,
        ),
    )
    conn.commit()
    return {"ok": True, "sport": str(sport).lower(), "game_id": str(game_id)}


def list_watchlist_games(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    ensure_watchlist_tables(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM user_watchlist_games
        WHERE user_id = ?
        ORDER BY saved_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [_row_to_game(row) for row in rows]


def delete_watchlist_game(conn: sqlite3.Connection, user_id: int, item_id: int) -> dict[str, Any]:
    ensure_watchlist_tables(conn)
    cur = conn.execute(
        "DELETE FROM user_watchlist_games WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    )
    conn.commit()
    return {"ok": True, "deleted": cur.rowcount > 0}


def _game_status_bucket(status: str | None) -> str:
    raw = str(status or "").lower()
    if any(tok in raw for tok in ("final", "game over", "official")):
        return "final"
    if any(tok in raw for tok in ("progress", "live", "inning", "quarter", "half")):
        return "live"
    if "postpone" in raw:
        return "postponed"
    if "cancel" in raw:
        return "canceled"
    return "upcoming"


def _lookup_schedule_game(sport: str, game_id: str) -> dict[str, Any] | None:
    try:
        if sport == "nfl":
            from app.services.schedule_nfl import get_nfl_game

            return get_nfl_game(game_id, slate_today())
        if sport == "mlb":
            from app.services.schedule_mlb import get_mlb_game

            return get_mlb_game(game_id, slate_today())
    except Exception:
        return None
    return None


def _current_prop_offer(item: dict[str, Any]) -> dict[str, Any] | None:
    sport = item.get("sport") or "mlb"
    player = str(item.get("player") or "").lower()
    market = str(item.get("market_type") or "")
    side = str(item.get("side") or "over")
    if not player or not market:
        return None
    try:
        from app.services.props_platform import search_props

        result = search_props(
            sport,
            slate_today(),
            player=item.get("player"),
            market_type=market,
            scan=False,
            refresh=False,
            limit=40,
        )
    except Exception:
        return None
    for prop in result.get("props") or []:
        if str(prop.get("player") or "").lower() != player:
            continue
        if str(prop.get("market_type") or "") != market:
            continue
        rec_side = str(prop.get("recommended_side") or prop.get("side") or "").lower()
        if rec_side and rec_side != side:
            continue
        return prop
    return None


def _tracker_result(item: dict[str, Any]) -> str | None:
    try:
        from app.services.prop_pick_tracker import list_recent_picks

        rows = list_recent_picks(days=14, limit=400)
    except Exception:
        return None
    player = str(item.get("player") or "").lower()
    market = str(item.get("market_type") or "")
    side = str(item.get("side") or "")
    line = item.get("saved_line")
    for row in rows:
        if str(row.get("player") or "").lower() != player:
            continue
        if str(row.get("market_type") or "") != market:
            continue
        if str(row.get("side") or "") != side:
            continue
        if line is not None and row.get("line") is not None and float(row["line"]) != float(line):
            continue
        status = str(row.get("result_status") or row.get("status") or "").lower()
        hit = row.get("hit")
        if status == "push" or hit is None and status == "push":
            return "PUSH"
        if hit is True:
            return "WIN"
        if hit is False:
            return "LOSS"
    return None


def enrich_watchlist(props: list[dict[str, Any]], games: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach current cached market/status. Never overwrites saved snapshot fields."""
    for item in props:
        live = _current_prop_offer(item)
        if live:
            current_line = live.get("line")
            item["current_line"] = current_line
            item["current_odds"] = live.get("recommended_odds")
            item["current_model_probability"] = live.get("model_probability") or live.get(
                "recommended_probability"
            )
            item["current_model_edge"] = live.get("edge") if live.get("edge") is not None else live.get("edge_pct")
            if current_line is not None and item.get("saved_line") is not None:
                item["movement"] = float(current_line) - float(item["saved_line"])
        else:
            item["current_unavailable"] = True
        if item.get("game_id"):
            detail = _lookup_schedule_game(item.get("sport") or "mlb", str(item["game_id"]))
            game = (detail or {}).get("game") or {}
            item["status"] = _game_status_bucket(game.get("status"))
        result = _tracker_result(item)
        if result:
            item["result"] = result
            item["status"] = "final"
    for item in games:
        if item.get("game_id"):
            detail = _lookup_schedule_game(item.get("sport") or "mlb", str(item["game_id"]))
            game = (detail or {}).get("game") or {}
            item["status"] = _game_status_bucket(game.get("status"))
            item["current_matchup"] = (
                f"{game.get('away_team')} @ {game.get('home_team')}"
                if game.get("home_team")
                else item.get("matchup")
            )
    counts = {"upcoming": 0, "live": 0, "final": 0}
    for item in props + games:
        bucket = item.get("status") or "upcoming"
        if bucket in counts:
            counts[bucket] += 1
        else:
            counts["upcoming"] += 1
    return {"props": props, "games": games, "counts": counts}
