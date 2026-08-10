"""Regenerate or validate data/processed/nfl_fantasy_rankings_2026.json.

V1 uses a curated static board (not live ESPN/Sleeper ADP). Edit the JSON by hand
or run this script to validate schema and rewrite with sorted ranks.

  python scripts/generate_nfl_fantasy_rankings.py
  python scripts/generate_nfl_fantasy_rankings.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed" / "nfl_fantasy_rankings_2026.json"
REQUIRED = (
    "player_id",
    "name",
    "position",
    "team",
    "adp",
    "rank_std",
    "rank_half",
    "rank_ppr",
)
POSITIONS = {"QB", "RB", "WR", "TE", "DST", "K"}


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    players = data.get("players") or []
    if not (200 <= len(players) <= 280):
        errors.append(f"expected ~200–250 players, got {len(players)}")
    ids: set[str] = set()
    for i, p in enumerate(players):
        for key in REQUIRED:
            if key not in p:
                errors.append(f"player[{i}] missing {key}")
        pid = p.get("player_id")
        if pid in ids:
            errors.append(f"duplicate player_id {pid}")
        ids.add(pid)
        if p.get("position") not in POSITIONS:
            errors.append(f"{pid}: bad position {p.get('position')}")
    counts = Counter(p.get("position") for p in players)
    for pos in POSITIONS:
        if counts.get(pos, 0) < 8:
            errors.append(f"too few {pos}: {counts.get(pos, 0)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate only; do not rewrite the file.",
    )
    args = parser.parse_args()

    if not OUT.exists():
        print(f"missing {OUT}", file=sys.stderr)
        return 1

    data = json.loads(OUT.read_text(encoding="utf-8"))
    errors = validate(data)
    if errors:
        print("validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if args.check:
        print(f"ok: {len(data['players'])} players")
        return 0

    data["updated"] = date.today().isoformat()
    data.setdefault(
        "source",
        "Curated static consensus-style board for NTG Fantasy Draft Helper V1 "
        "(not live ESPN/Sleeper ADP). Refresh manually by editing this file or "
        "re-running scripts/generate_nfl_fantasy_rankings.py.",
    )
    # Stable sort by half-PPR rank for readability
    data["players"] = sorted(
        data["players"],
        key=lambda p: (int(p.get("rank_half") or 999), p.get("name") or ""),
    )
    OUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(data['players'])} players)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
