"""Live totals scoring for daily slate."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.features.mlb_totals_pregame import build_totals_features_for_slate
from app.models.mlb_totals import (
    load_totals_artifact,
    predict_expected_total_runs,
    predict_prob_over,
    prob_over_poisson,
    score_totals_pick,
)
from app.odds.odds_math import market_probs_from_american_totals
from app.odds.team_aliases import is_valid_american_odds
from app.odds.totals_odds import attach_totals_odds
from app.parlay.slate import build_slate_dataframe, build_slate_from_history


def _safe_int_odds(value) -> int | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if pd.isna(value):
        return None
    return int(value)


def build_totals_slate(
    game_date: date,
    use_cache: bool = False,
    moneyline_slate: pd.DataFrame | None = None,
    attach_market_odds: bool = True,
    force_refresh: bool = False,
) -> pd.DataFrame:
    ml = moneyline_slate
    if ml is None:
        ml = (
            build_slate_from_history(game_date)
            if use_cache
            else build_slate_dataframe(game_date)
        )
    if ml.empty:
        return pd.DataFrame()

    base = ml[["game_id", "date", "home_team", "away_team"]].copy()
    if "season" in ml.columns:
        base["season"] = ml["season"]
    else:
        base["season"] = pd.to_datetime(base["date"]).dt.year
    for col in ("home_starting_pitcher", "away_starting_pitcher"):
        if col in ml.columns:
            base[col] = ml[col]

    artifact = load_totals_artifact()
    featured = build_totals_features_for_slate(
        base,
        era_medians=artifact["era_medians"],
        rest_fill=artifact["rest_fill"],
    )
    featured["expected_total_runs"] = predict_expected_total_runs(featured)

    if attach_market_odds:
        merged = attach_totals_odds(
            featured, game_date, use_cache=use_cache, force_refresh=force_refresh
        )
    else:
        merged = featured.copy()

    featured_by_game = {
        str(gid): featured.iloc[[pos]]
        for pos, gid in enumerate(featured["game_id"].astype(str))
    }

    rows: list[dict] = []
    seen_games: set[str] = set()
    for row in merged.itertuples(index=False):
        gid = str(row.game_id)
        if gid in seen_games:
            continue
        seen_games.add(gid)
        feat_row = featured_by_game.get(gid)
        if feat_row is None:
            continue
        ou = getattr(row, "ou_line", None)
        market_over = None
        over_am = _safe_int_odds(getattr(row, "over_odds", None))
        under_am = _safe_int_odds(getattr(row, "under_odds", None))
        if pd.notna(ou) and over_am is not None and under_am is not None:
            if is_valid_american_odds(over_am) and is_valid_american_odds(under_am):
                market_over, _ = market_probs_from_american_totals(over_am, under_am)
        model_over = None
        if pd.notna(ou):
            model_over = float(predict_prob_over(feat_row, float(ou))[0])
        ou_val = float(ou) if pd.notna(ou) else None
        scored = score_totals_pick(
            float(row.expected_total_runs),
            ou_val,
            model_over,
            market_over,
        )
        if scored.get("ou_line") is not None and pd.isna(scored["ou_line"]):
            scored["ou_line"] = None
        rows.append(
            {
                "game_id": str(row.game_id),
                "date": row.date,
                "home_team": row.home_team,
                "away_team": row.away_team,
                "matchup": f"{row.away_team} @ {row.home_team}",
                **scored,
                "over_odds": over_am,
                "under_odds": under_am,
            }
        )
    return pd.DataFrame(rows)
