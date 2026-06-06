"""Game insights API tests (Phase C)."""



import json

from datetime import date

from unittest.mock import patch



import pytest

from fastapi.testclient import TestClient



from app.main import app

from app.services import game_insights as gi

from app.services.daily_board import DAILY_BOARD_CACHE



client = TestClient(app)



SAMPLE_GAME = {

    "game_id": "777001",

    "home_team": "New York Yankees",

    "away_team": "Boston Red Sox",

    "home_team_id": 147,

    "away_team_id": 111,

    "start_time_utc": "2025-08-15T23:05:00Z",

    "status": "Preview",

    "home_score": None,

    "away_score": None,

}



SAMPLE_BOARD = {

    "date": "2025-08-15",

    "mode": "demo",

    "odds_source": "historical_cache",

    "warnings": [],

    "edge_threshold": 0.08,

    "slate": [

        {

            "game_id": "777001",

            "home_team": "New York Yankees",

            "away_team": "Boston Red Sox",

            "model_prob_home": 0.58,

            "display_prob_home": 0.55,

            "market_prob_home": 0.52,

            "ml_edge_best": 0.06,

            "ml_confidence": "Medium",

            "plus_ev_single": False,

            "best_pick": {

                "side": "home",

                "team": "New York Yankees",

                "edge": 0.06,

                "american_odds": -120,

            },

            "ou_line": 8.5,

            "expected_total_runs": 8.2,

            "totals_pick": "Over",

            "total_edge": 0.05,

            "totals_confidence": "Medium",

            "plus_ev_total": False,

        }

    ],

    "top_parlays": [

        {

            "num_legs": 2,

            "ev": 0.09,

            "ev_pct": "9.0%",

            "legs": [

                {

                    "game_id": "777001",

                    "team": "New York Yankees",

                    "side": "home",

                    "american_odds": -120,

                },

                {

                    "game_id": "777002",

                    "team": "Chicago Cubs",

                    "side": "away",

                    "american_odds": 110,

                },

            ],

        },

        {

            "num_legs": 2,

            "ev": 0.07,

            "ev_pct": "7.0%",

            "legs": [

                {

                    "game_id": "999999",

                    "team": "Other",

                    "side": "home",

                    "american_odds": -110,

                },

                {

                    "game_id": "888888",

                    "team": "Other2",

                    "side": "away",

                    "american_odds": 105,

                },

            ],

        },

    ],

}



SAMPLE_LINES = {

    "source": "historical_cache",

    "away_ml": 130,

    "home_ml": -150,

    "total_line": 8.5,

    "over_am": -110,

    "under_am": -110,

    "away_spread": {"point": 1.5, "american": -120},

    "home_spread": {"point": -1.5, "american": 100},

}





@pytest.fixture

def isolated_board(tmp_path, monkeypatch):

    board_path = tmp_path / "daily_board.json"

    monkeypatch.setattr(gi, "DAILY_BOARD_CACHE", board_path)

    monkeypatch.setattr("app.services.daily_board.DAILY_BOARD_CACHE", board_path)

    monkeypatch.setattr("app.services.schedule_mlb.DAILY_BOARD_CACHE", board_path)

    return board_path





def test_parlays_for_game_filters_correctly():

    parlays = gi._parlays_for_game(SAMPLE_BOARD, "777001")

    assert len(parlays) == 1

    assert parlays[0]["ev"] == 0.09





def test_build_model_from_board_row():

    model = gi._build_model(SAMPLE_BOARD["slate"][0])

    assert model["pick"] == "New York Yankees"

    assert model["win_pct"] == 55.0

    assert model["expected_runs"] == 8.2

    assert model["totals_pick"] == "Over"





def test_build_market_cards_shape():

    cards = gi._build_market_cards(SAMPLE_LINES)

    assert cards["source"] == "historical_cache"

    assert cards["away"]["moneyline_american"] == 130

    assert cards["home"]["moneyline_american"] == -150

    assert cards["total"]["line"] == 8.5

    assert cards["total"]["over_american"] == -110

    assert cards["away"]["spread"]["point"] == 1.5





