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

echo "==> restart service (if systemd unit exists)"
if systemctl is-enabled parlay-builder >/dev/null 2>&1; then
  sudo systemctl restart parlay-builder
  sleep 2
fi

PORT="${PORT:-8000}"
BASE="${DEPLOY_URL:-http://127.0.0.1:${PORT}}"

echo "==> verify build endpoint"
BUILD_JSON=$($PY - <<'PY' || true
import json, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/build", timeout=10) as r:
        print(r.read().decode())
except Exception as e:
    print(json.dumps({"error": str(e)}))
PY
)
echo "$BUILD_JSON"

echo "==> verify props API (must not be 401)"
HTTP=$($PY - <<'PY'
import urllib.request, urllib.error
try:
    req = urllib.request.Request("http://127.0.0.1:8000/api/daily/props?limit=1")
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
  echo "ERROR: props API blocked by auth — update app/auth/admin_auth.py on server"
  exit 1
fi

echo "==> key files"
for f in app/services/props_mlb.py app/services/prop_scoring.py static/index.html static/app.js data/processed/props_repository; do
  if [ -e "$f" ]; then echo "  ok $f"; else echo "  MISSING $f"; exit 1; fi
done

echo "Deploy complete. Hard-refresh browser (Ctrl+Shift+R). Check $BASE/api/build"
