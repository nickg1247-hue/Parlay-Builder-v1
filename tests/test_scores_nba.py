"""Phase D NBA scores tests."""

from datetime import date, timedelta
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
    "name": "Eastern Conference Finals - Game 5",
    "competitions": [
        {
            "date": "2025-04-15T23:30Z",
            "series": {"summary": "ORL leads 3-2"},
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
                    "records": [{"name": "overall", "summary": "45-37", "type": "total"}],
                    "team": {
                        "id": "19",
                        "displayName": "Orlando Magic",
                        "abbreviation": "ORL",
                        "logo": "https://a.espncdn.com/i/teamlogos/nba/500/scoreboard/orl.png",
                    },
                },
                {
                    "homeAway": "away",
                    "score": "95",
                    "records": [{"name": "overall", "summary": "40-42", "type": "total"}],
                    "team": {
                        "id": "1",
                        "displayName": "Atlanta Hawks",
                        "abbreviation": "ATL",
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
    assert row["home_record"] == "45-37"
    assert row["away_record"] == "40-42"
    assert row["home_team_abbr"] == "ORL"
    assert row["series_summary"] == "ORL leads 3-2"


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
    assert data["auto_advanced"] is False
    assert data["days_ahead"] == 0


@patch("app.services.scores_nba.fetch_nba_scores_day")
@patch("app.services.schedule_nba.fetch_nba_scores_day")
def test_api_scores_nba_auto_resolve(mock_sched_fetch, mock_scores_fetch):
    today = date.today()
    target = today + timedelta(days=1)

    def side_effect(game_date: date):
        if game_date == target:
            return [ESPN_EVENT]
        return []

    mock_sched_fetch.side_effect = side_effect
    mock_scores_fetch.side_effect = side_effect
    resp = client.get("/api/scores/today?sport=nba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_advanced"] is True
    assert data["days_ahead"] == 1
    assert data["resolved_date"] == target.isoformat()
    assert data["games_count"] == 1