def test_build_highlights_from_model():

    model = gi._build_model(SAMPLE_BOARD["slate"][0])

    highlights = gi._build_highlights(model)

    assert highlights["moneyline_side"] == "home"

    assert highlights["spread_side"] == "home"

    assert highlights["total_side"] == "over"

    assert highlights["moneyline_tier"] == "medium"

    assert highlights["spread_tier"] == "medium"

    assert highlights["total_tier"] == "medium"




def test_confidence_tier_mapping():

    assert gi._confidence_tier("Low") == "low"

    assert gi._confidence_tier("Medium") == "medium"

    assert gi._confidence_tier("High") == "high"

    assert gi._confidence_tier("Extremely high") == "high"

    assert gi._confidence_tier("—") is None

    assert gi._confidence_tier(None) is None





@patch("app.services.game_insights.get_mlb_game")

@patch("app.services.game_insights._sportsbook_lines")

def test_build_game_insights_success(mock_lines, mock_game, isolated_board):

    isolated_board.write_text(json.dumps(SAMPLE_BOARD), encoding="utf-8")

    mock_game.return_value = {"game": SAMPLE_GAME, "date": "2025-08-15"}

    mock_lines.return_value = SAMPLE_LINES



    result = gi.build_game_insights(

        "777001",

        game_date=date(2025, 8, 15),

        use_cache=True,

        refresh=False,

    )



    assert result is not None

    assert result["board_row"]["game_id"] == "777001"

    assert len(result["parlays"]) == 1

    assert result["model"]["pick"] == "New York Yankees"

    assert result["market_cards"]["away"]["moneyline_american"] == 130

    assert result["highlights"]["moneyline_side"] == "home"

    assert "markets" not in result

    mock_game.assert_called_once()





@patch("app.services.game_insights.get_mlb_game", return_value=None)

def test_build_game_insights_not_found(_mock_game):

    assert gi.build_game_insights("999", game_date=date(2025, 8, 15)) is None





@patch("app.services.game_insights.build_daily_board")

@patch("app.services.game_insights.get_mlb_game")

@patch("app.services.game_insights._sportsbook_lines")

def test_build_game_insights_refresh_passthrough(

    mock_lines, mock_game, mock_board, isolated_board

):

    mock_game.return_value = {"game": SAMPLE_GAME}

    mock_board.return_value = SAMPLE_BOARD

    mock_lines.return_value = {**SAMPLE_LINES, "source": "none", "away_ml": None, "home_ml": None}



    gi.build_game_insights(

        "777001",

        game_date=date(2025, 8, 15),

        use_cache=False,

        refresh=True,

    )



    mock_board.assert_called_once()

    call_kw = mock_board.call_args.kwargs

    assert call_kw["refresh"] is True

    assert call_kw["skip_totals"] is False





@patch("app.services.game_insights.get_mlb_game")

@patch("app.services.game_insights._sportsbook_lines")

def test_build_game_insights_warns_when_no_market_lines(mock_lines, mock_game, isolated_board):

    isolated_board.write_text(json.dumps(SAMPLE_BOARD), encoding="utf-8")

    mock_game.return_value = {"game": SAMPLE_GAME}

    mock_lines.return_value = {

        "source": "none",

        "away_ml": None,

        "home_ml": None,

        "total_line": None,

        "over_am": None,

        "under_am": None,

        "away_spread": {"point": None, "american": None},

        "home_spread": {"point": None, "american": None},

    }



    result = gi.build_game_insights("777001", game_date=date(2025, 8, 15), use_cache=False)



    assert any("Market lines unavailable" in w for w in result["warnings"])





@patch("app.main.build_game_insights")

def test_api_insights_demo(mock_build):

    mock_build.return_value = {

        "game_id": "777001",

        "date": "2025-08-15",

        "market_cards": {"source": "historical_cache"},

        "highlights": {},

        "model": {},

        "parlays": [],

    }

    resp = client.get(

        "/api/games/mlb/777001/insights?date=2025-08-15&use_cache=true"

    )

    assert resp.status_code == 200

    mock_build.assert_called_once()

    assert mock_build.call_args.kwargs["use_cache"] is True





@patch("app.main.build_game_insights", return_value=None)

def test_api_insights_404(mock_build):

    resp = client.get("/api/games/mlb/999/insights")

    assert resp.status_code == 404


