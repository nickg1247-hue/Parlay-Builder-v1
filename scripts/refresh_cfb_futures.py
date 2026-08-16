"""Rebuild the Sunday CFB futures snapshot (conference placement + playoff)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.cfb_futures import build_cfb_futures, current_cfb_season


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh CFB conference/playoff futures")
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--date", default=None, help="As-of date YYYY-MM-DD (defaults today)")
    args = parser.parse_args()
    as_of = date.fromisoformat(args.date) if args.date else date.today()
    season = args.season or current_cfb_season(as_of)
    payload = build_cfb_futures(season=season, as_of=as_of, refresh=True)
    confs = payload.get("conferences") or []
    playoff = (payload.get("playoff") or {}).get("seeds") or []
    print(
        f"CFB futures {payload.get('season')} week {payload.get('week_id')}: "
        f"{len(confs)} conferences, {len(playoff)} playoff seeds, "
        f"model={payload.get('model')}"
    )
    if payload.get("error"):
        print(f"error: {payload['error']}")
        raise SystemExit(1)
    for conf in confs:
        champ = conf.get("champion")
        print(f"  {conf['name']}: {champ}")


if __name__ == "__main__":
    main()
