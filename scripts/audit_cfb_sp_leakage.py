"""Audit CFBD SP+ weekly cache for leakage (identical ratings across weeks)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.cfb_sp_plus import (
    SP_PLUS_CACHE_DIR,
    _load_cache_file,
    run_sp_leakage_audit,
    week_files_for_season,
)

SAMPLE_TEAMS = ("Georgia", "Alabama", "Ohio State")


def run_audit() -> int:
    exit_code, reports = run_sp_leakage_audit()
    if exit_code == 2:
        if not SP_PLUS_CACHE_DIR.exists():
            print("SP+ cache missing — run ensure_sp_plus_cache first.")
            print(f"Expected directory: {SP_PLUS_CACHE_DIR}")
        else:
            print("No weekly SP+ cache files found.")
        return 2

    print("=" * 60)
    print("CFB SP+ LEAKAGE AUDIT")
    print("=" * 60)
    print(f"Cache dir: {SP_PLUS_CACHE_DIR}")
    print()

    for report in reports:
        season = report["season"]
        files = week_files_for_season(season)
        print(
            f"Season {season}: {report['week_files']} week files "
            f"(max avg diff {report['max_avg_rating_diff']})"
        )
        for team in SAMPLE_TEAMS:
            vals = []
            for path in files[:8]:
                ratings = _load_cache_file(path)
                week = path.stem.split("_week_")[-1]
                sp = ratings.get(team)
                vals.append(f"w{week}={sp.overall:.1f}" if sp else f"w{week}=?")
            suffix = " …" if len(files) > 8 else ""
            print(f"  {team}: {', '.join(vals)}{suffix}")
        if report["leakage_confirmed"]:
            print("  VERDICT: LEAKAGE RISK — flat weekly ratings (end-of-season reused)")
        else:
            print(f"  VERDICT: OK — last confirmed week {report['last_confirmed_week']}")
        print()

    preseason = sorted(SP_PLUS_CACHE_DIR.glob("*_preseason.json"))
    if preseason:
        print(f"Preseason snapshots: {len(preseason)}")
        for path in preseason:
            print(f"  {path.name}: {len(_load_cache_file(path))} teams")
        print()

    print("=" * 60)
    print("ADVISOR SUMMARY")
    print("=" * 60)
    if exit_code == 1:
        print(
            "CFBD /ratings/sp ignores the `week` parameter — weekly files are identical. "
            "Production uses preseason snapshot for week-1 games only; weeks 2+ zero SP+ "
            "features until real weekly variation exists."
        )
        print("Exit code: 1 (leakage confirmed)")
    else:
        print("Weekly SP+ cache varies by week. Safe to use pregame week W-1 lookup.")
        print("Exit code: 0")
    return exit_code


def main() -> None:
    raise SystemExit(run_audit())


if __name__ == "__main__":
    main()
