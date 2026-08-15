"""NFL daily board API tests."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MOCK_SCHEDULE = {
    "date": "2025-09-07",
    "resolved_date": "2025-09-07",
    "games": [
        {
            "game_id": "401671617",
            "home_team": "Kansas City Chiefs",
            "away_team": "Baltimore Ravens",
            "home_logo_url": "https://example.com/kc.png",
            "away_logo_url": "https://example.com/bal.png",
            "start_time_utc": "2025-09-07T20:00:00Z",
            "status": "Preview",
            "game_type": "regular",
        }
    ],
}

MOCK_PRED = {
    "401671617": {
        "game_id": "401671617",
        "home_team": "Kansas City Chiefs",
        "away_team": "Baltimore Ravens",
        "game_type": "regular",
        "model_prob_home": 0.62,
        "model_prob_away": 0.38,
        "model_pick": "Kansas City Chiefs",
        "model_pick_side": "home",
        "ml_confidence": "Medium",
        "home_ml": -140,
        "away_ml": 120,
        "market_prob_home": 0.56,
        "ev_home": 0.06,
        "ev_away": -0.06,
        "plus_ev_ml": False,
        "model_margin": 3.5,
        "spread_pick": "Kansas City Chiefs -3",
        "home_spread_point": -3.0,
        "spread_line_source": "book",
        "expected_total_pts": 46.0,
        "totals_pick": "Over 44.5",
        "ou_line": 44.5,
    }
}


@patch("app.services.nfl_daily_board.top_parlays_payload", return_value=[])
@patch("app.services.nfl_daily_board.predict_slate")
@patch("app.services.nfl_daily_board.get_nfl_schedule")
def test_nfl_daily_api(mock_schedule, mock_predict, _mock_parlays):
    mock_schedule.return_value = MOCK_SCHEDULE
    mock_predict.return_value = MOCK_PRED

    resp = client.get("/api/nfl/daily", params={"date": "2025-09-07", "use_cache": "true"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sport"] == "nfl"
    assert data["mode"] == "demo"
    assert len(data["slate"]) == 1
    row = data["slate"][0]
    assert row["model_prob_home"] == 0.62
    assert row["spread_pick"] == "Kansas City Chiefs -3"
    assert row["totals_pick"] == "Over 44.5"


def test_nfl_board_page():
    resp = client.get("/nfl/board")
    assert resp.status_code == 200
    assert "NFL Daily Board" in resp.text
    assert "nfl_board.js" in resp.text
    assert "Run live" in resp.text
