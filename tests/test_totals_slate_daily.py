"""Totals columns on daily board must include model runs and O/U when odds exist."""

from datetime import date

import pandas as pd

from app.parlay.totals_slate import build_totals_slate
from app.services.daily_board import build_daily_board


def test_build_totals_slate_survives_duplicate_odds_merge():
    """Duplicate book lines must not crash totals scoring (game page board rebuild)."""
    from unittest.mock import patch

    one_game = pd.DataFrame(
        [
            {
                "game_id": "777001",
                "date": pd.Timestamp("2026-06-21"),
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "season": 2026,
                "home_starting_pitcher": "A",
                "away_starting_pitcher": "B",
            }
        ]
    )

    def _attach(featured, *_args, **_kwargs):
        rows = []
        for ou in (8.5, 9.0):
            row = featured.iloc[0].to_dict()
            row["ou_line"] = ou
            row["over_odds"] = -110
            row["under_odds"] = -110
            rows.append(row)
        return pd.DataFrame(rows)

    with patch("app.parlay.totals_slate.build_slate_dataframe", return_value=one_game):
        with patch("app.parlay.totals_slate.attach_totals_odds", side_effect=_attach):
            out = build_totals_slate(date(2026, 6, 21), use_cache=False)
    assert len(out) == 1
    assert str(out.iloc[0]["game_id"]) == "777001"


def test_demo_board_fills_totals_columns():
    board = build_daily_board(
        game_date=date(2025, 8, 15),
        use_cache=True,
        refresh=True,
        skip_totals=False,
    )
    assert board["slate"], "expected games on demo date"
    with_runs = [g for g in board["slate"] if g.get("expected_total_runs") is not None]
    assert len(with_runs) == len(board["slate"])
    with_line = [g for g in board["slate"] if g.get("ou_line") is not None]
    assert with_line, "demo date should match cached totals odds"
    sample = with_line[0]
    assert sample.get("totals_pick") in ("OVER", "UNDER", None)
    assert sample.get("model_prob_over") is not None
