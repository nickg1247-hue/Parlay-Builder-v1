"""User-weighted 15-factor NBA moneyline model."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.data.nba_team_stats import load_team_stats_table
from app.features.nba_custom_factors import (
    breakdown_for_slate_row,
    build_custom_features_for_slate,
)
from app.services.nba_custom_weights import load_custom_weights_config


def load_custom_weights() -> dict[str, Any]:
    return load_custom_weights_config()


def _attach_summer_flags(feat_df: pd.DataFrame, slate_df: pd.DataFrame) -> pd.DataFrame:
    """Carry is_summer / home-court knobs from slate onto feature rows by game_id."""
    if feat_df.empty or "game_id" not in feat_df.columns:
        return feat_df
    out = feat_df.copy()
    summer_by_id: dict[str, bool] = {}
    home_court_by_id: dict[str, float] = {}
    if "is_summer" in slate_df.columns:
        for row in slate_df.itertuples(index=False):
            gid = str(getattr(row, "game_id", ""))
            summer_by_id[gid] = bool(getattr(row, "is_summer", False))
            hc = getattr(row, "summer_home_court_edge", None)
            if hc is not None and not (isinstance(hc, float) and pd.isna(hc)):
                home_court_by_id[gid] = float(hc)
    if not summer_by_id:
        return out
    out["is_summer"] = out["game_id"].astype(str).map(lambda g: summer_by_id.get(g, False))
    if home_court_by_id:
        out["summer_home_court_edge"] = out["game_id"].astype(str).map(
            lambda g: home_court_by_id.get(g)
        )
    return out


def predict_custom_home_proba(
    slate_df: pd.DataFrame,
    game_date: date | None = None,
) -> np.ndarray:
    """Return home win probabilities from the weighted factor model."""
    cfg = load_custom_weights()
    weights: dict[str, float] = cfg["factors"]
    score_scale = float(cfg.get("score_scale", 4.0))
    feat_df = _attach_summer_flags(build_custom_features_for_slate(slate_df), slate_df)
    stats_df = load_team_stats_table()
    probs: list[float] = []
    for row in feat_df.to_dict(orient="records"):
        bd = breakdown_for_slate_row(
            row,
            weights,
            score_scale=score_scale,
            stats_df=stats_df,
            game_date=game_date,
        )
        probs.append(float(bd["model_prob_home"]))
    return np.array(probs, dtype=float)


def predict_custom_breakdowns(
    slate_df: pd.DataFrame,
    game_date: date | None = None,
) -> list[dict[str, Any]]:
    cfg = load_custom_weights()
    weights: dict[str, float] = cfg["factors"]
    score_scale = float(cfg.get("score_scale", 4.0))
    feat_df = _attach_summer_flags(build_custom_features_for_slate(slate_df), slate_df)
    stats_df = load_team_stats_table()
    out: list[dict[str, Any]] = []
    for row in feat_df.to_dict(orient="records"):
        out.append(
            breakdown_for_slate_row(
                row,
                weights,
                score_scale=score_scale,
                stats_df=stats_df,
                game_date=game_date,
            )
        )
    return out


def build_prediction_detail(
    game: dict[str, Any],
    game_date: date,
) -> dict[str, Any]:
    """Game-page payload: weighted factors, drivers, and data-source notes."""
    from app.odds.nba_team_aliases import normalize_nba_team_name
    from app.services.nba_daily_board import _nba_season_end_year

    cfg = load_custom_weights()
    season_end = _nba_season_end_year(game_date)
    is_summer = bool(game.get("is_summer") or game.get("league_tag") == "summer")
    try:
        from app.services.nba_summer_calibration import (
            shrink_home_prob,
            summer_home_court_edge,
            summer_prediction_disclaimer,
        )

        summer_hc = summer_home_court_edge() if is_summer else None
    except ImportError:
        summer_hc = 0.25 if is_summer else None
        shrink_home_prob = None  # type: ignore[assignment]
        summer_prediction_disclaimer = None  # type: ignore[assignment]

    slate_df = pd.DataFrame(
        [
            {
                "game_id": str(game["game_id"]),
                "date": game_date.isoformat(),
                "season": season_end,
                "home_team": normalize_nba_team_name(game["home_team"]),
                "away_team": normalize_nba_team_name(game["away_team"]),
                "is_summer": is_summer,
                "summer_home_court_edge": summer_hc,
            }
        ]
    )
    bd = predict_custom_breakdowns(slate_df, game_date=game_date)[0]
    model_prob_home = float(bd["model_prob_home"])
    if is_summer and shrink_home_prob is not None:
        model_prob_home = shrink_home_prob(model_prob_home)
    drivers = [
        f"{f['label']} ({f['weight_pct']}%): "
        f"{'favors home' if f['home_edge'] > 0.05 else 'favors away' if f['home_edge'] < -0.05 else 'neutral'}"
        for f in bd["factors"][:6]
        if abs(f["home_edge"]) >= 0.03 or f["factor"] == "home_court_advantage"
    ]
    if not drivers:
        drivers = ["Factor edges are balanced — slight lean from combined weighted score."]

    ml_prob = None
    try:
        from app.models.nba_baseline import predict_home_win_proba

        ml_prob = float(predict_home_win_proba(slate_df)[0])
        if is_summer and shrink_home_prob is not None:
            ml_prob = shrink_home_prob(ml_prob)
    except (FileNotFoundError, OSError, KeyError):
        pass

    note = (
        "Primary prediction uses your 15 weighted factors. "
        "Adjust global weights on the board via Factor weights. "
        "Run scripts/fetch_nba_team_stats.py for ORtg/DRTG/rebounding/3PT from stats.nba.com."
    )
    if is_summer and summer_prediction_disclaimer is not None:
        note = f"{summer_prediction_disclaimer()} {note}"

    return {
        "model_id": cfg.get("model_id", "custom_weighted_v1"),
        "model_version": cfg.get("model_id", "custom_weighted_v1"),
        "feature_set": "custom_15_factor",
        "feature_count": len(cfg.get("factors", {})),
        "data_sources": [
            "Matchup & tip: ESPN scoreboard (live)",
            "History & rolling stats: stats.nba.com via scripts/ingest_nba.py",
            "Advanced team stats: data/processed/nba_team_stats.parquet (scripts/fetch_nba_team_stats.py)",
            "Sportsbook lines (optional): The Odds API or nba_odds CSV",
        ],
        "model_prob_home": round(model_prob_home, 4),
        "model_prob_away": round(1.0 - model_prob_home, 4),
        "ml_prob_home": round(ml_prob, 4) if ml_prob is not None else None,
        "model_pick": game["home_team"] if model_prob_home >= 0.5 else game["away_team"],
        "weighted_score": bd["weighted_score"],
        "factors": bd["factors"],
        "drivers": drivers,
        "is_summer": is_summer,
        "note": note,
    }
