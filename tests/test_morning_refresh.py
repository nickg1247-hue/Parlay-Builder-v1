"""Morning refresh script and status API tests."""

import json
from datetime import date
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import morning_refresh as mr
client = TestClient(app)

SAMPLE_BOARD = {
    "date": "2026-06-06",
    "games_on_slate": 12,
    "odds_source": "the_odds_api",
    "slate": [],
}

SAMPLE_SCHEDULE = {
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


@pytest.fixture
def isolated_refresh_paths(tmp_path, monkeypatch):
    status_path = tmp_path / "last_morning_refresh.json"
    board_path = tmp_path / "daily_board.json"
    monkeypatch.setattr(mr, "LAST_MORNING_REFRESH", status_path)
    monkeypatch.setattr("app.services.daily_board.DAILY_BOARD_CACHE", board_path)
    return {"status": status_path, "board": board_path}


def test_run_morning_refresh_success(isolated_refresh_paths, monkeypatch, tmp_path):
    schedule_path = tmp_path / "mlb_schedule_2026-06-06.json"
    monkeypatch.setattr(
        "app.services.schedule_mlb.schedule_cache_path",
        lambda d: schedule_path,
    )

    with (
        patch(
            "app.services.mlb_data_freshness.ensure_mlb_ingest_fresh",
            return_value={"ran": False},
        ),
        patch(
            "app.services.mlb_data_freshness.ensure_odds_snapshot",
            return_value={"ran": False},
        ),
        patch(
            "app.services.morning_refresh.build_daily_board",
            return_value=SAMPLE_BOARD,
        ) as mock_build,
        patch(
            "app.services.morning_refresh.refresh_schedule_cache",
            return_value=SAMPLE_SCHEDULE,
        ) as mock_schedule,
    ):
        code = mr.run_morning_refresh(date(2026, 6, 6))

    assert code == 0
    mock_build.assert_called_once_with(
        game_date=date(2026, 6, 6),
        use_cache=False,
        refresh=True,
        skip_totals=True,
        min_edge=0.08,
        max_parlays=5,
        odds_force_refresh=False,
    )
    mock_schedule.assert_called_once_with(date(2026, 6, 6))

    status = json.loads(isolated_refresh_paths["status"].read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert status["date"] == "2026-06-06"
    assert status["games_on_slate"] == 12
    assert status["odds_source"] == "the_odds_api"
    assert status["error"] is None
    assert status["ran_at"] is not None


def test_run_morning_refresh_failure_preserves_board(isolated_refresh_paths):
    good_board = {"date": "2026-06-06", "slate": [{"game_id": "1"}]}
    isolated_refresh_paths["board"].write_text(
        json.dumps(good_board), encoding="utf-8"
    )

    with (
        patch(
            "app.services.mlb_data_freshness.ensure_mlb_ingest_fresh",
            return_value={"ran": False},
        ),
        patch(
            "app.services.mlb_data_freshness.ensure_odds_snapshot",
            return_value={"ran": False},
        ),
        patch(
            "app.services.morning_refresh.build_daily_board",
            side_effect=httpx.TimeoutException("timeout"),
        ),
    ):
        code = mr.run_morning_refresh(date(2026, 6, 6))

    assert code == 1
    assert json.loads(
        isolated_refresh_paths["board"].read_text(encoding="utf-8")
    ) == good_board

    status = json.loads(isolated_refresh_paths["status"].read_text(encoding="utf-8"))
    assert status["ok"] is False
    assert "timeout" in status["error"]


def test_get_refresh_status_default_when_missing(isolated_refresh_paths):
    with patch(
        "app.odds.odds_repository.get_today_snapshot",
        return_value={
            "fetched_at": None,
            "seconds_since_fetch": None,
            "source": None,
        },
    ):
        status = mr.get_refresh_status()
    assert status["ok"] is False
    assert status["ran_at"] is None
    assert status["error"] == "No morning refresh has run yet"
    assert "odds_fetched_at" in status


def test_api_status_refresh_returns_json(isolated_refresh_paths):
    isolated_refresh_paths["status"].write_text(
        json.dumps(
            {
                "ran_at": "2026-06-06T04:01:00+00:00",
                "ok": True,
                "date": "2026-06-06",
                "games_on_slate": 8,
                "odds_source": "the_odds_api",
                "error": None,
            }
        ),
        encoding="utf-8",
    )

    resp = client.get("/api/status/refresh")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["games_on_slate"] == 8


def test_display_refresh_timestamp_picks_latest():
    status = {
        "ran_at": "2026-06-06T04:01:00+00:00",
        "odds_fetched_at": "2026-06-06T05:30:00+00:00",
        "props_cached_at": "2026-06-06T03:00:00+00:00",
        "hourly_last": {"ok": True, "ran_at": "2026-06-06T06:00:00+00:00"},
    }
    display_at, source = mr._display_refresh_timestamp(status)
    assert source == "hourly_odds"
    assert display_at is not None
    assert "06:00:00" in display_at


def test_api_status_refresh_default_when_missing():
    with patch("app.main.get_refresh_status", return_value=mr._DEFAULT_STATUS):
        resp = client.get("/api/status/refresh")
    assert resp.status_code == 200
    assert resp.json()["error"] == "No morning refresh has run yet"
