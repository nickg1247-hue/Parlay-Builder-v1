"""Compare UFC matchup engine vs baseline logistic on holdout season."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ufc_model_comparison import run_model_comparison


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare UFC matchup vs baseline models")
    parser.add_argument("--no-write", action="store_true", help="Skip writing JSON cache")
    args = parser.parse_args()
    result = run_model_comparison(write_cache=not args.no_write)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
