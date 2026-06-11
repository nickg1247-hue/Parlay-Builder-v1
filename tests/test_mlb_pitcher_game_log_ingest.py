"""Boxscore pitching line parsing for pitcher game log."""

import pytest

from app.ingest.mlb import RawGame, _parse_ip, _parse_team_pitching_lines, _pitching_lines_from_boxscore


def test_parse_ip_mlb_fractions():
    assert _parse_ip("5.2") == pytest.approx(5 + 2 / 3, rel=1e-5)
    assert _parse_ip("6.0") == pytest.approx(6.0)
    assert _parse_ip("0.1") == pytest.approx(1 / 3, rel=1e-5)


def test_parse_team_pitching_lines_starter_flag():
    box = {
        "pitchers": [123, 456],
        "players": {
            "ID123": {
                "person": {"fullName": "Ace Starter"},
                "stats": {
                    "pitching": {
                        "inningsPitched": "6.0",
                        "earnedRuns": 2,
                        "hits": 5,
                        "baseOnBalls": 1,
                        "gamesStarted": 1,
                    }
                },
            },
            "ID456": {
                "person": {"fullName": "Relief Guy"},
                "stats": {
                    "pitching": {
                        "inningsPitched": "1.0",
                        "earnedRuns": 0,
                        "hits": 0,
                        "baseOnBalls": 0,
                        "gamesStarted": 0,
                    }
                },
            },
        },
    }
    rows = _parse_team_pitching_lines(
        box, game_id="99", game_date="2024-06-01", team_name="TeamA"
    )
    assert len(rows) == 2
    starter = next(r for r in rows if r["pitcher_name"] == "Ace Starter")
    reliever = next(r for r in rows if r["pitcher_name"] == "Relief Guy")
    assert starter["is_starter"] is True
    assert reliever["is_starter"] is False
    assert starter["pitcher_key"] == "ace starter"


def test_pitching_lines_from_boxscore_both_sides():
    game = RawGame("1", "2024-06-01", "Home", "Away", 4, 2)
    box = {
        "teams": {
            "home": {
                "pitchers": [1],
                "players": {
                    "ID1": {
                        "person": {"fullName": "H P"},
                        "stats": {
                            "pitching": {
                                "inningsPitched": "5.0",
                                "earnedRuns": 1,
                                "hits": 3,
                                "baseOnBalls": 0,
                                "gamesStarted": 1,
                            }
                        },
                    }
                },
            },
            "away": {
                "pitchers": [2],
                "players": {
                    "ID2": {
                        "person": {"fullName": "A P"},
                        "stats": {
                            "pitching": {
                                "inningsPitched": "4.0",
                                "earnedRuns": 2,
                                "hits": 4,
                                "baseOnBalls": 2,
                                "gamesStarted": 1,
                            }
                        },
                    }
                },
            },
        }
    }
    lines = _pitching_lines_from_boxscore(box, game)
    assert len(lines) == 2
    teams = {r["team"] for r in lines}
    assert teams == {"Home", "Away"}
