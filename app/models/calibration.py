"""Model calibration helpers: blend, favorite agreement, market comparison."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from app.config import PROJECT_ROOT
from app.odds.mlb_odds_free import ODDS_2025_CSV
from app.odds.odds_math import market_probs_from_american
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name

DISPLAY_BLEND_WEIGHT = 0.5
HEAVY_FAVORITE_THRESHOLD = 0.60


def blend_display_prob(model_prob: float, market_prob: float | None) -> float:
    """UI display probability: 50/50 model + market when odds exist."""
    if market_prob is None:
        return model_prob
    w = DISPLAY_BLEND_WEIGHT
    return w * model_prob + (1.0 - w) * market_prob


def model_disagrees_heavy_favorite(
    model_prob_home: float,
    market_prob_home: float | None,
) -> bool:
    if market_prob_home is None:
        return False
    market_prob_away = 1.0 - market_prob_home
    if market_prob_home >= HEAVY_FAVORITE_THRESHOLD and model_prob_home < 0.5:
        return True
    if market_prob_away >= HEAVY_FAVORITE_THRESHOLD and model_prob_home > 0.5:
        return True
    return False


def favorite_pick_agreement_rate(
    model_probs_home: np.ndarray,
    market_probs_home: np.ndarray,
) -> dict[str, Any]:
    """
    When market implies home win > 55%, how often does model also favor home (P > 0.5)?
    """
    mask = market_probs_home > 0.55
    n = int(mask.sum())
    if n == 0:
        return {"n_market_home_favorite": 0, "agreement_rate": None}
    agree = int((model_probs_home[mask] > 0.5).sum())
    return {
        "n_market_home_favorite": n,
        "n_model_agrees": agree,
        "agreement_rate": round(agree / n, 4),
    }


def market_log_loss_holdout(
    games: pd.DataFrame,
    odds_csv: Path | None = None,
) -> float | None:
    """Log loss of vig-free market home prob on 2025 games with matched odds."""
    path = odds_csv or ODDS_2025_CSV
    if not path.exists():
        return None
    odds = pd.read_csv(path)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    holdout = games[games["season"] == 2025].copy()
    holdout["date"] = holdout["date"].dt.strftime("%Y-%m-%d")
    holdout["home_team"] = holdout["home_team"].map(normalize_team_name)
    holdout["away_team"] = holdout["away_team"].map(normalize_team_name)
    merged = holdout.merge(odds, on=["date", "home_team", "away_team"], how="inner")
    valid = merged[
        merged.apply(
            lambda r: is_valid_american_odds(r.home_ml) and is_valid_american_odds(r.away_ml),
            axis=1,
        )
    ]
    if valid.empty:
        return None
    market_home = []
    for row in valid.itertuples(index=False):
        mh, _ = market_probs_from_american(int(row.home_ml), int(row.away_ml))
        market_home.append(mh)
    y = valid["home_win"].values
    return float(log_loss(y, np.clip(market_home, 1e-6, 1 - 1e-6)))
