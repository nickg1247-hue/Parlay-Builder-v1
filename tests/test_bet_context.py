"""Bet context: team form windows and line strength."""

from datetime import date
from unittest.mock import patch

import pandas as pd

from app.services import bet_context


def _games_frame() -> pd.DataFrame:
    rows = []
    for i, won in enumerate([True, True, False, True, True, False, True]):
        d = date(2026, 6, 8 + i)
        rows.append(
            {
                "date": pd.Timestamp(d),
                "home_team": "Team Alpha",
                "away_team": f"Opp {i}",
                "home_score": 5 if won else 2,
                "away_score": 2 if won else 5,
                "home_win": won,
            }
        )
    return pd.DataFrame(rows)


def test_team_win_rate_windows():
    games = _games_frame()
    rates = bet_context.team_win_rate_windows("Team Alpha", date(2026, 6, 16), games=games)
    assert rates["win_rate_l5"] == 0.6
    assert rates["win_rate_l10"] is not None


def test_ml_bet_line_strength_strong_with_edge_and_form():
    pick = {"team": "Team Alpha", "side": "home", "edge": 0.09}
    game = {
        "game_id": "1",
        "home_team": "Team Alpha",
        "away_team": "Opp",
        "away_pitcher_era": 4.9,
        "away_starting_pitcher": "Soft Thrower",
    }
    rates = {"win_rate_l5": 0.8, "win_rate_l10": 0.7, "win_rate_season": 0.6}
    out = bet_context.ml_bet_line_strength(pick, game, rates)
    assert out["line_strength"] == "strong"
    assert "Model edge" in out["line_insight"]
    assert "ERA" in out["line_insight"]


def test_enrich_ml_singles_attaches_form():
    slate = [
        {
            "game_id": "123",
            "matchup": "Opp @ Team Alpha",
            "home_team": "Team Alpha",
            "away_team": "Opp",
            "best_pick": {"side": "home", "team": "Team Alpha", "edge": 0.06, "american_odds": 120},
        }
    ]
    picks = [
        {
            "game_id": "123",
            "matchup": "Opp @ Team Alpha",
            "team": "Team Alpha",
            "side": "home",
            "edge": 0.06,
            "american_odds": 120,
        }
    ]
    with patch.object(bet_context, "load_games", return_value=_games_frame()):
        enriched = bet_context.enrich_ml_singles(picks, slate, date(2026, 6, 16))
    assert enriched[0]["win_rate_l5"] is not None
    assert enriched[0]["line_strength"] in ("strong", "moderate", "weak")
