"""Build today's MLB slate with model features from game history."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.features.mlb_pregame import build_features_for_slate
from app.models.mlb_baseline import (
    artifact_scoring_params,
    load_games,
    predict_home_win_proba,
)
from app.odds.team_aliases import normalize_team_name

logger = logging.getLogger(__name__)

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
ET = ZoneInfo("America/New_York")

# gameType: R=regular; P/F/D/L/W=postseason; S/E/A=spring/exhibition/all-star (exclude).
_BOARD_GAME_TYPES = frozenset({"R", "P", "F", "D", "L", "W"})
_EXCLUDED_GAME_TYPES = frozenset({"S", "E", "A"})

# MLB Stats API status (see /v1/gameStatus): postponed codedGameState D; cancelled C;
# suspended T/U. detailedState prefixes are the stable signal (Delayed uses P/I, not D).
_POSTPONED_PREFIXES = ("Postponed", "Cancelled", "Suspended")


def _game_et_date(game: dict[str, Any]) -> date:
    raw = game.get("gameDate") or game.get("officialDate") or ""
    dt = pd.to_datetime(raw, utc=True)
    return dt.tz_convert(ET).date()


def _board_game_exclusion_reason(
    game: dict[str, Any],
    board_date: date,
) -> str | None:
    status = game.get("status") or {}
    abstract = status.get("abstractGameState") or ""
    detailed = status.get("detailedState") or ""

    if any(detailed.startswith(prefix) for prefix in _POSTPONED_PREFIXES):
        return "postponed"
    if abstract in ("Final", "Game Over"):
        return "final"

    game_type = (game.get("gameType") or "R").upper()
    if game_type in _EXCLUDED_GAME_TYPES:
        return "game_type"
    if game_type not in _BOARD_GAME_TYPES:
        return "game_type"

    if _game_et_date(game) != board_date:
        return "date_mismatch"
    return None


def slate_filter_meta(
    games: list[dict[str, Any]],
    board_date: date,
) -> dict[str, int]:
    """Count API games excluded by reason before dedupe."""
    counts = {"final": 0, "postponed": 0, "date_mismatch": 0, "game_type": 0}
    seen_pks: set[Any] = set()
    for game in games:
        game_pk = game.get("gamePk")
        if game_pk in seen_pks:
            continue
        seen_pks.add(game_pk)
        reason = _board_game_exclusion_reason(game, board_date)
        if reason:
            counts[reason] += 1
    return counts


def filter_board_games(
    games: list[dict[str, Any]],
    board_date: date,
) -> list[dict[str, Any]]:
    """Keep playable MLB games for the board date (ET calendar day)."""
    kept: list[dict[str, Any]] = []
    seen_pks: set[Any] = set()
    for game in games:
        game_pk = game.get("gamePk")
        if game_pk in seen_pks:
            continue
        if _board_game_exclusion_reason(game, board_date):
            continue
        seen_pks.add(game_pk)
        kept.append(game)
    return kept


def fetch_mlb_schedule_day(game_date: date) -> list[dict[str, Any]]:
    params = {
        "sportId": 1,
        "date": game_date.isoformat(),
        "hydrate": "probablePitcher",
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.get(MLB_SCHEDULE_URL, params=params)
        response.raise_for_status()
        data = response.json()
    games: list[dict[str, Any]] = []
    for day in data.get("dates", []):
        games.extend(day.get("games", []))
    return games


def _pitcher_name(team_blob: dict[str, Any]) -> str | None:
    pitcher = team_blob.get("probablePitcher") or {}
    return pitcher.get("fullName")


def _scoring_params() -> tuple[dict, float]:
    try:
        return artifact_scoring_params()
    except FileNotFoundError:
        logger.warning("Model artifact missing — using default ERA/rest imputation for slate")
        return {"default": 4.0}, 1.0


def build_slate_dataframe(
    game_date: date,
    history: pd.DataFrame | None = None,
    api_games: list[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    history = history if history is not None else load_games()
    history = history[history["date"] < pd.Timestamp(game_date)].copy()

    if api_games is None:
        api_games = filter_board_games(fetch_mlb_schedule_day(game_date), game_date)
    rows: list[dict[str, Any]] = []
    for game in api_games:
        home = game["teams"]["home"]
        away = game["teams"]["away"]
        home_team = normalize_team_name(home["team"]["name"])
        away_team = normalize_team_name(away["team"]["name"])
        gdt = pd.to_datetime(game.get("gameDate", game_date.isoformat()), utc=True)
        gdt = gdt.tz_convert(None) if gdt.tzinfo else gdt
        season = gdt.year

        rows.append(
            {
                "game_id": str(game["gamePk"]),
                "date": game_date.isoformat(),
                "home_team": home_team,
                "away_team": away_team,
                "home_starting_pitcher": _pitcher_name(home),
                "away_starting_pitcher": _pitcher_name(away),
                "season": season,
            }
        )

    if not rows:
        return pd.DataFrame()

    era_medians, rest_fill = _scoring_params()
    slate = build_features_for_slate(
        pd.DataFrame(rows),
        history_df=history,
        era_medians=era_medians,
        rest_fill=rest_fill,
    )
    slate["model_prob_home"] = predict_home_win_proba(slate)
    slate["model_prob_away"] = 1.0 - slate["model_prob_home"]
    return slate


def build_slate_from_history(game_date: date) -> pd.DataFrame:
    """Replay slate from ingested games (for historical --use-cache demos)."""
    df = load_games()
    day = df[df["date"].dt.date == game_date].copy()
    if day.empty:
        return pd.DataFrame()
    hist = df[df["date"] < pd.Timestamp(game_date)].copy()
    day["date"] = game_date.isoformat()
    era_medians, rest_fill = _scoring_params()
    featured = build_features_for_slate(
        day,
        history_df=hist,
        era_medians=era_medians,
        rest_fill=rest_fill,
    )
    featured["model_prob_home"] = predict_home_win_proba(featured)
    featured["model_prob_away"] = 1.0 - featured["model_prob_home"]
    return featured
