"""Tests for MLB board slate filtering and odds merge on board_date."""

from datetime import date
from unittest.mock import patch

import pandas as pd

from app.parlay.ev_ranker import attach_market_odds
from app.parlay.slate import filter_board_games, slate_filter_meta


def _game(
    game_pk: int,
    game_date_iso: str,
    *,
    status: dict | None = None,
    game_type: str = "R",
    home: str = "New York Yankees",
    away: str = "Boston Red Sox",
) -> dict:
    return {
        "gamePk": game_pk,
        "gameDate": game_date_iso,
        "gameType": game_type,
        "status": status or {"abstractGameState": "Preview", "detailedState": "Scheduled"},
        "teams": {
            "home": {"team": {"id": 147, "name": home}, "probablePitcher": {}},
            "away": {"team": {"id": 111, "name": away}, "probablePitcher": {}},
        },
    }


def test_postponed_excluded():
    board_date = date(2026, 6, 15)
    games = [
        _game(
            1,
            "2026-06-16T02:05:00Z",
            status={
                "abstractGameState": "Final",
                "codedGameState": "D",
                "detailedState": "Postponed: Rain",
                "statusCode": "DR",
            },
        ),
        _game(2, "2026-06-16T02:10:00Z"),
    ]
    kept = filter_board_games(games, board_date)
    assert len(kept) == 1
    assert kept[0]["gamePk"] == 2
    meta = slate_filter_meta(games, board_date)
    assert meta["postponed"] == 1


def test_final_excluded():
    board_date = date(2026, 6, 15)
    games = [
        _game(
            1,
            "2026-06-16T01:05:00Z",
            status={"abstractGameState": "Final", "detailedState": "Final"},
        ),
        _game(2, "2026-06-16T02:05:00Z"),
    ]
    kept = filter_board_games(games, board_date)
    assert len(kept) == 1
    assert kept[0]["gamePk"] == 2
    meta = slate_filter_meta(games, board_date)
    assert meta["final"] == 1


def test_et_evening_game_kept_on_board_date():
    """UTC next-day timestamp that is still the board date in America/New_York."""
    board_date = date(2026, 6, 15)
    games = [_game(1, "2026-06-16T02:05:00Z")]
    kept = filter_board_games(games, board_date)
    assert len(kept) == 1
    meta = slate_filter_meta(games, board_date)
    assert meta["date_mismatch"] == 0


def test_date_mismatch_excluded():
    board_date = date(2026, 6, 15)
    games = [_game(1, "2026-06-16T14:00:00Z")]
    kept = filter_board_games(games, board_date)
    assert kept == []
    meta = slate_filter_meta(games, board_date)
    assert meta["date_mismatch"] == 1


def test_doubleheader_both_included():
    board_date = date(2026, 6, 15)
    games = [
        _game(101, "2026-06-15T17:05:00Z"),
        _game(102, "2026-06-16T00:05:00Z"),
    ]
    kept = filter_board_games(games, board_date)
    assert len(kept) == 2
    assert {g["gamePk"] for g in kept} == {101, 102}


def test_spring_training_excluded():
    board_date = date(2026, 3, 15)
    games = [_game(1, "2026-03-15T17:05:00Z", game_type="S")]
    kept = filter_board_games(games, board_date)
    assert kept == []
    meta = slate_filter_meta(games, board_date)
    assert meta["game_type"] == 1


@patch("app.parlay.ev_ranker.get_mlb_odds_for_date")
def test_attach_market_odds_dedupes_duplicate_matchups(mock_get_odds):
    board_date = date(2026, 6, 15)
    mock_get_odds.return_value = (
        [
            {
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "commence_time": "2026-06-16T02:00:00Z",
                "home_ml": -130,
                "away_ml": 110,
            },
            {
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "commence_time": "2026-06-16T02:05:00Z",
                "home_ml": -125,
                "away_ml": 105,
            },
        ],
        "the_odds_api_live",
    )
    slate = pd.DataFrame(
        [
            {
                "game_id": "777001",
                "date": board_date.isoformat(),
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "model_prob_home": 0.55,
            }
        ]
    )
    with patch("app.parlay.ev_ranker.has_date", return_value=True):
        merged, source = attach_market_odds(slate, board_date, use_cache=False)

    assert source == "the_odds_api"
    assert len(merged) == 1
    assert merged.iloc[0]["home_ml"] == -128


@patch("app.parlay.ev_ranker.get_mlb_odds_for_date")
def test_attach_market_odds_merges_on_board_date_not_utc_commence(mock_get_odds):
    """Odds commence UTC date can differ from ET board date; merge uses board_date."""
    board_date = date(2026, 6, 15)
    mock_get_odds.return_value = (
        [
            {
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "commence_time": "2026-06-16T02:00:00Z",
                "home_ml": -130,
                "away_ml": 110,
            }
        ],
        "the_odds_api_live",
    )
    slate = pd.DataFrame(
        [
            {
                "game_id": "777001",
                "date": board_date.isoformat(),
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "model_prob_home": 0.55,
            }
        ]
    )
    with patch("app.parlay.ev_ranker.has_date", return_value=True):
        merged, source = attach_market_odds(slate, board_date, use_cache=False)

    assert source == "the_odds_api"
    assert merged.iloc[0]["home_ml"] == -130
