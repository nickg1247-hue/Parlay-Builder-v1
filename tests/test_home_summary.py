"""Home page summary API tests."""

import json
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import home_summary as hs

client = TestClient(app)


@pytest.fixture
def isolated_board(tmp_path, monkeypatch):
    path = tmp_path / "daily_board.json"
    monkeypatch.setattr(hs, "DAILY_BOARD_CACHE", path)
    return path


def test_home_summary_from_board(isolated_board):
    isolated_board.write_text(
        json.dumps(
            {
                "date": "2026-06-06",
                "generated_at": "2026-06-06T12:00:00+00:00",
                "games_on_slate": 2,
                "games_with_odds": 2,
                "odds_source": "the_odds_api",
                "top_singles": [
                    {
                        "matchup": "A @ B",
                        "team": "B",
                        "edge": 0.09,
                        "american_odds": -120,
                    }
                ],
                "slate": [
                    {
                        "game_id": "1",
                        "matchup": "A @ B",
                        "away_team": "A",
                        "home_team": "B",
                        "plus_ev_single": True,
                        "best_pick": {"team": "B", "side": "home", "edge": 0.09},
                        "expected_total_runs": 8.5,
                        "ou_line": 8.0,
                        "ml_confidence": "Medium",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with patch("app.services.home_summary.get_today_snapshot", return_value={"fetched_at": "2026-06-06T11:00:00+00:00"}):
        summary = hs.get_home_today_summary(date(2026, 6, 6))

    assert summary["board_available"] is True
    assert summary["plus_ev_singles"] == 1
    assert "1" in summary["slate_by_game_id"]
    assert summary["top_singles"][0]["team"] == "B"


def test_api_home_today():
    with patch(
        "app.main.get_home_today_summary",
        return_value={"board_available": True, "games_on_slate": 5, "top_singles": []},
    ):
        resp = client.get("/api/home/today")
    assert resp.status_code == 200
    assert resp.json()["games_on_slate"] == 5
