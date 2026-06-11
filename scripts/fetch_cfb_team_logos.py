"""Download ESPN FBS team logos into data/processed/cfb_team_logos.json."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.cfb_team_logos import LOGO_MAP_PATH, refresh_cfb_logo_map


def main() -> None:
    lookup = refresh_cfb_logo_map(force=True)
    print(f"Wrote {len(lookup)} logo lookup keys -> {LOGO_MAP_PATH}")


if __name__ == "__main__":
    main()
