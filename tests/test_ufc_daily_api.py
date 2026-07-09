"""Tests for UFC daily board API including parlays."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.main.build_ufc_daily_board")
def test_ufc_daily_api_returns_top_parlays(mock_board):
    mock_board.return_value = {
        "date": "2024-01-13",
        "sport": "ufc",
        "mode": "demo",
        "slate": [],
        "top_parlays": [
            {
                "num_legs": 2,
                "ev": 0.12,
                "ev_pct": "12.0%",
                "model_joint_prob": 0.35,
                "market_joint_prob": 0.28,
                "decimal_payout": 4.5,
                "legs": [
                    {
                        "game_id": "1",
                        "team": "Fighter A",
                        "american_odds": -150,
                    }
                ],
            }
        ],
        "edge_threshold": 0.08,
    }
    resp = client.get("/api/ufc/daily?use_cache=true")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["top_parlays"]) == 1
    assert body["top_parlays"][0]["num_legs"] == 2
