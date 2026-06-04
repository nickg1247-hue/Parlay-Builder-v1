from datetime import date
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.parlay.ev_ranker import ParlayLeg, RankedParlay
from app.services.daily_board import build_daily_board

client = TestClient(app)

MOCK_SLATE = pd.DataFrame(
    [
        {
            "game_id": "1",
            "date": "2025-08-15",
            "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
            "model_prob_home": 0.55,
            "model_prob_away": 0.45,
            "home_ml": -120,
            "away_ml": 110,
        }
    ]
)


def test_build_daily_board_structure():
    merged = MOCK_SLATE.copy()
    merged["date_key"] = "2025-08-15"

    mock_leg = ParlayLeg(
        game_id="1",
        date="2025-08-15",
        matchup="Boston Red Sox @ New York Yankees",
        side="home",
        team="New York Yankees",
        model_prob=0.55,
        market_prob=0.5,
        american_odds=-120,
        leg_edge=0.05,
    )
    mock_parlay = RankedParlay(
        legs=[mock_leg, mock_leg],
        num_legs=2,
        model_joint_prob=0.3,
        market_joint_prob=0.25,
        decimal_payout=3.0,
        ev=0.06,
        edge_vs_market=0.05,
    )

    with (
        patch("app.services.daily_board.build_slate_from_history", return_value=MOCK_SLATE),
        patch(
            "app.services.daily_board.attach_market_odds",
            return_value=(merged, "historical_cache"),
        ),
        patch("app.services.daily_board._candidate_legs", return_value=[mock_leg]),
        patch("app.services.daily_board.rank_parlays", return_value=[mock_parlay]),
        patch("app.services.daily_board._status_footer", return_value={"mlb_games_count": 100}),
        patch("app.services.daily_board._write_cache"),
        patch("app.services.daily_board.build_totals_slate", return_value=pd.DataFrame()),
    ):
        board = build_daily_board(
            game_date=date(2025, 8, 15),
            use_cache=True,
            refresh=True,
        )

    assert board["date"] == "2025-08-15"
    assert board["disclaimer"]
    assert len(board["slate"]) == 1
    assert "model_prob_home" in board["slate"][0]
    assert board["odds_source"] == "historical_cache"
    assert isinstance(board["top_parlays"], list)


def test_api_daily_demo():
    response = client.get("/api/daily?date=2025-08-15&use_cache=true&refresh=true")
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2025-08-15"
    assert "slate" in body
    assert "top_parlays" in body
    assert "status" in body
