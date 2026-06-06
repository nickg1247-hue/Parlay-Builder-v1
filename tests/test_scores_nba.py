"""Phase D NBA scores tests."""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scores_nba as sn
from app.services import scores_today as st

client = TestClient(app)

ESPN_EVENT = {
    "id": "401766458",
    "date": "2025-04-15T23:30Z",
    "competitions": [
        {
            "date": "2025-04-15T23:30Z",
            "status": {
                "period": 4,
                "displayClock": "0.0",
                "type": {
                    "state": "post",
                    "description": "Final",
                    "shortDetail": "Final",
                },
            },
            "competitors": [
                {
                    "homeAway": "home",
                    "score": "120",
                    "team": {
                        "id": "19",
                        "displayName": "Orlando Magic",
                        "logo": "https://a.espncdn.com/i/teamlogos/nba/500/scoreboard/orl.png",
                    },
                },
                {
                    "homeAway": "away",
                    "score": "95",
                    "team": {
                        "id": "1",
                        "displayName": "Atlanta Hawks",
                        "logo": "https://a.espncdn.com/i/teamlogos/nba/500/scoreboard/atl.png",
                    },
                },
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def clear_nba_cache():
    sn.clear_scores_cache()
    yield
    sn.clear_scores_cache()


def test_live_game_record_nba():
    row = sn.live_game_record(ESPN_EVENT)
    assert row["sport"] == "nba"
    assert row["game_id"] == "401766458"
    assert row["home_team"] == "Orlando Magic"
    assert row["away_score"] == 95
    assert row["home_score"] == 120
    assert row["status"] == "Final"


@patch("app.services.scores_nba.fetch_nba_scores_day", return_value=[ESPN_EVENT])
def test_get_nba_scores_today(mock_fetch):
    payload = sn.get_nba_scores_today(date(2025, 4, 15))
    assert payload["games_count"] == 1
    mock_fetch.assert_called_once()


@patch("app.services.scores_today.get_nba_scores_today")
@patch("app.services.scores_today.get_mlb_scores_today")
def test_merged_scores_all(mock_mlb, mock_nba):
    mock_mlb.return_value = {
        "sport": "mlb",
        "date": "2026-06-06",
        "games": [{"game_id": "1", "start_time_utc": "2026-06-06T18:00:00Z"}],
        "games_count": 1,
        "cached_at": "2026-06-06T10:00:00+00:00",
        "cache_hit": False,
    }
    mock_nba.return_value = {
        "sport": "nba",
        "date": "2026-06-06",
        "games": [{"game_id": "2", "start_time_utc": "2026-06-06T20:00:00Z"}],
        "games_count": 1,
        "cached_at": "2026-06-06T10:00:00+00:00",
        "cache_hit": False,
    }
    merged = st.get_scores_today(sport="all", game_date=date(2026, 6, 6))
    assert merged["sport"] == "all"
    assert merged["games_count"] == 2
    assert merged["games"][0]["sport"] == "mlb"
    assert merged["games"][1]["sport"] == "nba"


def test_api_scores_nba():
    with patch(
        "app.services.scores_nba.fetch_nba_scores_day",
        return_value=[ESPN_EVENT],
    ):
        resp = client.get("/api/scores/today?sport=nba&date=2025-04-15")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sport"] == "nba"
    assert data["games"][0]["home_team"] == "Orlando Magic"
