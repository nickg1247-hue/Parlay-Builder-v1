"""Team goal rates for the NHL score model, from the public NHL stats API."""

from __future__ import annotations

import json
import logging
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

STATS_URL = "https://api.nhle.com/stats/rest/en/team/summary"
RATINGS_PATH = PROJECT_ROOT / "data" / "processed" / "nhl_team_ratings.json"
CACHE_TTL_SECONDS = 6 * 3600
LEAGUE_FALLBACK = 3.05

# ESPN abbreviations. Aliases cover the short forms ESPN sometimes sends.
ABBR_ALIASES = {
    "LA": "LAK",
    "SJ": "SJS",
    "TB": "TBL",
    "NJ": "NJD",
    "WAS": "WSH",
    "MON": "MTL",
    "VEG": "VGK",
}

_NAME_TO_ABBR = {
    "anaheim ducks": "ANA",
    "boston bruins": "BOS",
    "buffalo sabres": "BUF",
    "calgary flames": "CGY",
    "carolina hurricanes": "CAR",
    "chicago blackhawks": "CHI",
    "colorado avalanche": "COL",
    "columbus blue jackets": "CBJ",
    "dallas stars": "DAL",
    "detroit red wings": "DET",
    "edmonton oilers": "EDM",
    "florida panthers": "FLA",
    "los angeles kings": "LAK",
    "minnesota wild": "MIN",
    "montreal canadiens": "MTL",
    "nashville predators": "NSH",
    "new jersey devils": "NJD",
    "new york islanders": "NYI",
    "new york rangers": "NYR",
    "ottawa senators": "OTT",
    "philadelphia flyers": "PHI",
    "pittsburgh penguins": "PIT",
    "san jose sharks": "SJS",
    "seattle kraken": "SEA",
    "st louis blues": "STL",
    "tampa bay lightning": "TBL",
    "toronto maple leafs": "TOR",
    "utah mammoth": "UTA",
    "utah hockey club": "UTA",
    "vancouver canucks": "VAN",
    "vegas golden knights": "VGK",
    "washington capitals": "WSH",
    "winnipeg jets": "WPG",
}


def canonical_abbr(abbr: str | None) -> str:
    raw = (abbr or "").upper().strip()
    return ABBR_ALIASES.get(raw, raw)


def _norm_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().replace(".", "").replace("'", "").strip()


def team_abbr_from_name(name: str) -> str:
    return _NAME_TO_ABBR.get(_norm_name(name), "")


def nhl_season_id(game_date: date) -> str:
    start = game_date.year if game_date.month >= 9 else game_date.year - 1
    return f"{start}{start + 1}"


def prior_season_id(season_id: str) -> str:
    start = int(str(season_id)[:4]) - 1
    return f"{start}{start + 1}"


def _season_label(season_id: str) -> str:
    text = str(season_id)
    return f"{text[:4]}-{text[6:]}"


def _fetch_season(season_id: str) -> list[dict[str, Any]]:
    params = {
        "cayenneExp": f"seasonId={season_id} and gameTypeId=2",
        "limit": "50",
    }
    headers = {"User-Agent": "NTGSports/1.0 (nhl ratings)"}
    with httpx.Client(timeout=20.0, headers=headers, follow_redirects=True) as client:
        response = client.get(STATS_URL, params=params)
        response.raise_for_status()
        rows = response.json().get("data") or []
    return [row for row in rows if str(row.get("seasonId")) == str(season_id)]


def _row_rates(row: dict[str, Any]) -> dict[str, Any] | None:
    abbr = team_abbr_from_name(str(row.get("teamFullName") or ""))
    if not abbr:
        logger.info("NHL ratings skipped unmapped team %s", row.get("teamFullName"))
        return None
    games = int(row.get("gamesPlayed") or 0)
    if games <= 0:
        return None
    return {
        "abbr": abbr,
        "name": row.get("teamFullName") or abbr,
        "games": games,
        "gf_per_game": float(row.get("goalsForPerGame") or 0),
        "ga_per_game": float(row.get("goalsAgainstPerGame") or 0),
        "point_pct": float(row.get("pointPct") or 0),
    }


