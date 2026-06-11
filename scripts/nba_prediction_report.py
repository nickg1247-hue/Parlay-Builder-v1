"""Holdout prediction report — model vs benchmark market vs actual results."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datetime import date

from app.services.nba_daily_board import build_nba_daily_board


def main() -> None:
    game_date = date.fromisoformat("2026-04-10")
    board = build_nba_daily_board(game_date=game_date, use_cache=True, skip_totals=False)
    slate = board.get("slate") or []

    print(f"\n=== NBA prediction report — {game_date.isoformat()} ===")
    print(board.get("message", ""))
    print(f"Odds source: {board.get('odds_source')}  |  Eval mode: {board.get('board_eval_mode')}\n")

    if not slate:
        print("No slate rows.")
        return

    ml_ok = sum(1 for g in slate if g.get("model_ml_correct") is True)
    ml_n = sum(1 for g in slate if g.get("model_ml_correct") is not None)
    ou_ok = sum(1 for g in slate if g.get("model_ou_correct") is True)
    ou_n = sum(1 for g in slate if g.get("model_ou_correct") is not None)

    print(f"Moneyline pick accuracy: {ml_ok}/{ml_n}" + (f" ({100*ml_ok/ml_n:.1f}%)" if ml_n else ""))
    print(f"O/U lean accuracy:       {ou_ok}/{ou_n}" + (f" ({100*ou_ok/ou_n:.1f}%)" if ou_n else ""))
    print()
    print(
        f"{'Matchup':<42} {'ModP':>6} {'MktP':>6} {'Edge':>7} "
        f"{'Margin':>7} {'Est':>6} {'Line':>6} {'Act':>6} {'ML':>3} {'O/U':>3}"
    )
    print("-" * 100)

    for g in slate:
        edge = g.get("ml_edge_best")
        edge_s = f"{edge*100:+.1f}%" if edge is not None else "—"
        mp = g.get("model_prob_home")
        mk = g.get("market_prob_home")
        mm = g.get("model_margin")
        est = g.get("expected_total_pts")
        line = g.get("ou_line")
        act = g.get("actual_total_pts")
        ml = "Y" if g.get("model_ml_correct") else ("N" if g.get("model_ml_correct") is False else "-")
        ou = "Y" if g.get("model_ou_correct") else ("N" if g.get("model_ou_correct") is False else "-")
        print(
            f"{g.get('matchup', '')[:42]:<42} "
            f"{mp*100:5.1f}% {mk*100:5.1f}% {edge_s:>7} "
            f"{mm if mm is not None else '—':>7} "
            f"{est if est is not None else '—':>6} "
            f"{line if line is not None else '—':>6} "
            f"{act if act is not None else '—':>6} "
            f"{ml:>3} {ou:>3}"
        )

    print("\nModP=model P(home)  MktP=benchmark market  Margin=pred home-away  Est=model total  Act=final total")
    print("ML/O/U = model lean vs actual on completed games.")
    print("\nFor real closing lines: python scripts/load_nba_odds_free.py your.csv")
    print("Rolling backtest: python scripts/backtest_nba_recent.py --start 2026-03-25 --end 2026-04-10")


if __name__ == "__main__":
    main()
