"""CFB market eval — fixture parquet + lines."""

import json
from pathlib import Path

import pandas as pd
import pytest

from app.features.cfb_pregame import FEATURE_COLUMNS
from app.odds.cfb_market_eval import _merge_games_odds, run_market_evaluation


@pytest.fixture
def tiny_holdout(tmp_path, monkeypatch):
    games = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2025-09-06",
                "season": 2025,
                "home_team": "Georgia",
                "away_team": "Clemson",
                "home_score": 34,
                "away_score": 3,
                "home_win": 1,
            },
            {
                "game_id": "2",
                "date": "2025-09-13",
                "season": 2025,
                "home_team": "Alabama",
                "away_team": "Wisconsin",
                "home_score": 14,
                "away_score": 28,
                "home_win": 0,
            },
        ]
    )
    parquet = tmp_path / "cfb_games.parquet"
    games.to_parquet(parquet, index=False)

    artifact = {
        "model_version": "test_v2",
        "feature_columns": list(FEATURE_COLUMNS),
        "model": _FakeModel(),
    }

    def _feat_row(**kwargs):
        base = {
            "elo_diff": 30,
            "home_season_win_pct": 0.7,
            "away_season_win_pct": 0.5,
            "home_rest_days": 7,
            "away_rest_days": 7,
            "rest_diff": 0,
            "neutral_site": 0,
            "home_field_active": 1,
            "home_last5_win_pct": 0.6,
            "away_last5_win_pct": 0.5,
            "last5_win_pct_diff": 0.1,
            "home_home_win_pct": 0.65,
            "conf_win_pct_diff": 0.0,
            "home_b2b": 0,
            "away_b2b": 0,
        }
        base.update(kwargs)
        return base

    feat = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2025-09-06",
                "season": 2025,
                "home_team": "Georgia",
                "away_team": "Clemson",
                "home_win": 1,
                "home_score": 34,
                "away_score": 3,
                **_feat_row(elo_diff=50, home_season_win_pct=0.8, away_season_win_pct=0.6),
            },
            {
                "game_id": "2",
                "date": "2025-09-13",
                "season": 2025,
                "home_team": "Alabama",
                "away_team": "Wisconsin",
                "home_win": 0,
                "home_score": 14,
                "away_score": 28,
                **_feat_row(elo_diff=30, home_season_win_pct=0.75),
            },
        ]
    )

    odds = pd.DataFrame(
        [
            {
                "date": "2025-09-06",
                "home_team": "Georgia",
                "away_team": "Clemson",
                "home_ml": -200,
                "away_ml": 170,
                "home_spread_point": -7.0,
                "ou_line": 52.5,
                "odds_source": "cfbd_lines",
            },
            {
                "date": "2025-09-13",
                "home_team": "Alabama",
                "away_team": "Wisconsin",
                "home_ml": -150,
                "away_ml": 130,
                "home_spread_point": -14.0,
                "ou_line": 48.5,
                "odds_source": "cfbd_lines",
            },
        ]
    )

    cache_dir = tmp_path / "cfb_lines_cache"
    cache_dir.mkdir()
    (cache_dir / "2025-09-06.json").write_text(
        json.dumps(
            {
                "date": "2025-09-06",
                "games": [
                    {
                        "game_date": "2025-09-06",
                        "home_team": "Georgia",
                        "away_team": "Clemson",
                        "home_ml": -200,
                        "away_ml": 170,
                        "home_spread_point": -7.0,
                        "ou_line": 52.5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("app.models.cfb_baseline.PARQUET_PATH", parquet)
    monkeypatch.setattr("app.odds.cfb_market_eval.load_games", lambda: games)
    monkeypatch.setattr("app.odds.cfb_market_eval.load_model_artifact", lambda: artifact)
    monkeypatch.setattr("app.odds.cfb_market_eval.build_features_for_history", lambda _g: feat)
    monkeypatch.setattr("app.odds.cfb_market_eval.load_holdout_odds", lambda _d: odds)
    monkeypatch.setattr(
        "app.odds.cfb_market_eval.MARKET_METRICS_JSON",
        tmp_path / "cfb_market_metrics.json",
    )
    monkeypatch.setattr(
        "app.odds.cfb_market_eval.MARKET_EVAL_CSV",
        tmp_path / "cfb_market_eval.csv",
    )
    monkeypatch.setattr("app.odds.cfb_betting_lines.LINES_CACHE_DIR", cache_dir)
    monkeypatch.setattr("app.odds.cfb_market_eval._spread_cover_log_loss", lambda _m: None)
    monkeypatch.setattr("app.odds.cfb_market_eval._totals_over_log_loss", lambda _m: None)
    return tmp_path


class _FakeModel:
    def predict_proba(self, X):
        import numpy as np

        n = len(X)
        return np.column_stack([1 - np.full(n, 0.65), np.full(n, 0.65)])


def test_merge_games_odds_match_rate():
    games = pd.DataFrame(
        [
            {
                "date": "2025-09-06",
                "home_team": "Georgia",
                "away_team": "Clemson",
                "home_win": 1,
            }
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "date": "2025-09-06",
                "home_team": "Georgia",
                "away_team": "Clemson",
                "home_ml": -200,
                "away_ml": 170,
            }
        ]
    )
    merged = _merge_games_odds(games, odds)
    assert len(merged) == 1
    assert merged.iloc[0]["home_ml"] == -200


def test_run_market_evaluation_computes_log_loss(tiny_holdout):
    results = run_market_evaluation(edge_threshold=0.08)
    assert results["holdout_games"] == 2
    assert results["matched_games"] == 2
    assert results["match_rate_pct"] == 100.0
    assert results["log_loss_model"] is not None
    assert results["log_loss_market"] is not None
    assert (tiny_holdout / "cfb_market_metrics.json").exists()
