"""Live board bypass: Run live syncs repository + daily board for main site."""

import json
from datetime import date
from unittest.mock import patch

import pytest

from app.services import daily_board as db

GAME_DATE = date(2026, 6, 6)


@pytest.fixture
def isolated_board(tmp_path, monkeypatch):
    path = tmp_path / "daily_board.json"
    monkeypatch.setattr(db, "DAILY_BOARD_CACHE", path)
    return path


@patch("app.services.daily_board.attach_market_odds")
@patch("app.services.daily_board._build_slate")
@patch("app.services.daily_board._totals_by_game")
@patch("app.services.daily_board._slate_rows")
def test_live_test_forces_refresh_and_full_totals(
    mock_slate_rows,
    mock_totals,
    mock_build_slate,
    mock_attach,
    isolated_board,
):
    import pandas as pd

    mock_build_slate.return_value = pd.DataFrame(
        {
            "game_id": ["1"],
            "home_team": ["A"],
            "away_team": ["B"],
            "home_ml": [-130],
            "away_ml": [110],
        }
    )
    mock_attach.return_value = (mock_build_slate.return_value, "the_odds_api")
    mock_totals.return_value = {}
    mock_slate_rows.return_value = []

    board = db.build_daily_board(
        game_date=GAME_DATE,
        use_cache=False,
        refresh=False,
        skip_totals=True,
        live_test=True,
    )

    assert board["board_live_test"] is True
    assert board["synced_to_main"] is True
    assert board["skip_totals"] is False
    assert "_totals_" in board["cache_key"]
    mock_attach.assert_called_once()
    assert mock_attach.call_args.kwargs["force_refresh"] is True
    assert isolated_board.exists()


@patch("app.odds.odds_repository.load_date")
def test_today_snapshot_includes_board_generated_at(mock_load, tmp_path, monkeypatch):
    from app.odds import odds_repository as repo

    today = date.today()
    board_path = tmp_path / "daily_board.json"
    board_path.write_text(
        json.dumps(
            {
                "date": today.isoformat(),
                "generated_at": "2026-06-06T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        repo,
        "PROJECT_ROOT",
        tmp_path.parent,
    )
    monkeypatch.setattr(
        repo,
        "_daily_board_generated_at",
        lambda d: "2026-06-06T12:00:00+00:00" if d == today else None,
    )
    mock_load.return_value = {"fetched_at": "2026-06-06T11:00:00+00:00", "games": []}

    snap = repo.get_today_snapshot()

    assert snap["board_generated_at"] == "2026-06-06T12:00:00+00:00"
    assert snap["fetched_at"] == "2026-06-06T11:00:00+00:00"


def test_api_daily_live_test_query():
    from fastapi.testclient import TestClient
    from app.main import app

    with patch("app.main.build_daily_board") as mock_build:
        mock_build.return_value = {"board_live_test": True, "slate": []}
        client = TestClient(app)
        resp = client.get("/api/daily?live_test=true&refresh=true")

    assert resp.status_code == 200
    mock_build.assert_called_once()
    assert mock_build.call_args.kwargs["live_test"] is True
