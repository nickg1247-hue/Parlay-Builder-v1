"""Persistent NBA odds snapshots — live API only (no historical bulk burn)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.live_odds import live_odds_enabled
from app.odds.nba_team_aliases import normalize_nba_team_name
from app.odds.odds_repository import (
    ApiFetchResult,
    _median_float,
    _median_int,
    _release_quota_slot,
    _try_acquire_quota_slot,
)
from app.odds.team_aliases import is_valid_american_odds
from app.odds.the_odds_api import fetch_live_nba_odds

logger = logging.getLogger(__name__)

DEFAULT_REPO_DIR = PROJECT_ROOT / "data" / "processed" / "nba_odds_repository"


def _repo_root() -> Path:
    import os

    override = os.getenv("NBA_ODDS_REPOSITORY_DIR", "").strip()
    return Path(override) if override else DEFAULT_REPO_DIR


def repository_path(game_date: date) -> Path:
    return _repo_root() / f"{game_date.isoformat()}.json"


def load_date(game_date: date) -> dict[str, Any] | None:
    path = repository_path(game_date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read NBA odds repository %s: %s", path, exc)
        return None


def has_date(game_date: date) -> bool:
    return repository_path(game_date).exists()


def save_date(game_date: date, payload: dict[str, Any]) -> None:
    root = _repo_root()
    root.mkdir(parents=True, exist_ok=True)
    repository_path(game_date).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_nba_events(
    events: list[dict[str, Any]],
    *,
    require_h2h: bool = True,
) -> list[dict[str, Any]]:
    """Parse The Odds API events into normalized NBA rows (h2h + optional spreads/totals)."""
    games: list[dict[str, Any]] = []
    for event in events:
        home = normalize_nba_team_name(event.get("home_team", ""))
        away = normalize_nba_team_name(event.get("away_team", ""))
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
                        normalize_nba_team_name(o["name"]): int(o["price"])
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
                    hp = ap = None
                    hpr = apr = None
                    for outcome in market.get("outcomes", []):
                        team = normalize_nba_team_name(outcome.get("name", ""))
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

        if require_h2h and not home_prices:
            continue
        if not home_prices and not total_lines and not home_spread_points:
            continue

        home_ml = _median_int(home_prices) if home_prices else None
        away_ml = _median_int(away_prices) if away_prices else None
        if require_h2h and (
            home_ml is None
            or away_ml is None
            or not is_valid_american_odds(home_ml)
            or not is_valid_american_odds(away_ml)
        ):
            continue

        row: dict[str, Any] = {
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
        games.append(row)
    return games


def fetch_nba_from_api_if_allowed(
    game_date: date,
    *,
    include_spreads: bool = True,
    include_totals: bool = True,
) -> ApiFetchResult:
    """
    Quota-gated live NBA fetch only.

    Past dates are never fetched (no historical Odds API burn).
    """
    if game_date < date.today():
        return ApiFetchResult(denied=True, denied_reason="nba_live_only_no_historical")
    if not live_odds_enabled():
        return ApiFetchResult(denied=True, denied_reason="live_odds_disabled")

    allowed, deny_reason = _try_acquire_quota_slot()
    if not allowed:
        return ApiFetchResult(denied=True, denied_reason=deny_reason)

    try:
        events = fetch_live_nba_odds(
            include_spreads=include_spreads,
            include_totals=include_totals,
        )
        normalized = normalize_nba_events(events or [])
        return ApiFetchResult(events=normalized, source="the_odds_api_live")
    except Exception as exc:
        _release_quota_slot()
        logger.warning("NBA Odds API HTTP failed for %s: %s", game_date.isoformat(), exc)
        return ApiFetchResult(error=str(exc))


def get_nba_odds_for_date(
    game_date: date,
    *,
    force_refresh: bool = False,
    include_spreads: bool = True,
    include_totals: bool = True,
) -> tuple[list[dict[str, Any]] | None, str]:
    """Read repository snapshot; optional live fetch for today/future only."""
    if has_date(game_date) and not force_refresh:
        payload = load_date(game_date)
        if payload:
            return payload.get("games", []), payload.get("source", "repository")

    if game_date < date.today():
        return None, "none"

    api_result = fetch_nba_from_api_if_allowed(
        game_date,
        include_spreads=include_spreads,
        include_totals=include_totals,
    )
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


def repository_odds_dataframe(
    dates: set[str] | None = None,
) -> pd.DataFrame:
    """Load moneylines and spreads from on-disk NBA repository snapshots."""
    root = _repo_root()
    if not root.exists():
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        iso = path.stem
        if dates is not None and iso not in dates:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for game in payload.get("games", []):
            home_ml = game.get("home_ml")
            away_ml = game.get("away_ml")
            if home_ml is None or away_ml is None:
                continue
            if not is_valid_american_odds(home_ml) or not is_valid_american_odds(away_ml):
                continue
            rows.append(
                {
                    "date": iso,
                    "home_team": normalize_nba_team_name(game.get("home_team", "")),
                    "away_team": normalize_nba_team_name(game.get("away_team", "")),
                    "home_ml": int(home_ml),
                    "away_ml": int(away_ml),
                    "home_spread_point": game.get("home_spread_point"),
                    "home_spread_american": game.get("home_spread_american"),
                    "away_spread_point": game.get("away_spread_point"),
                    "away_spread_american": game.get("away_spread_american"),
                    "ou_line": game.get("ou_line"),
                    "over_odds": game.get("over_odds"),
                    "under_odds": game.get("under_odds"),
                    "odds_source": payload.get("source", "repository"),
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    ).reset_index(drop=True)
