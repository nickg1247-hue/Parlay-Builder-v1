"""CFB sportsbook totals (O/U) from CollegeFootballData.com lines API."""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

CFBD_BASE_URL = "https://api.collegefootballdata.com"
LINES_CACHE_DIR = PROJECT_ROOT / "data" / "processed" / "cfb_lines_cache"
CALENDAR_CACHE_DIR = PROJECT_ROOT / "data" / "processed" / "cfb_calendar_cache"
REQUEST_SLEEP_SECONDS = 0.6


def cfb_season_end_year(game_date: date) -> int:
    return game_date.year if game_date.month >= 8 else game_date.year - 1


def _api_key() -> str | None:
    key = (os.getenv("CFBD_API_KEY") or "").strip()
    return key or None


def _parse_cfbd_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return date.fromisoformat(raw[:10]) if len(raw) >= 10 else None


def _median_ou(lines: list[dict[str, Any]]) -> float | None:
    values: list[float] = []
    for row in lines:
        ou = row.get("overUnder")
        if ou is None:
            continue
        try:
            values.append(float(ou))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    med = float(statistics.median(values))
    rounded = round(med * 2) / 2
    if rounded == int(rounded):
        rounded += 0.5
    return rounded


def matchup_ou_line(
    home_pts_for: float,
    away_pts_for: float,
    home_pts_against: float,
    away_pts_against: float,
) -> float:
    """Pregame total proxy from season scoring averages (varies by matchup)."""
    home_exp = (float(home_pts_for) + float(away_pts_against)) / 2.0
    away_exp = (float(away_pts_for) + float(home_pts_against)) / 2.0
    total = home_exp + away_exp
    line = round(total * 2) / 2
    if line == int(line):
        line += 0.5
    return float(line)


