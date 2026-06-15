"""CFBD SP+ cache — weekly when API differs; preseason-only for week 1 when flat."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

from app.config import PROJECT_ROOT
from app.ingest.cfb import SEASONS
from app.odds.cfb_betting_lines import load_cfbd_calendar
from app.odds.cfb_team_aliases import normalize_team_name

logger = logging.getLogger(__name__)

CFBD_BASE_URL = "https://api.collegefootballdata.com"
SP_PLUS_CACHE_DIR = PROJECT_ROOT / "data" / "processed" / "cfb_sp_plus_cache"
REQUEST_SLEEP_SECONDS = 0.6
REQUEST_RETRIES = 4
RATING_EPS = 1e-9

WeeklyMode = Literal["ok", "flat"]


@dataclass(frozen=True)
class TeamSPPlus:
    overall: float
    offense: float
    defense: float


@dataclass
class SPPlusStore:
    weekly_mode: dict[int, WeeklyMode] = field(default_factory=dict)
    preseason: dict[tuple[int, str], TeamSPPlus] = field(default_factory=dict)
    weekly: dict[tuple[int, int, str], TeamSPPlus] = field(default_factory=dict)
    last_confirmed_week: dict[int, int | None] = field(default_factory=dict)


def _api_key() -> str | None:
    key = (os.getenv("CFBD_API_KEY") or "").strip()
    return key or None


def _week_cache_path(season: int, week: int) -> Path:
    SP_PLUS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return SP_PLUS_CACHE_DIR / f"{season}_week_{week}.json"


def _preseason_cache_path(season: int) -> Path:
    SP_PLUS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return SP_PLUS_CACHE_DIR / f"{season}_preseason.json"


def _meta_cache_path(season: int) -> Path:
    SP_PLUS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return SP_PLUS_CACHE_DIR / f"{season}_meta.json"


def pregame_sp_week(game_week: int) -> int:
    """For a game in week W, SP+ lookup week is W-1 (0 = preseason slot)."""
    return max(0, int(game_week) - 1)


def _parse_sp_row(row: dict[str, Any]) -> tuple[str, TeamSPPlus] | None:
    team_raw = row.get("team") or row.get("school")
    if not team_raw:
        return None
    team = normalize_team_name(str(team_raw))
    if not team:
        return None
    overall = row.get("rating")
    offense_obj = row.get("offense") or {}
    defense_obj = row.get("defense") or {}
    try:
        overall_f = float(overall) if overall is not None else 0.0
        offense_f = float(offense_obj.get("rating") or 0.0)
        defense_f = float(defense_obj.get("rating") or 0.0)
    except (TypeError, ValueError):
        return None
    return team, TeamSPPlus(overall=overall_f, offense=offense_f, defense=defense_f)


def _fetch_sp_plus(
    client: httpx.Client,
    season: int,
    *,
    api_key: str,
    week: int | None = None,
) -> dict[str, TeamSPPlus]:
    params: dict[str, str] = {
        "year": str(season),
        "seasonType": "regular",
    }
    if week is not None:
        params["week"] = str(week)
    headers = {"Authorization": f"Bearer {api_key}"}
    last_error: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            response = client.get(
                f"{CFBD_BASE_URL}/ratings/sp",
                params=params,
                headers=headers,
            )
            if response.status_code == 401:
                raise SystemExit(
                    "CFBD API returned 401 Unauthorized. Check CFBD_API_KEY in .env."
                )
            response.raise_for_status()
            data = response.json()
            rows = list(data) if isinstance(data, list) else []
            out: dict[str, TeamSPPlus] = {}
            for row in rows:
                parsed = _parse_sp_row(row)
                if parsed is None:
                    continue
                team, sp = parsed
                out[team] = sp
            return out
        except SystemExit:
            raise
        except Exception as exc:
            last_error = exc
            time.sleep(REQUEST_SLEEP_SECONDS * (attempt + 2))
    label = f"season {season}" + (f" week {week}" if week is not None else "")
    raise RuntimeError(f"Could not fetch SP+ for {label}") from last_error


def _serialize_cache(ratings: dict[str, TeamSPPlus]) -> list[dict[str, Any]]:
    return [
        {
            "team": team,
            "overall": sp.overall,
            "offense": sp.offense,
            "defense": sp.defense,
        }
        for team, sp in sorted(ratings.items())
    ]


def _load_cache_file(path: Path) -> dict[str, TeamSPPlus]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    ratings: dict[str, TeamSPPlus] = {}
    rows = raw.get("teams") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return ratings
    for row in rows:
        if not isinstance(row, dict):
            continue
        team = normalize_team_name(str(row.get("team") or ""))
        if not team:
            continue
        try:
            ratings[team] = TeamSPPlus(
                overall=float(row.get("overall") or 0.0),
                offense=float(row.get("offense") or 0.0),
                defense=float(row.get("defense") or 0.0),
            )
        except (TypeError, ValueError):
            continue
    return ratings


def _parse_week_path(path: Path) -> tuple[int, int] | None:
    parts = path.stem.split("_week_")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _ratings_overall(path: Path) -> dict[str, float]:
    return {team: sp.overall for team, sp in _load_cache_file(path).items()}


def compare_week_rating_files(left: Path, right: Path) -> dict[str, float]:
    a = _ratings_overall(left)
    b = _ratings_overall(right)
    shared = set(a) & set(b)
    if not shared:
        return {"avg_abs_diff": 0.0, "max_abs_diff": 0.0, "teams_compared": 0}
    diffs = [abs(a[t] - b[t]) for t in shared]
    return {
        "avg_abs_diff": sum(diffs) / len(diffs),
        "max_abs_diff": max(diffs),
        "teams_compared": len(shared),
    }


def week_files_for_season(season: int, cache_dir: Path | None = None) -> list[Path]:
    root = cache_dir or SP_PLUS_CACHE_DIR
    files = [p for p in root.glob(f"{season}_week_*.json") if _parse_week_path(p)]
    return sorted(files, key=lambda p: _parse_week_path(p)[1])


def audit_season_week_files(season: int, files: list[Path]) -> dict[str, Any]:
    """Return audit report; leakage_confirmed when all weekly ratings are flat."""
    weeks = sorted(_parse_week_path(f)[1] for f in files)
    max_avg_diff = 0.0
    last_confirmed_week: int | None = None

    for i in range(len(files) - 1):
        left, right = files[i], files[i + 1]
        cmp = compare_week_rating_files(left, right)
        max_avg_diff = max(max_avg_diff, cmp["avg_abs_diff"])
        if cmp["max_abs_diff"] > RATING_EPS:
            _, right_week = _parse_week_path(right)
            last_confirmed_week = right_week

    leakage_confirmed = len(files) >= 2 and max_avg_diff < RATING_EPS

    return {
        "season": season,
        "week_files": len(files),
        "weeks": weeks,
        "max_avg_rating_diff": round(max_avg_diff, 6),
        "last_confirmed_week": last_confirmed_week,
        "leakage_confirmed": leakage_confirmed,
        "weekly_mode": "flat" if leakage_confirmed else "ok",
    }


def run_sp_leakage_audit(cache_dir: Path | None = None) -> tuple[int, list[dict[str, Any]]]:
    """Audit weekly cache. Returns (exit_code, season_reports)."""
    root = cache_dir or SP_PLUS_CACHE_DIR
    if not root.exists():
        return 2, []

    week_files = sorted(root.glob("*_week_*.json"))
    if not week_files:
        return 2, []

    by_season: dict[int, list[Path]] = {}
    for path in week_files:
        parsed = _parse_week_path(path)
        if parsed is None:
            continue
        season, _ = parsed
        by_season.setdefault(season, []).append(path)

    reports = [audit_season_week_files(season, week_files_for_season(season, root)) for season in sorted(by_season)]
    if any(r["leakage_confirmed"] for r in reports):
        return 1, reports
    return 0, reports


def _write_meta(season: int, report: dict[str, Any]) -> None:
    payload = {
        "season": season,
        "weekly_mode": report["weekly_mode"],
        "last_confirmed_week": report["last_confirmed_week"],
        "leakage_confirmed": report["leakage_confirmed"],
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }
    _meta_cache_path(season).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_meta(season: int) -> dict[str, Any] | None:
    path = _meta_cache_path(season)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def fetch_and_cache_sp_plus_week(
    season: int,
    week: int,
    *,
    api_key: str | None = None,
    force: bool = False,
) -> dict[str, TeamSPPlus]:
    path = _week_cache_path(season, week)
    if path.exists() and not force:
        return _load_cache_file(path)

    key = api_key or _api_key()
    if not key:
        return _load_cache_file(path) if path.exists() else {}

    with httpx.Client(timeout=60.0) as client:
        ratings = _fetch_sp_plus(client, season, api_key=key, week=week)
    payload = {
        "season": season,
        "week": week,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "teams": _serialize_cache(ratings),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return ratings


def fetch_and_cache_preseason_sp(
    season: int,
    *,
    api_key: str | None = None,
    force: bool = False,
) -> dict[str, TeamSPPlus]:
    """Fetch SP+ without week param — preseason snapshot for week-1 games only."""
    path = _preseason_cache_path(season)
    if path.exists() and not force:
        return _load_cache_file(path)

    key = api_key or _api_key()
    if not key:
        return _load_cache_file(path) if path.exists() else {}

    with httpx.Client(timeout=60.0) as client:
        ratings = _fetch_sp_plus(client, season, api_key=key, week=None)
    payload = {
        "season": season,
        "kind": "preseason_no_week",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "teams": _serialize_cache(ratings),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Cached preseason SP+ for %s (%s teams)", season, len(ratings))
    return ratings


def regular_season_weeks(season: int, *, api_key: str | None = None) -> list[int]:
    calendar = load_cfbd_calendar(season, api_key=api_key)
    weeks: set[int] = set()
    for entry in calendar:
        if str(entry.get("seasonType") or "regular").lower() != "regular":
            continue
        week = entry.get("week")
        if week is not None:
            weeks.add(int(week))
    return sorted(weeks) if weeks else list(range(1, 16))


def ensure_sp_plus_cache(
    seasons: tuple[int, ...] | None = None,
    *,
    api_key: str | None = None,
    force: bool = False,
) -> int:
    """Warm weekly cache, audit, and fetch preseason snapshots when weeks are flat."""
    target_seasons = seasons or SEASONS
    key = api_key or _api_key()
    if not key:
        logger.warning("Skipping SP+ cache warm — CFBD_API_KEY not set")
        return 0

    count = 0
    for season in target_seasons:
        weeks = regular_season_weeks(season, api_key=key)
        for week in weeks:
            fetch_and_cache_sp_plus_week(season, week, api_key=key, force=force)
            count += 1
            time.sleep(REQUEST_SLEEP_SECONDS)

        files = week_files_for_season(season)
        report = audit_season_week_files(season, files) if files else {
            "season": season,
            "weekly_mode": "flat",
            "last_confirmed_week": None,
            "leakage_confirmed": True,
        }
        if report.get("leakage_confirmed"):
            fetch_and_cache_preseason_sp(season, api_key=key, force=force)
            count += 1
            time.sleep(REQUEST_SLEEP_SECONDS)
        _write_meta(season, report)

    return count


def load_sp_plus_store(seasons: tuple[int, ...] | None = None) -> SPPlusStore:
    store = SPPlusStore()
    if not SP_PLUS_CACHE_DIR.exists():
        return store

    target = set(seasons) if seasons else None

    for path in sorted(SP_PLUS_CACHE_DIR.glob("*_preseason.json")):
        try:
            season = int(path.stem.split("_preseason")[0])
        except ValueError:
            continue
        if target is not None and season not in target:
            continue
        for team, sp in _load_cache_file(path).items():
            store.preseason[(season, team)] = sp

    for path in sorted(SP_PLUS_CACHE_DIR.glob("*_week_*.json")):
        parsed = _parse_week_path(path)
        if parsed is None:
            continue
        season, week = parsed
        if target is not None and season not in target:
            continue
        for team, sp in _load_cache_file(path).items():
            store.weekly[(season, week, team)] = sp

    season_ids = target if target is not None else {
        s for s, _ in store.weekly.keys()
    } | {s for s, _ in store.preseason.keys()}

    for season in season_ids:
        meta = _read_meta(season)
        if meta:
            store.weekly_mode[season] = meta.get("weekly_mode", "flat")
            store.last_confirmed_week[season] = meta.get("last_confirmed_week")
        else:
            files = week_files_for_season(season)
            if files:
                report = audit_season_week_files(season, files)
                store.weekly_mode[season] = report["weekly_mode"]
                store.last_confirmed_week[season] = report["last_confirmed_week"]
            else:
                store.weekly_mode[season] = "flat"
                store.last_confirmed_week[season] = None

    return store


def load_sp_plus_lookup(seasons: tuple[int, ...] | None = None) -> SPPlusStore:
    """Backward-compatible alias — returns SPPlusStore for feature building."""
    return load_sp_plus_store(seasons)


def resolve_game_week(
    game_date: str | date,
    season: int,
    *,
    week: int | None = None,
    api_key: str | None = None,
) -> int:
    del api_key
    if week is not None:
        try:
            wk = int(week)
            if wk > 0:
                return wk
        except (TypeError, ValueError):
            pass
    if isinstance(game_date, str):
        game_day = date.fromisoformat(game_date[:10])
    else:
        game_day = game_date
    calendar = load_cfbd_calendar(season)
    for entry in calendar:
        if str(entry.get("seasonType") or "regular").lower() != "regular":
            continue
        start_raw = str(entry.get("firstGameStart") or "")[:10]
        end_raw = str(entry.get("lastGameStart") or "")[:10]
        wk = entry.get("week")
        if not start_raw or not end_raw or wk is None:
            continue
        try:
            start = date.fromisoformat(start_raw)
            end = date.fromisoformat(end_raw)
        except ValueError:
            continue
        if start <= game_day <= end:
            return int(wk)
    return 1


def _lookup_team_sp(
    store: SPPlusStore,
    season: int,
    week: int,
    team: str,
) -> TeamSPPlus | None:
    return store.weekly.get((season, week, team))


def sp_plus_diffs_for_game(
    *,
    season: int,
    game_week: int,
    home_team: str,
    away_team: str,
    lookup: SPPlusStore,
) -> tuple[float, float, float]:
    """Safe SP+ diffs — never uses end-of-season ratings for early-season games when flat."""
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    pg_week = pregame_sp_week(game_week)
    mode = lookup.weekly_mode.get(season, "flat")

    home_sp: TeamSPPlus | None = None
    away_sp: TeamSPPlus | None = None

    if mode == "ok":
        home_sp = _lookup_team_sp(lookup, season, pg_week, home)
        away_sp = _lookup_team_sp(lookup, season, pg_week, away)
    elif int(game_week) == 1:
        home_sp = lookup.preseason.get((season, home))
        away_sp = lookup.preseason.get((season, away))
    else:
        last_w = lookup.last_confirmed_week.get(season)
        if last_w is not None and pg_week > 0 and pg_week <= last_w:
            home_sp = _lookup_team_sp(lookup, season, pg_week, home)
            away_sp = _lookup_team_sp(lookup, season, pg_week, away)

    if home_sp is None or away_sp is None:
        return 0.0, 0.0, 0.0
    return (
        home_sp.overall - away_sp.overall,
        home_sp.offense - away_sp.offense,
        home_sp.defense - away_sp.defense,
    )
