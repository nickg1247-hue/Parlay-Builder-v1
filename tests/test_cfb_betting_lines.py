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


def test_resolve_ou_lines_merges_book_and_matchup(monkeypatch):
    from app.odds import cfb_betting_lines as mod

    df = pd.DataFrame(
        [
            {"game_id": "1", "date": "2024-11-30", "season": 2024, "home_team": "A", "away_team": "B"},
            {"game_id": "2", "date": "2024-11-30", "season": 2024, "home_team": "C", "away_team": "D"},
        ]
    )
    monkeypatch.setattr(mod, "get_book_ou_lines_for_date", lambda _d, ids: {"1": 47.5})
    monkeypatch.setattr(mod, "attach_matchup_ou_lines", lambda _df: {"1": 55.0, "2": 41.5})
    merged, book = mod.resolve_ou_lines_for_slate(df, date(2024, 11, 30))
    assert book == {"1": 47.5}
    assert merged["1"] == 47.5
    assert merged["2"] == 41.5
