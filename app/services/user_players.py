"""User player follows and personalized news feed."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from app.services.news_feed import fetch_news, news_matching_players
from app.services.prop_scoring import _http_client_get

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
PLAYER_FOLLOWS_KEY = "ntg_player_follows"


def ensure_user_player_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_player_follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            sport TEXT NOT NULL,
            player_id TEXT NOT NULL,
            player_name TEXT NOT NULL,
            team_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, sport, player_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()


def follow_player(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    sport: str,
    player_id: str,
    player_name: str,
    team_id: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO user_player_follows
            (user_id, sport, player_id, player_name, team_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, sport, player_id) DO UPDATE SET
            player_name = excluded.player_name,
            team_id = COALESCE(excluded.team_id, user_player_follows.team_id)
        """,
        (user_id, sport.lower(), str(player_id), player_name.strip(), team_id, now),
    )
    conn.commit()
    return {
        "ok": True,
        "sport": sport.lower(),
        "player_id": str(player_id),
        "player_name": player_name.strip(),
    }


def unfollow_player(
    conn: sqlite3.Connection,
    user_id: int,
    sport: str,
    player_id: str,
) -> dict[str, Any]:
    conn.execute(
        """
        DELETE FROM user_player_follows
        WHERE user_id = ? AND sport = ? AND player_id = ?
        """,
        (user_id, sport.lower(), str(player_id)),
    )
    conn.commit()
    return {"ok": True}


def list_player_follows(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT sport, player_id, player_name, team_id, created_at
        FROM user_player_follows
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    )
    return [
        {
            "sport": row[0],
            "player_id": row[1],
            "player_name": row[2],
            "team_id": row[3],
            "created_at": row[4],
        }
        for row in cur.fetchall()
    ]


def _mlb_next_game_for_team(team_id: int | str) -> dict[str, Any] | None:
    today = date.today().isoformat()
    url = f"{MLB_STATS_BASE}/schedule"
    params = {"sportId": 1, "teamId": team_id, "startDate": today, "endDate": today, "hydrate": "team"}
    try:
        resp = _http_client_get().get(url, params=params)
        resp.raise_for_status()
        dates = resp.json().get("dates") or []
        for day in dates:
            for g in day.get("games") or []:
                status = (g.get("status") or {}).get("detailedState") or ""
                if status in ("Final", "Game Over"):
                    continue
                teams = g.get("teams") or {}
                home = teams.get("home") or {}
                away = teams.get("away") or {}
                return {
                    "game_id": str(g.get("gamePk") or ""),
                    "start_time": g.get("gameDate"),
                    "matchup": f"{(away.get('team') or {}).get('abbreviation', '?')} @ {(home.get('team') or {}).get('abbreviation', '?')}",
                    "status": status or "Scheduled",
                }
    except Exception:
        return None
    return None


def _mlb_player_team_id(player_id: str) -> str | None:
    try:
        resp = _http_client_get().get(f"{MLB_STATS_BASE}/people/{player_id}")
        resp.raise_for_status()
        people = resp.json().get("people") or []
        if not people:
            return None
        team = people[0].get("currentTeam") or {}
        tid = team.get("id")
        return str(tid) if tid is not None else None
    except Exception:
        return None


def build_player_feed(follows: list[dict[str, Any]], *, news_limit: int = 20) -> dict[str, Any]:
    """News + next game per followed player."""
    if not follows:
        return {"players": [], "news": []}

    news_items = fetch_news()
    players_out: list[dict[str, Any]] = []
    all_news: list[dict[str, Any]] = []

    for f in follows:
        sport = f.get("sport") or "mlb"
        pid = f.get("player_id") or ""
        name = f.get("player_name") or ""
        team_id = f.get("team_id")
        next_game = None
        if sport == "mlb":
            if not team_id:
                team_id = _mlb_player_team_id(pid)
            if team_id:
                next_game = _mlb_next_game_for_team(team_id)
        matched = news_matching_players(news_items, [name], limit=5)
        for item in matched:
            tagged = {**item, "matched_player": name, "player_id": pid}
            if tagged not in all_news:
                all_news.append(tagged)
        players_out.append(
            {
                "sport": sport,
                "player_id": pid,
                "player_name": name,
                "team_id": team_id,
                "next_game": next_game,
                "news": matched,
            }
        )

    all_news.sort(key=lambda x: str(x.get("published") or ""), reverse=True)
    return {"players": players_out, "news": all_news[:news_limit]}
