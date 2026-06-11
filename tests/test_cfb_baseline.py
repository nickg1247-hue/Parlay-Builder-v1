"""CFB baseline model smoke tests."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.features.cfb_pregame import FEATURE_COLUMNS, build_features_for_history
from app.models.cfb_baseline import (
    BASE_TRAIN_SEASONS,
    HOLDOUT_SEASON,
    PLATT_SEASON,
    predict_home_win_proba,
    run_training,
)


def _tiny_games_df() -> pd.DataFrame:
    teams = [
        ("A", "B", 1, 0),
        ("C", "D", 1, 0),
        ("B", "C", 0, 1),
        ("D", "A", 0, 1),
        ("A", "C", 1, 0),
        ("B", "D", 1, 0),
        ("C", "A", 0, 1),
        ("D", "B", 0, 1),
    ]
    rows = []
    gid = 1
    for season in [2022, 2023, 2024, 2025]:
        for i, (home, away, hs, aws) in enumerate(teams):
            rows.append(
                {
                    "game_id": str(gid),
                    "date": f"{season}-09-{7 + i:02d}",
                    "season": season,
                    "game_type": "regular",
                    "home_team": home,
                    "away_team": away,
                    "home_score": 28 if hs else 14,
                    "away_score": 14 if hs else 28,
                    "home_win": hs,
                    "home_rest_days": 7.0,
                    "away_rest_days": 7.0,
                    "home_b2b": 0,
                    "away_b2b": 0,
                }
            )
            gid += 1
    return pd.DataFrame(rows)


@pytest.fixture
def tiny_parquet(tmp_path, monkeypatch):
    df = _tiny_games_df()
    path = tmp_path / "cfb_games.parquet"
    df.to_parquet(path, index=False)
    monkeypatch.setattr("app.models.cfb_baseline.PARQUET_PATH", path)
    monkeypatch.setattr("app.models.cfb_baseline.MODEL_ARTIFACT", tmp_path / "model.joblib")
    monkeypatch.setattr("app.models.cfb_baseline.METRICS_JSON", tmp_path / "metrics.json")
    monkeypatch.setattr("app.models.cfb_baseline.ACTIVE_CFB_MANIFEST", tmp_path / "active.json")
    monkeypatch.setattr("app.models.cfb_baseline.MODELS_DIR", tmp_path / "models")
    return df


def test_feature_columns_shape(tiny_parquet):
    feat = build_features_for_history(tiny_parquet)
    for col in FEATURE_COLUMNS:
        assert col in feat.columns
    assert len(feat) == len(tiny_parquet)


def test_train_smoke(tiny_parquet):
    results = run_training()
    assert results["holdout_season"] == HOLDOUT_SEASON
    assert results["base_train_seasons"] == list(BASE_TRAIN_SEASONS)
    assert results["platt_season"] == PLATT_SEASON
    assert "active_holdout" in results


def test_predict_returns_valid_probs(tiny_parquet):
    run_training()
    slate = pd.DataFrame(
        [
            {
                "game_id": "999",
                "date": "2025-09-07",
                "season": 2025,
                "home_team": "A",
                "away_team": "B",
            }
        ]
    )
    probs = predict_home_win_proba(slate)
    assert len(probs) == 1
    assert 0.0 < probs[0] < 1.0
