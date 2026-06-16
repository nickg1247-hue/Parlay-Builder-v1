"""Schedule API and cache tests (Phase A)."""

import json
import time
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import schedule_mlb as sm

client = TestClient(app)

SAMPLE_API_GAMES = [
    {
        "gamePk": 777001,
        "gameDate": "2026-06-06T23:05:00Z",
        "gameType": "R",
        "status": {"abstractGameState": "Preview", "detailedState": "Scheduled"},
        "teams": {
            "home": {
                "team": {"id": 147, "name": "New York Yankees"},
                "score": None,
            },
            "away": {
                "team": {"id": 111, "name": "Boston Red Sox"},
                "score": None,
            },
        },
    }
]


@pytest.fixture
def isolated_schedule_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(sm, "MLB_TEAMS_PATH", tmp_path / "mlb_teams.json")
    monkeypatch.setattr(sm, "DAILY_BOARD_CACHE", tmp_path / "daily_board.json")
    return tmp_path


def test_cache_is_fresh_within_ttl(isolated_schedule_paths):
    path = isolated_schedule_paths / "mlb_schedule_2026-06-06.json"
    path.write_text("{}", encoding="utf-8")
    assert sm.cache_is_fresh(path) is True


def test_cache_is_stale_after_ttl(isolated_schedule_paths, monkeypatch):
    path = isolated_schedule_paths / "mlb_schedule_2026-06-06.json"
    path.write_text("{}", encoding="utf-8")
    old = time.time() - sm.SCHEDULE_CACHE_TTL_SECONDS - 10
    import os

    os.utime(path, (old, old))
    assert sm.cache_is_fresh(path) is False


def test_get_mlb_schedule_cache_hit(isolated_schedule_paths):
    game_date = date(2026, 6, 6)
    cache_path = sm.schedule_cache_path(game_date)
    payload = {
        "date": "2026-06-06",
        "games": [
            {
                "game_id": "777001",
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "home_team_id": 147,
                "away_team_id": 111,
                "start_time_utc": "2026-06-06T23:05:00Z",
                "status": "Preview",
                "home_score": None,
                "away_score": None,
            }
        ],
        "games_count": 1,
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    with patch("app.services.schedule_mlb.fetch_mlb_schedule_day") as mock_fetch:
        result = sm.get_mlb_schedule(game_date)

    mock_fetch.assert_not_called()
    assert result["source"] == "cache"
    assert result["games_count"] == 1
    assert result["games"][0]["home_logo_url"]


def test_get_mlb_schedule_cache_miss_fetches_api(isolated_schedule_paths):
    game_date = date(2026, 6, 6)
    with patch(
        "app.services.schedule_mlb.fetch_mlb_schedule_day",
        return_value=SAMPLE_API_GAMES,
    ) as mock_fetch:
        result = sm.get_mlb_schedule(game_date)

    mock_fetch.assert_called_once_with(game_date)
    assert result["source"] == "api"
    assert result["games_count"] == 1
    assert sm.schedule_cache_path(game_date).exists()
    assert sm.MLB_TEAMS_PATH.exists()
    teams = json.loads(sm.MLB_TEAMS_PATH.read_text(encoding="utf-8"))
    assert teams["New York Yankees"] == 147


def test_get_mlb_game_with_board_row(isolated_schedule_paths):
    game_date = date(2026, 6, 6)
    isolated_schedule_paths.joinpath("daily_board.json").write_text(
        json.dumps(
            {
                "slate": [
                    {
                        "game_id": "777001",
                        "matchup": "Boston Red Sox @ New York Yankees",
                        "display_prob_home": 0.55,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with patch(
        "app.services.schedule_mlb.fetch_mlb_schedule_day",
        return_value=SAMPLE_API_GAMES,
    ):
        sm.refresh_schedule_cache(game_date)

    result = sm.get_mlb_game("777001", game_date)
    assert result is not None
    assert result["game"]["game_id"] == "777001"
    assert result["board_row"]["display_prob_home"] == 0.55


def test_api_schedule_mlb(isolated_schedule_paths, monkeypatch):
    monkeypatch.setattr(sm, "PROCESSED_DIR", isolated_schedule_paths)
    monkeypatch.setattr(sm, "MLB_TEAMS_PATH", isolated_schedule_paths / "mlb_teams.json")
    with patch(
        "app.services.schedule_mlb.fetch_mlb_schedule_day",
        return_value=SAMPLE_API_GAMES,
    ):
        resp = client.get("/api/schedule/mlb?date=2026-06-06")
    assert resp.status_code == 200
    data = resp.json()
    assert data["games_count"] == 1
    assert data["source"] in ("api", "cache")


def test_api_games_mlb_found(isolated_schedule_paths, monkeypatch):
    monkeypatch.setattr(sm, "PROCESSED_DIR", isolated_schedule_paths)
    monkeypatch.setattr(sm, "MLB_TEAMS_PATH", isolated_schedule_paths / "mlb_teams.json")
    with patch(
        "app.services.schedule_mlb.fetch_mlb_schedule_day",
        return_value=SAMPLE_API_GAMES,
    ):
        client.get("/api/schedule/mlb?date=2026-06-06")
        resp = client.get("/api/games/mlb/777001?date=2026-06-06")
    assert resp.status_code == 200
    assert resp.json()["game"]["home_team"] == "New York Yankees"


def test_api_games_mlb_not_found(isolated_schedule_paths, monkeypatch):
    monkeypatch.setattr(sm, "PROCESSED_DIR", isolated_schedule_paths)
    monkeypatch.setattr(sm, "MLB_TEAMS_PATH", isolated_schedule_paths / "mlb_teams.json")
    with patch(
        "app.services.schedule_mlb.fetch_mlb_schedule_day",
        return_value=SAMPLE_API_GAMES,
    ):
        client.get("/api/schedule/mlb?date=2026-06-06")
        resp = client.get("/api/games/mlb/999999?date=2026-06-06")
    assert resp.status_code == 404
