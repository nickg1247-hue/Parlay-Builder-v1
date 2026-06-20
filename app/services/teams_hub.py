"""Team directory and detail pages for MLB, NBA, and CFB."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT
from app.services.schedule_mlb import MLB_TEAMS_PATH, team_logo_url

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
CACHE_TTL_SECONDS = 30 * 60

ESPN_NBA_TEAMS = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
)
ESPN_CFB_TEAMS = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams"
)
MLB_TEAM_API = "https://statsapi.mlb.com/api/v1/teams/{team_id}"
MLB_ROSTER_API = "https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
MLB_SCHEDULE_API = "https://statsapi.mlb.com/api/v1/schedule"

SUPPORTED_SPORTS = frozenset({"mlb", "nba", "cfb"})

# (group_key, display label, position abbreviations)
FOOTBALL_POSITION_GROUPS: list[tuple[str, str, frozenset[str]]] = [
    ("QB", "Quarterbacks", frozenset({"QB"})),
    ("RB", "Running Backs", frozenset({"RB", "FB", "HB"})),
    ("WR", "Wide Receivers", frozenset({"WR"})),
    ("TE", "Tight Ends", frozenset({"TE"})),
    ("OL", "Offensive Line", frozenset({"OL", "OT", "OG", "C", "G", "T", "LT", "LG", "RG", "RT"})),
    ("DL", "Defensive Line", frozenset({"DL", "DE", "DT", "NT", "EDGE", "SDE", "WDE"})),
    ("LB", "Linebackers", frozenset({"LB", "ILB", "OLB", "MLB", "WLB", "SLB"})),
    ("DB", "Defensive Backs", frozenset({"DB", "CB", "S", "SS", "FS", "SAF", "NB", "DHB"})),
    ("ST", "Special Teams", frozenset({"K", "P", "PK", "LS", "KR", "PR"})),
]

NBA_POSITION_GROUPS: list[tuple[str, str, frozenset[str]]] = [
    ("G", "Guards", frozenset({"PG", "SG", "G", "GUARD"})),
    ("F", "Forwards", frozenset({"SF", "PF", "F", "FORWARD", "GF"})),
    ("C", "Centers", frozenset({"C", "CENTER"})),
]

MLB_POSITION_GROUPS: list[tuple[str, str, frozenset[str]]] = [
    ("P", "Pitchers", frozenset({"P", "SP", "RP", "CP", "LHP", "RHP"})),
    ("C", "Catchers", frozenset({"C"})),
    ("IF", "Infielders", frozenset({"1B", "2B", "3B", "SS", "IF", "INF"})),
    ("OF", "Outfielders", frozenset({"LF", "CF", "RF", "OF", "OFW"})),
    ("DH", "Designated Hitters", frozenset({"DH"})),
]


def _cache_path(name: str) -> Path:
    return PROCESSED_DIR / name


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    )
    return age.total_seconds() < CACHE_TTL_SECONDS


def _norm_pos(pos: str | None) -> str:
    return (pos or "").strip().upper()


def _mlb_player_photo(person_id: str | int) -> str:
    return (
        "https://img.mlbstatic.com/mlb-photos/image/upload/"
        f"w_160,q_auto:best/v1/people/{person_id}/headshot/silo/current"
    )


def _espn_player_photo(ath: dict[str, Any], sport: str) -> str | None:
    headshot = ath.get("headshot") or {}
    href = headshot.get("href") if isinstance(headshot, dict) else None
    if href:
        return href
    pid = ath.get("id")
    if not pid:
        return None
    if sport == "cfb":
        return f"https://a.espncdn.com/i/headshots/college-football/players/full/{pid}.png"
    if sport == "nba":
        return f"https://a.espncdn.com/i/headshots/nba/players/full/{pid}.png"
    return None


def _position_groups_for_sport(sport: str) -> list[tuple[str, str, frozenset[str]]]:
    if sport == "cfb":
        return FOOTBALL_POSITION_GROUPS
    if sport == "nba":
        return NBA_POSITION_GROUPS
    return MLB_POSITION_GROUPS


def group_roster_by_position(
    roster: list[dict[str, Any]], sport: str
) -> list[dict[str, Any]]:
    """Group players by position family (WR together, OL together, etc.)."""
    groups_spec = _position_groups_for_sport(sport)
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in groups_spec}
    other: list[dict[str, Any]] = []

    for player in roster:
        pos = _norm_pos(player.get("position"))
        matched = False
        for key, _, abbrevs in groups_spec:
            if pos in abbrevs:
                buckets[key].append(player)
                matched = True
                break
        if not matched:
            other.append(player)

    def sort_players(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def sort_key(p: dict[str, Any]) -> tuple[int, str]:
            jersey = p.get("jersey")
            try:
                num = int(jersey)
            except (TypeError, ValueError):
                num = 999
            return (num, (p.get("name") or "").lower())

        return sorted(players, key=sort_key)

    grouped: list[dict[str, Any]] = []
    for key, label, _ in groups_spec:
        players = sort_players(buckets.get(key) or [])
        if players:
            grouped.append({"key": key, "label": label, "players": players})

    if other:
        grouped.append(
            {
                "key": "OTHER",
                "label": "Other",
                "players": sort_players(other),
            }
        )
    return grouped


def _mlb_teams_index() -> list[dict[str, Any]]:
    raw = _read_json(MLB_TEAMS_PATH) or {}
    teams = []
    for name, team_id in sorted(raw.items(), key=lambda x: x[0]):
        tid = int(team_id)
        teams.append(
            {
                "id": str(tid),
                "name": name,
                "short_name": name.split()[-1],
                "logo_url": team_logo_url(tid),
                "sport": "mlb",
            }
        )
    return teams


def _espn_teams_index(sport: str) -> list[dict[str, Any]]:
    cache_name = f"{sport}_teams_index.json"
    path = _cache_path(cache_name)
    if _cache_fresh(path):
        cached = _read_json(path)
        if isinstance(cached, list):
            return cached

    url = ESPN_NBA_TEAMS if sport == "nba" else ESPN_CFB_TEAMS
    params = {"limit": 500}
    if sport == "cfb":
        params["groups"] = "80"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("ESPN team index failed (%s): %s", sport, exc)
        stale = _read_json(path)
        return stale if isinstance(stale, list) else []

    teams: list[dict[str, Any]] = []
    for entry in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        t = entry.get("team") or {}
        tid = t.get("id")
        if not tid:
            continue
        logo = t.get("logos") or []
        logo_url = logo[0].get("href") if logo else None
        teams.append(
            {
                "id": str(tid),
                "name": t.get("displayName") or t.get("name") or "",
                "short_name": t.get("abbreviation") or t.get("shortDisplayName") or "",
                "logo_url": logo_url,
                "sport": sport,
            }
        )
    teams.sort(key=lambda x: x["name"])
    _write_json(path, teams)
    return teams


def list_teams(sport: str, *, query: str | None = None) -> dict[str, Any]:
    sport_key = sport.lower().strip()
    if sport_key not in SUPPORTED_SPORTS:
        return {"sport": sport_key, "teams": [], "error": "Unsupported sport"}

    if sport_key == "mlb":
        teams = _mlb_teams_index()
    else:
        teams = _espn_teams_index(sport_key)

    if query:
        q = query.strip().lower()
        teams = [
            t
            for t in teams
            if q in t["name"].lower()
            or q in (t.get("short_name") or "").lower()
        ]

    return {"sport": sport_key, "teams": teams, "count": len(teams)}


def _parse_espn_roster_payload(data: dict[str, Any], sport: str) -> list[dict[str, Any]]:
    roster: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_player(ath: dict[str, Any], pos_hint: str | None = None) -> None:
        pid = ath.get("id")
        if not pid:
            return
        pid_str = str(pid)
        if pid_str in seen:
            return
        seen.add(pid_str)
        pos_obj = ath.get("position") or {}
        pos = pos_hint or pos_obj.get("abbreviation") or pos_obj.get("name")
        roster.append(
            {
                "id": pid_str,
                "name": ath.get("displayName") or ath.get("fullName") or "",
                "position": pos,
                "jersey": ath.get("jersey"),
                "photo_url": _espn_player_photo(ath, sport),
            }
        )

    athletes_root = data.get("athletes") or []
    for block in athletes_root:
        if isinstance(block, dict) and block.get("items"):
            block_pos = (block.get("position") or block.get("name") or "").upper()
            for item in block["items"]:
                if isinstance(item, dict):
                    add_player(item, block_pos if len(block_pos) <= 3 else None)
        elif isinstance(block, dict):
            add_player(block)

    team = data.get("team") or {}
    if not roster:
        for entry in team.get("athletes") or []:
            if isinstance(entry, dict) and entry.get("athlete"):
                ath = entry["athlete"]
                pos = (entry.get("position") or ath.get("position") or {}).get(
                    "abbreviation"
                )
                add_player({**ath, "jersey": entry.get("jersey") or ath.get("jersey")}, pos)
            elif isinstance(entry, dict):
                add_player(entry)

    if not roster:
        roster_block = team.get("roster") or {}
        for entry in roster_block.get("entries") or roster_block.get("athletes") or []:
            if isinstance(entry, dict) and entry.get("athlete"):
                ath = entry["athlete"]
                pos = (entry.get("position") or ath.get("position") or {}).get(
                    "abbreviation"
                )
                add_player({**ath, "jersey": entry.get("jersey") or ath.get("jersey")}, pos)
            elif isinstance(entry, dict):
                add_player(entry)

    return roster


def _mlb_team_detail(team_id: str) -> dict[str, Any]:
    detail_path = _cache_path(f"mlb_team_{team_id}.json")
    if _cache_fresh(detail_path):
        cached = _read_json(detail_path)
        if isinstance(cached, dict) and cached.get("roster_groups"):
            return cached

    try:
        with httpx.Client(timeout=30.0) as client:
            team_resp = client.get(
                MLB_TEAM_API.format(team_id=team_id),
                params={"hydrate": "record,leagueRecord"},
            )
            team_resp.raise_for_status()
            team_data = team_resp.json()["teams"][0]

            roster_resp = client.get(
                MLB_ROSTER_API.format(team_id=team_id),
                params={"rosterType": "active"},
            )
            roster_resp.raise_for_status()
            roster_data = roster_resp.json()

            end = date.today()
            start = end - timedelta(days=21)
            sched_resp = client.get(
                MLB_SCHEDULE_API,
                params={
                    "sportId": 1,
                    "teamId": team_id,
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "hydrate": "linescore",
                },
            )
            sched_resp.raise_for_status()
            sched_data = sched_resp.json()
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        logger.warning("MLB team detail failed for %s: %s", team_id, exc)
        raise

    record = team_data.get("record") or {}
    league_record = team_data.get("leagueRecord") or {}
    roster = []
    for entry in roster_data.get("roster", []):
        person = entry.get("person") or {}
        pos = entry.get("position") or {}
        pid = person.get("id")
        jersey = entry.get("jerseyNumber")
        roster.append(
            {
                "id": str(pid),
                "name": person.get("fullName") or "",
                "position": pos.get("abbreviation") or pos.get("name"),
                "jersey": jersey,
                "photo_url": _mlb_player_photo(pid) if pid else None,
            }
        )

    recent: list[dict[str, Any]] = []
    for day in sched_data.get("dates", []):
        for game in day.get("games", []):
            state = (game.get("status") or {}).get("abstractGameState", "")
            if state not in ("Final", "Game Over", "Live", "In Progress"):
                continue
            home = game["teams"]["home"]
            away = game["teams"]["away"]
            is_home = str(home["team"]["id"]) == str(team_id)
            us = home if is_home else away
            them = away if is_home else home
            us_score = us.get("score")
            them_score = them.get("score")
            won = (
                us_score is not None
                and them_score is not None
                and us_score > them_score
            )
            opp_name = them["team"].get("name") or ""
            recent.append(
                {
                    "game_id": str(game.get("gamePk")),
                    "date": game.get("gameDate"),
                    "opponent": opp_name,
                    "home_away": "vs" if is_home else "@",
                    "team_score": us_score,
                    "opp_score": them_score,
                    "won": won,
                    "status": state,
                }
            )
    recent.sort(key=lambda g: g.get("date") or "", reverse=True)
    recent = recent[:10]

    wins = league_record.get("wins")
    losses = league_record.get("losses")
    record_str = f"{wins}-{losses}" if wins is not None and losses is not None else None
    if not record_str:
        lr = record.get("leagueRecord") or {}
        if lr.get("wins") is not None:
            record_str = f"{lr.get('wins')}-{lr.get('losses')}"

    roster_groups = group_roster_by_position(roster, "mlb")
    payload = {
        "sport": "mlb",
        "id": str(team_id),
        "name": team_data.get("name") or "",
        "logo_url": team_logo_url(int(team_id)),
        "record": record_str,
        "standing": record.get("standingSummary"),
        "roster": roster,
        "roster_groups": roster_groups,
        "recent_games": recent,
    }
    _write_json(detail_path, payload)
    return payload


def _espn_team_detail(sport: str, team_id: str) -> dict[str, Any]:
    detail_path = _cache_path(f"{sport}_team_{team_id}.json")
    if _cache_fresh(detail_path):
        cached = _read_json(detail_path)
        if isinstance(cached, dict) and cached.get("roster_groups"):
            return cached

    sport_path = "basketball/nba" if sport == "nba" else "football/college-football"
    base = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/teams/{team_id}"
    try:
        with httpx.Client(timeout=30.0) as client:
            team_resp = client.get(base, params={"enable": "record"})
            team_resp.raise_for_status()
            data = team_resp.json()
            roster_resp = client.get(f"{base}/roster")
            roster_resp.raise_for_status()
            roster_data = roster_resp.json()
            sched_resp = client.get(f"{base}/schedule")
            sched_resp.raise_for_status()
            sched = sched_resp.json()
    except httpx.HTTPError as exc:
        logger.warning("ESPN team detail failed (%s/%s): %s", sport, team_id, exc)
        raise

    team = data.get("team") or {}
    logo = (team.get("logos") or [{}])[0].get("href")
    record = None
    for rec in team.get("record", {}).get("items", []):
        if rec.get("type") == "total":
            record = rec.get("summary")
            break
    if not record:
        for rec in team.get("record", {}).get("items", []):
            if rec.get("summary"):
                record = rec["summary"]
                break

    roster = _parse_espn_roster_payload(roster_data, sport)
    if not roster:
        roster = _parse_espn_roster_payload(data, sport)

    recent: list[dict[str, Any]] = []
    events = sched.get("events") or []
    for event in events:
        comp = (event.get("competitions") or [{}])[0]
        status = (comp.get("status") or {}).get("type", {})
        if status.get("state") not in ("post", "in"):
            continue
        competitors = comp.get("competitors") or []
        us = next(
            (c for c in competitors if str((c.get("team") or {}).get("id")) == str(team_id)),
            None,
        )
        if not us:
            us = next((c for c in competitors if c.get("homeAway") == "home"), {})
        them = next((c for c in competitors if c is not us), {})
        us_team = us.get("team") or {}
        them_team = them.get("team") or {}
        us_score = _parse_score(us.get("score"))
        them_score = _parse_score(them.get("score"))
        recent.append(
            {
                "game_id": str(event.get("id")),
                "date": event.get("date"),
                "opponent": them_team.get("displayName") or them_team.get("name") or "",
                "home_away": "vs" if us.get("homeAway") == "home" else "@",
                "team_score": us_score,
                "opp_score": them_score,
                "won": us_score is not None and them_score is not None and us_score > them_score,
                "status": status.get("description") or status.get("name"),
            }
        )
    recent.sort(key=lambda g: g.get("date") or "", reverse=True)
    recent = recent[:10]

    roster_groups = group_roster_by_position(roster, sport)
    payload = {
        "sport": sport,
        "id": str(team_id),
        "name": team.get("displayName") or team.get("name") or "",
        "logo_url": logo,
        "record": record,
        "standing": team.get("standingSummary"),
        "roster": roster,
        "roster_groups": roster_groups,
        "recent_games": recent,
    }
    _write_json(detail_path, payload)
    return payload


def _parse_score(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_team_detail(sport: str, team_id: str) -> dict[str, Any] | None:
    sport_key = sport.lower().strip()
    if sport_key not in SUPPORTED_SPORTS:
        return None
    try:
        if sport_key == "mlb":
            return _mlb_team_detail(team_id)
        return _espn_team_detail(sport_key, team_id)
    except Exception:
        return None
