"""ESPN NFL player game logs and injury context for prop projections.

Only information dated before the slate game is used (no future leakage).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from functools import lru_cache
from typing import Any

import httpx

from app.ingest.nfl import normalize_abbr
from app.odds.nfl_team_aliases import normalize_nfl_team

logger = logging.getLogger(__name__)

ESPN_TEAMS = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"
ESPN_GAMELOG = (
    "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{athlete_id}/gamelog"
)
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"

_NAME_STRIP = re.compile(r"[^a-z0-9]+")


def _http() -> httpx.Client:
    return httpx.Client(timeout=12.0)


def _norm_name(name: str) -> str:
    return _NAME_STRIP.sub("", (name or "").lower())


@lru_cache(maxsize=4)
def _teams_payload() -> list[dict[str, Any]]:
    try:
        with _http() as client:
            resp = client.get(ESPN_TEAMS, params={"limit": 50})
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN NFL teams list failed: %s", exc)
        return []
    sports = data.get("sports") or []
    leagues = (sports[0].get("leagues") if sports else None) or []
    teams = (leagues[0].get("teams") if leagues else None) or []
    out = []
    for wrap in teams:
        team = wrap.get("team") or wrap
        abbr = normalize_nfl_team(team.get("abbreviation") or team.get("displayName") or "")
        tid = team.get("id")
        if abbr and tid:
            out.append({"id": str(tid), "abbr": abbr, "name": team.get("displayName") or abbr})
    return out


def espn_team_id(abbr: str) -> str | None:
    key = normalize_abbr(abbr)
    for team in _teams_payload():
        if team["abbr"] == key:
            return team["id"]
    return None


@lru_cache(maxsize=40)
def _roster_for_team(team_id: str) -> list[dict[str, Any]]:
    try:
        with _http() as client:
            resp = client.get(ESPN_ROSTER.format(team_id=team_id))
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN NFL roster failed for %s: %s", team_id, exc)
        return []
    athletes: list[dict[str, Any]] = []
    for group in data.get("athletes") or []:
        for item in group.get("items") or []:
            name = item.get("displayName") or item.get("fullName") or ""
            pos = ((item.get("position") or {}).get("abbreviation")) or ""
            aid = item.get("id")
            if name and aid:
                athletes.append(
                    {
                        "athlete_id": str(aid),
                        "name": name,
                        "name_key": _norm_name(name),
                        "position": pos,
                        "team_id": team_id,
                    }
                )
    return athletes


def resolve_nfl_player(
    player_name: str,
    team_abbr: str | None,
) -> dict[str, Any] | None:
    name_key = _norm_name(player_name)
    if not name_key:
        return None
    candidates: list[dict[str, Any]] = []
    team_id = espn_team_id(team_abbr) if team_abbr else None
    if team_id:
        candidates = list(_roster_for_team(team_id))
    if not candidates:
        for team in _teams_payload():
            candidates.extend(_roster_for_team(team["id"]))
    exact = [p for p in candidates if p["name_key"] == name_key]
    if exact:
        return exact[0]
    last = name_key.split()[-1] if " " not in player_name else _norm_name(player_name.split()[-1])
    last_hits = [p for p in candidates if p["name_key"].endswith(last) and last]
    if len(last_hits) == 1:
        return last_hits[0]
    return None


def _stat_number(row: dict[str, Any], *keys: str) -> float:
    for key in keys:
        raw = row.get(key)
        if raw in (None, "", "--"):
            continue
        try:
            return float(str(raw).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return 0.0


def _parse_game_date(raw: str | None) -> date | None:
    if not raw:
        return None
    text = str(raw)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(str(raw)[:18], fmt).date()
        except ValueError:
            continue
    return None


@lru_cache(maxsize=256)
def _raw_gamelog(athlete_id: str) -> dict[str, Any]:
    try:
        with _http() as client:
            resp = client.get(ESPN_GAMELOG.format(athlete_id=athlete_id))
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN NFL gamelog failed for %s: %s", athlete_id, exc)
        return {}


def nfl_game_log_values(
    athlete_id: str,
    market_stat: str,
    *,
    before: date | None = None,
) -> list[float]:
    """Chronological (oldest-first) per-game values dated strictly before *before*."""
    payload = _raw_gamelog(athlete_id)
    events: list[dict[str, Any]] = []

    season_types = payload.get("seasonTypes") or []
    for block in season_types:
        for cat in block.get("categories") or []:
            events.extend(cat.get("events") or [])
    if not events:
        for key in ("events", "games"):
            if isinstance(payload.get(key), list):
                events.extend(payload[key])

    rows: list[tuple[date, float]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        game_date = _parse_game_date(
            event.get("gameDate") or event.get("date") or (event.get("event") or {}).get("date")
        )
        if game_date is None:
            continue
        if before is not None and game_date >= before:
            continue
        stats = event.get("stats") or event.get("statistics") or event
        if isinstance(stats, list):
            # Some payloads use ordered stat names + values.
            continue
        passing_yds = _stat_number(stats, "passingYards", "passYds", "passingYds")
        rushing_yds = _stat_number(stats, "rushingYards", "rushYds")
        rec_yds = _stat_number(stats, "receivingYards", "recYds")
        rec_td = _stat_number(stats, "receivingTouchdowns", "receivingTDs", "recTd")
        rush_td = _stat_number(stats, "rushingTouchdowns", "rushingTDs", "rushTd")
        pass_td = _stat_number(stats, "passingTouchdowns", "passingTDs", "passTd")
        mapping = {
            "passingYards": passing_yds,
            "passingTouchdowns": pass_td,
            "passingAttempts": _stat_number(stats, "passingAttempts", "passAtt"),
            "passingCompletions": _stat_number(stats, "passingCompletions", "completions", "passComp"),
            "interceptions": _stat_number(stats, "interceptions", "ints"),
            "passingLong": _stat_number(stats, "passingLong", "longestPass"),
            "rushingYards": rushing_yds,
            "rushingAttempts": _stat_number(stats, "rushingAttempts", "carries", "rushAtt"),
            "rushingLong": _stat_number(stats, "rushingLong", "longestRush"),
            "receptions": _stat_number(stats, "receptions", "rec"),
            "receivingYards": rec_yds,
            "receivingLong": _stat_number(stats, "receivingLong", "longestReception"),
            "rushRecYards": rushing_yds + rec_yds,
            "anytimeTd": 1.0 if (rec_td + rush_td + pass_td) >= 1 else 0.0,
        }
        rows.append((game_date, float(mapping.get(market_stat, 0.0))))

    rows.sort(key=lambda item: item[0])
    return [value for _, value in rows]


@lru_cache(maxsize=2)
def _injury_payload() -> list[dict[str, Any]]:
    try:
        with _http() as client:
            resp = client.get(ESPN_INJURIES)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN NFL injuries failed: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for team_block in data.get("injuries") or []:
        team = ((team_block.get("team") or {}).get("abbreviation")) or ""
        for item in team_block.get("injuries") or []:
            athlete = item.get("athlete") or {}
            out.append(
                {
                    "name": athlete.get("displayName") or "",
                    "name_key": _norm_name(athlete.get("displayName") or ""),
                    "team": normalize_nfl_team(team),
                    "status": str(item.get("status") or ""),
                    "detail": str((item.get("details") or {}).get("detail") or item.get("shortComment") or ""),
                }
            )
    return out


def player_injury_note(player_name: str, team_abbr: str | None) -> str | None:
    name_key = _norm_name(player_name)
    team = normalize_abbr(team_abbr or "")
    for row in _injury_payload():
        if row["name_key"] != name_key:
            continue
        if team and row.get("team") and row["team"] != team:
            continue
        status = (row.get("status") or "").strip()
        if not status:
            continue
        detail = (row.get("detail") or "").strip()
        return f"{status}" + (f" — {detail}" if detail else "")
    return None


def game_environment(
    *,
    team_abbr: str,
    opponent_abbr: str,
    home: bool,
    spread_home: float | None,
    total: float | None,
) -> dict[str, Any]:
    """Team-centric spread and implied total from a home-coded market."""
    team_spread = None
    if spread_home is not None:
        team_spread = float(spread_home) if home else -float(spread_home)
    implied = None
    if total is not None and team_spread is not None:
        implied = float(total) / 2.0 - team_spread / 2.0
    elif total is not None:
        implied = float(total) / 2.0
    return {
        "team": normalize_abbr(team_abbr),
        "opponent": normalize_abbr(opponent_abbr),
        "home": home,
        "team_spread": team_spread,
        "game_total": total,
        "team_implied_total": round(implied, 2) if implied is not None else None,
    }
