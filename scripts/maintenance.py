"""Toggle NTG Sports construction mode. No rebuild or restart.

Usage:
  python scripts/maintenance.py on
  python scripts/maintenance.py off
  python scripts/maintenance.py status
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth.maintenance import (  # noqa: E402
    maintenance_enabled,
    primary_flag_path,
    turn_maintenance_off,
    turn_maintenance_on,
)


def _usage() -> int:
    print("Usage: python scripts/maintenance.py on|off|status")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _usage()
    command = args[0].strip().lower()
    if command not in {"on", "off", "status"}:
        return _usage()

    if command == "on":
        path = turn_maintenance_on()
        print(f"Construction ON ({path})")
        print("Visitors see the under-construction page. Preview: /test")
        return 0
    if command == "off":
        turn_maintenance_off()
        print("Construction OFF — public site is live.")
        return 0

    state = "ON" if maintenance_enabled() else "OFF"
    print(f"Construction is {state} ({primary_flag_path()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
