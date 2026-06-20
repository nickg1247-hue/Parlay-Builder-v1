"""Align moneyline probabilities with visible pregame factor consensus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.models.mlb_baseline import attach_elo_for_slate
from app.models.mlb_ensemble import elo_prob_home
from app.services.mlb_game_explanations import build_mlb_factor_comparison

# When factor vote gap >= this and model confidence is weak, nudge pick toward factors.
MISMATCH_VOTE_GAP = 3
WEAK_PICK_MAX = 0.58
STRONG_MISMATCH_GAP = 5
STRONG_MISMATCH_MAX_CONF = 0.62


@dataclass
class ReconcileResult:
    prob_home: float
    adjusted: bool
    reason: str | None
    factor_votes: dict[str, int]
    factor_majority_side: str | None
    raw_prob_home: float


def count_factor_votes(factors: list[dict[str, Any]]) -> dict[str, int]:
    votes = {"home": 0, "away": 0, "neutral": 0}
    for item in factors:
        edge = str(item.get("edge") or "neutral")
        if edge not in votes:
            edge = "neutral"
        votes[edge] += 1
    return votes


def factor_implied_prob_home(
    home_votes: int,
    away_votes: int,
    home_elo: float | None,
    away_elo: float | None,
) -> float:
    total = home_votes + away_votes
    if home_elo is not None and away_elo is not None:
        elo_p = elo_prob_home(float(home_elo), float(away_elo))
    else:
        elo_p = 0.5
    if total <= 0:
        return elo_p
    vote_share = home_votes / total
    return 0.35 * vote_share + 0.65 * elo_p


def reconcile_model_prob_home(
    model_home: float,
    factors: list[dict[str, Any]],
    home_elo: float | None,
    away_elo: float | None,
    *,
    market_home: float | None = None,
) -> ReconcileResult:
    """
    When the ensemble lean is weak but pregame factors strongly favor the other side,
    blend probability toward factor + Elo consensus so picks match what users see.
    """
    raw = float(model_home)
    votes = count_factor_votes(factors)
    home_v, away_v = votes["home"], votes["away"]
    if home_v == away_v:
        return ReconcileResult(raw, False, None, votes, None, raw)

    majority = "home" if home_v > away_v else "away"
    gap = abs(home_v - away_v)
    pick_side = "home" if raw >= 0.5 else "away"
    pick_prob = raw if pick_side == "home" else 1.0 - raw

    if pick_side == majority:
        return ReconcileResult(raw, False, None, votes, majority, raw)

    factor_prob = factor_implied_prob_home(home_v, away_v, home_elo, away_elo)
    blend_weight = 0.65
    if market_home is not None:
        market_side = "home" if float(market_home) >= 0.5 else "away"
        if market_side == majority:
            blend_weight = 0.75

    should_adjust = False
    reason: str | None = None
    if gap >= STRONG_MISMATCH_GAP and pick_prob < STRONG_MISMATCH_MAX_CONF:
        should_adjust = True
        reason = "factor_consensus_override"
    elif gap >= MISMATCH_VOTE_GAP and pick_prob < WEAK_PICK_MAX:
        should_adjust = True
        reason = "weak_pick_factor_alignment"

    if not should_adjust:
        return ReconcileResult(raw, False, None, votes, majority, raw)

    blended = (1.0 - blend_weight) * raw + blend_weight * factor_prob
    if majority == "home":
        new_home = max(blended, 0.505)
    else:
        new_home = min(blended, 0.495)

    return ReconcileResult(
        round(new_home, 4),
        True,
        reason,
        votes,
        majority,
        raw,
    )


def reconcile_slate_dataframe(
    slate: pd.DataFrame,
    *,
    market_probs_home: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Adjust model_prob_home per game when weak ensemble leans oppose factor consensus."""
    if slate.empty or "model_prob_home" not in slate.columns:
        return slate

    work = slate.copy()
    if "elo_home_pre" not in work.columns:
        work = attach_elo_for_slate(work)

    raw_probs: list[float] = []
    reconciled: list[bool] = []
    reasons: list[str | None] = []

    for idx, row in work.iterrows():
        if pd.isna(row.get("model_prob_home")):
            raw_probs.append(float("nan"))
            reconciled.append(False)
            reasons.append(None)
            continue
        feats = row.to_dict()
        factors = build_mlb_factor_comparison(
            feats, str(row["home_team"]), str(row["away_team"])
        )
        market_home = None
        if market_probs_home is not None:
            market_home = market_probs_home.get(str(row.get("game_id")))
        rec = reconcile_model_prob_home(
            float(row["model_prob_home"]),
            factors,
            _optional_float(feats.get("elo_home_pre")),
            _optional_float(feats.get("elo_away_pre")),
            market_home=market_home,
        )
        work.at[idx, "model_prob_home"] = rec.prob_home
        raw_probs.append(rec.raw_prob_home)
        reconciled.append(rec.adjusted)
        reasons.append(rec.reason)

    work["model_prob_home_raw"] = raw_probs
    work["model_prob_away"] = 1.0 - pd.to_numeric(work["model_prob_home"], errors="coerce")
    work["pick_reconciled"] = reconciled
    work["pick_reconcile_reason"] = reasons
    return work


def _optional_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
