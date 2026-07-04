"""UFC baseline model training on synthetic fights."""

import pandas as pd
import pytest

from app.features.ufc_pregame import FEATURE_COLUMNS, build_features_for_history
from app.models.ufc_baseline import (
    BASE_TRAIN_SEASONS,
    HOLDOUT_SEASON,
    PLATT_SEASON,
    compute_metrics,
    train_logistic,
)


def _synthetic_fights(n_per_season: int = 40) -> pd.DataFrame:
    rows = []
    fid = 0
    for season in (2021, 2022, 2023, 2024):
        for i in range(n_per_season):
            fid += 1
            home = f"Fighter_H_{season}_{i % 8}"
            away = f"Fighter_A_{season}_{(i + 1) % 8}"
            home_win = 1 if i % 3 != 0 else 0
            rows.append(
                {
                    "fight_id": str(fid),
                    "event_id": f"ev_{season}",
                    "event_name": f"UFC {season}",
                    "date": f"{season}-{(i % 12) + 1:02d}-15",
                    "season": season,
                    "home_team": home,
                    "away_team": away,
                    "home_win": home_win,
                    "weight_class": "Lightweight",
                    "card_segment": "",
                    "home_rest_days": 90.0,
                    "away_rest_days": 90.0,
                    "home_b2b": 0,
                    "away_b2b": 0,
                }
            )
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_build_features_for_history_shape():
    fights = _synthetic_fights()
    feat = build_features_for_history(fights)
    assert not feat.empty
    for col in FEATURE_COLUMNS:
        assert col in feat.columns
    assert "home_win" in feat.columns


def test_train_logistic_on_synthetic():
    fights = _synthetic_fights(n_per_season=50)
    feat = build_features_for_history(fights)
    base = feat[feat["season"].isin(BASE_TRAIN_SEASONS)]
    holdout = feat[feat["season"] == HOLDOUT_SEASON]
    model = train_logistic(base)
    probs = model.predict_proba(holdout[FEATURE_COLUMNS].values)[:, 1]
    metrics = compute_metrics("test", holdout["home_win"].values, probs)
    assert metrics.log_loss < 1.0
    assert 0.0 <= metrics.brier <= 1.0


def test_time_splits_nonempty():
    fights = _synthetic_fights()
    feat = build_features_for_history(fights)
    base = feat[feat["season"].isin(BASE_TRAIN_SEASONS)]
    platt = feat[feat["season"] == PLATT_SEASON]
    holdout = feat[feat["season"] == HOLDOUT_SEASON]
    assert len(base) > 0
    assert len(platt) > 0
    assert len(holdout) > 0
