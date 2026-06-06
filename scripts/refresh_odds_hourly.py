"""Hourly odds repository refresh (Task Scheduler or cron)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.odds_hourly_refresh import run_hourly_odds_refresh


def main() -> int:
    return run_hourly_odds_refresh()


if __name__ == "__main__":
    sys.exit(main())
