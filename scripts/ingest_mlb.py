"""Run MLB data ingest from project root: python scripts/ingest_mlb.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.mlb import run_ingest

if __name__ == "__main__":
    run_ingest()
