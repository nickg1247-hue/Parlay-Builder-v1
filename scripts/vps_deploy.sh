#!/usr/bin/env bash
# Full VPS deploy: pull code + data, install deps, restart service, verify props build.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> git pull"
git pull origin main

echo "==> pip install"
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python -m pip install -q -r requirements.txt
  PY=".venv/bin/python"
else
  python3 -m pip install -q -r requirements.txt
  PY="python3"
fi

PORT="${PORT:-8000}"
BASE="${DEPLOY_URL:-http://127.0.0.1:${PORT}}"

echo "==> rebuild daily board if stale (fast path, no ingest)"
$PY - <<'PY' || echo "WARN: daily board rebuild failed — check logs"
from app.services.daily_board import ensure_today_daily_board
ensure_today_daily_board(skip_ingest=True)
PY

echo "==> restart service (if systemd unit exists)"
if systemctl is-enabled parlay-builder >/dev/null 2>&1; then
  sudo systemctl restart parlay-builder
  for i in 1 2 3 4 5 6; do
    sleep 2
    if curl -sf "${BASE}/health" >/dev/null 2>&1; then
      break
    fi
    if [ "$i" -eq 6 ]; then
      echo "WARN: service not responding on /health yet — check: sudo systemctl status parlay-builder"
    fi
  done
fi

echo "==> verify build endpoint"
BUILD_JSON=$($PY - <<PY || true
import json, urllib.request
try:
    with urllib.request.urlopen("${BASE}/api/build", timeout=10) as r:
        print(r.read().decode())
except Exception as e:
    print(json.dumps({"error": str(e)}))
PY
)
echo "$BUILD_JSON"

echo "==> verify props API (must not be 401)"
HTTP=$($PY - <<PY
import urllib.request, urllib.error
try:
    req = urllib.request.Request("${BASE}/api/daily/props?limit=1")
    with urllib.request.urlopen(req, timeout=15) as r:
        print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print("ERR")
PY
)
echo "GET /api/daily/props -> HTTP $HTTP"
if [ "$HTTP" = "401" ]; then
  echo "ERROR: props API returned 401 (user props auth)."
  echo "  Fix: git pull latest, or set PROPS_REQUIRE_VERIFIED_USER=false in .env and restart."
  exit 1
fi

echo "==> verify player stats API (must not be 401)"
PLAYER_HTTP=$($PY - <<PY
import urllib.request, urllib.error
try:
    req = urllib.request.Request("${BASE}/api/players/mlb/592450/prop-context?market_type=batter_hits&line=0.5&side=over")
    with urllib.request.urlopen(req, timeout=15) as r:
        print(r.status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print("ERR")
PY
)
echo "GET /api/players/.../prop-context -> HTTP $PLAYER_HTTP"
if [ "$PLAYER_HTTP" = "401" ]; then
  echo "ERROR: player stats API returned 401 — deploy latest app/auth/user_auth.py"
  exit 1
fi

echo "==> key files"
for f in app/services/props_mlb.py app/services/prop_scoring.py static/index.html static/app.js static/mlb_props.html static/app.css static/home-v2.css; do
  if [ -e "$f" ]; then echo "  ok $f"; else echo "  MISSING $f"; exit 1; fi
done

echo "Deploy complete. Hard-refresh browser (Ctrl+Shift+R). Check $BASE/api/build"
