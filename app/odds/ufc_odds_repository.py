"""Persistent UFC odds snapshots — live API only."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.odds.live_odds import live_odds_enabled
from app.odds.odds_repository import (
    ApiFetchResult,
    _median_int,
    _release_quota_slot,
    _try_acquire_quota_slot,
)
from app.odds.team_aliases import is_valid_american_odds
from app.odds.the_odds_api import fetch_live_ufc_odds
from app.odds.ufc_fighter_aliases import fighter_match_key, normalize_fighter_name

logger = logging.getLogger(__name__)

DEFAULT_REPO_DIR = PROJECT_ROOT / "data" / "processed" / "ufc_odds_repository"


def _repo_root() -> Path:
    import os

    override = os.getenv("UFC_ODDS_REPOSITORY_DIR", "").strip()
    return Path(override) if override else DEFAULT_REPO_DIR


def repository_path(game_date: date) -> Path:
    return _repo_root() / f"{game_date.isoformat()}.json"


def index_path() -> Path:
    return _repo_root() / "index.json"


def load_date(game_date: date) -> dict[str, Any] | None:
    path = repository_path(game_date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read UFC odds repository %s: %s", path, exc)
        return None


def has_date(game_date: date) -> bool:
    return repository_path(game_date).exists()


def _update_index(game_date: date, payload: dict[str, Any]) -> None:
    root = _repo_root()
    root.mkdir(parents=True, exist_ok=True)
    idx: dict[str, Any] = {}
    ip = index_path()
    if ip.exists():
        try:
            idx = json.loads(ip.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            idx = {}
    idx[game_date.isoformat()] = {
        "fetched_at": payload.get("fetched_at"),
        "source": payload.get("source"),
        "fights_count": len(payload.get("fights") or []),
    }
    ip.write_text(json.dumps(idx, indent=2), encoding="utf-8")


def save_date(game_date: date, payload: dict[str, Any]) -> None:
    root = _repo_root()
    root.mkdir(parents=True, exist_ok=True)
    repository_path(game_date).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _update_index(game_date, payload)


def normalize_ufc_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fights: list[dict[str, Any]] = []
    for event in events:
        home = normalize_fighter_name(event.get("home_team", ""))
        away = normalize_fighter_name(event.get("away_team", ""))
        if not home or not away:
            continue
        home_prices: list[int] = []
        away_prices: list[int] = []
        totals_lines: list[float] = []
        over_prices: list[int] = []
        under_prices: list[int] = []
        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                key = market.get("key")
                if key == "h2h":
                    prices = {
                        normalize_fighter_name(o["name"]): int(o["price"])
                        for o in market.get("outcomes", [])
                        if o.get("price") is not None
                    }
                    if home in prices and away in prices:
                        if is_valid_american_odds(prices[home]) and is_valid_american_odds(
                            prices[away]
                        ):
                            home_prices.append(prices[home])
                            away_prices.append(prices[away])
                elif key == "totals":
                    over_am = under_am = None
                    line_val = None
                    for o in market.get("outcomes", []):
                        name = (o.get("name") or "").lower()
                        if o.get("price") is None:
                            continue
                        if name == "over":
                            over_am = int(o["price"])
                            line_val = o.get("point")
                        elif name == "under":
                            under_am = int(o["price"])
                            if line_val is None:
                                line_val = o.get("point")
                    if (
                        line_val is not None
                        and over_am is not None
                        and under_am is not None
                        and is_valid_american_odds(over_am)
                        and is_valid_american_odds(under_am)
                    ):
                        totals_lines.append(float(line_val))
                        over_prices.append(over_am)
                        under_prices.append(under_am)
        if not home_prices:
            continue
        home_ml = _median_int(home_prices)
        away_ml = _median_int(away_prices)
        if home_ml is None or away_ml is None:
            continue
        fight_row: dict[str, Any] = {
            "home_team": home,
            "away_team": away,
            "home_match_key": fighter_match_key(home),
            "away_match_key": fighter_match_key(away),
            "commence_time": event.get("commence_time"),
            "home_ml": home_ml,
            "away_ml": away_ml,
            "odds_source": "the_odds_api_live",
        }
        if totals_lines:
            mid = len(totals_lines) // 2
            fight_row["totals_line"] = sorted(totals_lines)[mid]
            fight_row["over_odds"] = _median_int(over_prices)
            fight_row["under_odds"] = _median_int(under_prices)
        fights.append(fight_row)
    return fights


def fetch_ufc_from_api_if_allowed(game_date: date) -> ApiFetchResult:
    if game_date < date.today():
        return ApiFetchResult(denied=True, denied_reason="ufc_live_only_no_historical")
    if not live_odds_enabled():
        return ApiFetchResult(denied=True, denied_reason="live_odds_disabled")

    allowed, deny_reason = _try_acquire_quota_slot()
    if not allowed:
        return ApiFetchResult(denied=True, denied_reason=deny_reason)

    try:
        events = fetch_live_ufc_odds(include_totals=True)
        normalized = normalize_ufc_events(events or [])
        return ApiFetchResult(events=normalized, source="the_odds_api_live")
    except Exception as exc:
        _release_quota_slot()
        logger.warning("UFC Odds API HTTP failed for %s: %s", game_date.isoformat(), exc)
        return ApiFetchResult(error=str(exc))


def get_ufc_odds_for_date(
    game_date: date,
    *,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]] | None, str]:
    if has_date(game_date) and not force_refresh:
        payload = load_date(game_date)
        if payload:
            return payload.get("fights", []), payload.get("source", "repository")

    if game_date < date.today():
        return None, "none"

    api_result = fetch_ufc_from_api_if_allowed(game_date)
    if api_result.denied or api_result.error or api_result.events is None:
        if has_date(game_date):
            payload = load_date(game_date)
            if payload:
                return payload.get("fights", []), payload.get("source", "repository_stale")
        return None, "none"

    payload = {
        "date": game_date.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": api_result.source or "the_odds_api_live",
        "fights": api_result.events,
    }
    save_date(game_date, payload)
    return api_result.events, payload["source"]


def repository_odds_dataframe(
    dates: set[str] | None = None,
) -> pd.DataFrame:
    """Load moneylines from on-disk UFC odds repository snapshots."""
    import pandas as pd

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
        for fight in payload.get("fights") or []:
            home_ml = fight.get("home_ml")
            away_ml = fight.get("away_ml")
            if home_ml is None or away_ml is None:
                continue
            if not is_valid_american_odds(home_ml) or not is_valid_american_odds(away_ml):
                continue
            rows.append(
                {
                    "date": iso,
                    "home_team": normalize_fighter_name(fight.get("home_team", "")),
                    "away_team": normalize_fighter_name(fight.get("away_team", "")),
                    "home_ml": int(home_ml),
                    "away_ml": int(away_ml),
                    "odds_source": payload.get("source", "repository"),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(
        subset=["date", "home_team", "away_team"], keep="first"
    )
