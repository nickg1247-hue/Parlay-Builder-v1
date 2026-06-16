"""Diagnose MLB model home/away pick skew."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.production_pipeline import get_active_model_info, load_active_artifact
from app.services.daily_board import build_daily_board


def summarize(label: str, slate: list[dict]) -> None:
    print(f"\n=== {label} ===")
    print("Games:", len(slate))
    if not slate:
        return
    home_picks = sum(1 for g in slate if g.get("model_pick_side") == "home")
    away_picks = sum(1 for g in slate if g.get("model_pick_side") == "away")
    probs = [float(g["model_prob_home"]) for g in slate]
    print("Home picks:", home_picks, "| Away picks:", away_picks)
    print(
        "P(home) min/mean/max:",
        round(min(probs), 3),
        round(sum(probs) / len(probs), 3),
        round(max(probs), 3),
    )
    print("Games with P(home) < 0.5:", sum(1 for p in probs if p < 0.5))
    for g in slate:
        ph = g["model_prob_home"]
        print(
            f"  {g['matchup']}: ph={ph:.3f} pick={g.get('model_pick_team')} "
            f"side={g.get('model_pick_side')} conf={g.get('model_confidence')}"
        )


def main() -> None:
    info = get_active_model_info("moneyline")
    art = load_active_artifact("moneyline")
    print("Active model:", info)
    print("Ensemble:", bool(art.get("ensemble_version")))

    cache = ROOT / "data" / "processed" / "daily_board.json"
    if cache.exists():
        b = json.loads(cache.read_text(encoding="utf-8"))
        summarize(f"Cached board ({b.get('date')} {b.get('mode')})", b.get("slate") or [])

    for d, cache_flag in [(date(2025, 8, 15), True), (date(2026, 6, 16), True)]:
        board = build_daily_board(d, use_cache=cache_flag, skip_totals=True)
        summarize(f"Built board {d} demo={cache_flag}", board.get("slate") or [])


if __name__ == "__main__":
    main()
