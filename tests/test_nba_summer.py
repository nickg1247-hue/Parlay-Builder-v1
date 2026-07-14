"""NBA Summer League schedule + board smoke tests."""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scores_nba_summer as sns
from app.services.nba_summer_daily_board import build_nba_summer_daily_board

client = TestClient(app)

SAMPLE_GAME = {
    "sport": "nba-summer",
    "game_id": "401881859",
    "home_team": "Houston Rockets",
    "away_team": "Philadelphia 76ers",
    "home_team_abbr": "HOU",
    "away_team_abbr": "PHI",
    "start_time_utc": "2026-07-14T20:00Z",
    "status": "Preview",
    "series_summary": "Las Vegas Summer League",
    "summer_league": "nba-summer-las-vegas",
    "home_score": None,
    "away_score": None,
}


def test_summer_leagues_default():
    leagues = sns.summer_leagues()
    assert "nba-summer-las-vegas" in leagues


@patch("app.services.schedule_nba_summer.fetch_nba_summer_scores_day")
def test_api_schedule_nba_summer(mock_fetch):
    mock_fetch.return_value = [
        {
            "id": "401881859",
            "date": "2026-07-14T20:00Z",
            "name": "Philadelphia 76ers at Houston Rockets",
            "competitions": [
                {
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {
                                "id": "10",
                                "displayName": "Houston Rockets",
                                "abbreviation": "HOU",
                            },
                            "score": "",
                        },
                        {
                            "homeAway": "away",
                            "team": {
                                "id": "20",
                                "displayName": "Philadelphia 76ers",
                                "abbreviation": "PHI",
                            },
                            "score": "",
                        },
                    ],
                    "status": {"type": {"state": "pre", "description": "Scheduled"}},
                }
            ],
            "_summer_league": "nba-summer-las-vegas",
        }
    ]
    resp = client.get("/api/schedule/nba-summer?date=2026-07-14")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sport"] == "nba-summer"
    assert body["games_count"] >= 1
    assert body["games"][0]["game_id"] == "401881859"


@patch("app.services.nba_summer_daily_board.live_odds_enabled", return_value=False)
@patch("app.services.nba_summer_daily_board.get_nba_summer_schedule")
def test_build_summer_board_schedule_only(mock_sched, _live):
    mock_sched.return_value = {
        "date": "2026-07-14",
        "games": [SAMPLE_GAME],
        "games_count": 1,
    }
    board = build_nba_summer_daily_board(date(2026, 7, 14), refresh=True)
    assert board["sport"] == "nba-summer"
    assert board["pick_mode"] == "market_implied"
    assert board["betting_ready"] is False
    assert len(board["slate"]) == 1
    assert board["slate"][0]["model_pick_team"] is None


def test_nba_summer_pages_exist():
    assert client.get("/nba-summer").status_code == 200
    assert client.get("/nba-summer/board").status_code == 200
    assert client.get("/nba-summer/game/401881859").status_code == 200
