"""Print a complete pregame explanation for one CFB prediction."""
from __future__ import annotations
import argparse, json, sys
from datetime import date
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from app.services.cfb_prediction_diagnostic import diagnose_cfb_prediction
def main() -> None:
    parser = argparse.ArgumentParser(description="Explain one CFB moneyline prediction")
    parser.add_argument("game_id")
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    print(json.dumps(diagnose_cfb_prediction(args.game_id, args.date), indent=2))
if __name__ == "__main__": main()
