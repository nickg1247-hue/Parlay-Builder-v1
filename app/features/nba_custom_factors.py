"""Fifteen-factor home-edge scores for the user weighted NBA model."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.data.nba_team_stats import (
    load_team_stats_table,
    proxy_team_stats_from_features,
    team_stats_row,
)
from app.features.nba_pregame import build_features_for_slate

from app.services.nba_custom_overrides import get_game_override

FACTOR_LABELS: dict[str, str] = {
    "team_offensive_rating": "Team Offensive Rating",
    "team_defensive_rating": "Team Defensive Rating",
    "starting_lineup_strength": "Starting Lineup Strength",
    "player_availability_injuries": "Player Availability / Injuries",
    "recent_form_last10": "Recent Form (Last 10 Games)",
    "home_court_advantage": "Home Court Advantage",
    "bench_production": "Bench Production",
    "matchup_advantages": "Matchup Advantages",
    "rest_fatigue": "Rest / Fatigue",
    "pace_of_play_matchup": "Pace of Play Matchup",
    "rebounding_edge": "Rebounding Edge",
    "turnover_differential": "Turnover Differential",
    "three_point_shooting_efficiency": "Three-Point Shooting Efficiency",
    "free_throw_rate": "Free Throw Rate",
    "travel_situation": "Travel Situation",
    "coaching_adjustments": "Coaching / Adjustments",
}


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _edge_from_diff(diff: float, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return _clamp(diff / scale)


def _pairwise_rating_edge(home_val: float, away_val: float) -> float:
    """Map two 0–1 strength ratings to a home edge in [-1, 1]."""
    return _clamp(2.0 * (home_val - away_val))


def _override_edge(
    override: dict[str, Any],
    key: str,
    *,
    default: float = 0.5,
) -> float:
    block = override.get(key) or {}
    home = float(block.get("home", default))
    away = float(block.get("away", default))
    return _pairwise_rating_edge(home, away)


def compute_factor_edges(
    feat: dict[str, Any],
    *,
    home_stats: dict[str, float] | None = None,
    away_stats: dict[str, float] | None = None,
    override: dict[str, Any] | None = None,
) -> dict[str, float]:
    """
    Each factor returns home_edge in [-1, 1] (positive favors home).
    Uses advanced team stats when cached; otherwise proxies from game history.
    """
    override = override or {}
    if home_stats is None or away_stats is None:
        home_stats, away_stats = proxy_team_stats_from_features(feat)

    home_ortg = float(home_stats.get("off_rating", 110.0))
    away_ortg = float(away_stats.get("off_rating", 110.0))
    home_drtg = float(home_stats.get("def_rating", 110.0))
    away_drtg = float(away_stats.get("def_rating", 110.0))
    home_pace = float(home_stats.get("pace", 220.0))
    away_pace = float(away_stats.get("pace", 220.0))

    home_reb = float(home_stats.get("reb_pct", 0.50))
    away_reb = float(away_stats.get("reb_pct", 0.50))
    home_tov = float(home_stats.get("tov_pct", 0.14))
    away_tov = float(away_stats.get("tov_pct", 0.14))
    home_fg3 = float(home_stats.get("fg3_pct", 0.36))
    away_fg3 = float(away_stats.get("fg3_pct", 0.36))
    home_ft = float(home_stats.get("ft_rate", 0.25))
    away_ft = float(away_stats.get("ft_rate", 0.25))
    home_bench = float(home_stats.get("bench_pts_proxy", 0.5))
    away_bench = float(away_stats.get("bench_pts_proxy", 0.5))

    home_rest = float(feat.get("home_rest_days", 2.0))
    away_rest = float(feat.get("away_rest_days", 2.0))
    home_b2b = int(feat.get("home_b2b", 0))
    away_b2b = int(feat.get("away_b2b", 0))

    matchup_home = home_ortg - away_drtg
    matchup_away = away_ortg - home_drtg

    rest_score = (home_rest - away_rest) + (away_b2b - home_b2b) * 0.75
    travel_away = 0.0
    if away_b2b:
        travel_away += 0.55
    if away_rest + 1.0 < home_rest:
        travel_away += 0.35
    if override.get("travel_situation"):
        travel_edge = _override_edge(override, "travel_situation")
    else:
        travel_edge = _clamp(travel_away)

    return {
        "team_offensive_rating": _edge_from_diff(home_ortg - away_ortg, scale=6.0),
        "team_defensive_rating": _edge_from_diff(away_drtg - home_drtg, scale=6.0),
        "starting_lineup_strength": _override_edge(
            override, "starting_lineup_strength", default=0.5
        ),
        "player_availability_injuries": _override_edge(
            override, "player_availability_injuries", default=0.5
        ),
        "recent_form_last10": _edge_from_diff(
            float(feat.get("home_last10_win_pct", 0.5))
            - float(feat.get("away_last10_win_pct", 0.5)),
            scale=0.18,
        ),
        "home_court_advantage": 1.0,
        "bench_production": (
            _override_edge(override, "bench_production", default=0.5)
            if override.get("bench_production")
            else _pairwise_rating_edge(home_bench, away_bench)
        ),
        "matchup_advantages": _edge_from_diff(matchup_home - matchup_away, scale=8.0),
        "rest_fatigue": _edge_from_diff(rest_score, scale=2.5),
        "pace_of_play_matchup": _edge_from_diff(home_pace - away_pace, scale=10.0),
        "rebounding_edge": _edge_from_diff(home_reb - away_reb, scale=0.06),
        "turnover_differential": _edge_from_diff(away_tov - home_tov, scale=0.03),
        "three_point_shooting_efficiency": _edge_from_diff(home_fg3 - away_fg3, scale=0.04),
        "free_throw_rate": _edge_from_diff(home_ft - away_ft, scale=0.06),
        "travel_situation": travel_edge,
        "coaching_adjustments": _override_edge(
            override, "coaching_adjustments", default=0.5
        ),
    }


def build_factor_breakdown(
    feat: dict[str, Any],
    weights: dict[str, float],
    *,
    home_stats: dict[str, float] | None = None,
    away_stats: dict[str, float] | None = None,
    override: dict[str, Any] | None = None,
    score_scale: float = 4.0,
) -> dict[str, Any]:
    edges = compute_factor_edges(
        feat,
        home_stats=home_stats,
        away_stats=away_stats,
        override=override,
    )
    contributions: list[dict[str, Any]] = []
    weighted_sum = 0.0
    for key, weight in weights.items():
        edge = float(edges.get(key, 0.0))
        contrib = weight * edge
        weighted_sum += contrib
        contributions.append(
            {
                "factor": key,
                "label": FACTOR_LABELS.get(key, key),
                "weight_pct": round(weight * 100, 1),
                "home_edge": round(edge, 4),
                "weighted_contribution": round(contrib, 4),
            }
        )
    contributions.sort(key=lambda c: abs(c["weighted_contribution"]), reverse=True)
    prob_home = 1.0 / (1.0 + pow(2.718281828, -weighted_sum * score_scale))
    return {
        "weighted_score": round(weighted_sum, 4),
        "model_prob_home": round(prob_home, 4),
        "model_prob_away": round(1.0 - prob_home, 4),
        "factors": contributions,
        "factor_edges": {k: round(v, 4) for k, v in edges.items()},
    }


def build_custom_features_for_slate(
    slate_df: pd.DataFrame,
    *,
    stats_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Pregame feature rows for each slate game (same leakage rules as baseline)."""
    return build_features_for_slate(slate_df)


def breakdown_for_slate_row(
    feat_row: dict[str, Any],
    weights: dict[str, float],
    *,
    score_scale: float = 4.0,
    stats_df: pd.DataFrame | None = None,
    game_date: date | None = None,
) -> dict[str, Any]:
    season = int(feat_row.get("season", 0))
    home = str(feat_row.get("home_team", ""))
    away = str(feat_row.get("away_team", ""))
    game_id = str(feat_row.get("game_id", ""))
    table = stats_df if stats_df is not None else load_team_stats_table()
    home_stats = team_stats_row(home, season, stats_df=table)
    away_stats = team_stats_row(away, season, stats_df=table)
    if home_stats is None or away_stats is None:
        home_stats, away_stats = proxy_team_stats_from_features(feat_row)
    override = get_game_override(game_date, game_id)
    return build_factor_breakdown(
        feat_row,
        weights,
        home_stats=home_stats,
        away_stats=away_stats,
        override=override,
        score_scale=score_scale,
    )
