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

from app.ingest.nfl import NFL_DIVISIONS, normalize_abbr
from app.odds.nfl_team_aliases import normalize_nfl_team

logger = logging.getLogger(__name__)

ESPN_TEAMS = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"
ESPN_GAMELOG = (
    "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{athlete_id}/gamelog"
)
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
ESPN_TEAM_STATS = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/statistics"
)

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


def nfl_season_year(game_date: date) -> int:
    """NFL season label: Aug–Dec use calendar year; Jan–Jul belong to the prior season."""
    return game_date.year if game_date.month >= 8 else game_date.year - 1


def similar_opponent_abbrs(opponent: str | None) -> set[str]:
    """Same-division teams (plus the opponent). Used when same-team history is thin."""
    opp = normalize_nfl_team(opponent or "")
    if not opp:
        return set()
    division = NFL_DIVISIONS.get(normalize_abbr(opp), "")
    peers = {abbr for abbr, div in NFL_DIVISIONS.items() if div == division} if division else set()
    peers.add(opp)
    return peers


@lru_cache(maxsize=512)
def _raw_gamelog(athlete_id: str, season: int | None = None) -> dict[str, Any]:
    params = {"season": season} if season else None
    try:
        with _http() as client:
            resp = client.get(ESPN_GAMELOG.format(athlete_id=athlete_id), params=params)
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN NFL gamelog failed for %s season=%s: %s", athlete_id, season, exc)
        return {}


def _stats_dict(event: dict[str, Any], names: list[str]) -> dict[str, Any]:
    stats = event.get("stats") or event.get("statistics")
    if isinstance(stats, list):
        out: dict[str, Any] = {}
        for idx, name in enumerate(names):
            if idx < len(stats):
                out[str(name)] = stats[idx]
        return out
    if isinstance(stats, dict):
        return stats
    return {}


def _market_stat_map(stats: dict[str, Any]) -> dict[str, float]:
    passing_yds = _stat_number(stats, "passingYards", "passYds", "passingYds")
    rushing_yds = _stat_number(stats, "rushingYards", "rushYds")
    rec_yds = _stat_number(stats, "receivingYards", "recYds")
    rec_td = _stat_number(stats, "receivingTouchdowns", "receivingTDs", "recTd")
    rush_td = _stat_number(stats, "rushingTouchdowns", "rushingTDs", "rushTd")
    pass_td = _stat_number(stats, "passingTouchdowns", "passingTDs", "passTd")
    return {
        "passingYards": passing_yds,
        "passingTouchdowns": pass_td,
        "passingAttempts": _stat_number(stats, "passingAttempts", "passAtt"),
        "passingCompletions": _stat_number(stats, "passingCompletions", "completions", "passComp"),
        "interceptions": _stat_number(stats, "interceptions", "ints"),
        "passingLong": _stat_number(stats, "passingLong", "longestPass", "longPassing"),
        "rushingYards": rushing_yds,
        "rushingAttempts": _stat_number(stats, "rushingAttempts", "carries", "rushAtt"),
        "rushingLong": _stat_number(stats, "rushingLong", "longestRush", "longRushing"),
        "receptions": _stat_number(stats, "receptions", "rec"),
        "receivingYards": rec_yds,
        "receivingLong": _stat_number(stats, "receivingLong", "longestReception", "longReception"),
        "rushRecYards": rushing_yds + rec_yds,
        "anytimeTd": 1.0 if (rec_td + rush_td + pass_td) >= 1 else 0.0,
    }


def _opponent_abbr(event: dict[str, Any]) -> str:
    opp = event.get("opponent") or event.get("opponentTeam") or {}
    if isinstance(opp, str):
        return normalize_nfl_team(opp)
    if isinstance(opp, dict):
        return normalize_nfl_team(
            opp.get("abbreviation") or opp.get("displayName") or opp.get("name") or ""
        )
    team = event.get("team") or {}
    if isinstance(team, dict):
        return normalize_nfl_team(team.get("abbreviation") or "")
    return ""


def nfl_game_log_entries(
    athlete_id: str,
    market_stat: str,
    *,
    before: date | None = None,
) -> list[dict[str, Any]]:
    """Chronological regular/postseason rows dated strictly before *before*.

    ESPN stores per-game stats as arrays aligned with ``names``. Week-1 slates
    have no current-season games, so we also pull the prior one or two seasons.
    """
    slate = before or date.today()
    season_now = nfl_season_year(slate)
    seasons = [season_now, season_now - 1, season_now - 2]
    merged: dict[str, dict[str, Any]] = {}

    for season in seasons:
        payload = _raw_gamelog(athlete_id, season)
        names = [str(n) for n in (payload.get("names") or [])]
        meta_by_id = payload.get("events") or {}
        if not isinstance(meta_by_id, dict):
            meta_by_id = {}
        blocks = payload.get("seasonTypes") or []
        for block in blocks:
            block_name = str(block.get("displayName") or block.get("name") or "").lower()
            if "preseason" in block_name:
                continue
            for cat in block.get("categories") or []:
                for event in cat.get("events") or []:
                    if not isinstance(event, dict):
                        continue
                    eid = str(event.get("eventId") or event.get("id") or "")
                    meta = dict(meta_by_id.get(eid) or {})
                    combined = {**meta, **event}
                    game_date = _parse_game_date(
                        combined.get("gameDate")
                        or combined.get("date")
                        or (combined.get("event") or {}).get("date")
                    )
                    if game_date is None:
                        continue
                    if before is not None and game_date >= before:
                        continue
                    stats = _stats_dict(combined, names)
                    if not stats:
                        continue
                    mapping = _market_stat_map(stats)
                    merged[eid or f"{game_date.isoformat()}:{combined.get('opponent')}"] = {
                        "date": game_date.isoformat(),
                        "season_year": nfl_season_year(game_date),
                        "opponent": _opponent_abbr(combined),
                        "stat_value": float(mapping.get(market_stat, 0.0)),
                        "stats": {
                            "passingYards": mapping["passingYards"],
                            "rushingYards": mapping["rushingYards"],
                            "receivingYards": mapping["receivingYards"],
                            "receptions": mapping["receptions"],
                        },
                    }
        if len(merged) >= 12 and season < season_now:
            break

    rows = list(merged.values())
    rows.sort(key=lambda item: item["date"])
    return rows


