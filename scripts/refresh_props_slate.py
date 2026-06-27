#!/usr/bin/env python3
"""Fetch full-market MLB player props for today's slate (resumable)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.props_mlb import refresh_props_slate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh MLB props for the full slate.")
    parser.add_argument("--date", help="ISO date (default: today)")
    parser.add_argument(
        "--book",
        help="Sportsbook key (default: PROP_SLATE_BOOKMAKER / DraftKings)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch every game even when cache is fresh",
    )
    parser.add_argument(
        "--alternates",
        action="store_true",
        help="Include alternate / ladder markets (more API credits)",
    )
    parser.add_argument(
        "--loop",
        type=int,
        default=1,
        metavar="N",
        help="Run up to N passes until slate complete or quota stops (default: 1)",
    )
    args = parser.parse_args()

    game_date = date.fromisoformat(args.date) if args.date else date.today()
    last: dict | None = None
    for attempt in range(1, max(1, args.loop) + 1):
        last = refresh_props_slate(
            game_date,
            bookmaker=args.book,
            force=args.force and attempt == 1,
            include_alternates=args.alternates or None,
        )
        pending = last.get("pending_game_ids") or []
        print(json.dumps(last, indent=2))
        if not pending or not last.get("quota_stopped"):
            break
        if attempt < args.loop:
            print(f"--- pass {attempt} done; {len(pending)} games pending, retrying ---")
            args.force = False

    if not last:
        return 1
    pending = last.get("pending_game_ids") or []
    if pending:
        print(
            f"WARNING: {len(pending)} games still pending — re-run when quota resets.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
