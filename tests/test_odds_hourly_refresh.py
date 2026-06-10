"""Hourly odds refresh tests."""

import json
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import odds_hourly_refresh as ohr

client = TestClient(app)


@pytest.fixture
def isolated_hourly_status(tmp_path, monkeypatch):
    status_path = tmp_path / "last_odds_hourly_refresh.json"
    monkeypatch.setattr(ohr, "LAST_HOURLY_ODDS_REFRESH", status_path)
    return status_path


@patch.dict("os.environ", {"USE_LIVE_ODDS": "false"}, clear=False)
def test_hourly_refresh_skips_when_free_mode(isolated_hourly_status):
    assert ohr.run_hourly_odds_refresh() == 0
    status = json.loads(isolated_hourly_status.read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert status["skipped"] == "USE_LIVE_ODDS=false"


@patch.dict("os.environ", {"ODDS_API_KEY": "key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.services.odds_hourly_refresh.get_mlb_schedule", return_value={"games": []})
def test_hourly_refresh_skips_no_games(_sched):
    assert ohr.run_hourly_odds_refresh() == 0


@patch.dict("os.environ", {"ODDS_API_KEY": "key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.services.odds_hourly_refresh.repository_age_seconds", return_value=None)
@patch("app.services.odds_hourly_refresh.get_mlb_schedule")
@patch("app.services.odds_hourly_refresh.get_mlb_odds_for_date")
@patch("app.services.odds_hourly_refresh.last_fetch_meta", return_value={"source": "the_odds_api_live"})
def test_hourly_refresh_calls_force_refresh(mock_meta, mock_get, mock_sched, _age, isolated_hourly_status):
    mock_sched.return_value = {"games": [{"game_id": "1"}]}
    mock_get.return_value = ([], "the_odds_api_live")

    assert ohr.run_hourly_odds_refresh() == 0
    mock_get.assert_called_once_with(date.today(), force_refresh=True)
    status = json.loads(isolated_hourly_status.read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert status["odds_source"] == "the_odds_api_live"


@patch.dict("os.environ", {"ODDS_API_KEY": "key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.services.odds_hourly_refresh.repository_age_seconds", return_value=120.0)
@patch("app.services.odds_hourly_refresh.get_mlb_schedule")
@patch("app.services.odds_hourly_refresh.get_mlb_odds_for_date")
def test_hourly_refresh_skips_when_recent(mock_get, mock_sched, _age):
    mock_sched.return_value = {"games": [{"game_id": "1"}]}

    assert ohr.run_hourly_odds_refresh() == 0
    mock_get.assert_not_called()


@patch.dict("os.environ", {"ODDS_API_KEY": "key", "USE_LIVE_ODDS": "true"}, clear=False)
@patch("app.services.odds_hourly_refresh.get_mlb_schedule")
@patch("app.services.odds_hourly_refresh.get_mlb_odds_for_date")
@patch("app.services.odds_hourly_refresh.last_fetch_meta")
def test_hourly_refresh_ok_when_quota_denied(mock_meta, mock_get, mock_sched):
    mock_sched.return_value = {"games": [{"game_id": "1"}]}
    mock_get.return_value = ([], "repository")
    mock_meta.return_value = {"quota_denied": True, "denied_reason": "hour_limit"}

    assert ohr.run_hourly_odds_refresh() == 0


@patch("app.odds.odds_repository.get_today_snapshot")
def test_api_odds_today(mock_snap):
    mock_snap.return_value = {
        "date": "2026-06-06",
        "fetched_at": "2026-06-06T12:00:00+00:00",
        "source": "the_odds_api_live",
        "games": [],
        "quota": {"hour_count": 3, "hour_max": 20, "day_count": 3, "day_max": 500},
    }
    resp = client.get("/api/odds/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["quota"]["hour_max"] == 20
