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
                    "date": "2026-06-16",
                    "generated_at": "2026-06-16T12:00:00+00:00",
                "games_on_slate": 2,
                "games_with_odds": 2,
                "odds_source": "the_odds_api",
                "top_singles": [
                    {
                        "game_id": "1",
                        "matchup": "Opp @ Team Alpha",
                        "team": "Team Alpha",
                        "side": "home",
                        "edge": 0.09,
                        "american_odds": -120,
                    }
                ],
                "slate": [
                    {
                        "game_id": "1",
                        "matchup": "Opp @ Team Alpha",
                        "away_team": "Opp",
                        "home_team": "Team Alpha",
                        "plus_ev_single": True,
                        "best_pick": {"team": "Team Alpha", "side": "home", "edge": 0.09},
                        "expected_total_runs": 8.5,
                        "ou_line": 8.0,
                        "ml_confidence": "Medium",
                        "away_pitcher_era": 4.8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with patch("app.services.home_summary.get_today_snapshot", return_value={"fetched_at": "2026-06-16T11:00:00+00:00"}):
        with patch("app.services.bet_context.load_games") as load_games:
            from tests.test_bet_context import _games_frame

            load_games.return_value = _games_frame()
            summary = hs.get_home_today_summary(date(2026, 6, 16))

    assert summary["board_available"] is True
    assert summary["plus_ev_singles"] == 1
    assert "1" in summary["slate_by_game_id"]
    pick = summary["top_singles"][0]
    assert pick["team"] == "Team Alpha"
    assert pick.get("win_rate_l5") is not None
    assert pick.get("line_strength") in ("strong", "moderate", "weak")


@pytest.mark.parametrize(
    "rates,expected",
    [
        ({"win_rate_l5": 0.8, "win_rate_l10": 0.7, "win_rate_season": 0.6}, 0.7),
        ({"win_rate_l5": None, "win_rate_l10": 0.5, "win_rate_season": None}, 0.5),
    ],
)
def test_form_composite_score(rates, expected):
    from app.services.bet_context import form_composite_score

    assert form_composite_score(rates) == pytest.approx(expected)


def test_home_summary_uses_form_ranking(isolated_board):
    isolated_board.write_text(
        json.dumps(
            {
                "date": "2026-06-16",
                "generated_at": "2026-06-16T12:00:00+00:00",
                "games_on_slate": 2,
                "games_with_odds": 2,
                "slate": [
                    {
                        "game_id": "1",
                        "matchup": "Cold @ Hot",
                        "away_team": "Cold",
                        "home_team": "Hot",
                        "model_pick_side": "home",
                        "model_pick_team": "Hot",
                        "model_prob_home": 0.58,
                        "home_ml": -130,
                        "away_ml": 110,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    fake_prop = {
        "player": "Test Player",
        "market_type": "batter_hits",
        "market_label": "Hits",
        "line": 1.5,
        "recommended_side": "over",
        "recommended_odds": -110,
        "hit_rate_over_l5": 0.9,
        "hit_rate_over_l10": 0.85,
        "hit_rate_over_season": 0.8,
        "game_id": "1",
    }

    with patch("app.services.home_summary.get_today_snapshot", return_value={"fetched_at": "x"}):
        with patch(
            "app.services.props_mlb.build_daily_top_props",
            return_value={"very_strong_props": [fake_prop], "top_props": []},
        ):
            summary = hs.get_home_today_summary(date(2026, 6, 16))

    assert summary["board_available"] is True
    assert summary["top_singles"][0]["bet_type"] == "prop"
    assert summary["top_singles"][0]["player"] == "Test Player"


def test_api_home_today():
    with patch(
        "app.main.get_home_today_summary",
        return_value={"board_available": True, "games_on_slate": 5, "top_singles": []},
    ):
        resp = client.get("/api/home/today")
    assert resp.status_code == 200
    assert resp.json()["games_on_slate"] == 5
