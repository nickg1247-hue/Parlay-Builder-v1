"""Rebuild the Wednesday NFL futures snapshot (division standings)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.nfl_futures import build_nfl_futures, current_nfl_season


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh NFL division futures")
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--date", default=None, help="As-of date YYYY-MM-DD (defaults today)")
    args = parser.parse_args()
    as_of = date.fromisoformat(args.date) if args.date else date.today()
    season = args.season or current_nfl_season(as_of)
    payload = build_nfl_futures(season=season, as_of=as_of, refresh=True)
    divisions = payload.get("divisions") or []
    print(
        f"NFL futures {payload.get('season')} week {payload.get('week_id')}: "
        f"{len(divisions)} divisions, model={payload.get('model')}"
    )
    if payload.get("error"):
        print(f"error: {payload['error']}")
        raise SystemExit(1)
    for div in divisions:
        champ = div.get("champion")
        print(f"  {div['name']}: {champ}")


if __name__ == "__main__":
    main()
