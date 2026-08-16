"""Season-level CFB priors — talent, returning production, FPI, coaches.

One CFBD call per endpoint per season. Current-year FPI is stored but only
prior-year FPI is used as a feature (no end-of-season leakage).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT
from app.ingest.cfb import SEASONS
from app.odds.cfb_team_aliases import normalize_team_name

logger = logging.getLogger(__name__)

CFBD_BASE_URL = "https://api.collegefootballdata.com"
PRIORS_CACHE_DIR = PROJECT_ROOT / "data" / "processed" / "cfb_priors_cache"
REQUEST_SLEEP_SECONDS = 0.6
REQUEST_RETRIES = 4
PRIOR_LOOKBACK_YEAR = 1


@dataclass
class TeamSeasonPriors:
    talent: float = 0.0
    returning_pct: float = 0.0
    returning_pass_pct: float = 0.0
    fpi: float = 0.0
    coach_key: str = ""


@dataclass
class PriorsStore:
    talent: dict[tuple[int, str], float] = field(default_factory=dict)
    returning_pct: dict[tuple[int, str], float] = field(default_factory=dict)
    returning_pass_pct: dict[tuple[int, str], float] = field(default_factory=dict)
    fpi: dict[tuple[int, str], float] = field(default_factory=dict)
    coaches: dict[tuple[int, str], str] = field(default_factory=dict)


def _api_key() -> str | None:
    key = (os.getenv("CFBD_API_KEY") or "").strip()
    return key or None


def _cache_path(kind: str, season: int) -> Path:
    PRIORS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return PRIORS_CACHE_DIR / f"{season}_{kind}.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_json(
    client: httpx.Client,
    path: str,
    *,
    api_key: str,
    params: dict[str, str],
) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {api_key}"}
    last_error: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            response = client.get(
                f"{CFBD_BASE_URL}{path}",
                params=params,
                headers=headers,
            )
            if response.status_code == 401:
                raise SystemExit(
                    "CFBD API returned 401 Unauthorized. Check CFBD_API_KEY in .env."
                )
            response.raise_for_status()
            data = response.json()
            return list(data) if isinstance(data, list) else []
        except SystemExit:
            raise
        except Exception as exc:
            last_error = exc
            time.sleep(REQUEST_SLEEP_SECONDS * (attempt + 2))
    raise RuntimeError(f"Could not fetch CFBD {path} {params}") from last_error


def _write_cache(kind: str, season: int, rows: list[dict[str, Any]]) -> None:
    payload = {
        "season": season,
        "kind": kind,
        "fetched_at": _iso_now(),
        "rows": rows,
    }
    _cache_path(kind, season).write_text(json.dumps(payload), encoding="utf-8")


def _load_cache_rows(kind: str, season: int) -> list[dict[str, Any]]:
    path = _cache_path(kind, season)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = raw.get("rows") if isinstance(raw, dict) else raw
    return rows if isinstance(rows, list) else []


def fetch_talent(client: httpx.Client, season: int, *, api_key: str) -> list[dict[str, Any]]:
    rows = _get_json(client, "/talent", api_key=api_key, params={"year": str(season)})
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        team = normalize_team_name(str(row.get("school") or row.get("team") or ""))
        if not team:
            continue
        try:
            talent = float(row.get("talent") or 0.0)
        except (TypeError, ValueError):
            continue
        out.append({"team": team, "talent": talent})
    return out


def fetch_returning(client: httpx.Client, season: int, *, api_key: str) -> list[dict[str, Any]]:
    rows = _get_json(
        client, "/player/returning", api_key=api_key, params={"year": str(season)}
    )
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        team = normalize_team_name(str(row.get("team") or row.get("school") or ""))
        if not team:
            continue
        try:
            pct = float(row.get("percentPPA") or row.get("percent_ppa") or 0.0)
            pass_pct = float(
                row.get("percentPassingPPA") or row.get("percent_passing_ppa") or 0.0
            )
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "team": team,
                "returning_pct": pct,
                "returning_pass_pct": pass_pct,
            }
        )
    return out


def fetch_fpi(client: httpx.Client, season: int, *, api_key: str) -> list[dict[str, Any]]:
    rows = _get_json(client, "/ratings/fpi", api_key=api_key, params={"year": str(season)})
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        team = normalize_team_name(str(row.get("team") or row.get("school") or ""))
        if not team:
            continue
        try:
            fpi = float(row.get("fpi") or 0.0)
        except (TypeError, ValueError):
            continue
        out.append({"team": team, "fpi": fpi})
    return out


def _coach_name(row: dict[str, Any]) -> str:
    first = str(row.get("firstName") or row.get("first_name") or "").strip()
    last = str(row.get("lastName") or row.get("last_name") or "").strip()
    return f"{first} {last}".strip().lower()


def fetch_coaches(client: httpx.Client, season: int, *, api_key: str) -> list[dict[str, Any]]:
    rows = _get_json(client, "/coaches", api_key=api_key, params={"year": str(season)})
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _coach_name(row)
        if not name:
            continue
        seasons = row.get("seasons") or []
        if isinstance(seasons, list) and seasons:
            for season_row in seasons:
                if not isinstance(season_row, dict):
                    continue
                year_raw = season_row.get("year")
                try:
                    year = int(year_raw)
                except (TypeError, ValueError):
                    continue
                if year != season:
                    continue
                team = normalize_team_name(str(season_row.get("school") or ""))
                if team:
                    out[team] = name
        else:
            team = normalize_team_name(str(row.get("team") or row.get("school") or ""))
            if team:
                out[team] = name
    return [{"team": team, "coach_key": coach} for team, coach in sorted(out.items())]


def fetch_and_cache_kind(
    kind: str,
    season: int,
    *,
    api_key: str,
    force: bool = False,
) -> list[dict[str, Any]]:
    path = _cache_path(kind, season)
    if path.exists() and not force:
        return _load_cache_rows(kind, season)
    fetchers = {
        "talent": fetch_talent,
        "returning": fetch_returning,
        "fpi": fetch_fpi,
        "coaches": fetch_coaches,
    }
    fetcher = fetchers[kind]
    with httpx.Client(timeout=30.0) as client:
        rows = fetcher(client, season, api_key=api_key)
    _write_cache(kind, season, rows)
    return rows


def prior_seasons_for(game_seasons: tuple[int, ...]) -> tuple[int, ...]:
    if not game_seasons:
        extra = (min(SEASONS) - PRIOR_LOOKBACK_YEAR,)
        return tuple(sorted(set(SEASONS) | set(extra)))
    lo = min(game_seasons) - PRIOR_LOOKBACK_YEAR
    hi = max(game_seasons)
    return tuple(range(lo, hi + 1))


def ensure_priors_cache(
    seasons: tuple[int, ...] | None = None,
    *,
    api_key: str | None = None,
    force: bool = False,
) -> int:
    """Warm talent / returning / FPI / coaches caches. Includes year-1 for priors."""
    game_seasons = seasons or SEASONS
    target = prior_seasons_for(tuple(int(s) for s in game_seasons))
    key = api_key or _api_key()
    if not key:
        logger.warning("Skipping CFB priors cache warm — CFBD_API_KEY not set")
        return 0

    count = 0
    for season in target:
        for kind in ("talent", "returning", "fpi", "coaches"):
            try:
                fetch_and_cache_kind(kind, season, api_key=key, force=force)
                count += 1
            except SystemExit:
                raise
            except Exception as exc:
                logger.warning("CFB priors %s %s failed: %s", kind, season, exc)
            time.sleep(REQUEST_SLEEP_SECONDS)
    return count


def load_priors_store(seasons: tuple[int, ...] | None = None) -> PriorsStore:
    target = prior_seasons_for(tuple(int(s) for s in (seasons or SEASONS)))
    store = PriorsStore()
    for season in target:
        for row in _load_cache_rows("talent", season):
            team = normalize_team_name(str(row.get("team") or ""))
            if team:
                store.talent[(season, team)] = float(row.get("talent") or 0.0)
        for row in _load_cache_rows("returning", season):
            team = normalize_team_name(str(row.get("team") or ""))
            if not team:
                continue
            store.returning_pct[(season, team)] = float(row.get("returning_pct") or 0.0)
            store.returning_pass_pct[(season, team)] = float(
                row.get("returning_pass_pct") or 0.0
            )
        for row in _load_cache_rows("fpi", season):
            team = normalize_team_name(str(row.get("team") or ""))
            if team:
                store.fpi[(season, team)] = float(row.get("fpi") or 0.0)
        for row in _load_cache_rows("coaches", season):
            team = normalize_team_name(str(row.get("team") or ""))
            if team:
                store.coaches[(season, team)] = str(row.get("coach_key") or "")
    return store


def _lookup(table: dict[tuple[int, str], float], season: int, team: str) -> float:
    return float(table.get((season, normalize_team_name(team)), 0.0))


def prior_feature_diffs(
    *,
    season: int,
    home_team: str,
    away_team: str,
    store: PriorsStore,
) -> dict[str, float]:
    """Safe pregame priors — current-year talent/returning, prior-year FPI only."""
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    prior_year = season - PRIOR_LOOKBACK_YEAR
    home_coach = store.coaches.get((season, home), "")
    away_coach = store.coaches.get((season, away), "")
    home_prev_coach = store.coaches.get((prior_year, home), "")
    away_prev_coach = store.coaches.get((prior_year, away), "")
    return {
        "talent_diff": _lookup(store.talent, season, home) - _lookup(store.talent, season, away),
        "returning_pct_diff": _lookup(store.returning_pct, season, home)
        - _lookup(store.returning_pct, season, away),
        "returning_pass_pct_diff": _lookup(store.returning_pass_pct, season, home)
        - _lookup(store.returning_pass_pct, season, away),
        "prior_fpi_diff": _lookup(store.fpi, prior_year, home)
        - _lookup(store.fpi, prior_year, away),
        "coach_change_home": float(
            1.0 if home_coach and home_prev_coach and home_coach != home_prev_coach else 0.0
        ),
        "coach_change_away": float(
            1.0 if away_coach and away_prev_coach and away_coach != away_prev_coach else 0.0
        ),
    }
