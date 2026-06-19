"""Diagnose top props vs raw DraftKings lines."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import props_mlb  # noqa: E402

def main() -> None:
    slate_path = ROOT / "data/processed/props_repository/slate_2026-06-19.draftkings.json"
    if not slate_path.exists():
        print("No slate file")
        return
    data = json.loads(slate_path.read_text(encoding="utf-8"))
    picks = data.get("all_props") or []
    bettable = [p for p in picks if props_mlb.prop_is_bettable(p)]
    bettable.sort(key=props_mlb.prop_rank_key)
    print(f"Slate: {len(picks)} total, {len(bettable)} bettable")
    print("\nTop 10 bettable:")
    for i, p in enumerate(bettable[:10], 1):
        print(
            f"{i}. {p['player']} {p['recommended_side']} {p['line']} {p['market_type']} "
            f"@{p.get('recommended_odds')} complete={p.get('complete_market')} "
            f"books={p.get('offered_books')} game={p.get('game_id')}"
        )

    vs = data.get("very_strong_props")
    if vs:
        print(f"\nCached very_strong_props ({len(vs)}):")
        for i, p in enumerate(vs[:5], 1):
            ok = props_mlb.prop_is_bettable(p)
            print(
                f"{i}. {p['player']} {p['recommended_side']} {p['line']} {p['market_type']} "
                f"bettable={ok} books={p.get('offered_books')}"
            )

    out = props_mlb.build_daily_top_props(
        __import__("datetime").date(2026, 6, 19), limit=10, scan=False, bookmaker="draftkings"
    )
    print("\nAPI top_props:")
    for i, p in enumerate(out.get("top_props") or [], 1):
        print(
            f"{i}. {p['player']} {p['recommended_side']} {p['line']} {p['market_type']} "
            f"books={p.get('offered_books')}"
        )
    print("\nAPI very_strong_props:")
    for i, p in enumerate(out.get("very_strong_props") or [], 1):
        ok = props_mlb.prop_is_bettable(p)
        print(
            f"{i}. {p['player']} {p['recommended_side']} {p['line']} {p['market_type']} "
            f"bettable={ok} books={p.get('offered_books')}"
        )

if __name__ == "__main__":
    main()
