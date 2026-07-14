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
from app.models.nba_custom import load_custom_weights, predict_custom_home_proba
from app.odds.live_odds import live_odds_enabled
from app.odds.nba_odds_free import load_odds_for_date
from app.odds.nba_odds_repository import get_nba_odds_for_date
from app.odds.nba_team_aliases import normalize_nba_team_name
from app.odds.odds_math import market_probs_from_american, market_probs_from_american_totals
from app.odds.team_aliases import is_valid_american_odds
from app.services.daily_board import confidence_label
from app.services.nba_forward_clv import log_live_picks
from app.services.schedule_nba import get_nba_schedule

logger = logging.getLogger(__name__)

LIVE_ODDS_SOURCES = frozenset({"the_odds_api", "the_odds_api_live"})

NBA_DISCLAIMER = (
    "NBA weighted factor model — not betting advice. "
    "15 user-defined factors; optional overrides for injuries/lineups."
)

NBA_SUMMER_BOARD_NOTE = (
    "Summer League games use the same moneyline / spread / totals stack as the NBA season, "
    "with pace, variance, and home-court adjustments. Franchise form ≠ summer rosters."
)

SPREAD_DISCLAIMER = (
    "NBA spread model is experimental (margin GBR + Normal cover); not betting-ready. "
    "See SPREAD_NBA.md."
)

TOTALS_DISCLAIMER = (
    "NBA O/U model is experimental (GBR + Normal over prob); separate gate from moneyline. "
    "See TOTALS_NBA.md."
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


def _blank_odds_columns(merged: pd.DataFrame) -> pd.DataFrame:
    out = merged.copy()
    for col in (
        "home_ml",
        "away_ml",
        "home_spread_point",
        "home_spread_american",
        "away_spread_point",
        "away_spread_american",
        "ou_line",
        "over_odds",
        "under_odds",
    ):
        out[col] = np.nan
    return out


def _apply_odds_matchups(
    merged: pd.DataFrame,
    odds_games: list[dict[str, Any]],
    *,
    only_summer: bool | None = None,
) -> int:
    """Attach odds by matchup. Returns count of rows updated."""
    if not odds_games:
        return 0
    odds_by_matchup: dict[tuple[str, str], dict[str, Any]] = {}
    for og in odds_games:
        key = (
            normalize_nba_team_name(og.get("home_team", "")),
            normalize_nba_team_name(og.get("away_team", "")),
        )
        odds_by_matchup[key] = og

    updated = 0
    for idx, row in merged.iterrows():
        is_summer = bool(row.get("is_summer")) if "is_summer" in merged.columns else False
        if only_summer is True and not is_summer:
            continue
        if only_summer is False and is_summer:
            continue
        key = (
            normalize_nba_team_name(row["home_team"]),
            normalize_nba_team_name(row["away_team"]),
        )
        match = odds_by_matchup.get(key)
        if not match:
            continue
        merged.at[idx, "home_ml"] = match.get("home_ml")
        merged.at[idx, "away_ml"] = match.get("away_ml")
        for col in (
            "home_spread_point",
            "home_spread_american",
            "away_spread_point",
            "away_spread_american",
            "ou_line",
            "over_odds",
            "under_odds",
        ):
            val = match.get(col)
            if val is not None:
                merged.at[idx, col] = val
        updated += 1
    return updated


def _attach_nba_odds(
    slate_df: pd.DataFrame,
    game_date: date,
    *,
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, str]:
    merged = _blank_odds_columns(slate_df)
    sources: list[str] = []

    has_summer = (
        "is_summer" in merged.columns and bool(merged["is_summer"].fillna(False).any())
    )
    has_regular = (
        "is_summer" not in merged.columns
        or bool((~merged["is_summer"].fillna(False)).any())
    )

    if has_regular:
        odds_games, source = get_nba_odds_for_date(
            game_date,
            force_refresh=force_refresh,
            include_spreads=True,
            include_totals=True,
        )
        if odds_games:
            _apply_odds_matchups(merged, odds_games, only_summer=False)
            if source and source != "none":
                sources.append(source)

    if has_summer:
        try:
            from app.odds.nba_summer_odds_repository import get_nba_summer_odds_for_date

            summer_odds, summer_source = get_nba_summer_odds_for_date(
                game_date,
                force_refresh=force_refresh,
                include_spreads=True,
                include_totals=True,
            )
            if summer_odds:
                _apply_odds_matchups(merged, summer_odds, only_summer=True)
                if summer_source and summer_source != "none":
                    sources.append(f"summer:{summer_source}")
        except Exception as exc:
            logger.warning("NBA Summer odds attach failed: %s", exc)

    if not sources:
        return merged, "none"
    if len(sources) == 1:
        return merged, sources[0]
    return merged, "+".join(sources)


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
    merged["ou_line"] = np.nan
    merged["over_odds"] = np.nan
    merged["under_odds"] = np.nan

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
                "ou_line",
                "over_odds",
                "under_odds",
            ):
                if col in match.index and pd.notna(match[col]):
                    merged.at[idx, col] = match[col]

    return merged, source


