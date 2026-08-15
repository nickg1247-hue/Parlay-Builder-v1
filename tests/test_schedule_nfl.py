"""NFL schedule resolver and auto-advance tests."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scores_nfl as sc
from app.services.schedule_nfl import get_nfl_schedule, resolve_nfl_slate_date

client = TestClient(app)

ESPN_EVENT = {
    "id": "401671617",
    "date": "2024-09-05T00:20Z",
    "season": {"year": 2024, "type": 2},
    "week": {"number": 1},
    "competitions": [
        {
            "date": "2024-09-05T00:20Z",
            "neutralSite": False,
            "status": {"type": {"state": "pre", "description": "Scheduled"}, "period": 0},
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {
                        "id": "12",
                        "displayName": "Kansas City Chiefs",
                        "abbreviation": "KC",
                        "logo": "https://a.espncdn.com/i/teamlogos/nfl/500/kc.png",
                    },
                    "records": [{"name": "overall", "summary": "0-0"}],
                },
                {
                    "homeAway": "away",
                    "team": {
                        "id": "33",
                        "displayName": "Baltimore Ravens",
                        "abbreviation": "BAL",
                        "logo": "https://a.espncdn.com/i/teamlogos/nfl/500/bal.png",
                    },
                    "records": [{"name": "overall", "summary": "0-0"}],
                },
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def clear_nfl_cache(tmp_path, monkeypatch):
    sc.clear_scores_cache()
    monkeypatch.setattr(
        "app.services.schedule_nfl.schedule_cache_path",
        lambda d: tmp_path / f"nfl_schedule_{d.isoformat()}.json",
    )
    yield
    sc.clear_scores_cache()


@patch("app.services.schedule_nfl.fetch_nfl_scores_day")
def test_resolve_nfl_slate_date_advances_when_today_empty(mock_fetch):
    start = date.today()

    def side_effect(game_date: date):
        offset = (game_date - start).days
        if offset == 3:
            return [ESPN_EVENT]
        return []

    mock_fetch.side_effect = side_effect
    resolved, days_ahead = resolve_nfl_slate_date(start)
    assert resolved == start + timedelta(days=3)
    assert days_ahead == 3


@patch("app.services.schedule_nfl.fetch_nfl_scores_day")
def test_schedule_api_uses_espn_game_id(mock_fetch):
    mock_fetch.return_value = [ESPN_EVENT]
    payload = get_nfl_schedule(date(2026, 9, 10), auto_resolve=False, force_live=True)
    assert payload["games_count"] == 1
    game = payload["games"][0]
    assert game["game_id"] == "401671617"
    assert game["sport"] == "nfl"
    assert game["home_team_abbr"] == "KC"


@patch("app.services.schedule_nfl.fetch_nfl_scores_day", side_effect=Exception("espn down"))
def test_resolve_survives_espn_failure(mock_fetch):
    start = date.today()
    resolved, days_ahead = resolve_nfl_slate_date(start)
    assert resolved == start + timedelta(days=7)
    assert days_ahead == 7


@patch("app.services.scores_today.get_ufc_scores_today", side_effect=TimeoutError)
@patch("app.services.scores_today.get_cfb_scores_today", return_value={"games": [], "games_count": 0})
@patch("app.services.scores_today.get_nba_scores_today", return_value={"games": [], "games_count": 0})
@patch("app.services.scores_today.get_mlb_scores_today", return_value={"games": [], "games_count": 0, "cached_at": ""})
@patch("app.services.scores_today.get_nfl_scores_today", side_effect=TimeoutError("nfl hung"))
def test_merged_scores_survive_nfl_failure(_nfl, _mlb, _nba, _cfb, _ufc):
    from app.services.scores_today import get_scores_today

    merged = get_scores_today(sport="all", game_date=date(2026, 8, 14))
    assert merged["sport"] == "all"
    assert merged["games_count"] == 0


@patch("app.services.schedule_nfl.fetch_nfl_scores_day")
def test_schedule_endpoint(mock_fetch):
    mock_fetch.return_value = [ESPN_EVENT]
    resp = client.get("/api/schedule/nfl", params={"date": "2026-09-10", "refresh": "true"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["games"][0]["game_id"] == "401671617"
