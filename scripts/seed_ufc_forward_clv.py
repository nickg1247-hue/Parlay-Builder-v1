"""Seed demo rows into UFC forward CLV log (development / empty-log UX only)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ufc_forward_clv import FORWARD_CLV_UFC_LOG, pick_id

DEMO_ROWS = [
    {
        "sport": "ufc",
        "pick_id": pick_id("2024-01-13", "401623977", "home"),
        "logged_at": "2024-01-13T14:00:00+00:00",
        "board_date": "2024-01-13",
        "fight_id": "401623977",
        "game_id": "401623977",
        "home_team": "Magomed Ankalaev",
        "away_team": "Johnny Walker",
        "matchup": "Magomed Ankalaev vs Johnny Walker",
        "side": "home",
        "fighter": "Magomed Ankalaev",
        "american_odds_at_pick": -165,
        "model_prob": 0.68,
        "market_prob_at_pick": 0.58,
        "edge_at_pick": 0.10,
        "min_edge_threshold": 0.08,
        "model_version": "demo_seed",
        "odds_source": "demo_seed",
        "close_american_odds": -180,
        "close_market_prob": 0.61,
        "close_fetched_at": "2024-01-13T22:00:00+00:00",
        "commence_time": "2024-01-14T03:00:00+00:00",
        "close_status": "filled",
        "clv_implied_prob": 0.03,
        "clv_decimal_ratio": 0.028,
        "home_win": 1,
        "pick_won": True,
        "betting_ready": False,
        "demo": True,
    },
    {
        "sport": "ufc",
        "pick_id": pick_id("2024-01-13", "401623978", "away"),
        "logged_at": "2024-01-13T14:05:00+00:00",
        "board_date": "2024-01-13",
        "fight_id": "401623978",
        "game_id": "401623978",
        "home_team": "Andrei Arlovski",
        "away_team": "Waldo Cortes-Acosta",
        "matchup": "Andrei Arlovski vs Waldo Cortes-Acosta",
        "side": "away",
        "fighter": "Waldo Cortes-Acosta",
        "american_odds_at_pick": -120,
        "model_prob": 0.57,
        "market_prob_at_pick": 0.52,
        "edge_at_pick": 0.05,
        "min_edge_threshold": 0.08,
        "model_version": "demo_seed",
        "odds_source": "demo_seed",
        "close_american_odds": None,
        "close_market_prob": None,
        "close_fetched_at": None,
        "commence_time": "2024-01-14T03:30:00+00:00",
        "close_status": "pending",
        "clv_implied_prob": None,
        "clv_decimal_ratio": None,
        "home_win": None,
        "pick_won": None,
        "betting_ready": False,
        "demo": True,
    },
]


def seed_demo(*, force: bool = False) -> int:
    if FORWARD_CLV_UFC_LOG.exists() and not force:
        existing = [
            line.strip()
            for line in FORWARD_CLV_UFC_LOG.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if existing:
            print(
                f"Log already has {len(existing)} rows — use --force to append demo rows."
            )
            return 0

    FORWARD_CLV_UFC_LOG.parent.mkdir(parents=True, exist_ok=True)
    logged_at = datetime.now(timezone.utc).isoformat()
    with FORWARD_CLV_UFC_LOG.open("a", encoding="utf-8") as fh:
        for row in DEMO_ROWS:
            out = {**row, "seeded_at": logged_at}
            fh.write(json.dumps(out, default=str) + "\n")
    print(f"Wrote {len(DEMO_ROWS)} demo rows to {FORWARD_CLV_UFC_LOG}")
    return len(DEMO_ROWS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo UFC forward CLV log rows")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Append clearly labeled demo rows (required flag)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Append even when log is non-empty",
    )
    args = parser.parse_args()
    if not args.demo:
        parser.error("Pass --demo to confirm demo seed rows")
    seed_demo(force=args.force)


if __name__ == "__main__":
    main()
