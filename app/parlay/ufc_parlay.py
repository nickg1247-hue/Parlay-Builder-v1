"""Cross-fight UFC parlay EV ranker (independence assumption)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

import pandas as pd

from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.team_aliases import is_valid_american_odds
from app.parlay.ev_ranker import (
    DEFAULT_MAX_LEGS,
    DEFAULT_MAX_PARLAYS,
    DEFAULT_MIN_LEGS,
    ParlayLeg,
    rank_parlays,
)


def _candidate_legs_from_slate(slate: list[dict[str, Any]]) -> list[ParlayLeg]:
    legs: list[ParlayLeg] = []
    for row in slate:
        home_ml = row.get("home_ml")
        away_ml = row.get("away_ml")
        if home_ml is None or away_ml is None:
            continue
        if not is_valid_american_odds(home_ml) or not is_valid_american_odds(away_ml):
            continue
        prob_home = row.get("model_prob_home")
        prob_away = row.get("model_prob_away")
        mkt_home = row.get("market_prob_home")
        mkt_away = row.get("market_prob_away")
        if prob_home is None or prob_away is None or mkt_home is None or mkt_away is None:
            continue

        home = row.get("home_team") or ""
        away = row.get("away_team") or ""
        matchup = row.get("matchup") or f"{away} vs {home}"
        fid = str(row.get("fight_id") or row.get("game_id") or "")
        fight_date = str(row.get("date") or "")

        options = [
            ("home", home, float(prob_home), float(mkt_home), int(home_ml)),
            ("away", away, float(prob_away), float(mkt_away), int(away_ml)),
        ]
        side, fighter, model_p, market_p, am = max(options, key=lambda x: x[2] - x[3])
        leg_edge = model_p - market_p
        if leg_edge <= 0:
            continue
        legs.append(
            ParlayLeg(
                game_id=fid,
                date=fight_date,
                matchup=matchup,
                side=side,
                team=fighter,
                model_prob=model_p,
                market_prob=market_p,
                american_odds=am,
                leg_edge=leg_edge,
            )
        )
    return legs


def top_parlays_payload(
    slate: list[dict[str, Any]],
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_parlays: int = DEFAULT_MAX_PARLAYS,
    min_legs: int = DEFAULT_MIN_LEGS,
    max_legs: int = DEFAULT_MAX_LEGS,
) -> list[dict[str, Any]]:
    if not any(g.get("home_ml") is not None for g in slate):
        return []
    legs = _candidate_legs_from_slate(slate)
    parlays = rank_parlays(
        legs,
        min_legs=min_legs,
        max_legs=max_legs,
        min_edge=min_edge,
        max_parlays=max_parlays,
    )
    return [
        {
            "num_legs": p.num_legs,
            "ev": round(p.ev, 4),
            "ev_pct": f"{p.ev:.1%}",
            "model_joint_prob": round(p.model_joint_prob, 4),
            "market_joint_prob": round(p.market_joint_prob, 4),
            "decimal_payout": round(p.decimal_payout, 2),
            "legs": [asdict(leg) for leg in p.legs],
        }
        for p in parlays
    ]


def run_ufc_parlay_ranker(
    game_date: date,
    slate: list[dict[str, Any]],
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
    max_parlays: int = DEFAULT_MAX_PARLAYS,
) -> dict[str, Any]:
    parlays = top_parlays_payload(
        slate,
        min_edge=min_edge,
        max_parlays=max_parlays,
    )
    return {
        "date": game_date.isoformat(),
        "sport": "ufc",
        "parlay_count": len(parlays),
        "top_parlays": parlays,
        "disclaimer": "Experimental cross-fight parlays — independence assumption.",
    }
