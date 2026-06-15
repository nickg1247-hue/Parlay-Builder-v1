"""CFB sportsbook lines (spread + O/U) from CollegeFootballData.com lines API."""

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
from app.odds.cfb_game_match import attach_cfbd_ids_to_slate, build_cfbd_lines_index, match_key
from app.odds.cfb_team_aliases import normalize_team_name
from app.odds.odds_repository import _median_float, _median_int
from app.odds.team_aliases import is_valid_american_odds

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


def _median_spread(lines: list[dict[str, Any]]) -> float | None:
    values: list[float] = []
    for row in lines:
        sp = row.get("spread")
        if sp is None:
            continue
        try:
            values.append(float(sp))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return _median_float(values)


def _median_moneylines(
    lines: list[dict[str, Any]],
) -> tuple[int | None, int | None]:
    home_prices: list[int] = []
    away_prices: list[int] = []
    for row in lines:
        hm = row.get("homeMoneyline")
        am = row.get("awayMoneyline")
        if hm is not None and is_valid_american_odds(hm):
            home_prices.append(int(hm))
        if am is not None and is_valid_american_odds(am):
            away_prices.append(int(am))
    return _median_int(home_prices), _median_int(away_prices)


def _parse_cfbd_line_game(game: dict[str, Any]) -> dict[str, Any] | None:
    gid = game.get("id")
    if gid is None:
        return None
    home = normalize_team_name(str(game.get("homeTeam") or game.get("home_team") or ""))
    away = normalize_team_name(str(game.get("awayTeam") or game.get("away_team") or ""))
    if not home or not away:
        return None
    start = game.get("startDate") or game.get("date") or ""
    game_date = _parse_cfbd_date(str(start))
    if game_date is None:
        return None
    provider_lines = game.get("lines") or []
    ou = _median_ou(provider_lines)
    home_spread = _median_spread(provider_lines)
    home_ml, away_ml = _median_moneylines(provider_lines)
    return {
        "cfbd_game_id": str(gid),
        "game_id": str(gid),
        "game_date": game_date.isoformat(),
        "date": game_date.isoformat(),
        "home_team": home,
        "away_team": away,
        "ou_line": ou,
        "home_spread_point": home_spread,
        "home_ml": home_ml,
        "away_ml": away_ml,
    }


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


def _load_lines_cache(game_date: date) -> list[dict[str, Any]] | None:
    path = _lines_cache_path(game_date)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("games"):
            return list(raw["games"])
        # Legacy cache: {lines: {cfbd_id: ou}}
        if isinstance(raw, dict) and raw.get("lines"):
            legacy: list[dict[str, Any]] = []
            for gid, ou in raw["lines"].items():
                legacy.append(
                    {
                        "cfbd_game_id": str(gid),
                        "game_id": str(gid),
                        "ou_line": float(ou),
                    }
                )
            return legacy
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return None