def _index_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        parsed = _row_rates(row)
        if parsed:
            out[parsed["abbr"]] = parsed
    return out


def _blend(
    prior: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    teams: dict[str, dict[str, Any]] = {}
    for abbr in set(prior) | set(current):
        base = prior.get(abbr)
        live = current.get(abbr)
        if base and live and live["games"] > 0:
            weight = min(0.80, live["games"] / 25.0)
            teams[abbr] = {
                "abbr": abbr,
                "name": live["name"] or base["name"],
                "games": int(base["games"]) + int(live["games"]),
                "prior_games": int(base["games"]),
                "current_games": int(live["games"]),
                "gf_per_game": (1.0 - weight) * base["gf_per_game"] + weight * live["gf_per_game"],
                "ga_per_game": (1.0 - weight) * base["ga_per_game"] + weight * live["ga_per_game"],
                "point_pct": (1.0 - weight) * base["point_pct"] + weight * live["point_pct"],
            }
        elif live:
            teams[abbr] = {**live, "prior_games": 0, "current_games": live["games"]}
        elif base:
            teams[abbr] = {**base, "prior_games": base["games"], "current_games": 0}
    return teams


def _league_average(teams: dict[str, dict[str, Any]]) -> float:
    rates = [float(t["gf_per_game"]) for t in teams.values() if t.get("gf_per_game")]
    if not rates:
        return LEAGUE_FALLBACK
    return sum(rates) / len(rates)


def build_ratings(game_date: date | None = None) -> dict[str, Any]:
    day = game_date or date.today()
    current_id = nhl_season_id(day)
    prior_id = prior_season_id(current_id)
    prior_rows = _fetch_season(prior_id)
    try:
        current_rows = _fetch_season(current_id)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("NHL current-season ratings failed for %s: %s", current_id, exc)
        current_rows = []
    prior = _index_rows(prior_rows)
    current = _index_rows(current_rows)
    teams = _blend(prior, current)
    current_games = sum(int(t.get("current_games") or 0) for t in teams.values())
    if current_games > 0:
        note = (
            f"Goal rates blend the {_season_label(prior_id)} regular season "
            f"with {_season_label(current_id)} results so far."
        )
    else:
        note = f"Goal rates are the full {_season_label(prior_id)} regular season. This season has no completed games in the ratings pull yet."
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": STATS_URL,
        "prior_season": prior_id,
        "current_season": current_id,
        "league_gf_per_game": round(_league_average(teams) or LEAGUE_FALLBACK, 3),
        "team_count": len(teams),
        "ratings_note": note,
        "teams": teams,
    }


def _read_cache() -> dict[str, Any] | None:
    if not RATINGS_PATH.exists():
        return None
    try:
        return json.loads(RATINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_fresh(payload: dict[str, Any]) -> bool:
    raw = payload.get("fetched_at")
    if not raw:
        return False
    try:
        fetched = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - fetched).total_seconds()
    return age < CACHE_TTL_SECONDS and bool(payload.get("teams"))


def load_ratings(game_date: date | None = None, *, force: bool = False) -> dict[str, Any]:
    cached = _read_cache()
    if cached and not force and _cache_fresh(cached):
        return cached
    try:
        payload = build_ratings(game_date)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("NHL ratings fetch failed: %s", exc)
        if cached and cached.get("teams"):
            return cached
        return {
            "fetched_at": "",
            "source": "fallback",
            "league_gf_per_game": LEAGUE_FALLBACK,
            "team_count": 0,
            "ratings_note": "Team ratings were unavailable, so every club is treated as league average.",
            "teams": {},
        }
    if not payload.get("teams") and cached and cached.get("teams"):
        return cached
    RATINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RATINGS_PATH.write_text(json.dumps(payload), encoding="utf-8")
    return payload
