"""Live scores API tests (Phase B)."""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scores_mlb as sm

client = TestClient(app)

LIVE_API_GAME = {
    "gamePk": 777001,
    "gameDate": "2026-06-06T23:05:00Z",
    "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
    "linescore": {"currentInning": 7, "inningState": "Bottom"},
    "teams": {
        "home": {
            "team": {"id": 147, "name": "New York Yankees"},
            "score": 5,
        },
        "away": {
            "team": {"id": 111, "name": "Boston Red Sox"},
            "score": 3,
        },
    },
}


@pytest.fixture(autouse=True)
def clear_cache():
    sm.clear_scores_cache()
    yield
    sm.clear_scores_cache()


def test_period_label_bot_seventh():
    assert sm.period_label(LIVE_API_GAME) == "Bot 7th"


def test_live_game_record_fields():
    row = sm.live_game_record(LIVE_API_GAME)
    assert row["game_id"] == "777001"
    assert row["home_score"] == 5
    assert row["away_score"] == 3
    assert row["period_label"] == "Bot 7th"
    assert row["status"] == "Live"


def test_get_scores_today_cache_hit():
    game_date = date(2026, 6, 6)
    with patch(
        "app.services.scores_mlb.fetch_mlb_scores_day",
        return_value=[LIVE_API_GAME],
    ) as mock_fetch:
        first = sm.get_scores_today(game_date=game_date)
        second = sm.get_scores_today(game_date=game_date)

    mock_fetch.assert_called_once()
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["games_count"] == 1


def test_api_scores_today():
    with patch(
        "app.services.scores_mlb.fetch_mlb_scores_day",
        return_value=[LIVE_API_GAME],
    ):
        resp = client.get("/api/scores/today?sport=mlb&date=2026-06-06")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sport"] == "mlb"
    assert data["games"][0]["period_label"] == "Bot 7th"


def test_get_live_game():
    with patch(
        "app.services.scores_mlb.fetch_mlb_scores_day",
        return_value=[LIVE_API_GAME],
    ):
        game = sm.get_live_game("777001", date(2026, 6, 6))
    assert game is not None
    assert game["home_team"] == "New York Yankees"
