"""CFB daily board API tests."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MOCK_SCHEDULE = {
    "date": "2024-11-30",
    "resolved_date": "2024-11-30",
    "games": [
        {
            "game_id": "401635000",
            "home_team": "Georgia",
            "away_team": "Georgia Tech",
            "home_logo_url": "https://example.com/uga.png",
            "away_logo_url": "https://example.com/gt.png",
            "start_time_utc": "2024-11-30T20:00:00Z",
            "status": "Preview",
        }
    ],
}

MOCK_PRED = {
    "401635000": {
        "game_id": "401635000",
        "home_team": "Georgia",
        "away_team": "Georgia Tech",
        "model_prob_home": 0.78,
        "model_prob_away": 0.22,
        "model_pick": "Georgia",
        "model_pick_side": "home",
        "ml_confidence": "High",
        "home_ml": -350,
        "away_ml": 280,
        "market_prob_home": 0.72,
        "ev_home": 0.06,
        "ev_away": -0.06,
        "plus_ev_ml": False,
        "model_margin": 14.2,
        "spread_pick": "Georgia -7",
        "home_spread_point": -7.0,
        "spread_line_source": "book",
        "expected_total_pts": 55.0,
        "totals_pick": "Over 51.5",
        "ou_line": 51.5,
    }
}


@patch("app.services.cfb_daily_board.predict_slate")
@patch("app.services.cfb_daily_board.get_cfb_schedule")
@patch("app.services.cfb_daily_board.enrich_games_logos")
def test_cfb_daily_api(mock_logos, mock_schedule, mock_predict):
    mock_schedule.return_value = MOCK_SCHEDULE
    mock_predict.return_value = MOCK_PRED
    mock_logos.side_effect = lambda games: games

    resp = client.get("/api/cfb/daily", params={"date": "2024-11-30", "use_cache": "true"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sport"] == "cfb"
    assert data["mode"] == "demo"
    assert len(data["slate"]) == 1
    row = data["slate"][0]
    assert row["model_prob_home"] == 0.78
    assert row["spread_pick"] == "Georgia -7"
    assert row["totals_pick"] == "Over 51.5"


def test_cfb_board_page():
    resp = client.get("/cfb/board")
    assert resp.status_code == 200
    assert "CFB Daily Board" in resp.text
    assert "cfb_board.js" in resp.text
    assert "Run live" in resp.text