def _fetch_json(
    client: httpx.Client,
    path: str,
    params: dict[str, str],
    *,
    api_key: str,
) -> list[dict[str, Any]]:
    response = client.get(
        f"{CFBD_BASE_URL}/{path.lstrip('/')}",
        params=params,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if response.status_code == 401:
        logger.warning("CFBD lines API unauthorized — check CFBD_API_KEY")
        return []
    response.raise_for_status()
    data = response.json()
    return list(data) if isinstance(data, list) else []


def _calendar_cache_path(season: int) -> Path:
    CALENDAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CALENDAR_CACHE_DIR / f"{season}.json"


def load_cfbd_calendar(season: int, *, api_key: str | None = None) -> list[dict[str, Any]]:
    path = _calendar_cache_path(season)
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
        except (json.JSONDecodeError, OSError):
            pass
    key = api_key or _api_key()
    if not key:
        return []
    try:
        with httpx.Client(timeout=45.0) as client:
            rows = _fetch_json(client, "calendar", {"year": str(season)}, api_key=key)
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return rows
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("CFBD calendar fetch failed for %s: %s", season, exc)
        return []


def resolve_season_week(game_date: date, calendar: list[dict[str, Any]]) -> tuple[str, int] | None:
    for entry in calendar:
        start = _parse_cfbd_date(str(entry.get("firstGameStart") or ""))
        end = _parse_cfbd_date(str(entry.get("lastGameStart") or ""))
        week = entry.get("week")
        season_type = entry.get("seasonType") or "regular"
        if start is None or end is None or week is None:
            continue
        if start <= game_date <= end:
            return str(season_type), int(week)
    return None


def _lines_cache_path(game_date: date) -> Path:
    LINES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return LINES_CACHE_DIR / f"{game_date.isoformat()}.json"


def _load_lines_cache(game_date: date) -> dict[str, float] | None:
    path = _lines_cache_path(game_date)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("lines"):
            return {str(k): float(v) for k, v in raw["lines"].items()}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return None


def _write_lines_cache(
    game_date: date,
    lines: dict[str, float],
    *,
    source: str,
    meta: dict[str, Any] | None = None,
) -> None:
    payload = {
        "date": game_date.isoformat(),
        "source": source,
        "lines": lines,
        "meta": meta or {},
    }
    _lines_cache_path(game_date).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def fetch_cfbd_lines_for_week(
    season: int,
    week: int,
    *,
    season_type: str = "regular",
    api_key: str | None = None,
) -> dict[str, float]:
    key = api_key or _api_key()
    if not key:
        return {}
    params = {
        "year": str(season),
        "week": str(week),
        "seasonType": season_type,
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            games = _fetch_json(client, "lines", params, api_key=key)
        time.sleep(REQUEST_SLEEP_SECONDS)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("CFBD lines fetch failed (%s w%s): %s", season, week, exc)
        return {}

    out: dict[str, float] = {}
    for game in games:
        gid = str(game.get("id") or "")
        if not gid:
            continue
        ou = _median_ou(game.get("lines") or [])
        if ou is not None:
            out[gid] = ou
    return out


def fetch_cfbd_lines_for_game_ids(
    game_ids: list[str],
    *,
    api_key: str | None = None,
) -> dict[str, float]:
    key = api_key or _api_key()
    if not key or not game_ids:
        return {}
    out: dict[str, float] = {}
    try:
        with httpx.Client(timeout=45.0) as client:
            for gid in game_ids:
                games = _fetch_json(client, "lines", {"gameId": str(gid)}, api_key=key)
                if not games:
                    continue
                ou = _median_ou((games[0].get("lines") or []))
                if ou is not None:
                    out[str(gid)] = ou
                time.sleep(REQUEST_SLEEP_SECONDS)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("CFBD per-game lines fetch failed: %s", exc)
    return out


def get_book_ou_lines_for_date(
    game_date: date,
    game_ids: list[str] | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, float]:
    """Return game_id -> consensus O/U line for a slate date (cached)."""
    if not force_refresh:
        cached = _load_lines_cache(game_date)
        if cached is not None:
            if game_ids is None:
                return cached
            wanted = {str(g) for g in game_ids}
            return {gid: line for gid, line in cached.items() if gid in wanted}

    key = _api_key()
    if not key:
        return {}

    season = cfb_season_end_year(game_date)
    calendar = load_cfbd_calendar(season, api_key=key)
    resolved = resolve_season_week(game_date, calendar) if calendar else None

    lines: dict[str, float] = {}
    meta: dict[str, Any] = {"season": season}
    if resolved:
        season_type, week = resolved
        meta.update({"season_type": season_type, "week": week})
        lines = fetch_cfbd_lines_for_week(
            season, week, season_type=season_type, api_key=key
        )
    elif game_ids:
        lines = fetch_cfbd_lines_for_game_ids(game_ids, api_key=key)
        meta["fallback"] = "game_id"

    if game_ids:
        wanted = {str(g) for g in game_ids}
        missing = [gid for gid in wanted if gid not in lines]
        if missing and resolved:
            extra = fetch_cfbd_lines_for_game_ids(missing, api_key=key)
            lines.update(extra)

    if lines:
        _write_lines_cache(game_date, lines, source="cfbd", meta=meta)
    return lines if game_ids is None else {gid: lines[gid] for gid in map(str, game_ids) if gid in lines}


def attach_matchup_ou_lines(df) -> dict[str, float]:
    """Fallback O/U when sportsbook lines are unavailable."""
    from app.features.cfb_totals_pregame import build_totals_features_for_slate

    if df.empty:
        return {}
    prepared = build_totals_features_for_slate(df)
    out: dict[str, float] = {}
    for row in prepared.itertuples(index=False):
        gid = str(row.game_id)
        out[gid] = matchup_ou_line(
            row.home_season_pts_for,
            row.away_season_pts_for,
            row.home_season_pts_against,
            row.away_season_pts_against,
        )
    return out


def resolve_ou_lines_for_slate(
    df,
    game_date: date,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return (all lines, book lines only) for slate game ids."""
    game_ids = [str(g) for g in df["game_id"].astype(str).tolist()]
    book = get_book_ou_lines_for_date(game_date, game_ids)
    fallback = attach_matchup_ou_lines(df)
    merged = dict(fallback)
    merged.update(book)
    return merged, book
