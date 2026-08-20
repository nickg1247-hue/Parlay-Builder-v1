#!/usr/bin/env bash
# Toggle public construction mode on the live VPS. No restart needed.
#   bash scripts/maintenance.sh on
#   bash scripts/maintenance.sh off
#   bash scripts/maintenance.sh status
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

exec "$PY" "$ROOT/scripts/maintenance.py" "$@"
