"""CFB betting lines (O/U) from CFBD and matchup fallback."""

from datetime import date

import pandas as pd

from app.odds.cfb_betting_lines import (
    _median_ou,
    matchup_ou_line,
    resolve_season_week,
)


def test_median_ou_rounds_to_half_point():
    lines = [
        {"provider": "A", "overUnder": 49},
        {"provider": "B", "overUnder": 50.5},
        {"provider": "C", "overUnder": 51},
    ]
    assert _median_ou(lines) == 50.5


def test_matchup_ou_varies_by_team():
    high = matchup_ou_line(40, 38, 28, 30)
    low = matchup_ou_line(22, 20, 18, 19)
    assert high != low
    assert high > low


def test_resolve_season_week_nov_30_2024():
    calendar = [
        {
            "week": 14,
            "seasonType": "regular",
            "firstGameStart": "2024-11-25T08:00:00.000Z",
            "lastGameStart": "2024-12-02T07:59:00.000Z",
        }
    ]
    resolved = resolve_season_week(date(2024, 11, 30), calendar)
    assert resolved == ("regular", 14)


def test_resolve_lines_for_slate_merges_book_and_matchup(monkeypatch):
    from app.odds import cfb_betting_lines as mod

    df = pd.DataFrame(
        [
            {"game_id": "espn-1", "date": "2024-11-30", "season": 2024, "home_team": "Georgia", "away_team": "Georgia Tech"},
            {"game_id": "espn-2", "date": "2024-11-30", "season": 2024, "home_team": "Alabama", "away_team": "Auburn"},
        ]
    )
    cfbd_games = [
        {
            "cfbd_game_id": "401628472",
            "game_date": "2024-11-30",
            "home_team": "Georgia",
            "away_team": "Georgia Tech",
            "ou_line": 47.5,
            "home_spread_point": -10.5,
        }
    ]
    monkeypatch.setattr(mod, "get_cfbd_book_games_for_date", lambda _d, **kw: cfbd_games)
    monkeypatch.setattr(mod, "attach_matchup_ou_lines", lambda _df: {"espn-1": 55.0, "espn-2": 41.5})
    merged, spread, book = mod.resolve_lines_for_slate(df, date(2024, 11, 30))
    assert book == {"espn-1": 47.5}
    assert spread == {"espn-1": -10.5}
    assert merged["espn-1"] == 47.5
    assert merged["espn-2"] == 41.5


def test_parse_cfbd_line_game_extracts_spread():
    from app.odds.cfb_betting_lines import _parse_cfbd_line_game

    game = {
        "id": 401628472,
        "homeTeam": "Pitt",
        "awayTeam": "West Virginia",
        "startDate": "2024-11-30T17:00:00.000Z",
        "lines": [
            {"provider": "DraftKings", "spread": -3.5, "overUnder": 49.5},
            {"provider": "FanDuel", "spread": -4.0, "overUnder": 50.0},
        ],
    }
    parsed = _parse_cfbd_line_game(game)
    assert parsed is not None
    assert parsed["home_team"] == "Pitt"
    assert parsed["ou_line"] == 50.5
    assert parsed["home_spread_point"] == -3.75
