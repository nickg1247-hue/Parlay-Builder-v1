"""NBA baseline model tests (small fixtures — no full parquet required)."""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.features import nba_pregame as nfp
from app.models import nba_baseline as nb


def _mini_games() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_id": "g1",
                "date": "2023-10-24",
                "season": 2024,
                "game_type": "regular",
                "home_team": "Boston Celtics",
                "away_team": "New York Knicks",
                "home_score": 108,
                "away_score": 104,
                "home_win": 1,
                "home_rest_days": 2.0,
                "away_rest_days": 2.0,
                "home_b2b": 0,
                "away_b2b": 0,
            },
            {
                "game_id": "g2",
                "date": "2023-10-26",
                "season": 2024,
                "game_type": "regular",
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "home_score": 110,
                "away_score": 100,
                "home_win": 1,
                "home_rest_days": 2.0,
                "away_rest_days": 2.0,
                "home_b2b": 0,
                "away_b2b": 0,
            },
            {
                "game_id": "g3",
                "date": "2024-10-22",
                "season": 2025,
                "game_type": "regular",
                "home_team": "Boston Celtics",
                "away_team": "New York Knicks",
                "home_score": 100,
                "away_score": 105,
                "home_win": 0,
                "home_rest_days": 2.0,
                "away_rest_days": 2.0,
                "home_b2b": 0,
                "away_b2b": 0,
            },
            {
                "game_id": "g4",
                "date": "2025-10-21",
                "season": 2026,
                "game_type": "regular",
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "home_score": 112,
                "away_score": 108,
                "home_win": 1,
                "home_rest_days": 2.0,
                "away_rest_days": 2.0,
                "home_b2b": 0,
                "away_b2b": 0,
            },
        ]
    )


def test_feature_columns_schema():
    assert "elo_home_pre" in nfp.FEATURE_COLUMNS
    assert len(nfp.FEATURE_COLUMNS) == 10


def test_build_features_no_leakage_last10():
    feats = nfp.build_features(_mini_games(), rest_fill=2.0)
    first = feats[feats["game_id"] == "g1"].iloc[0]
    assert first["home_last10_win_pct"] == nfp.NEUTRAL_LAST10_WIN_PCT
    third = feats[feats["game_id"] == "g3"].iloc[0]
    assert third["home_last10_win_pct"] == 1.0


def test_time_split_seasons():
    feat = nfp.build_features(_mini_games(), rest_fill=2.0)
    train, test = nb.time_split(feat)
    assert set(train["season"]) <= {2024, 2025}
    assert test["season"].unique().tolist() == [2026]


def test_production_gate_logic():
    assert nb.production_gate_passes(0.65, 0.70, 0.68) is True
    assert nb.production_gate_passes(0.70, 0.65, 0.68) is False


@patch("app.models.nba_baseline.save_nba_promotion")
@patch("app.models.nba_baseline.load_games")
def test_run_training_mini(mock_load, mock_save, tmp_path, monkeypatch):
    monkeypatch.setattr(nb, "METRICS_JSON", tmp_path / "nba_baseline_metrics.json")
    games = _mini_games()
    extra_rows = [
        ("t1", "2024-01-05", 2024),
        ("t2", "2024-02-05", 2024),
        ("t3", "2025-01-05", 2025),
        ("t4", "2025-02-05", 2025),
        ("h1", "2026-01-05", 2026),
        ("h2", "2026-02-05", 2026),
    ]
    extra = []
    for gid, dt, season in extra_rows:
        row = games.iloc[0].to_dict()
        row.update(
            {
                "game_id": gid,
                "date": dt,
                "season": season,
                "home_win": 1 if int(gid[-1]) % 2 else 0,
            }
        )
        extra.append(row)
    padded = pd.concat([games, pd.DataFrame(extra)], ignore_index=True)
    mock_load.return_value = padded.sort_values(["date", "game_id"]).reset_index(drop=True)
    results = nb.run_training()
    mock_save.assert_called_once()
    assert "logistic_regression_v1" in results["metrics"]
    assert "phase_gate" in results


def test_compute_metrics():
    y = np.array([1, 0, 1])
    p = np.array([0.9, 0.1, 0.6])
    m = nb.compute_metrics("test", y, p)
    assert 0.0 < m.log_loss < 1.0
    assert m.accuracy >= 0.0
