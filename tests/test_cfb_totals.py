"""CFB totals (O/U) model tests."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.features.cfb_pregame import TOTALS_FEATURE_COLUMNS, build_totals_features_for_history
from app.models.cfb_totals import (
    actual_went_over,
    enrich_totals_columns,
    prob_over_normal,
    predict_expected_total,
    run_training,
)
from tests.test_cfb_margin import _tiny_games_df


@pytest.fixture
def tiny_parquet(tmp_path, monkeypatch):
    df = _tiny_games_df()
    path = tmp_path / "cfb_games.parquet"
    df.to_parquet(path, index=False)
    monkeypatch.setattr("app.models.cfb_baseline.PARQUET_PATH", path)
    monkeypatch.setattr("app.models.cfb_totals.MODEL_ARTIFACT", tmp_path / "totals.joblib")
    monkeypatch.setattr("app.models.cfb_totals.METRICS_JSON", tmp_path / "totals_metrics.json")
    monkeypatch.setattr(
        "app.models.cfb_totals.ACTIVE_TOTALS_MANIFEST", tmp_path / "active_totals.json"
    )
    return df


def test_totals_feature_columns(tiny_parquet):
    feat = build_totals_features_for_history(tiny_parquet)
    for col in TOTALS_FEATURE_COLUMNS:
        assert col in feat.columns


def test_prob_over_normal_half_point():
    p = prob_over_normal(55.0, 10.0, 52.5)
    assert 0.0 < p < 1.0
    assert prob_over_normal(40.0, 10.0, 52.5) < prob_over_normal(60.0, 10.0, 52.5)


def test_actual_went_over():
    assert actual_went_over(53.0, 52.5) == 1
    assert actual_went_over(52.0, 52.5) == 0


def test_totals_train_smoke(tiny_parquet):
    results = run_training()
    assert "holdout_mae_total_pts" in results
    assert results["proxy_ou_line"] is not None


def test_predict_expected_total(tiny_parquet):
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
    expected = predict_expected_total(slate)
    assert len(expected) == 1
    assert expected[0] > 0
    enriched = enrich_totals_columns(slate)
    assert enriched["expected_total_pts"].iloc[0] > 0
    assert enriched["model_prob_over"].iloc[0] is not None
