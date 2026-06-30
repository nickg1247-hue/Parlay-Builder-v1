"""MLB periodic ingest + board refresh tests."""

import json
from datetime import date
from unittest.mock import patch

import pytest

from app.services import mlb_periodic_refresh as mpr


@pytest.fixture
def isolated_periodic_status(tmp_path, monkeypatch):
    status_path = tmp_path / "last_mlb_periodic_refresh.json"
    monkeypatch.setattr(mpr, "LAST_MLB_PERIODIC_REFRESH", status_path)
    return status_path


@patch.dict("os.environ", {"MLB_PERIODIC_REFRESH": "false"}, clear=False)
def test_periodic_skips_when_disabled(isolated_periodic_status):
    assert mpr.run_mlb_periodic_refresh() == 0
    status = json.loads(isolated_periodic_status.read_text(encoding="utf-8"))
    assert status["skipped"] == "disabled"


@patch.dict("os.environ", {"MLB_PERIODIC_REFRESH": "true"}, clear=False)
@patch("app.services.mlb_periodic_refresh.get_mlb_schedule", return_value={"games": []})
def test_periodic_skips_no_games(_sched, isolated_periodic_status):
    assert mpr.run_mlb_periodic_refresh() == 0
    status = json.loads(isolated_periodic_status.read_text(encoding="utf-8"))
    assert status["skipped"] == "no_games_on_slate"


@patch.dict(
    "os.environ",
    {"MLB_PERIODIC_REFRESH": "true", "MLB_PERIODIC_FORCE_INGEST": "false"},
    clear=False,
)
@patch("app.services.mlb_periodic_refresh.build_daily_board")
@patch("app.services.mlb_periodic_refresh.ensure_odds_snapshot")
@patch("app.services.mlb_periodic_refresh.ensure_mlb_ingest_fresh", return_value={"ran": False})
@patch(
    "app.services.mlb_periodic_refresh.get_mlb_schedule",
    return_value={"games": [{"game_id": "1"}]},
)
def test_periodic_rebuilds_board(
    _sched, _ingest, _odds, mock_board, isolated_periodic_status
):
    mock_board.return_value = {
        "games_on_slate": 5,
        "odds_source": "the_odds_api",
        "slate": [],
    }
    assert mpr.run_mlb_periodic_refresh(date(2026, 6, 30)) == 0
    mock_board.assert_called_once()
    status = json.loads(isolated_periodic_status.read_text(encoding="utf-8"))
    assert status["ok"] is True
    assert status["board_games"] == 5


@patch.dict("os.environ", {"MLB_PERIODIC_REFRESH": "true"}, clear=False)
@patch("app.services.mlb_periodic_refresh.build_daily_board")
@patch(
    "app.services.mlb_periodic_refresh.get_mlb_schedule",
    return_value={"games": [{"game_id": "1"}]},
)
def test_periodic_respects_interval(_sched, mock_board, isolated_periodic_status):
    from datetime import datetime, timezone

    isolated_periodic_status.write_text(
        json.dumps(
            {
                "ran_at": datetime.now(timezone.utc).isoformat(),
                "ok": True,
                "date": "2026-06-30",
            }
        ),
        encoding="utf-8",
    )
    assert mpr.run_mlb_periodic_refresh(date(2026, 6, 30)) == 0
    mock_board.assert_not_called()
