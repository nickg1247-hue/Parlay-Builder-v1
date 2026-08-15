"""NFL spread / margin model tests."""

import pandas as pd
import pytest

from app.features.nfl_pregame import MARGIN_FEATURE_COLUMNS, build_margin_features_for_history
from app.models.nfl_baseline import HOLDOUT_SEASON, REGRESSION_TRAIN_SEASONS
from app.models.nfl_margin import PROXY_HOME_SPREAD, predict_spread_covers, run_training


def _tiny_games_df() -> pd.DataFrame:
    teams = [
        ("KC", "BAL", 1, 27, 20),
        ("BUF", "MIA", 1, 31, 10),
        ("BAL", "BUF", 0, 17, 28),
        ("MIA", "KC", 0, 14, 26),
        ("KC", "BUF", 1, 24, 17),
        ("BAL", "MIA", 1, 28, 14),
        ("BUF", "KC", 0, 20, 27),
        ("MIA", "BAL", 0, 13, 30),
    ]
    rows = []
    gid = 1
    for season in [2019, 2020, 2021, 2022, 2023, 2024, 2025]:
        for i, (home, away, hs, hsc, asc) in enumerate(teams):
            rows.append(
                {
                    "game_id": str(gid),
                    "date": f"{season}-09-{8 + i:02d}",
                    "season": season,
                    "week": 1 + i,
                    "game_type": "regular",
                    "home_team": home,
                    "away_team": away,
                    "home_team_abbr": home,
                    "away_team_abbr": away,
                    "home_score": hsc,
                    "away_score": asc,
                    "home_win": hs,
                    "home_rest_days": 7.0,
                    "away_rest_days": 7.0,
                    "divisional": 0,
                    "neutral_site": 0,
                }
            )
            gid += 1
    return pd.DataFrame(rows)


@pytest.fixture
def tiny_parquet(tmp_path, monkeypatch):
    df = _tiny_games_df()
    path = tmp_path / "nfl_games.parquet"
    df.to_parquet(path, index=False)
    monkeypatch.setattr("app.models.nfl_baseline.PARQUET_PATH", path)
    monkeypatch.setattr("app.models.nfl_margin.MODEL_ARTIFACT", tmp_path / "margin.joblib")
    monkeypatch.setattr("app.models.nfl_margin.METRICS_JSON", tmp_path / "margin_metrics.json")
    monkeypatch.setattr(
        "app.models.nfl_margin.ACTIVE_MARGIN_MANIFEST", tmp_path / "active_margin.json"
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


def test_predict_spread_covers(tiny_parquet):
    run_training()
    slate = pd.DataFrame(
        [
            {
                "game_id": "999",
                "date": "2025-09-07",
                "season": 2025,
                "home_team": "KC",
                "away_team": "BAL",
                "home_team_abbr": "KC",
                "away_team_abbr": "BAL",
            }
        ]
    )
    enriched = predict_spread_covers(slate)
    assert enriched["model_prob_home_cover"].iloc[0] is not None
    assert 0.0 < enriched["model_prob_home_cover"].iloc[0] <= 1.0
