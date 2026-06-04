"""Totals columns on daily board must include model runs and O/U when odds exist."""

from datetime import date

from app.services.daily_board import build_daily_board


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
