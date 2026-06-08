"""NBA market evaluation tests (mocked — no Odds API)."""

from unittest.mock import patch

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.models.constants import DEFAULT_MIN_EDGE
from app.odds import nba_market_eval as nme
from app.odds.odds_math import market_probs_from_american


def _mini_games() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_id": "g1",
                "date": "2025-10-21",
                "season": 2026,
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
                "date": "2025-10-22",
                "season": 2026,
                "home_team": "Miami Heat",
                "away_team": "Boston Celtics",
                "home_score": 100,
                "away_score": 110,
                "home_win": 0,
                "home_rest_days": 2.0,
                "away_rest_days": 1.0,
                "home_b2b": 0,
                "away_b2b": 1,
            },
        ]
    )


def _mini_odds() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2025-10-21",
                "home_team": "Boston Celtics",
                "away_team": "New York Knicks",
                "home_ml": -150,
                "away_ml": 130,
                "odds_source": "test_csv",
            },
            {
                "date": "2025-10-22",
                "home_team": "Miami Heat",
                "away_team": "Boston Celtics",
                "home_ml": 110,
                "away_ml": -130,
                "odds_source": "test_csv",
            },
        ]
    )


def test_market_probs_remove_vig():
    home_p, away_p = market_probs_from_american(-110, -110)
    assert abs(home_p + away_p - 1.0) < 1e-9
    assert 0.45 < home_p < 0.55


def test_merge_games_odds():
    games = _mini_games()
    odds = _mini_odds()
    from app.features.nba_pregame import build_features

    feat = build_features(games, rest_fill=2.0, attach_elo=True)
    merged = nme._merge_games_odds(feat, odds)
    assert len(merged) == 2


def test_pick_side_respects_threshold():
    row = pd.Series({"edge_home": 0.10, "edge_away": 0.03})
    assert nme._pick_side(row, DEFAULT_MIN_EDGE) == "home"
    row2 = pd.Series({"edge_home": 0.02, "edge_away": 0.02})
    assert nme._pick_side(row2, DEFAULT_MIN_EDGE) is None


@patch("app.odds.nba_market_eval.build_features_for_history")
@patch("app.odds.nba_market_eval.load_holdout_odds")
@patch("app.odds.nba_market_eval.load_games")
@patch("app.odds.nba_market_eval.load_model_artifact")
def test_run_market_evaluation_mocked(
    mock_artifact, mock_games, mock_odds, mock_features
):
    games = _mini_games()
    mock_games.return_value = games
    mock_odds.return_value = _mini_odds()

    from app.features.nba_pregame import build_features

    mock_features.return_value = build_features(games, rest_fill=2.0, attach_elo=True)

    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=500)),
        ]
    )
    feat = mock_features.return_value
    from app.features.nba_pregame import FEATURE_COLUMNS

    pipe.fit(feat[FEATURE_COLUMNS].values, feat["home_win"].values)

    mock_artifact.return_value = {
        "model_version": "test_v1",
        "feature_columns": FEATURE_COLUMNS,
        "model": pipe,
    }

    results = nme.run_market_evaluation(edge_threshold=0.05)
    assert results["matched_games"] == 2
    assert results["plus_ev_picks"] >= 0
    assert results["betting_ready"] is False
    assert results["clv_required"] is True


@patch("app.odds.nba_market_eval.load_holdout_odds")
@patch("app.odds.nba_market_eval.load_games")
@patch("app.odds.nba_market_eval.load_model_artifact")
def test_run_market_evaluation_no_odds(mock_artifact, mock_games, mock_odds, tmp_path, monkeypatch):
    mock_games.return_value = _mini_games()
    mock_odds.return_value = pd.DataFrame()
    mock_artifact.return_value = {
        "model_version": "test_v1",
        "feature_columns": ["home_rest_days"],
        "model": None,
    }
    out = tmp_path / "nba_market_eval.json"
    monkeypatch.setattr(nme, "MARKET_EVAL_JSON", out)
    results = nme.run_market_evaluation()
    assert results["matched_games"] == 0
    assert out.exists()