def _active_margin_model_info() -> dict[str, Any]:
    from app.models.nba_margin import load_margin_manifest

    manifest = load_margin_manifest()
    return manifest or {}


def _active_totals_model_info() -> dict[str, Any]:
    from app.models.nba_totals import load_totals_manifest

    return load_totals_manifest() or {}


def _totals_row_fields(row, min_edge: float) -> dict[str, Any]:
    exp = getattr(row, "expected_total_pts", None)
    exp_out = (
        round(float(exp), 1)
        if exp is not None and not (isinstance(exp, float) and np.isnan(exp))
        else None
    )
    empty: dict[str, Any] = {
        "ou_line": None,
        "over_odds": None,
        "under_odds": None,
        "expected_total_pts": exp_out,
        "model_prob_over": getattr(row, "model_prob_over", None),
        "market_prob_over": None,
        "total_edge": None,
        "totals_pick": None,
        "totals_confidence": confidence_label(None),
        "plus_ev_total": False,
    }
    line = getattr(row, "ou_line", None)
    over_am = getattr(row, "over_odds", None)
    under_am = getattr(row, "under_odds", None)
    if line is None or pd.isna(line):
        return empty
    if not is_valid_american_odds(over_am) or not is_valid_american_odds(under_am):
        empty["ou_line"] = float(line)
        return empty

    market_over, _ = market_probs_from_american_totals(int(over_am), int(under_am))
    model_over = getattr(row, "model_prob_over", None)
    edge = None
    if model_over is not None and not (isinstance(model_over, float) and np.isnan(model_over)):
        edge = float(model_over) - market_over

    totals_pick = None
    plus_ev = False
    if edge is not None:
        if edge >= min_edge:
            totals_pick = f"Over {float(line):g}"
            plus_ev = True
        elif edge <= -min_edge:
            totals_pick = f"Under {float(line):g}"
            plus_ev = True

    return {
        "ou_line": float(line),
        "over_odds": int(over_am),
        "under_odds": int(under_am),
        "expected_total_pts": (
            round(float(row.expected_total_pts), 1)
            if getattr(row, "expected_total_pts", None) is not None
            and not pd.isna(getattr(row, "expected_total_pts", None))
            else None
        ),
        "model_prob_over": model_over,
        "market_prob_over": round(market_over, 4),
        "total_edge": round(edge, 4) if edge is not None else None,
        "totals_pick": totals_pick,
        "totals_confidence": confidence_label(abs(edge) if edge is not None else None),
        "plus_ev_total": plus_ev,
    }


