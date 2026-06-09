"""NBA daily board — model + odds + forward CLV hook for /nba/board UI."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.models.constants import DEFAULT_MIN_EDGE
from app.models.nba_baseline import ACTIVE_NBA_MANIFEST, load_model_artifact, predict_home_win_proba
from app.odds.live_odds import live_odds_enabled
from app.odds.nba_odds_free import load_odds_for_date
from app.odds.nba_odds_repository import get_nba_odds_for_date
from app.odds.nba_team_aliases import normalize_nba_team_name
from app.odds.odds_math import market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds
from app.services.daily_board import confidence_label
from app.services.nba_forward_clv import log_live_picks
from app.services.schedule_nba import get_nba_schedule

logger = logging.getLogger(__name__)

LIVE_ODDS_SOURCES = frozenset({"the_odds_api", "the_odds_api_live"})

NBA_DISCLAIMER = (
    "NBA moneyline model — not betting advice. betting_ready=false until forward CLV validates edge."
)

SPREAD_DISCLAIMER = (
    "NBA spread model is experimental (margin GBR + Normal cover); not betting-ready. "
    "See SPREAD_NBA.md."
)


def _nba_season_end_year(game_date: date) -> int:
    """End-year season label (2026 = 2025-26); season starts in October."""
    return game_date.year + 1 if game_date.month >= 10 else game_date.year


def _has_odds_api_key() -> bool:
    return bool(os.getenv("ODDS_API_KEY", "").strip())


def _active_nba_model_info() -> dict[str, Any]:
    if ACTIVE_NBA_MANIFEST.exists():
        try:
            return json.loads(ACTIVE_NBA_MANIFEST.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    try:
        artifact = load_model_artifact()
        return {"model_version": artifact.get("model_version", "unknown")}
    except FileNotFoundError:
        return {}


def _attach_nba_odds(
    slate_df: pd.DataFrame,
    game_date: date,
    *,
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, str]:
    merged = slate_df.copy()
    merged["home_ml"] = np.nan
    merged["away_ml"] = np.nan
    merged["home_spread_point"] = np.nan
    merged["home_spread_american"] = np.nan
    merged["away_spread_point"] = np.nan
    merged["away_spread_american"] = np.nan

    odds_games, source = get_nba_odds_for_date(
        game_date, force_refresh=force_refresh, include_spreads=True
    )
    if not odds_games:
        return merged, "none"

    odds_by_matchup: dict[tuple[str, str], dict[str, Any]] = {}
    for og in odds_games:
        key = (
            normalize_nba_team_name(og.get("home_team", "")),
            normalize_nba_team_name(og.get("away_team", "")),
        )
        odds_by_matchup[key] = og

    for idx, row in merged.iterrows():
        key = (
            normalize_nba_team_name(row["home_team"]),
            normalize_nba_team_name(row["away_team"]),
        )
        match = odds_by_matchup.get(key)
        if match:
            merged.at[idx, "home_ml"] = match.get("home_ml")
            merged.at[idx, "away_ml"] = match.get("away_ml")
            for col in (
                "home_spread_point",
                "home_spread_american",
                "away_spread_point",
                "away_spread_american",
            ):
                val = match.get(col)
                if val is not None:
                    merged.at[idx, col] = val

    return merged, source


def _attach_cached_odds(
    slate_df: pd.DataFrame,
    game_date: date,
) -> tuple[pd.DataFrame, str]:
    """CSV + nba_odds_repository only — never calls The Odds API."""
    merged = slate_df.copy()
    merged["home_ml"] = np.nan
    merged["away_ml"] = np.nan
    merged["home_spread_point"] = np.nan
    merged["home_spread_american"] = np.nan
    merged["away_spread_point"] = np.nan
    merged["away_spread_american"] = np.nan

    odds_df, source = load_odds_for_date(game_date)
    if odds_df.empty:
        return merged, "none"

    odds_by_matchup: dict[tuple[str, str], pd.Series] = {}
    for _, og in odds_df.iterrows():
        key = (
            normalize_nba_team_name(og["home_team"]),
            normalize_nba_team_name(og["away_team"]),
        )
        odds_by_matchup[key] = og

    for idx, row in merged.iterrows():
        key = (
            normalize_nba_team_name(row["home_team"]),
            normalize_nba_team_name(row["away_team"]),
        )
        match = odds_by_matchup.get(key)
        if match is not None:
            merged.at[idx, "home_ml"] = match["home_ml"]
            merged.at[idx, "away_ml"] = match["away_ml"]
            for col in (
                "home_spread_point",
                "home_spread_american",
                "away_spread_point",
                "away_spread_american",
            ):
                if col in match.index and pd.notna(match[col]):
                    merged.at[idx, col] = match[col]

    return merged, source


def _active_margin_model_info() -> dict[str, Any]:
    from app.models.nba_margin import load_margin_manifest

    manifest = load_margin_manifest()
    return manifest or {}


def _slate_rows(
    merged: pd.DataFrame,
    has_odds: bool,
    min_edge: float,
    *,
    spread_enabled: bool = False,
) -> list[dict[str, Any]]:
    from app.services.daily_board import _spread_row_fields

    rows: list[dict[str, Any]] = []
    for row in merged.itertuples(index=False):
        matchup = f"{row.away_team} @ {row.home_team}"
        model_home = float(row.model_prob_home)
        market_home = None
        edge_home = None
        ml_edge_best = None
        plus_ev = False
        best_pick = None

        if has_odds and pd.notna(getattr(row, "home_ml", None)):
            if is_valid_american_odds(row.home_ml) and is_valid_american_odds(row.away_ml):
                market_home, market_away = market_probs_from_american(
                    int(row.home_ml), int(row.away_ml)
                )
                model_away = float(row.model_prob_away)
                edge_home = model_home - market_home
                edge_away = model_away - market_away
                ml_edge_best = max(edge_home, edge_away)
                options = [
                    ("home", row.home_team, edge_home, int(row.home_ml)),
                    ("away", row.away_team, edge_away, int(row.away_ml)),
                ]
                side, team, edge, am = max(options, key=lambda x: x[2])
                if edge >= min_edge:
                    plus_ev = True
                    best_pick = {
                        "side": side,
                        "team": team,
                        "edge": round(edge, 4),
                        "american_odds": am,
                    }

        spread = _spread_row_fields(row, min_edge) if spread_enabled else {}
        rows.append(
            {
                "game_id": str(row.game_id),
                "matchup": matchup,
                "away_team": row.away_team,
                "home_team": row.home_team,
                "model_prob_home": round(model_home, 4),
                "market_prob_home": round(market_home, 4) if market_home else None,
                "edge_home": round(edge_home, 4) if edge_home is not None else None,
                "ml_edge_best": round(ml_edge_best, 4) if ml_edge_best is not None else None,
                "ml_confidence": confidence_label(ml_edge_best),
                "plus_ev_single": plus_ev,
                "best_pick": best_pick,
                **spread,
            }
        )
    return rows


def build_nba_daily_board(
    game_date: date | None = None,
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
    force_refresh: bool = False,
    use_cache: bool = False,
    log_clv: bool = True,
) -> dict[str, Any]:
    """Build NBA slate with model probs and odds; logs forward CLV on live +EV singles."""
    game_date = game_date or date.today()
    mode = "demo" if use_cache else "live"
    warnings: list[str] = []

    if not use_cache and not live_odds_enabled():
        if not _has_odds_api_key():
            warnings.append(
                "Free mode: no live sportsbook lines (ODDS_API_KEY not set). "
                "Showing model picks only. See DEV.md to enable USE_LIVE_ODDS."
            )
        else:
            warnings.append(
                "Free mode: USE_LIVE_ODDS=false — model picks only, no sportsbook API calls."
            )

    schedule = get_nba_schedule(game_date, auto_resolve=False)
    games = schedule.get("games") or []

    base: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": game_date.isoformat(),
        "sport": "nba",
        "mode": mode,
        "disclaimer": NBA_DISCLAIMER,
        "warnings": warnings,
        "message": None,
        "quota_warning": None,
        "odds_source": "none",
        "edge_threshold": min_edge,
        "betting_ready": False,
        "active_moneyline_model": _active_nba_model_info(),
        "slate": [],
        "plus_ev_count": 0,
        "clv_logged_count": 0,
    }

    if not games:
        base["message"] = "No NBA games scheduled for this date."
        base["warnings"] = warnings
        return base

    season_end = _nba_season_end_year(game_date)
    slate_df = pd.DataFrame(
        [
            {
                "game_id": str(g["game_id"]),
                "date": game_date.isoformat(),
                "season": season_end,
                "home_team": normalize_nba_team_name(g["home_team"]),
                "away_team": normalize_nba_team_name(g["away_team"]),
            }
            for g in games
        ]
    )

    try:
        slate_df["model_prob_home"] = predict_home_win_proba(slate_df)
    except FileNotFoundError as exc:
        base["error"] = str(exc)
        base["message"] = "NBA model not trained — run scripts/train_nba_baseline.py"
        base["warnings"] = warnings
        return base

    slate_df["model_prob_away"] = 1.0 - slate_df["model_prob_home"]

    spread_enabled = False
    try:
        from app.models.nba_margin import is_margin_production_ready

        spread_enabled = is_margin_production_ready()
    except ImportError:
        spread_enabled = False

    if use_cache:
        merged, odds_source = _attach_cached_odds(slate_df, game_date)
    else:
        merged, odds_source = _attach_nba_odds(
            slate_df, game_date, force_refresh=force_refresh
        )

    from app.odds.odds_repository import last_fetch_meta

    meta = last_fetch_meta()
    quota_warning = meta.get("quota_warning")
    base["quota_warning"] = quota_warning
    if quota_warning:
        warnings.append(quota_warning)

    if spread_enabled:
        try:
            from app.models.nba_margin import predict_spread_covers

            merged = predict_spread_covers(merged)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("NBA spread cover predictions skipped: %s", exc)
            spread_enabled = False

    has_odds = odds_source not in ("none",) and merged["home_ml"].notna().any()
    base["odds_source"] = odds_source
    base["games_with_odds"] = int(merged["home_ml"].notna().sum()) if has_odds else 0
    base["board_spread_enabled"] = spread_enabled
    base["active_margin_model"] = _active_margin_model_info() if spread_enabled else {}
    if spread_enabled:
        base["spread_disclaimer"] = SPREAD_DISCLAIMER

    if odds_source == "none":
        if use_cache:
            warnings.append(
                "Demo mode — no cached odds for this date; model columns only."
            )
            base["message"] = (
                "Model-only demo — import nba_odds_2026.csv or capture live odds "
                "to see market columns."
            )
        elif not live_odds_enabled():
            base["message"] = (
                "Live odds disabled — use Demo mode or set USE_LIVE_ODDS=true."
            )
            warnings.append("Odds unavailable — slate shows model probabilities only.")
        else:
            warnings.append("Odds unavailable — slate shows model probabilities only.")

    slate = _slate_rows(merged, has_odds, min_edge, spread_enabled=spread_enabled)
    base["slate"] = slate
    base["plus_ev_count"] = sum(1 for g in slate if g.get("plus_ev_single"))
    base["warnings"] = warnings

    if (
        not use_cache
        and log_clv
        and live_odds_enabled()
        and odds_source in LIVE_ODDS_SOURCES
        and base.get("plus_ev_count", 0) > 0
    ):
        try:
            logged = log_live_picks(base)
            base["clv_logged"] = logged
            base["clv_logged_count"] = len(logged) if isinstance(logged, list) else 0
        except Exception as exc:
            logger.warning("NBA forward CLV log skipped: %s", exc)
            base["clv_log_error"] = str(exc)

    return base