def _write_lines_cache(
    game_date: date,
    games: list[dict[str, Any]],
    *,
    source: str,
    meta: dict[str, Any] | None = None,
) -> None:
    payload = {
        "date": game_date.isoformat(),
        "source": source,
        "games": games,
        "meta": meta or {},
    }
    _lines_cache_path(game_date).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def fetch_cfbd_lines_for_week(
    season: int,
    week: int,
    *,
    season_type: str = "regular",
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    key = api_key or _api_key()
    if not key:
        return []
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
        return []

    out: list[dict[str, Any]] = []
    for game in games:
        parsed = _parse_cfbd_line_game(game)
        if parsed is not None:
            out.append(parsed)
    return out


def fetch_cfbd_lines_for_game_ids(
    game_ids: list[str],
    *,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    key = api_key or _api_key()
    if not key or not game_ids:
        return []
    out: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=45.0) as client:
            for gid in game_ids:
                games = _fetch_json(client, "lines", {"gameId": str(gid)}, api_key=key)
                if not games:
                    continue
                parsed = _parse_cfbd_line_game(games[0])
                if parsed is not None:
                    out.append(parsed)
                time.sleep(REQUEST_SLEEP_SECONDS)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.warning("CFBD per-game lines fetch failed: %s", exc)
    return out


def get_cfbd_book_games_for_date(
    game_date: date,
    *,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Return parsed CFBD line rows for games on *game_date* (cached)."""
    iso = game_date.isoformat()
    if not force_refresh:
        cached = _load_lines_cache(game_date)
        if cached is not None:
            dated = [
                g
                for g in cached
                if str(g.get("game_date") or g.get("date") or iso)[:10] == iso
            ]
            return dated if dated else cached

    key = _api_key()
    if not key:
        stale = _load_lines_cache(game_date)
        return stale or []

    season = cfb_season_end_year(game_date)
    calendar = load_cfbd_calendar(season, api_key=key)
    resolved = resolve_season_week(game_date, calendar) if calendar else None

    games: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"season": season}
    if resolved:
        season_type, week = resolved
        meta.update({"season_type": season_type, "week": week})
        week_games = fetch_cfbd_lines_for_week(
            season, week, season_type=season_type, api_key=key
        )
        iso = game_date.isoformat()
        games = [g for g in week_games if str(g.get("game_date", ""))[:10] == iso]
        if not games:
            games = week_games
    if games:
        _write_lines_cache(game_date, games, source="cfbd", meta=meta)
    return games


def _map_cfbd_to_slate(
    slate_df,
    cfbd_games: list[dict[str, Any]],
) -> tuple[dict[str, float], dict[str, float]]:
    """Map CFBD lines to ESPN slate game_ids via team + date crosswalk."""
    index = build_cfbd_lines_index(cfbd_games)

    book_ou: dict[str, float] = {}
    spread_by_gid: dict[str, float] = {}

    for row in slate_df.itertuples(index=False):
        gid = str(row.game_id)
        key = match_key(str(row.date)[:10], row.home_team, row.away_team)
        hit = index.get(key)
        if not hit:
            continue
        ou = hit.get("ou_line")
        if ou is not None:
            book_ou[gid] = float(ou)
        sp = hit.get("home_spread_point")
        if sp is not None:
            spread_by_gid[gid] = float(sp)

    return book_ou, spread_by_gid


def get_book_ou_lines_for_date(
    game_date: date,
    game_ids: list[str] | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, float]:
    """Legacy: CFBD game_id -> O/U (not slate game_id). Prefer resolve_lines_for_slate."""
    games = get_cfbd_book_games_for_date(game_date, force_refresh=force_refresh)
    out = {
        str(g["cfbd_game_id"]): float(g["ou_line"])
        for g in games
        if g.get("ou_line") is not None and g.get("cfbd_game_id")
    }
    if game_ids is None:
        return out
    wanted = {str(g) for g in game_ids}
    return {gid: line for gid, line in out.items() if gid in wanted}


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


def resolve_lines_for_slate(
    df,
    game_date: date,
    *,
    force_refresh: bool = False,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """
    Return (merged_ou, spread_by_slate_gid, book_ou_by_slate_gid).

    Merged O/U prefers CFBD book line over matchup proxy. Spread is book-only
    (no proxy). Keys are ESPN slate game_id after crosswalk.
    """
    cfbd_games = get_cfbd_book_games_for_date(game_date, force_refresh=force_refresh)
    book_ou, spread_by_gid = _map_cfbd_to_slate(df, cfbd_games)

    fallback = attach_matchup_ou_lines(df)
    merged_ou = dict(fallback)
    merged_ou.update(book_ou)

    return merged_ou, spread_by_gid, book_ou


def resolve_ou_lines_for_slate(
    df,
    game_date: date,
) -> tuple[dict[str, float], dict[str, float]]:
    """Backward-compatible wrapper: (merged_ou, book_ou)."""
    merged, _spread, book = resolve_lines_for_slate(df, game_date)
    return merged, book


def load_cfbd_holdout_lines(
    holdout_dates: set[str] | None = None,
) -> "pd.DataFrame":
    """Load CFBD spread/O/U/ML rows for market eval (cache + optional fetch)."""
    import pandas as pd

    rows: list[dict[str, Any]] = []
    if LINES_CACHE_DIR.exists():
        for path in sorted(LINES_CACHE_DIR.glob("*.json")):
            iso = path.stem
            if holdout_dates is not None and iso not in holdout_dates:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for game in payload.get("games") or []:
                gd = str(game.get("game_date") or game.get("date") or iso)[:10]
                if holdout_dates is not None and gd not in holdout_dates:
                    continue
                rows.append(
                    {
                        "date": gd,
                        "home_team": normalize_team_name(game.get("home_team", "")),
                        "away_team": normalize_team_name(game.get("away_team", "")),
                        "home_ml": game.get("home_ml"),
                        "away_ml": game.get("away_ml"),
                        "home_spread_point": game.get("home_spread_point"),
                        "ou_line": game.get("ou_line"),
                        "odds_source": "cfbd_lines",
                    }
                )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    ).reset_index(drop=True)
