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


def predict_custom_home_proba(
    slate_df: pd.DataFrame,
    game_date: date | None = None,
) -> np.ndarray:
    """Return home win probabilities from the weighted factor model."""
    cfg = load_custom_weights()
    weights: dict[str, float] = cfg["factors"]
    score_scale = float(cfg.get("score_scale", 4.0))
    feat_df = build_custom_features_for_slate(slate_df)
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
    feat_df = build_custom_features_for_slate(slate_df)
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
    slate_df = pd.DataFrame(
        [
            {
                "game_id": str(game["game_id"]),
                "date": game_date.isoformat(),
                "season": season_end,
                "home_team": normalize_nba_team_name(game["home_team"]),
                "away_team": normalize_nba_team_name(game["away_team"]),
            }
        ]
    )
    bd = predict_custom_breakdowns(slate_df, game_date=game_date)[0]
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
    except (FileNotFoundError, OSError, KeyError):
        pass

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
        "model_prob_home": bd["model_prob_home"],
        "model_prob_away": bd["model_prob_away"],
        "ml_prob_home": round(ml_prob, 4) if ml_prob is not None else None,
        "model_pick": game["home_team"] if bd["model_prob_home"] >= 0.5 else game["away_team"],
        "weighted_score": bd["weighted_score"],
        "factors": bd["factors"],
        "drivers": drivers,
        "note": (
            "Primary prediction uses your 15 weighted factors. "
            "Adjust global weights on the board via Factor weights. "
            "Run scripts/fetch_nba_team_stats.py for ORtg/DRTG/rebounding/3PT from stats.nba.com."
        ),
    }