def nfl_game_log_values(
    athlete_id: str,
    market_stat: str,
    *,
    before: date | None = None,
) -> list[float]:
    """Chronological (oldest-first) per-game values dated strictly before *before*."""
    return [float(row["stat_value"]) for row in nfl_game_log_entries(athlete_id, market_stat, before=before)]


def _walk_named_stats(node: Any, found: dict[str, float]) -> None:
    if isinstance(node, dict):
        name = str(node.get("name") or node.get("abbreviation") or node.get("displayName") or "").lower()
        name = name.replace(" ", "").replace("_", "")
        value = node.get("value")
        if value is None:
            value = node.get("displayValue")
        try:
            num = float(str(value).replace(",", "")) if value not in (None, "") else None
        except (TypeError, ValueError):
            num = None
        if num is not None and name:
            found[name] = num
        for child in node.values():
            _walk_named_stats(child, found)
    elif isinstance(node, list):
        for child in node:
            _walk_named_stats(child, found)


@lru_cache(maxsize=64)
def opponent_defense_profile(team_abbr: str) -> dict[str, Any] | None:
    """Season opponent-allowed rates from ESPN team statistics (public, no key)."""
    team_id = espn_team_id(team_abbr)
    if not team_id:
        return None
    try:
        with _http() as client:
            resp = client.get(ESPN_TEAM_STATS.format(team_id=team_id))
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN NFL team stats failed for %s: %s", team_abbr, exc)
        return None
    named: dict[str, float] = {}
    _walk_named_stats(payload, named)

    def _pick(*keys: str) -> float | None:
        for key in keys:
            compact = key.replace(" ", "").replace("_", "").lower()
            if compact in named:
                return named[compact]
        return None

    pass_yds = _pick(
        "passingYardsAllowed",
        "opponentPassingYards",
        "passingYardsPerGameAllowed",
        "netPassingYardsAllowed",
    )
    rush_yds = _pick(
        "rushingYardsAllowed",
        "opponentRushingYards",
        "rushingYardsPerGameAllowed",
    )
    points = _pick("pointsAllowed", "pointsAgainst", "opponentPoints", "pointsPerGameAllowed")
    if pass_yds is None and rush_yds is None and points is None:
        return None
    return {
        "team": normalize_abbr(team_abbr),
        "pass_yds_allowed": round(pass_yds, 1) if pass_yds is not None else None,
        "rush_yds_allowed": round(rush_yds, 1) if rush_yds is not None else None,
        "points_allowed": round(points, 1) if points is not None else None,
        "source": "espn_team_statistics",
    }


def nfl_stat_on_date(
    player_name: str,
    market_type: str,
    game_date: date,
    team_abbr: str | None = None,
) -> float | None:
    """Actual ESPN box-stat for one player on a calendar date (None if DNP)."""
    from app.services.prop_engine.nfl_markets import MARKET_STAT

    stat_key = MARKET_STAT.get(str(market_type or ""))
    if not stat_key:
        return None
    resolved = resolve_nfl_player(player_name, team_abbr)
    if not resolved:
        return None
    payload = _raw_gamelog(str(resolved["athlete_id"]), nfl_season_year(game_date))
    names = [str(n) for n in (payload.get("names") or [])]
    meta_by_id = payload.get("events") or {}
    events: list[dict[str, Any]] = []
    for block in payload.get("seasonTypes") or []:
        for cat in block.get("categories") or []:
            events.extend(cat.get("events") or [])
    for event in events:
        if not isinstance(event, dict):
            continue
        eid = str(event.get("eventId") or event.get("id") or "")
        combined = {**(meta_by_id.get(eid) or {} if isinstance(meta_by_id, dict) else {}), **event}
        when = _parse_game_date(
            combined.get("gameDate") or combined.get("date") or (combined.get("event") or {}).get("date")
        )
        if when != game_date:
            continue
        stats = _stats_dict(combined, names)
        if not stats:
            return None
        mapping = _market_stat_map(stats)
        return float(mapping.get(stat_key, 0.0))
    return None


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
