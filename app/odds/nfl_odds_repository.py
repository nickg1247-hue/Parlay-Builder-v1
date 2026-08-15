"""Persistent NFL odds snapshots — live Odds API only (no historical bulk burn)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.live_odds import live_odds_enabled
from app.odds.nfl_team_aliases import normalize_nfl_team
from app.odds.odds_repository import (
    ApiFetchResult,
    _median_float,
    _median_int,
    _release_quota_slot,
    _try_acquire_quota_slot,
)
from app.odds.team_aliases import is_valid_american_odds
from app.odds.the_odds_api import fetch_live_nfl_odds

logger = logging.getLogger(__name__)

DEFAULT_REPO_DIR = PROJECT_ROOT / "data" / "processed" / "nfl_odds_repository"


def _repo_root() -> Path:
    import os

    override = os.getenv("NFL_ODDS_REPOSITORY_DIR", "").strip()
    return Path(override) if override else DEFAULT_REPO_DIR


def repository_path(game_date: date) -> Path:
    return _repo_root() / f"{game_date.isoformat()}.json"


def has_date(game_date: date) -> bool:
    return repository_path(game_date).exists()


def load_date(game_date: date) -> dict[str, Any] | None:
    path = repository_path(game_date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read NFL odds repository %s: %s", path, exc)
        return None


def save_date(game_date: date, payload: dict[str, Any]) -> None:
    root = _repo_root()
    root.mkdir(parents=True, exist_ok=True)
    repository_path(game_date).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    idx_path = root / "index.json"
    idx: dict[str, Any] = {}
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            idx = {}
    idx[game_date.isoformat()] = {
        "fetched_at": payload.get("fetched_at"),
        "source": payload.get("source"),
        "games_count": len(payload.get("games") or []),
    }
    idx_path.write_text(json.dumps(idx, indent=2), encoding="utf-8")


def normalize_nfl_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    for event in events:
        home = normalize_nfl_team(event.get("home_team", ""))
        away = normalize_nfl_team(event.get("away_team", ""))
        if not home or not away:
            continue
        home_prices: list[int] = []
        away_prices: list[int] = []
        home_spread_points: list[float] = []
        home_spread_prices: list[int] = []
        away_spread_points: list[float] = []
        away_spread_prices: list[int] = []
        total_lines: list[float] = []
        over_prices: list[int] = []
        under_prices: list[int] = []

        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                key = market.get("key")
                if key == "h2h":
                    prices = {
                        normalize_nfl_team(o["name"]): int(o["price"])
                        for o in market.get("outcomes", [])
                        if o.get("price") is not None
                    }
                    if home in prices and away in prices:
                        if is_valid_american_odds(prices[home]) and is_valid_american_odds(
                            prices[away]
                        ):
                            home_prices.append(prices[home])
                            away_prices.append(prices[away])
                elif key == "spreads":
                    hp = ap = hpr = apr = None
                    for outcome in market.get("outcomes", []):
                        team = normalize_nfl_team(outcome.get("name", ""))
                        point = outcome.get("point")
                        price = outcome.get("price")
                        if point is None or price is None or not is_valid_american_odds(price):
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

        if not home_prices:
            continue
        home_ml = _median_int(home_prices)
        away_ml = _median_int(away_prices)
        if home_ml is None or away_ml is None:
            continue
        games.append(
            {
                "home_team": home,
                "away_team": away,
                "commence_time": event.get("commence_time"),
                "home_ml": home_ml,
                "away_ml": away_ml,
                "odds_source": "the_odds_api_live",
                "home_spread_point": _median_float(home_spread_points),
                "home_spread_american": _median_int(home_spread_prices),
                "away_spread_point": _median_float(away_spread_points),
                "away_spread_american": _median_int(away_spread_prices),
                "ou_line": _median_float(total_lines),
                "over_odds": _median_int(over_prices),
                "under_odds": _median_int(under_prices),
            }
        )
    return games


def fetch_nfl_from_api_if_allowed(game_date: date) -> ApiFetchResult:
    if game_date < date.today():
        return ApiFetchResult(denied=True, denied_reason="nfl_live_only_no_historical")
    if not live_odds_enabled():
        return ApiFetchResult(denied=True, denied_reason="live_odds_disabled")
    allowed, deny_reason = _try_acquire_quota_slot()
    if not allowed:
        return ApiFetchResult(denied=True, denied_reason=deny_reason)
    try:
        events = fetch_live_nfl_odds(include_spreads=True, include_totals=True)
        return ApiFetchResult(
            events=normalize_nfl_events(events or []),
            source="the_odds_api_live",
        )
    except Exception as exc:
        _release_quota_slot()
        logger.warning("NFL Odds API HTTP failed for %s: %s", game_date.isoformat(), exc)
        return ApiFetchResult(error=str(exc))


def get_nfl_odds_for_date(
    game_date: date,
    *,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, str]:
    if has_date(game_date) and not force_refresh:
        payload = load_date(game_date)
        if payload:
            return payload.get("games", []), payload.get("source", "repository")
    if game_date < date.today():
        return None, "none"
    api_result = fetch_nfl_from_api_if_allowed(game_date)
    if api_result.denied or api_result.error or api_result.events is None:
        if has_date(game_date):
            payload = load_date(game_date)
            if payload:
                return payload.get("games", []), payload.get("source", "repository_stale")
        return None, "none"
    payload = {
        "date": game_date.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": api_result.source or "the_odds_api_live",
        "games": api_result.events,
    }
    save_date(game_date, payload)
    return api_result.events, payload["source"]


def repository_odds_dataframe(dates: set[str] | None = None) -> pd.DataFrame:
    root = _repo_root()
    if not root.exists():
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        iso = path.stem
        if dates is not None and iso not in dates:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for game in payload.get("games", []):
            if game.get("home_ml") is None or game.get("away_ml") is None:
                continue
            rows.append(
                {
                    "date": iso,
                    "home_team": normalize_nfl_team(game.get("home_team", "")),
                    "away_team": normalize_nfl_team(game.get("away_team", "")),
                    "home_ml": int(game["home_ml"]),
                    "away_ml": int(game["away_ml"]),
                    "home_spread_point": game.get("home_spread_point"),
                    "ou_line": game.get("ou_line"),
                    "odds_source": payload.get("source", "repository"),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    )
