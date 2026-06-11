"""CFB spread / margin model tests."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.features.cfb_pregame import MARGIN_FEATURE_COLUMNS, build_margin_features_for_history
from app.models.cfb_baseline import HOLDOUT_SEASON, REGRESSION_TRAIN_SEASONS
from app.models.cfb_margin import (
    PROXY_HOME_SPREAD,
    predict_margin,
    predict_spread_covers,
    run_training,
)


def _tiny_games_df() -> pd.DataFrame:
    teams = [
        ("A", "B", 1, 0, 35, 14),
        ("C", "D", 1, 0, 42, 21),
        ("B", "C", 0, 1, 17, 28),
        ("D", "A", 0, 1, 21, 35),
        ("A", "C", 1, 0, 38, 24),
        ("B", "D", 1, 0, 31, 17),
        ("C", "A", 0, 1, 20, 27),
        ("D", "B", 0, 1, 14, 42),
    ]
    rows = []
    gid = 1
    for season in [2022, 2023, 2024, 2025]:
        for i, (home, away, hs, aws, hsc, asc) in enumerate(teams):
            rows.append(
                {
                    "game_id": str(gid),
                    "date": f"{season}-09-{7 + i:02d}",
                    "season": season,
                    "game_type": "regular",
                    "home_team": home,
                    "away_team": away,
                    "home_score": hsc,
                    "away_score": asc,
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
    monkeypatch.setattr("app.models.cfb_margin.MODEL_ARTIFACT", tmp_path / "margin.joblib")
    monkeypatch.setattr("app.models.cfb_margin.METRICS_JSON", tmp_path / "margin_metrics.json")
    monkeypatch.setattr(
        "app.models.cfb_margin.ACTIVE_MARGIN_MANIFEST", tmp_path / "active_margin.json"
    )
    return df


def test_margin_feature_columns(tiny_parquet):
    feat = build_margin_features_for_history(tiny_parquet)
    for col in MARGIN_FEATURE_COLUMNS:
        assert col in feat.columns


def test_margin_train_smoke(tiny_parquet):
    results = run_training()
    assert results["holdout_season"] == HOLDOUT_SEASON
    assert results["train_seasons"] == list(REGRESSION_TRAIN_SEASONS)
    assert "holdout_mae_margin" in results
    assert results["proxy_lines"]["home_spread_point"] == PROXY_HOME_SPREAD


def test_predict_margin_and_cover(tiny_parquet):
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
    margins = predict_margin(slate)
    assert len(margins) == 1
    enriched = predict_spread_covers(slate)
    assert enriched["model_prob_home_cover"].iloc[0] is not None
    assert 0.0 < enriched["model_prob_home_cover"].iloc[0] <= 1.0
