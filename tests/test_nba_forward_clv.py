"""NBA forward CLV logging and summary tests."""

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import nba_forward_clv as nfc

client = TestClient(app)

SAMPLE_SLATE_GAME = {
    "game_id": "401766458",
    "matchup": "Atlanta Hawks @ Orlando Magic",
    "away_team": "Atlanta Hawks",
    "home_team": "Orlando Magic",
    "model_prob_home": 0.58,
    "market_prob_home": 0.50,
    "plus_ev_single": True,
    "best_pick": {
        "side": "home",
        "team": "Orlando Magic",
        "edge": 0.10,
        "american_odds": 105,
    },
}


@pytest.fixture
def isolated_nba_clv_log(tmp_path, monkeypatch):
    log_path = tmp_path / "forward_clv_nba_log.jsonl"
    monkeypatch.setattr(nfc, "FORWARD_CLV_NBA_LOG", log_path)
    return log_path


def test_log_live_picks_writes_nba_plus_ev_singles(isolated_nba_clv_log):
    payload = {
        "mode": "live",
        "odds_source": "the_odds_api_live",
        "date": "2026-06-10",
        "edge_threshold": 0.08,
        "active_moneyline_model": {"model_version": "v1_logistic"},
        "slate": [SAMPLE_SLATE_GAME],
    }
    written = nfc.log_live_picks(payload)
    assert len(written) == 1
    assert written[0]["pick_id"] == "nba:2026-06-10:401766458:home"
    assert written[0]["sport"] == "nba"
    assert written[0]["betting_ready"] is False
    assert len(nfc._read_all_rows()) == 1


def test_log_live_picks_skips_non_live_sources(isolated_nba_clv_log):
    payload = {
        "mode": "live",
        "odds_source": "none",
        "date": "2026-06-10",
        "slate": [SAMPLE_SLATE_GAME],
    }
    assert nfc.log_live_picks(payload) == []


def test_backfill_nba_sets_close_fields(isolated_nba_clv_log, monkeypatch):
    nfc._append_row(
        {
            "sport": "nba",
            "pick_id": "nba:2026-06-10:401766458:home",
            "board_date": "2026-06-10",
            "home_team": "Orlando Magic",
            "away_team": "Atlanta Hawks",
            "side": "home",
            "american_odds_at_pick": 105,
            "market_prob_at_pick": 0.45,
            "game_id": "401766458",
        }
    )
    odds_df = pd.DataFrame(
        [
            {
                "date_key": "2026-06-10",
                "home_team": "Orlando Magic",
                "away_team": "Atlanta Hawks",
                "home_ml": -110,
                "away_ml": 100,
                "commence_time": "2026-06-10T23:00:00Z",
            }
        ]
    )
    monkeypatch.setattr(nfc, "_live_nba_odds_df", lambda: odds_df)
    monkeypatch.setattr(nfc, "load_games", lambda: pd.DataFrame())
    result = nfc.backfill_closing_odds(date(2026, 6, 10))
    assert result["updated"] == 1
    latest = nfc._latest_by_pick_id(nfc._read_all_rows())[
        "nba:2026-06-10:401766458:home"
    ]
    assert latest["close_american_odds"] == -110
    assert latest["clv_implied_prob"] is not None


def test_backfill_nba_dry_run_no_writes(isolated_nba_clv_log, monkeypatch):
    nfc._append_row(
        {
            "sport": "nba",
            "pick_id": "nba:2026-06-10:401766458:home",
            "board_date": "2026-06-10",
            "home_team": "Orlando Magic",
            "away_team": "Atlanta Hawks",
            "side": "home",
            "american_odds_at_pick": 105,
            "market_prob_at_pick": 0.45,
            "game_id": "401766458",
        }
    )
    odds_df = pd.DataFrame(
        [
            {
                "date_key": "2026-06-10",
                "home_team": "Orlando Magic",
                "away_team": "Atlanta Hawks",
                "home_ml": -110,
                "away_ml": 100,
                "commence_time": "2026-06-10T23:00:00Z",
            }
        ]
    )
    monkeypatch.setattr(nfc, "_live_nba_odds_df", lambda: odds_df)
    monkeypatch.setattr(nfc, "load_games", lambda: pd.DataFrame())
    result = nfc.backfill_closing_odds(date(2026, 6, 10), dry_run=True)
    assert result["dry_run"] is True
    assert result["updated"] == 1
    assert len(nfc._read_all_rows()) == 1


def test_api_clv_summary_nba():
    response = client.get("/api/clv/summary?sport=nba&days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["sport"] == "nba"
    assert body["betting_ready"] is False
    assert "edge_buckets" in body


@patch("app.services.nba_daily_board.get_nba_schedule")
@patch("app.services.nba_daily_board.predict_home_win_proba")
@patch("app.services.nba_daily_board.get_nba_odds_for_date")
@patch("app.services.nba_daily_board.log_live_picks")
def test_nba_daily_logs_clv_on_live_plus_ev(
    mock_log, mock_odds, mock_predict, mock_schedule, isolated_nba_clv_log
):
    mock_schedule.return_value = {
        "date": "2026-06-10",
        "games": [
            {
                "game_id": "401766458",
                "home_team": "Orlando Magic",
                "away_team": "Atlanta Hawks",
            }
        ],
    }
    mock_predict.return_value = pd.Series([0.70])
    mock_odds.return_value = (
        [
            {
                "home_team": "Orlando Magic",
                "away_team": "Atlanta Hawks",
                "home_ml": -130,
                "away_ml": 110,
            }
        ],
        "the_odds_api_live",
    )
    mock_log.return_value = [{"pick_id": "nba:2026-06-10:401766458:home"}]

    with patch("app.services.nba_daily_board.live_odds_enabled", return_value=True):
        resp = client.get("/api/nba/daily?date=2026-06-10&refresh=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["plus_ev_count"] >= 1
    mock_log.assert_called_once()
