"""Starting pitchers and batting lineups for MLB game detail (Stats API)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
MLB_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
MLB_PEOPLE_URL = "https://statsapi.mlb.com/api/v1/people/{person_id}"
HEADSHOT_URL = (
    "https://img.mlbstatic.com/mlb-photos/image/upload/"
    "w_120,q_auto:best/v1/people/{person_id}/headshot/silo/current"
)
CACHE_TTL_SECONDS = 120

_lineup_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}


def _headshot(person_id: int | str) -> str:
    return HEADSHOT_URL.format(person_id=person_id)


def _season_hitting(stats_list: list[dict[str, Any]]) -> dict[str, Any]:
    for block in stats_list or []:
        if block.get("group", {}).get("displayName") != "hitting":
            continue
        for split in block.get("splits", []):
            if split.get("stat"):
                s = split["stat"]
                return {
                    "avg": s.get("avg"),
                    "homeRuns": s.get("homeRuns"),
                    "rbi": s.get("rbi"),
                    "runs": s.get("runs"),
                    "ops": s.get("ops"),
                }
    return {}


def _season_pitching(stats_list: list[dict[str, Any]]) -> dict[str, Any]:
    for block in stats_list or []:
        if block.get("group", {}).get("displayName") != "pitching":
            continue
        for split in block.get("splits", []):
            if split.get("stat"):
                s = split["stat"]
                return {
                    "era": s.get("era"),
                    "wins": s.get("wins"),
                    "losses": s.get("losses"),
                    "strikeOuts": s.get("strikeOuts"),
                    "whip": s.get("whip"),
                    "inningsPitched": s.get("inningsPitched"),
                }
    return {}


def _fetch_person_stats(person_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not person_ids:
        return {}
    ids_param = ",".join(str(i) for i in person_ids[:50])
    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.get(
                "https://statsapi.mlb.com/api/v1/people",
                params={
                    "personIds": ids_param,
                    "hydrate": "stats(group=[hitting,pitching],type=season)",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("Could not load player stats: %s", exc)
        return {}

    out: dict[int, dict[str, Any]] = {}
    for person in data.get("people", []):
        pid = int(person["id"])
        stats = person.get("stats", [])
        out[pid] = {
            "hitting": _season_hitting(stats),
            "pitching": _season_pitching(stats),
        }
    return out


def _player_from_blob(
    person_id: int,
    person: dict[str, Any],
    stats_map: dict[int, dict[str, Any]],
    *,
    is_pitcher: bool,
) -> dict[str, Any]:
    stats = stats_map.get(person_id, {})
    block = stats.get("pitching" if is_pitcher else "hitting", {})
    return {
        "id": person_id,
        "name": person.get("fullName") or person.get("name", ""),
        "photo_url": _headshot(person_id),
        "position": (person.get("primaryPosition") or {}).get("abbreviation"),
        "stats": block,
    }


def _probable_pitcher(team_blob: dict[str, Any]) -> dict[str, Any] | None:
    pitcher = team_blob.get("probablePitcher") or {}
    pid = pitcher.get("id")
    if not pid:
        return None
    return {
        "id": int(pid),
        "name": pitcher.get("fullName") or "",
        "photo_url": _headshot(pid),
        "position": "P",
        "stats": {},
    }


def _lineup_from_boxscore(
    boxscore: dict[str, Any],
    side: str,
    stats_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    team = (boxscore.get("teams") or {}).get(side) or {}
    players = team.get("players") or {}
    batters = team.get("batters") or []
    rows: list[tuple[int, dict[str, Any]]] = []

    for pid in batters:
        key = f"ID{pid}" if f"ID{pid}" in players else str(pid)
        blob = players.get(key) or {}
        person = blob.get("person") or {}
        order_raw = blob.get("battingOrder")
        if not order_raw:
            continue
        try:
            order_num = int(order_raw)
        except (TypeError, ValueError):
            continue
        if order_num < 100:
            slot = order_num
        else:
            slot = order_num // 100
        if slot < 1 or slot > 9:
            continue
        pos = (blob.get("position") or {}).get("abbreviation") or (
            (person.get("primaryPosition") or {}).get("abbreviation")
        )
        person_id = int(person.get("id") or pid)
        rows.append(
            (
                slot,
                {
                    "order": slot,
                    "id": person_id,
                    "name": person.get("fullName") or "",
                    "photo_url": _headshot(person_id),
                    "position": pos,
                    "stats": stats_map.get(person_id, {}).get("hitting", {}),
                },
            )
        )

    rows.sort(key=lambda x: x[0])
    seen: set[int] = set()
    lineup: list[dict[str, Any]] = []
    for _, row in rows:
        if row["order"] in seen:
            continue
        seen.add(row["order"])
        lineup.append(row)
    return lineup[:9]


def _starting_pitcher_from_boxscore(
    boxscore: dict[str, Any],
    side: str,
    stats_map: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    team = (boxscore.get("teams") or {}).get(side) or {}
    prob = team.get("probablePitcher") or {}
    if prob.get("id"):
        pid = int(prob["id"])
        return _player_from_blob(
            pid,
            prob,
            stats_map,
            is_pitcher=True,
        )
    players = team.get("players") or {}
    for pid in team.get("pitchers") or []:
        key = f"ID{pid}" if f"ID{pid}" in players else str(pid)
        blob = players.get(key) or {}
        if blob.get("battingOrder"):
            continue
        person = blob.get("person") or {}
        person_id = int(person.get("id") or pid)
        return _player_from_blob(person_id, person, stats_map, is_pitcher=True)
    return None


def _fetch_schedule_game(game_id: str, game_date: date) -> dict[str, Any] | None:
    params = {
        "sportId": 1,
        "date": game_date.isoformat(),
        "gamePk": game_id,
        "hydrate": "probablePitcher,lineups",
    }
    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.get(MLB_SCHEDULE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("Schedule lookup failed for game %s: %s", game_id, exc)
        return None
    for day in data.get("dates", []):
        for game in day.get("games", []):
            if str(game.get("gamePk")) == str(game_id):
                return game
    return None


def _fetch_boxscore(game_id: str) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.get(MLB_BOXSCORE_URL.format(game_pk=game_id))
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        logger.debug("Boxscore not available for %s: %s", game_id, exc)
        return None


def clear_lineup_cache() -> None:
    _lineup_cache.clear()


def get_mlb_game_lineup(game_id: str, game_date: date | None = None) -> dict[str, Any]:
    """Return starting pitchers and lineups for both teams."""
    game_date = game_date or date.today()
    cache_key = f"{game_id}:{game_date.isoformat()}"
    cached = _lineup_cache.get(cache_key)
    if cached:
        age = (datetime.now(timezone.utc) - cached[0]).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return cached[1]

    schedule_game = _fetch_schedule_game(game_id, game_date)
    boxscore = _fetch_boxscore(game_id)

    person_ids: set[int] = set()
    sides: dict[str, dict[str, Any]] = {"away": {}, "home": {}}

    if schedule_game:
        for side in ("away", "home"):
            team_blob = schedule_game["teams"][side]
            sp = _probable_pitcher(team_blob)
            if sp:
                person_ids.add(sp["id"])
                sides[side]["starting_pitcher"] = sp

    if boxscore:
        for side in ("away", "home"):
            sp = _starting_pitcher_from_boxscore(boxscore, side, {})
            if sp:
                person_ids.add(sp["id"])
                sides[side]["starting_pitcher"] = sp

    stats_map = _fetch_person_stats(list(person_ids))

    for side in ("away", "home"):
        sp = sides[side].get("starting_pitcher")
        if sp and sp["id"] in stats_map:
            sp["stats"] = stats_map[sp["id"]].get("pitching", {})

    if boxscore:
        for side in ("away", "home"):
            lineup = _lineup_from_boxscore(boxscore, side, stats_map)
            if lineup:
                sides[side]["lineup"] = lineup
                for row in lineup:
                    person_ids.add(row["id"])

    # Re-fetch stats if lineup added more ids
    if boxscore:
        extra = [i for i in person_ids if i not in stats_map]
        if extra:
            stats_map.update(_fetch_person_stats(extra))
            for side in ("away", "home"):
                for row in sides[side].get("lineup") or []:
                    row["stats"] = stats_map.get(row["id"], {}).get("hitting", {})

    payload: dict[str, Any] = {
        "game_id": str(game_id),
        "date": game_date.isoformat(),
        "away": {
            "starting_pitcher": sides["away"].get("starting_pitcher"),
            "lineup": sides["away"].get("lineup") or [],
        },
        "home": {
            "starting_pitcher": sides["home"].get("starting_pitcher"),
            "lineup": sides["home"].get("lineup") or [],
        },
        "lineup_available": bool(
            sides["away"].get("lineup") or sides["home"].get("lineup")
        ),
    }
    if not payload["lineup_available"] and not (
        payload["away"]["starting_pitcher"] or payload["home"]["starting_pitcher"]
    ):
        payload["message"] = "Lineups and probable pitchers publish closer to first pitch."

    _lineup_cache[cache_key] = (datetime.now(timezone.utc), payload)
    return payload
