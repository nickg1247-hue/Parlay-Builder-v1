"""Persistent on-disk Odds API repository with quota-gated HTTP."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import httpx

from app.config import PROJECT_ROOT
from app.odds.live_odds import live_odds_enabled
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name
from app.odds.the_odds_api import (
    DEFAULT_MLB_PROP_MARKETS,
    estimate_odds_api_credits,
    fetch_historical_mlb_odds,
    fetch_live_mlb_odds,
    fetch_mlb_event_odds,
    fetch_mlb_events,
    prop_regions,
)

logger = logging.getLogger(__name__)

DEFAULT_REPO_DIR = PROJECT_ROOT / "data" / "processed" / "odds_repository"

_lock = threading.Lock()
_date_fetch_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_last_fetch_meta: dict[str, Any] = {}
_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def min_refresh_seconds() -> int:
    """Minimum seconds between live HTTP refreshes for the same date (default 5 min)."""
    return max(60, int(os.getenv("ODDS_REPO_MIN_REFRESH_SECONDS", "300")))


def reset_fetch_locks_for_tests() -> None:
    """Clear per-date fetch locks (tests only)."""
    global _date_fetch_locks
    with _locks_guard:
        _date_fetch_locks = {}


def _lock_for_date(iso_date: str) -> threading.Lock:
    with _locks_guard:
        if iso_date not in _date_fetch_locks:
            _date_fetch_locks[iso_date] = threading.Lock()
        return _date_fetch_locks[iso_date]


def _payload_age_seconds(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    fetched = payload.get("fetched_at")
    if not fetched:
        return None
    try:
        ts = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (_clock() - ts).total_seconds()
    except (TypeError, ValueError):
        return None


def repository_age_seconds(game_date: date) -> float | None:
    """Seconds since repository snapshot was written, or None if missing."""
    return _payload_age_seconds(load_date(game_date))


def _repository_fresh_enough(game_date: date, min_seconds: int | None = None) -> bool:
    age = repository_age_seconds(game_date)
    if age is None:
        return False
    limit = min_seconds if min_seconds is not None else min_refresh_seconds()
    return age < limit


def set_clock_for_tests(fn: Callable[[], datetime] | None) -> None:
    """Override UTC clock (tests only)."""
    global _clock
    _clock = fn or (lambda: datetime.now(timezone.utc))


def last_fetch_meta() -> dict[str, Any]:
    return dict(_last_fetch_meta)


def _set_fetch_meta(**kwargs: Any) -> None:
    global _last_fetch_meta
    _last_fetch_meta = kwargs


def _repo_root() -> Path:
    override = os.getenv("ODDS_REPOSITORY_DIR", "").strip()
    if override:
        return Path(override)
    return DEFAULT_REPO_DIR


def repository_path(game_date: date) -> Path:
    return _repo_root() / f"{game_date.isoformat()}.json"


def index_path() -> Path:
    return _repo_root() / "index.json"


def quota_path() -> Path:
    return _repo_root() / "quota.json"


def quota_limits() -> tuple[int, int]:
    """HTTP call caps (secondary guardrail; credit caps are the main budget)."""
    return (
        int(os.getenv("ODDS_API_MAX_PER_HOUR", "25")),
        int(os.getenv("ODDS_API_MAX_PER_DAY", "100")),
    )


def quota_credit_limits() -> tuple[int, int]:
    """
    The Odds API bills event-odds as markets × regions; board calls ≈ 3 credits.

    Defaults target ~20k credits/month: ~650/day × 30 ≈ 19.5k (see .env.example).
    """
    return (
        int(os.getenv("ODDS_API_MAX_CREDITS_PER_HOUR", "200")),
        int(os.getenv("ODDS_API_MAX_CREDITS_PER_DAY", "650")),
    )


def has_date(game_date: date) -> bool:
    return repository_path(game_date).exists()


def load_date(game_date: date) -> dict[str, Any] | None:
    path = repository_path(game_date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read odds repository %s: %s", path, exc)
        return None


def _default_quota() -> dict[str, Any]:
    now = _clock()
    day = now.strftime("%Y-%m-%d")
    hour_bucket = now.strftime("%Y-%m-%dT%H")
    return {
        "day": day,
        "day_count": 0,
        "day_credits": 0,
        "hour_bucket": hour_bucket,
        "hour_count": 0,
        "hour_credits": 0,
        "last_call_at": None,
        "last_denied_at": None,
        "last_denied_reason": None,
    }


def _roll_quota(quota: dict[str, Any]) -> dict[str, Any]:
    now = _clock()
    day = now.strftime("%Y-%m-%d")
    hour_bucket = now.strftime("%Y-%m-%dT%H")
    if quota.get("day") != day:
        quota["day"] = day
        quota["day_count"] = 0
        quota["day_credits"] = 0
    if quota.get("hour_bucket") != hour_bucket:
        quota["hour_bucket"] = hour_bucket
        quota["hour_count"] = 0
        quota["hour_credits"] = 0
    quota.setdefault("day_credits", 0)
    quota.setdefault("hour_credits", 0)
    return quota


def load_quota() -> dict[str, Any]:
    path = quota_path()
    if not path.exists():
        return _default_quota()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _roll_quota(data)
    except (json.JSONDecodeError, OSError):
        return _default_quota()


def save_quota(quota: dict[str, Any]) -> None:
    root = _repo_root()
    root.mkdir(parents=True, exist_ok=True)
    quota_path().write_text(json.dumps(quota, indent=2), encoding="utf-8")


def get_quota_summary() -> dict[str, Any]:
    max_hour, max_day = quota_limits()
    max_hour_credits, max_day_credits = quota_credit_limits()
    with _lock:
        q = load_quota()
    return {
        "day": q.get("day"),
        "hour_bucket": q.get("hour_bucket"),
        "hour_count": q.get("hour_count", 0),
        "hour_max": max_hour,
        "hour_credits": q.get("hour_credits", 0),
        "hour_credits_max": max_hour_credits,
        "day_count": q.get("day_count", 0),
        "day_max": max_day,
        "day_credits": q.get("day_credits", 0),
        "day_credits_max": max_day_credits,
        "denied": False,
        "last_denied_at": q.get("last_denied_at"),
        "last_denied_reason": q.get("last_denied_reason"),
        "timezone": "UTC",
    }


def _try_acquire_quota_slot(*, credit_cost: int = 1) -> tuple[bool, str | None]:
    max_hour, max_day = quota_limits()
    max_hour_credits, max_day_credits = quota_credit_limits()
    cost = max(1, int(credit_cost))
    with _lock:
        q = _roll_quota(load_quota())
        hour_credits = int(q.get("hour_credits", 0))
        day_credits = int(q.get("day_credits", 0))
        if q["hour_count"] >= max_hour:
            q["last_denied_at"] = _clock().isoformat()
            q["last_denied_reason"] = "hour_limit"
            save_quota(q)
            logger.warning(
                "Odds API denied: hour_limit (%s/%s) bucket=%s",
                q["hour_count"],
                max_hour,
                q["hour_bucket"],
            )
            return False, "hour_limit"
        if hour_credits + cost > max_hour_credits:
            q["last_denied_at"] = _clock().isoformat()
            q["last_denied_reason"] = "hour_credit_limit"
            save_quota(q)
            logger.warning(
                "Odds API denied: hour_credit_limit (%s+%s>%s) bucket=%s",
                hour_credits,
                cost,
                max_hour_credits,
                q["hour_bucket"],
            )
            return False, "hour_credit_limit"
        if q["day_count"] >= max_day:
            q["last_denied_at"] = _clock().isoformat()
            q["last_denied_reason"] = "day_limit"
            save_quota(q)
            logger.warning(
                "Odds API denied: day_limit (%s/%s) day=%s",
                q["day_count"],
                max_day,
                q["day"],
            )
            return False, "day_limit"
        if day_credits + cost > max_day_credits:
            q["last_denied_at"] = _clock().isoformat()
            q["last_denied_reason"] = "day_credit_limit"
            save_quota(q)
            logger.warning(
                "Odds API denied: day_credit_limit (%s+%s>%s) day=%s",
                day_credits,
                cost,
                max_day_credits,
                q["day"],
            )
            return False, "day_credit_limit"
        q["hour_count"] += 1
        q["day_count"] += 1
        q["hour_credits"] = hour_credits + cost
        q["day_credits"] = day_credits + cost
        q["last_call_at"] = _clock().isoformat()
        q["last_denied_reason"] = None
        save_quota(q)
        return True, None


def _release_quota_slot(*, credit_cost: int = 1) -> None:
    cost = max(1, int(credit_cost))
    with _lock:
        q = _roll_quota(load_quota())
        q["hour_count"] = max(0, int(q.get("hour_count", 0)) - 1)
        q["day_count"] = max(0, int(q.get("day_count", 0)) - 1)
        q["hour_credits"] = max(0, int(q.get("hour_credits", 0)) - cost)
        q["day_credits"] = max(0, int(q.get("day_credits", 0)) - cost)
        save_quota(q)


@dataclass
class ApiFetchResult:
    events: list[dict[str, Any]] | None = None
    source: str | None = None
    denied: bool = False
    denied_reason: str | None = None
    error: str | None = None
    credit_cost: int = 1


def _historical_snapshot_date(game_date: date) -> str:
    return f"{game_date.isoformat()}T23:59:00Z"


def _do_http_fetch(
    game_date: date,
    *,
    include_totals: bool,
    include_spreads: bool,
) -> tuple[list[dict[str, Any]], str]:
    """Low-level HTTP — only call from fetch_from_api_if_allowed."""
    if game_date >= date.today():
        events = fetch_live_mlb_odds(
            include_totals=include_totals,
            include_spreads=include_spreads,
        )
        return events or [], "the_odds_api_live"

    snapshot = _historical_snapshot_date(game_date)
    events = fetch_historical_mlb_odds(
        snapshot_date=snapshot,
        include_totals=include_totals,
        include_spreads=include_spreads,
    )
    return events or [], "the_odds_api_historical"


def fetch_from_api_if_allowed(
    game_date: date,
    *,
    include_totals: bool = True,
    include_spreads: bool = True,
) -> ApiFetchResult:
    """
    Central gate for all Odds API HTTP calls.

    Reserves a quota slot before HTTP; releases slot on failure (no credit charged).
    """
    if not live_odds_enabled():
        return ApiFetchResult(denied=True, denied_reason="live_odds_disabled")

    allowed, deny_reason = _try_acquire_quota_slot()
    if not allowed:
        return ApiFetchResult(denied=True, denied_reason=deny_reason)

    try:
        events, source = _do_http_fetch(
            game_date,
            include_totals=include_totals,
            include_spreads=include_spreads,
        )
        return ApiFetchResult(events=events, source=source)
    except Exception as exc:
        _release_quota_slot()
        logger.warning("Odds API HTTP failed for %s: %s", game_date.isoformat(), exc)
        return ApiFetchResult(error=str(exc))


def _load_index() -> dict[str, Any]:
    path = index_path()
    if not path.exists():
        return {"dates": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "dates" not in data:
            data["dates"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return {"dates": []}


def _write_index(index: dict[str, Any]) -> None:
    root = _repo_root()
    root.mkdir(parents=True, exist_ok=True)
    index_path().write_text(json.dumps(index, indent=2), encoding="utf-8")


def _update_index(
    game_date: date,
    payload: dict[str, Any],
    *,
    api_fetch: bool = False,
) -> None:
    iso = game_date.isoformat()
    games = payload.get("games") or []
    entry = {
        "date": iso,
        "fetched_at": payload.get("fetched_at"),
        "source": payload.get("source"),
        "games_matched": len(games),
        "api_fetch_count": 0,
    }
    with _lock:
        index = _load_index()
        existing = next((d for d in index["dates"] if d.get("date") == iso), None)
        if existing:
            entry["api_fetch_count"] = existing.get("api_fetch_count", 0)
            if api_fetch:
                entry["api_fetch_count"] += 1
            index["dates"] = [d for d in index["dates"] if d.get("date") != iso]
        elif api_fetch:
            entry["api_fetch_count"] = 1
        index["dates"].append(entry)
        index["dates"].sort(key=lambda d: d.get("date") or "", reverse=True)
        _write_index(index)


def save_date(
    game_date: date,
    payload: dict[str, Any],
    *,
    api_fetch: bool = False,
) -> None:
    root = _repo_root()
    root.mkdir(parents=True, exist_ok=True)
    repository_path(game_date).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    _update_index(game_date, payload, api_fetch=api_fetch)


def clear_repository(tmp_root: Path | None = None) -> None:
    """Remove repository + quota files (tests only)."""
    root = tmp_root or _repo_root()
    if root.exists():
        for f in root.glob("*.json"):
            f.unlink()


def _median_int(values: list[int]) -> int | None:
    if not values:
        return None
    return int(pd.Series(values).median())


def _median_float(values: list[float]) -> float | None:
    if not values:
        return None
    return float(pd.Series(values).median())


def normalize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    for event in events:
        home = normalize_team_name(event.get("home_team", ""))
        away = normalize_team_name(event.get("away_team", ""))
        if not home or not away:
            continue

        home_ml_prices: list[int] = []
        away_ml_prices: list[int] = []
        total_lines: list[float] = []
        over_prices: list[int] = []
        under_prices: list[int] = []
        home_spread_points: list[float] = []
        home_spread_prices: list[int] = []
        away_spread_points: list[float] = []
        away_spread_prices: list[int] = []

        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                key = market.get("key")
                if key == "h2h":
                    prices = {
                        normalize_team_name(o["name"]): int(o["price"])
                        for o in market.get("outcomes", [])
                    }
                    if home in prices and away in prices:
                        if is_valid_american_odds(prices[home]) and is_valid_american_odds(
                            prices[away]
                        ):
                            home_ml_prices.append(prices[home])
                            away_ml_prices.append(prices[away])
                elif key == "totals":
                    over_point = over_price = under_price = None
                    for outcome in market.get("outcomes", []):
                        name = (outcome.get("name") or "").lower()
                        if name == "over":
                            over_point = outcome.get("point")
                            over_price = outcome.get("price")
                        elif name == "under":
                            under_price = outcome.get("price")
                    if (
                        over_point is not None
                        and over_price is not None
                        and under_price is not None
                        and is_valid_american_odds(over_price)
                        and is_valid_american_odds(under_price)
                    ):
                        total_lines.append(float(over_point))
                        over_prices.append(int(over_price))
                        under_prices.append(int(under_price))
                elif key == "spreads":
                    hp = ap = None
                    hpr = apr = None
                    for outcome in market.get("outcomes", []):
                        team = normalize_team_name(outcome.get("name", ""))
                        point = outcome.get("point")
                        price = outcome.get("price")
                        if point is None or price is None:
                            continue
                        if not is_valid_american_odds(price):
                            continue
                        if team == home:
                            hp, hpr = float(point), int(price)
                        elif team == away:
                            ap, apr = float(point), int(price)
                    if hp is not None and hpr is not None:
                        home_spread_points.append(hp)
                        home_spread_prices.append(hpr)
                    if ap is not None and apr is not None:
                        away_spread_points.append(ap)
                        away_spread_prices.append(apr)

        if not home_ml_prices and not total_lines and not home_spread_points:
            continue

        games.append(
            {
                "home_team": home,
                "away_team": away,
                "commence_time": event.get("commence_time"),
                "home_ml": _median_int(home_ml_prices),
                "away_ml": _median_int(away_ml_prices),
                "ou_line": _median_float(total_lines),
                "over_odds": _median_int(over_prices),
                "under_odds": _median_int(under_prices),
                "home_spread_point": _median_float(home_spread_points),
                "home_spread_american": _median_int(home_spread_prices),
                "away_spread_point": _median_float(away_spread_points),
                "away_spread_american": _median_int(away_spread_prices),
            }
        )
    return games


def find_game(
    games: list[dict[str, Any]],
    home_team: str,
    away_team: str,
) -> dict[str, Any] | None:
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    for g in games:
        if g.get("home_team") == home and g.get("away_team") == away:
            return g
    return None


def games_to_ml_dataframe(
    games: list[dict[str, Any]],
    source: str,
    game_date: date | None = None,
) -> pd.DataFrame:
    board_source = _board_odds_source(source)
    fallback_date = game_date.isoformat() if game_date else None
    rows: list[dict[str, Any]] = []
    for g in games:
        if g.get("home_ml") is None and g.get("away_ml") is None:
            continue
        commence = g.get("commence_time") or ""
        row_date = (
            commence[:10]
            if isinstance(commence, str) and len(commence) >= 10
            else fallback_date
        )
        rows.append(
            {
                "date": row_date,
                "home_team": g["home_team"],
                "away_team": g["away_team"],
                "home_ml": g.get("home_ml"),
                "away_ml": g.get("away_ml"),
                "home_spread_point": g.get("home_spread_point"),
                "home_spread_american": g.get("home_spread_american"),
                "away_spread_point": g.get("away_spread_point"),
                "away_spread_american": g.get("away_spread_american"),
                "odds_source": board_source,
            }
        )
    return pd.DataFrame(rows)


def games_to_totals_dataframe(
    games: list[dict[str, Any]],
    game_date: date | None = None,
) -> pd.DataFrame:
    fallback_date = game_date.isoformat() if game_date else None
    rows: list[dict[str, Any]] = []
    for g in games:
        if g.get("ou_line") is None:
            continue
        commence = g.get("commence_time") or ""
        row_date = (
            commence[:10]
            if isinstance(commence, str) and len(commence) >= 10
            else fallback_date
        )
        rows.append(
            {
                "date": row_date,
                "home_team": g["home_team"],
                "away_team": g["away_team"],
                "ou_line": g.get("ou_line"),
                "over_odds": g.get("over_odds"),
                "under_odds": g.get("under_odds"),
            }
        )
    return pd.DataFrame(rows)


def _board_odds_source(repo_source: str) -> str:
    if repo_source in ("the_odds_api_live", "the_odds_api_historical", "the_odds_api"):
        return "the_odds_api"
    if repo_source == "csv_import":
        return "historical_cache"
    return repo_source


def _quota_warning_message(reason: str | None) -> str:
    max_hour, max_day = quota_limits()
    if reason == "hour_limit":
        return (
            f"Odds API hourly limit reached ({max_hour}/hour). "
            "Showing last saved lines."
        )
    if reason == "day_limit":
        return (
            f"Odds API daily limit reached ({max_day}/day UTC). "
            "Showing last saved lines."
        )
    return "Odds API call blocked. Showing last saved lines."


def _stale_from_repo(game_date: date) -> tuple[list[dict[str, Any]] | None, str]:
    payload = load_date(game_date)
    if payload is None:
        return None, "none"
    return payload.get("games", []), payload.get("source", "repository")


def get_mlb_odds_for_date(
    game_date: date,
    *,
    force_refresh: bool = False,
    include_totals: bool = True,
    include_spreads: bool = True,
    bypass_min_ttl: bool = False,
) -> tuple[list[dict[str, Any]] | None, str]:
    """
    Return normalized games for a date from repository or quota-gated API.

    Live HTTP is skipped when the on-disk snapshot is newer than
    ODDS_REPO_MIN_REFRESH_SECONDS unless bypass_min_ttl=True (board Run live).
    Concurrent callers for the same date share one in-flight HTTP request.
    """
    iso = game_date.isoformat()

    def _serve_repo() -> tuple[list[dict[str, Any]] | None, str]:
        payload = load_date(game_date)
        if payload is None:
            return None, "none"
        return payload.get("games", []), payload.get("source", "repository")

    _set_fetch_meta()

    if has_date(game_date) and not force_refresh:
        return _serve_repo()

    if (
        force_refresh
        and has_date(game_date)
        and not bypass_min_ttl
        and _repository_fresh_enough(game_date)
    ):
        age = _payload_age_seconds(load_date(game_date))
        logger.info(
            "Odds API skipped for %s: repository fresh (%.0fs < %ss min TTL)",
            iso,
            age or 0,
            min_refresh_seconds(),
        )
        wait = max(0, int(min_refresh_seconds() - (age or 0)))
        _set_fetch_meta(
            skipped_http=True,
            skip_reason="min_ttl",
            seconds_since_fetch=age,
            min_refresh_seconds=min_refresh_seconds(),
            quota_warning=(
                f"Using cached lines (saved {int(age or 0)}s ago). "
                f"Next live pull allowed in {wait}s."
            ),
        )
        return _serve_repo()

    if not live_odds_enabled():
        return None, "none"

    reason = (
        "force_refresh"
        if force_refresh and has_date(game_date)
        else "historical_backfill"
        if game_date < date.today()
        else "initial_fetch"
    )

    with _lock_for_date(iso):
        if has_date(game_date) and not force_refresh:
            return _serve_repo()
        if (
            force_refresh
            and has_date(game_date)
            and not bypass_min_ttl
            and _repository_fresh_enough(game_date)
        ):
            age = _payload_age_seconds(load_date(game_date))
            wait = max(0, int(min_refresh_seconds() - (age or 0)))
            _set_fetch_meta(
                skipped_http=True,
                skip_reason="min_ttl",
                seconds_since_fetch=age,
                min_refresh_seconds=min_refresh_seconds(),
                quota_warning=(
                    f"Using cached lines (saved {int(age or 0)}s ago). "
                    f"Next live pull allowed in {wait}s."
                ),
            )
            return _serve_repo()

        api_result = fetch_from_api_if_allowed(
            game_date,
            include_totals=include_totals,
            include_spreads=include_spreads,
        )

        if api_result.denied:
            warning = _quota_warning_message(api_result.denied_reason)
            _set_fetch_meta(
                quota_denied=True,
                denied_reason=api_result.denied_reason,
                quota_warning=warning,
            )
            if has_date(game_date):
                games, source = _stale_from_repo(game_date)
                logger.warning(
                    "Odds API denied for %s (%s) — using stale repository",
                    iso,
                    api_result.denied_reason,
                )
                return games, source
            return None, "none"

        if api_result.error or api_result.events is None:
            if has_date(game_date):
                logger.warning(
                    "Odds API failed for %s (%s) — using stale repository",
                    iso,
                    api_result.error,
                )
                return _stale_from_repo(game_date)
            return None, "none"

        games = normalize_events(api_result.events)
        fetched_at = _clock().isoformat()
        payload = {
            "date": iso,
            "fetched_at": fetched_at,
            "source": api_result.source,
            "games": games,
        }
        save_date(game_date, payload, api_fetch=True)
        logger.info(
            "Odds API call: date=%s reason=%s source=%s games=%d",
            iso,
            reason,
            api_result.source,
            len(games),
        )
        return games, api_result.source or "the_odds_api"


def _daily_board_generated_at(game_date: date) -> str | None:
    board_path = PROJECT_ROOT / "data" / "processed" / "daily_board.json"
    if not board_path.exists():
        return None
    try:
        board = json.loads(board_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if board.get("date") != game_date.isoformat():
        return None
    return board.get("generated_at")


def get_today_snapshot() -> dict[str, Any]:
    """Repository snapshot for today + quota summary (no API)."""
    today = date.today()
    payload = load_date(today) or {}
    age = _payload_age_seconds(payload)
    quota = get_quota_summary()
    if quota.get("last_denied_reason"):
        quota = {**quota, "denied": True}
    return {
        "date": today.isoformat(),
        "fetched_at": payload.get("fetched_at"),
        "seconds_since_fetch": round(age) if age is not None else None,
        "min_refresh_seconds": min_refresh_seconds(),
        "board_generated_at": _daily_board_generated_at(today),
        "source": payload.get("source"),
        "games": payload.get("games", []),
        "quota": quota,
    }


def game_row_to_market_cards(row: dict[str, Any], source: str) -> dict[str, Any]:
    label = _board_odds_source(source)
    if source in ("the_odds_api_live", "the_odds_api_historical"):
        label = "the_odds_api"
    return {
        "source": label,
        "away": {
            "moneyline_american": row.get("away_ml"),
            "spread": {
                "point": row.get("away_spread_point"),
                "american": row.get("away_spread_american"),
            },
        },
        "home": {
            "moneyline_american": row.get("home_ml"),
            "spread": {
                "point": row.get("home_spread_point"),
                "american": row.get("home_spread_american"),
            },
        },
        "total": {
            "line": row.get("ou_line"),
            "over_american": row.get("over_odds"),
            "under_american": row.get("under_odds"),
        },
    }


def import_games_from_csv_rows(
    game_date: date,
    rows: list[dict[str, Any]],
) -> None:
    fetched_at = _clock().isoformat()
    payload = {
        "date": game_date.isoformat(),
        "fetched_at": fetched_at,
        "source": "csv_import",
        "games": rows,
    }
    save_date(game_date, payload, api_fetch=False)


def fetch_mlb_events_if_allowed() -> ApiFetchResult:
    """Quota-gated MLB events list (for event id lookup)."""
    if not live_odds_enabled():
        return ApiFetchResult(denied=True, denied_reason="live_odds_disabled")
    allowed, deny_reason = _try_acquire_quota_slot()
    if not allowed:
        return ApiFetchResult(denied=True, denied_reason=deny_reason)
    try:
        events = fetch_mlb_events()
        return ApiFetchResult(events=events or [], source="the_odds_api_live")
    except Exception as exc:
        _release_quota_slot()
        logger.warning("Odds API events list failed: %s", exc)
        return ApiFetchResult(error=str(exc))


def fetch_mlb_event_props_if_allowed(
    event_id: str,
    markets: str = DEFAULT_MLB_PROP_MARKETS,
    bookmakers: str | None = None,
    *,
    regions: str | None = None,
) -> ApiFetchResult:
    """Quota-gated player props for one MLB event."""
    if not live_odds_enabled():
        return ApiFetchResult(denied=True, denied_reason="live_odds_disabled")
    region_str = regions if regions is not None else prop_regions()
    credit_cost = estimate_odds_api_credits(markets, region_str)
    allowed, deny_reason = _try_acquire_quota_slot(credit_cost=credit_cost)
    if not allowed:
        return ApiFetchResult(denied=True, denied_reason=deny_reason)
    try:
        event = fetch_mlb_event_odds(
            event_id,
            markets=markets,
            bookmakers=bookmakers,
            regions=region_str,
        )
        if not event:
            _release_quota_slot(credit_cost=credit_cost)
            return ApiFetchResult(error="empty_response")
        return ApiFetchResult(
            events=[event],
            source="the_odds_api_live",
            credit_cost=credit_cost,
        )
    except httpx.HTTPStatusError as exc:
        _release_quota_slot(credit_cost=credit_cost)
        detail = exc.response.text[:200] if exc.response is not None else str(exc)
        logger.warning(
            "Odds API event props HTTP %s for %s: %s",
            exc.response.status_code if exc.response else "?",
            event_id,
            detail,
        )
        if exc.response is not None and exc.response.status_code == 422:
            return ApiFetchResult(
                error="Invalid sportsbook key for Odds API — pick another book."
            )
        return ApiFetchResult(error=f"odds_api_http_{exc.response.status_code}")
    except Exception as exc:
        _release_quota_slot(credit_cost=credit_cost)
        logger.warning("Odds API event props failed for %s: %s", event_id, exc)
        return ApiFetchResult(error=str(exc))
