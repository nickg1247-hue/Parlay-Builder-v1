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


def normalize_nba_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse The Odds API events into normalized NBA game rows (h2h only)."""
    games: list[dict[str, Any]] = []
    for event in events:
        home = normalize_nba_team_name(event.get("home_team", ""))
        away = normalize_nba_team_name(event.get("away_team", ""))
        if not home or not away:
            continue
        home_prices: list[int] = []
        away_prices: list[int] = []
        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                prices = {
                    normalize_nba_team_name(o["name"]): int(o["price"])
                    for o in market.get("outcomes", [])
                    if o.get("price") is not None
                }
                if home in prices and away in prices:
                    home_prices.append(prices[home])
                    away_prices.append(prices[away])
        if not home_prices:
            continue
        home_ml = int(pd.Series(home_prices).median())
        away_ml = int(pd.Series(away_prices).median())
        if not is_valid_american_odds(home_ml) or not is_valid_american_odds(away_ml):
            continue
        games.append(
            {
                "home_team": home,
                "away_team": away,
                "commence_time": event.get("commence_time"),
                "home_ml": home_ml,
                "away_ml": away_ml,
                "odds_source": "the_odds_api_live",
            }
        )
    return games


def fetch_nba_from_api_if_allowed(game_date: date) -> ApiFetchResult:
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
        events = fetch_live_nba_odds()
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
) -> tuple[list[dict[str, Any]] | None, str]:
    """Read repository snapshot; optional live fetch for today/future only."""
    if has_date(game_date) and not force_refresh:
        payload = load_date(game_date)
        if payload:
            return payload.get("games", []), payload.get("source", "repository")

    if game_date < date.today():
        return None, "none"

    api_result = fetch_nba_from_api_if_allowed(game_date)
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
    """Load moneylines from on-disk NBA repository snapshots."""
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
                    "odds_source": payload.get("source", "repository"),
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    ).reset_index(drop=True)
