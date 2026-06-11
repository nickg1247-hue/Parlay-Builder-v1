"""CFB schedule resolver and auto-advance tests."""

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scores_cfb as sc
from app.services.schedule_cfb import get_cfb_schedule, resolve_cfb_slate_date

client = TestClient(app)

ESPN_EVENT = {
    "id": "401635525",
    "date": "2024-11-30T17:00Z",
    "competitions": [
        {
            "date": "2024-11-30T17:00Z",
            "status": {"type": {"state": "pre", "description": "Scheduled"}, "period": 0},
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {
                        "id": "61",
                        "displayName": "Georgia Bulldogs",
                        "abbreviation": "UGA",
                        "logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/61.png",
                    },
                    "records": [{"name": "overall", "summary": "10-1"}],
                },
                {
                    "homeAway": "away",
                    "team": {
                        "id": "2",
                        "displayName": "Georgia Tech Yellow Jackets",
                        "abbreviation": "GT",
                        "logo": "https://a.espncdn.com/i/teamlogos/ncaa/500/2.png",
                    },
                    "records": [{"name": "overall", "summary": "7-4"}],
                },
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def clear_cfb_cache(tmp_path, monkeypatch):
    sc.clear_scores_cache()
    monkeypatch.setattr(
        "app.services.schedule_cfb.schedule_cache_path",
        lambda d: tmp_path / f"cfb_schedule_{d.isoformat()}.json",
    )
    yield
    sc.clear_scores_cache()


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
def test_resolve_cfb_slate_date_advances_when_today_empty(mock_fetch):
    start = date.today()

    def side_effect(game_date: date):
        offset = (game_date - start).days
        if offset == 3:
            return [ESPN_EVENT]
        return []

    mock_fetch.side_effect = side_effect
    resolved, days_ahead = resolve_cfb_slate_date(start)
    assert resolved == start + timedelta(days=3)
    assert days_ahead == 3


def test_resolve_cfb_slate_date_same_day_when_ingest_has_games():
    start = date(2024, 11, 30)
    resolved, days_ahead = resolve_cfb_slate_date(start)
    assert resolved == start
    assert days_ahead == 0


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
def test_get_cfb_schedule_returns_logos(mock_fetch):
    future = date.today() + timedelta(days=30)
    mock_fetch.return_value = [ESPN_EVENT]
    payload = get_cfb_schedule(future, auto_resolve=False)
    assert payload["games_count"] == 1
    game = payload["games"][0]
    assert game["home_logo_url"]
    assert game["away_logo_url"]
    assert game["home_record"] == "10-1"


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
def test_api_schedule_cfb_auto_advanced_fields(mock_fetch):
    today = date.today()
    target = today + timedelta(days=5)

    def side_effect(game_date: date):
        if game_date == target:
            return [ESPN_EVENT]
        return []

    mock_fetch.side_effect = side_effect
    resp = client.get("/api/schedule/cfb")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_advanced"] is True
    assert data["days_ahead"] == 5


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
def test_schedule_cache_hit(mock_fetch, tmp_path):
    game_date = date(2024, 11, 30)
    cache_path = tmp_path / f"cfb_schedule_{game_date.isoformat()}.json"
    cache_path.write_text(
        json.dumps(
            {
                "date": game_date.isoformat(),
                "sport": "cfb",
                "games": [sc.live_game_record(ESPN_EVENT)],
                "games_count": 1,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    payload = get_cfb_schedule(game_date, auto_resolve=False)
    assert payload["source"] == "cache"
    mock_fetch.assert_not_called()


@patch("app.services.schedule_cfb.fetch_cfb_scores_day")
@patch("app.services.schedule_cfb.games_from_ingest")
def test_past_date_uses_ingest_and_saves_cache(mock_ingest, mock_fetch, tmp_path):
    past = date(2020, 1, 1)
    mock_ingest.return_value = [
        {
            "sport": "cfb",
            "game_id": "999",
            "home_team": "Alabama",
            "away_team": "Auburn",
            "home_score": 28,
            "away_score": 14,
            "status": "Final",
        }
    ]
    payload = get_cfb_schedule(past, auto_resolve=False)
    assert payload["source"] == "ingest"
    assert payload["games_count"] == 1
    mock_fetch.assert_not_called()
    cache_path = tmp_path / f"cfb_schedule_{past.isoformat()}.json"
    assert cache_path.exists()

    payload2 = get_cfb_schedule(past, auto_resolve=False)
    assert payload2["source"] == "ingest"
    mock_fetch.assert_not_called()
    mock_ingest.assert_called_once()


def test_cfb_slate_page():
    resp = client.get("/cfb")
    assert resp.status_code == 200
    assert "College Football" in resp.text
    assert "/api/cfb/predictions" in resp.text
    assert "slate-date-input" in resp.text
