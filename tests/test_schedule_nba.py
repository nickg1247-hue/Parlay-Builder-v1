"""NBA schedule resolver and auto-advance tests."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scores_nba as sn
from app.services.schedule_nba import get_nba_game, get_nba_schedule, resolve_nba_slate_date

client = TestClient(app)

ESPN_EVENT = {
    "id": "401766458",
    "date": "2025-04-15T23:30Z",
    "competitions": [
        {
            "date": "2025-04-15T23:30Z",
            "status": {"type": {"state": "pre", "description": "Scheduled"}},
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {"id": "19", "displayName": "Orlando Magic"},
                },
                {
                    "homeAway": "away",
                    "team": {"id": "1", "displayName": "Atlanta Hawks"},
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


@patch("app.services.schedule_nba.fetch_nba_scores_day")
def test_resolve_nba_slate_date_empty_week_stays_today(mock_fetch):
    start = date(2025, 6, 10)
    mock_fetch.return_value = []
    resolved, days_ahead = resolve_nba_slate_date(start)
    assert resolved == start
    assert days_ahead == 0
    assert mock_fetch.call_count == 8  # today + 7 look-ahead days


@patch("app.services.schedule_nba.fetch_nba_scores_day")
def test_resolve_nba_slate_date_advances_when_today_empty(mock_fetch):
    start = date(2025, 6, 10)

    def side_effect(game_date: date):
        offset = (game_date - start).days
        if offset == 2:
            return [ESPN_EVENT]
        return []

    mock_fetch.side_effect = side_effect
    resolved, days_ahead = resolve_nba_slate_date(start)
    assert resolved == start + timedelta(days=2)
    assert days_ahead == 2


@patch("app.services.schedule_nba.fetch_nba_scores_day")
def test_resolve_nba_slate_date_same_day_when_games(mock_fetch):
    start = date(2025, 6, 10)
    mock_fetch.return_value = [ESPN_EVENT]
    resolved, days_ahead = resolve_nba_slate_date(start)
    assert resolved == start
    assert days_ahead == 0
    mock_fetch.assert_called_once_with(start)


@patch("app.services.schedule_nba.fetch_nba_scores_day")
def test_get_nba_schedule_explicit_date_skips_resolve(mock_fetch):
    explicit = date(2025, 4, 15)
    mock_fetch.return_value = [ESPN_EVENT]
    payload = get_nba_schedule(explicit, auto_resolve=False)
    assert payload["resolved_date"] == explicit.isoformat()
    assert payload["requested_date"] == explicit.isoformat()
    assert payload["days_ahead"] == 0
    assert payload["auto_advanced"] is False


@patch("app.services.schedule_nba.fetch_nba_scores_day")
def test_api_schedule_nba_auto_advanced_fields(mock_fetch):
    today = date.today()
    target = today + timedelta(days=2)

    def side_effect(game_date: date):
        if game_date == target:
            return [ESPN_EVENT]
        if (game_date - today).days <= 1:
            return []
        return []

    mock_fetch.side_effect = side_effect
    resp = client.get("/api/schedule/nba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_advanced"] is True
    assert data["days_ahead"] == 2
    assert data["resolved_date"] == target.isoformat()
    assert data["requested_date"] == today.isoformat()


@patch("app.services.schedule_nba.fetch_nba_scores_day")
def test_api_schedule_nba_explicit_date_no_auto(mock_fetch):
    mock_fetch.return_value = [ESPN_EVENT]
    resp = client.get("/api/schedule/nba?date=2025-04-15")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_advanced"] is False
    assert data["days_ahead"] == 0
    assert data["resolved_date"] == "2025-04-15"


@patch("app.services.schedule_nba.fetch_nba_scores_day")
@patch("app.services.schedule_nba.cache_is_fresh", return_value=True)
def test_get_nba_game_bypasses_stale_empty_cache(
    mock_fresh, mock_fetch, tmp_path, monkeypatch
):
    future = date.today() + timedelta(days=2)
    cache_path = tmp_path / f"nba_schedule_{future.isoformat()}.json"
    cache_path.write_text(
        json.dumps(
            {
                "date": future.isoformat(),
                "games": [],
                "games_count": 0,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.schedule_nba.schedule_cache_path",
        lambda d: tmp_path / f"nba_schedule_{d.isoformat()}.json",
    )
    mock_fetch.return_value = [ESPN_EVENT]
    detail = get_nba_game("401766458", future)
    assert detail is not None
    assert detail["game"]["game_id"] == "401766458"
    mock_fetch.assert_called()


@patch("app.services.schedule_nba.fetch_nba_scores_day")
@patch("app.services.schedule_nba.cache_is_fresh", return_value=True)
def test_api_games_nba_returns_200_with_stale_empty_cache(
    mock_fresh, mock_fetch, tmp_path, monkeypatch
):
    future = date.today() + timedelta(days=2)
    cache_path = tmp_path / f"nba_schedule_{future.isoformat()}.json"
    cache_path.write_text(
        json.dumps(
            {
                "date": future.isoformat(),
                "games": [],
                "games_count": 0,
                "cached_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.schedule_nba.schedule_cache_path",
        lambda d: tmp_path / f"nba_schedule_{d.isoformat()}.json",
    )
    mock_fetch.return_value = [ESPN_EVENT]
    resp = client.get(f"/api/games/nba/401766458?date={future.isoformat()}")
    assert resp.status_code == 200
    assert resp.json()["game"]["home_team"] == "Orlando Magic"
