"""Warm CFB season-level prior caches (talent, returning, FPI, coaches)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingest.cfb_priors import ensure_priors_cache


def main() -> None:
    count = ensure_priors_cache(force=False)
    print(f"CFB prior cache files written/kept: {count}")


if __name__ == "__main__":
    main()
