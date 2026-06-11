"""NBA rolling backtest report tests (mocked — no Odds API)."""

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.services import nba_backtest_report as nbr

MODEL_KEYS = {"log_loss", "accuracy_pct", "winner_pick_rate_pct"}
MARKET_KEYS = {
    "games_with_odds",
    "odds_sources",
    "log_loss_market",
    "log_loss_model",
    "model_beats_market",
    "plus_ev_picks",
    "paper_trade_roi",
    "paper_trade_profit_units",
    "plus_ev_hit_rate",
    "edge_threshold",
}


def _sample_holdout_games():
    rows = []
    for i, day in enumerate(pd.date_range("2026-03-25", "2026-04-05", freq="D")):
        rows.append(
            {
                "game_id": f"nb{i:03d}",
                "date": day,
                "season": 2026,
                "home_team": "Orlando Magic",
                "away_team": "Atlanta Hawks",
                "home_win": i % 2,
                "home_score": 110,
                "away_score": 105,
            }
        )
    return pd.DataFrame(rows)


def _sample_features(games):
    feat = games.copy()
    for col in [
        "home_rest_days",
        "away_rest_days",
        "home_b2b",
        "away_b2b",
        "home_last10_win_pct",
        "away_last10_win_pct",
        "home_season_win_pct",
        "away_season_win_pct",
        "elo_home_pre",
        "elo_away_pre",
    ]:
        feat[col] = 0.5 if "win_pct" in col or "elo" in col else 1.0
    return feat


def test_empty_report_shape():
    report = nbr._empty_report(days=14, error="test")
    assert report["status"] == "error"
    assert MODEL_KEYS <= set(report["model"].keys())
    assert MARKET_KEYS <= set(report["market"].keys())


@patch("app.services.nba_backtest_report.load_holdout_odds")
@patch("app.services.nba_backtest_report.predict_home_win_proba")
@patch("app.services.nba_backtest_report.build_features_for_history")
@patch("app.services.nba_backtest_report.load_model_artifact")
@patch("app.services.nba_backtest_report.load_games")
def test_run_backtest_explicit_window(
    mock_load_games,
    mock_artifact,
    mock_feat,
    mock_predict,
    mock_odds,
    tmp_path,
    monkeypatch,
):
    games = _sample_holdout_games()
    features = _sample_features(games)
    mock_load_games.return_value = games
    mock_feat.return_value = features
    mock_predict.return_value = np.full(len(features), 0.6)
    mock_odds.return_value = pd.DataFrame()
    mock_artifact.return_value = {"model_version": "v1_logistic"}
    out_path = tmp_path / "nba_backtest_report.json"
    monkeypatch.setattr(nbr, "REPORT_JSON", out_path)

    report = nbr.run_nba_backtest_report(
        start_date=date(2026, 3, 25),
        end_date=date(2026, 4, 5),
        write_cache=True,
    )

    assert report["status"] == "ok"
    assert report["start_date"] == "2026-03-25"
    assert report["end_date"] == "2026-04-05"
    assert report["games_in_window"] == len(games)
    assert report["model"]["log_loss"] is not None
    assert report["model"]["accuracy_pct"] is not None
    assert out_path.exists()


@patch("app.services.nba_backtest_report._market_metrics")
@patch("app.services.nba_backtest_report._model_metrics")
@patch("app.services.nba_backtest_report.build_features_for_history")
@patch("app.services.nba_backtest_report.load_model_artifact")
@patch("app.services.nba_backtest_report.load_games")
def test_run_backtest_market_block_when_odds_present(
    mock_load_games,
    mock_artifact,
    mock_feat,
    mock_model_metrics,
    mock_market_metrics,
):
    games = _sample_holdout_games()
    mock_load_games.return_value = games
    mock_feat.return_value = _sample_features(games)
    mock_artifact.return_value = {"model_version": "v1_logistic"}
    mock_model_metrics.return_value = {
        "log_loss": 0.61,
        "accuracy_pct": 65.0,
        "winner_pick_rate_pct": 65.0,
    }
    mock_market_metrics.return_value = (
        {
            "games_with_odds": 8,
            "odds_sources": ["historical_cache"],
            "log_loss_market": 0.63,
            "log_loss_model": 0.61,
            "model_beats_market": True,
            "plus_ev_picks": 2,
            "paper_trade_roi": 0.15,
            "paper_trade_profit_units": 0.3,
            "plus_ev_hit_rate": 0.5,
            "edge_threshold": 0.08,
        },
        None,
    )

    report = nbr.run_nba_backtest_report(days=14, write_cache=False)

    assert report["market"]["plus_ev_picks"] == 2
    assert report["market"]["paper_trade_roi"] == 0.15
    assert report["market"]["model_beats_market"] is True


@patch("app.services.nba_backtest_report.load_games")
def test_run_backtest_days_mode(mock_load_games):
    games = _sample_holdout_games()
    mock_load_games.return_value = games
    with patch("app.services.nba_backtest_report.build_features_for_history") as mock_feat:
        mock_feat.return_value = _sample_features(games)
        with patch(
            "app.services.nba_backtest_report.predict_home_win_proba",
            side_effect=lambda df: np.full(len(df), 0.55),
        ):
            with patch(
                "app.services.nba_backtest_report.load_model_artifact",
                return_value={"model_version": "v1"},
            ):
                with patch(
                    "app.services.nba_backtest_report.load_holdout_odds",
                    return_value=pd.DataFrame(),
                ):
                    report = nbr.run_nba_backtest_report(days=7, write_cache=False)

    assert report["days"] == 7
    assert report["games_in_window"] >= 1