def _top_totals(slate: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    picks = [
        {
            "matchup": g["matchup"],
            "pick": g["totals_pick"],
            "ou_line": g["ou_line"],
            "expected_total_pts": g.get("expected_total_pts"),
            "edge": g["total_edge"],
        }
        for g in slate
        if g.get("plus_ev_total") and g.get("totals_pick") and g.get("total_edge") is not None
    ]
    picks.sort(key=lambda p: p["edge"], reverse=True)
    return picks[:limit]


def _slate_rows(
    merged: pd.DataFrame,
    has_odds: bool,
    min_edge: float,
    *,
    spread_enabled: bool = False,
    totals_enabled: bool = False,
    eval_mode: bool = False,
) -> list[dict[str, Any]]:
    from app.services.daily_board import _spread_row_fields
    from app.services.nba_eval_slate import eval_row_fields

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
        if spread_enabled:
            mm = getattr(row, "model_margin", None)
            if mm is not None and not (isinstance(mm, float) and np.isnan(mm)):
                spread["model_margin"] = round(float(mm), 1)
        totals = _totals_row_fields(row, min_edge) if totals_enabled else {}
        pick_side = "home" if model_home >= 0.5 else "away"
        model_pick = row.home_team if pick_side == "home" else row.away_team
        # Model-strength confidence (used by slate win-% bars / Toss-up meter).
        model_gap = abs(model_home - 0.5)
        if model_gap < 0.03:
            model_conf = "Lean only"
        elif model_gap < 0.06:
            model_conf = "Low"
        elif model_gap < 0.10:
            model_conf = "Medium"
        elif model_gap < 0.16:
            model_conf = "High"
        else:
            model_conf = "Extremely high"
        row_dict: dict[str, Any] = {
            "game_id": str(row.game_id),
            "matchup": matchup,
            "away_team": row.away_team,
            "home_team": row.home_team,
            "model_prob_home": round(model_home, 4),
            "model_prob_away": round(1.0 - model_home, 4),
            "ml_prob_home": (
                round(float(row.ml_prob_home), 4)
                if hasattr(row, "ml_prob_home") and pd.notna(getattr(row, "ml_prob_home", None))
                else None
            ),
            "model_pick": model_pick,
            "model_pick_side": pick_side,
            "model_confidence": model_conf,
            "market_prob_home": round(market_home, 4) if market_home else None,
            "edge_home": round(edge_home, 4) if edge_home is not None else None,
            "ml_edge_best": round(ml_edge_best, 4) if ml_edge_best is not None else None,
            "ml_confidence": confidence_label(ml_edge_best)
            if ml_edge_best is not None
            else model_conf,
            "plus_ev_single": plus_ev,
            "best_pick": best_pick,
            **spread,
            **totals,
        }
        if hasattr(row, "is_summer") and bool(getattr(row, "is_summer", False)):
            row_dict["is_summer"] = True
            row_dict["league_tag"] = "summer"
            row_dict["series_summary"] = getattr(row, "series_summary", None) or "NBA Summer League"
            if hasattr(row, "summer_league") and getattr(row, "summer_league", None):
                row_dict["summer_league"] = getattr(row, "summer_league")
            if hasattr(row, "pick_source") and getattr(row, "pick_source", None):
                row_dict["pick_source"] = getattr(row, "pick_source")
            try:
                from app.services.nba_summer_model import load_calibration_params

                summer_edge = float(load_calibration_params().get("min_edge", 0.22))
            except Exception:
                summer_edge = 0.22
            row_dict["summer_min_edge"] = summer_edge
            row_dict["summer_actionable"] = model_gap >= summer_edge
            if not row_dict["summer_actionable"] and model_conf not in (
                "Lean only",
                "Blocked (stale data)",
            ):
                # Softer label when outside the backtested selective band.
                row_dict["model_confidence"] = "Lean only" if model_gap < 0.10 else "Low"
                if ml_edge_best is None:
                    row_dict["ml_confidence"] = row_dict["model_confidence"]
        if has_odds and pd.notna(getattr(row, "home_ml", None)):
            row_dict["home_ml"] = int(row.home_ml)
            row_dict["away_ml"] = int(row.away_ml)
            for col in (
                "home_spread_point",
                "home_spread_american",
                "away_spread_point",
                "away_spread_american",
                "ou_line",
                "over_odds",
                "under_odds",
            ):
                val = getattr(row, col, None)
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    row_dict[col] = float(val) if col == "ou_line" or "point" in col else int(val)
        if eval_mode:
            row_dict.update(eval_row_fields(row))
        rows.append(row_dict)
    return rows


def build_nba_daily_board(
    game_date: date | None = None,
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
    force_refresh: bool = False,
    use_cache: bool = False,
    log_clv: bool = True,
    skip_totals: bool | None = None,
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
        "active_custom_model": load_custom_weights(),
        "prediction_model": "custom_weighted",
        "slate": [],
        "plus_ev_count": 0,
        "clv_logged_count": 0,
        "board_eval_mode": False,
    }

    if not games:
        base["message"] = "No NBA games scheduled for this date."
        base["warnings"] = warnings
        return base

    season_end = _nba_season_end_year(game_date)
    try:
        from app.services.nba_summer_calibration import summer_home_court_edge
        summer_hc = summer_home_court_edge()
    except ImportError:
        summer_hc = 0.25

    slate_df = pd.DataFrame(
        [
            {
                "game_id": str(g["game_id"]),
                "date": game_date.isoformat(),
                "season": season_end,
                "home_team": normalize_nba_team_name(g["home_team"]),
                "away_team": normalize_nba_team_name(g["away_team"]),
                "is_summer": bool(g.get("is_summer") or g.get("league_tag") == "summer"),
                "summer_league": g.get("summer_league"),
                "series_summary": g.get("series_summary"),
                "summer_home_court_edge": summer_hc
                if (g.get("is_summer") or g.get("league_tag") == "summer")
                else None,
            }
            for g in games
        ]
    )

    try:
        slate_df["model_prob_home"] = predict_custom_home_proba(slate_df, game_date)
        try:
            slate_df["ml_prob_home"] = predict_home_win_proba(slate_df)
        except (FileNotFoundError, OSError, KeyError):
            slate_df["ml_prob_home"] = np.nan
    except FileNotFoundError as exc:
        try:
            slate_df["model_prob_home"] = predict_home_win_proba(slate_df)
            slate_df["ml_prob_home"] = slate_df["model_prob_home"]
        except FileNotFoundError:
            base["error"] = str(exc)
            base["message"] = (
                "NBA model not trained — run scripts/bootstrap_nba.py "
                "(or scripts/ingest_nba.py then scripts/train_nba_baseline.py)"
            )
            base["warnings"] = warnings
            return base

    slate_df["model_prob_away"] = 1.0 - slate_df["model_prob_home"]

    # Summer League: replace franchise-season ML with Elo + prior-season backtested model.
    if "is_summer" in slate_df.columns and slate_df["is_summer"].fillna(False).any():
        try:
            from app.services.nba_summer_model import predict_slate_probs

            summer_probs = predict_slate_probs(slate_df, game_date=game_date)
            for i, prob in enumerate(summer_probs):
                if prob is None or (isinstance(prob, float) and np.isnan(prob)):
                    continue
                slate_df.iat[i, slate_df.columns.get_loc("model_prob_home")] = float(prob)
                if "pick_source" not in slate_df.columns:
                    slate_df["pick_source"] = None
                slate_df.iat[i, slate_df.columns.get_loc("pick_source")] = "summer_model"
            slate_df["model_prob_away"] = 1.0 - slate_df["model_prob_home"]
            if "ml_prob_home" in slate_df.columns:
                for i, prob in enumerate(summer_probs):
                    if prob is None or (isinstance(prob, float) and np.isnan(prob)):
                        continue
                    slate_df.iat[i, slate_df.columns.get_loc("ml_prob_home")] = float(prob)
        except Exception as exc:
            logger.warning("NBA Summer model skipped: %s", exc)

    if use_cache:
        merged, odds_source = _attach_cached_odds(slate_df, game_date)
    else:
        merged, odds_source = _attach_nba_odds(
            slate_df, game_date, force_refresh=force_refresh
        )
        if odds_source == "none":
            cached_merged, cached_source = _attach_cached_odds(slate_df, game_date)
            if cached_source != "none":
                merged, odds_source = cached_merged, cached_source

    from app.odds.odds_repository import last_fetch_meta

    meta = last_fetch_meta()
    quota_warning = meta.get("quota_warning")
    base["quota_warning"] = quota_warning
    if quota_warning:
        warnings.append(quota_warning)

    spread_enabled = False
    spread_production_ready = False
    try:
        from app.models.nba_margin import (
            is_margin_production_ready,
            predict_spread_covers,
        )

        merged = predict_spread_covers(merged)
        spread_enabled = True
        spread_production_ready = is_margin_production_ready()
    except FileNotFoundError:
        warnings.append(
            "Spread model missing — run scripts/train_nba_margin.py for point-diff columns."
        )
    except (ImportError, ValueError) as exc:
        logger.warning("NBA spread predictions skipped: %s", exc)

    if skip_totals is None:
        skip_totals = False

    totals_enabled = False
    totals_production_ready = False
    if not skip_totals:
        try:
            from app.models.nba_totals import enrich_totals_columns, is_totals_production_ready

            merged = enrich_totals_columns(merged)
            totals_enabled = True
            totals_production_ready = is_totals_production_ready()
        except FileNotFoundError:
            warnings.append(
                "Totals model missing — run scripts/train_nba_totals.py for O/U columns."
            )
        except (ImportError, ValueError) as exc:
            logger.warning("NBA totals predictions skipped: %s", exc)
    else:
        warnings.append(
            "O/U skipped (skip_totals=true) — pass skip_totals=false for total-points model."
        )

    demo_synthetic = False
    if use_cache and not (
        odds_source not in ("none",) and merged["home_ml"].notna().any()
    ):
        from app.odds.nba_demo_odds import apply_demo_benchmark_odds

        merged = apply_demo_benchmark_odds(merged)
        demo_synthetic = True
        odds_source = "demo_benchmark"
        try:
            from app.models.nba_margin import predict_spread_covers

            if spread_enabled:
                merged = predict_spread_covers(merged)
        except (FileNotFoundError, ImportError, ValueError):
            pass
        try:
            from app.models.nba_totals import enrich_totals_columns

            if totals_enabled:
                merged = enrich_totals_columns(merged)
        except (FileNotFoundError, ImportError, ValueError):
            pass

    # Summer League: shrink ML toward 50/50, inflate totals pace, dampen margins.
    summer_count = 0
    if "is_summer" in merged.columns:
        summer_count = int(merged["is_summer"].fillna(False).sum())
    if summer_count:
        from app.services.nba_summer_calibration import (
            apply_summer_calibration,
            summer_prediction_disclaimer,
        )

        merged = apply_summer_calibration(merged)
        base["summer_games_count"] = summer_count
        base["summer_disclaimer"] = summer_prediction_disclaimer()
        warnings.append(NBA_SUMMER_BOARD_NOTE)

    if demo_synthetic:
        from app.services.nba_eval_slate import attach_actual_results

        merged = attach_actual_results(merged, game_date)
        base["board_eval_mode"] = bool(
            merged["actual_home_win"].notna().any()
            if "actual_home_win" in merged.columns
            else False
        )

    has_odds = odds_source not in ("none",) and merged["home_ml"].notna().any()
    base["odds_source"] = odds_source
    base["demo_synthetic_odds"] = demo_synthetic
    base["games_with_odds"] = int(merged["home_ml"].notna().sum()) if has_odds else 0
    base["board_spread_enabled"] = spread_enabled
    base["board_spread_production_ready"] = spread_production_ready
    base["board_totals_enabled"] = totals_enabled
    base["board_totals_production_ready"] = totals_production_ready
    base["skip_totals"] = bool(skip_totals)
    base["active_margin_model"] = _active_margin_model_info() if spread_enabled else {}
    base["active_totals_model"] = _active_totals_model_info() if totals_enabled else {}
    if spread_enabled:
        base["spread_disclaimer"] = SPREAD_DISCLAIMER
        if not spread_production_ready and not use_cache:
            warnings.append(
                "Spread model shown for research — holdout gate not passed (see SPREAD_NBA.md)."
            )
    if totals_enabled:
        base["totals_disclaimer"] = TOTALS_DISCLAIMER
        if not totals_production_ready and not use_cache:
            warnings.append(
                "O/U model shown for research — holdout gate not passed (see TOTALS_NBA.md)."
            )

    if demo_synthetic:
        base["message"] = (
            "Demo board — benchmark market (54% home / -5.5 / 224.5 total), "
            "not model-derived. Compare Model vs Market vs Actual (holdout games)."
        )
    elif odds_source == "none":
        if use_cache:
            warnings.append(
                "No cached odds for this date — run scripts/bootstrap_nba.py and use Demo on 2026-04-10."
            )
            base["message"] = (
                "Model-only demo — train models with scripts/bootstrap_nba.py."
            )
        elif not live_odds_enabled():
            base["message"] = (
                "Live odds disabled — use Demo mode or set USE_LIVE_ODDS=true."
            )
            warnings.append("Odds unavailable — slate shows model probabilities only.")
        else:
            warnings.append("Odds unavailable — slate shows model probabilities only.")

    slate = _slate_rows(
        merged,
        has_odds,
        min_edge,
        spread_enabled=spread_enabled,
        totals_enabled=totals_enabled,
        eval_mode=bool(base.get("board_eval_mode")),
    )
    base["slate"] = slate
    base["plus_ev_count"] = sum(1 for g in slate if g.get("plus_ev_single"))
    base["top_totals"] = _top_totals(slate) if totals_enabled else []
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
