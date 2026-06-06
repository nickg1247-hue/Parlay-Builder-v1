"""Daily board morning cache fallback tests."""

import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.services import daily_board as db

GAME_DATE = date(2026, 6, 6)
MORNING_CACHE_KEY = (
    f"{GAME_DATE.isoformat()}_live_totals_edge0.08_parlays5"
)


@pytest.fixture
def isolated_board(tmp_path, monkeypatch):
    path = tmp_path / "daily_board.json"
    monkeypatch.setattr(db, "DAILY_BOARD_CACHE", path)
    return path


def _write_morning_board(path, *, age_seconds: float = 60) -> None:
    generated = datetime.now(timezone.utc) - __import__("datetime").timedelta(
        seconds=age_seconds
    )
    payload = {
        "generated_at": generated.isoformat(),
        "cache_key": MORNING_CACHE_KEY,
        "date": GAME_DATE.isoformat(),
        "mode": "live",
        "skip_totals": False,
        "odds_source": "the_odds_api",
        "slate": [{"game_id": "1", "matchup": "A @ B"}],
        "top_parlays": [],
        "warnings": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@patch("app.services.daily_board._build_slate")
def test_morning_board_fallback_skip_totals_true(mock_slate, isolated_board):
    _write_morning_board(isolated_board)
    mock_slate.side_effect = AssertionError("should not rebuild")

    board = db.build_daily_board(
        game_date=GAME_DATE,
        use_cache=False,
        refresh=False,
        skip_totals=True,
    )

    assert board["cache_key"] == MORNING_CACHE_KEY
    assert board["skip_totals"] is False
    mock_slate.assert_not_called()


@patch("app.services.daily_board._build_slate")
def test_api_daily_uses_morning_board(mock_slate, isolated_board):
    from fastapi.testclient import TestClient
    from app.main import app

    _write_morning_board(isolated_board)
    mock_slate.side_effect = AssertionError("should not rebuild")

    client = TestClient(app)
    resp = client.get(f"/api/daily?date={GAME_DATE.isoformat()}")

    assert resp.status_code == 200
    assert resp.json()["slate"][0]["game_id"] == "1"
    mock_slate.assert_not_called()
