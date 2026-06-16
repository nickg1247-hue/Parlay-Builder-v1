"""Tests for MLB team recent-game blocks."""

from datetime import date

from app.services.mlb_team_recent import recent_games_for_matchup, team_last_n_games


def test_team_last_n_games_returns_newest_first():
    games = team_last_n_games("New York Yankees", date(2025, 8, 15), n=5)
    assert len(games) == 5
    dates = [g["date"] for g in games]
    assert dates == sorted(dates, reverse=True)
    assert all("won" in g and "team_runs" in g and "opp_runs" in g for g in games)
    assert all(g["at_vs"] in ("@", "vs") for g in games)


def test_recent_games_for_matchup_both_sides():
    recent = recent_games_for_matchup(
        "New York Yankees",
        "Boston Red Sox",
        date(2025, 8, 15),
    )
    assert set(recent.keys()) == {"home", "away"}
    assert len(recent["home"]) == 5
    assert len(recent["away"]) == 5
    assert recent["home"][0]["result"][0] in ("W", "L")
    assert recent["away"][0]["opponent_short"]
