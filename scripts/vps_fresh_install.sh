#!/usr/bin/env bash
# Replace /var/www/parlay-builder with a fresh git clone (fixes non-git uploads).
set -euo pipefail

APP_DIR="${APP_DIR:-/var/www/parlay-builder}"
REPO_URL="${REPO_URL:-https://github.com/nickg1247-hue/Parlay-Builder-v1.git}"
BACKUP_ENV="/root/parlay-builder.env.backup"

echo "==> App directory: $APP_DIR"

if [ -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env" "$BACKUP_ENV"
  echo "==> Backed up .env to $BACKUP_ENV"
fi

STAMP=$(date +%Y%m%d%H%M%S)
if [ -d "$APP_DIR" ]; then
  mv "$APP_DIR" "${APP_DIR}.old.${STAMP}"
  echo "==> Moved old folder to ${APP_DIR}.old.${STAMP}"
fi

echo "==> Cloning $REPO_URL"
git clone "$REPO_URL" "$APP_DIR"
cd "$APP_DIR"
git log -1 --oneline

if [ -f "$BACKUP_ENV" ]; then
  cp "$BACKUP_ENV" "$APP_DIR/.env"
  echo "==> Restored .env"
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

echo "==> File checks (must all pass)"
grep -q 'home-picks-grid' static/index.html && echo "  ok index two-column layout"
grep -q 'best-props' static/index.html && echo "  ok best-props column"
grep -q 'props-body' static/game.html && echo "  ok game props UI"
test -f app/services/props_mlb.py && echo "  ok props backend"
test -d data/processed/props_repository && echo "  ok props cache data"

echo "==> systemd unit (verify WorkingDirectory matches $APP_DIR)"
systemctl cat parlay-builder 2>/dev/null | grep -E 'WorkingDirectory|ExecStart' || true

echo "==> restart"
if systemctl is-enabled parlay-builder >/dev/null 2>&1; then
  systemctl restart parlay-builder
  sleep 2
fi

echo "==> API checks"
curl -sf "http://127.0.0.1:8000/api/build" | head -c 400 || echo "WARN: /api/build failed — check PORT and service"
curl -sf "http://127.0.0.1:8000/" | grep -o 'best-props' | head -1 || echo "WARN: homepage missing best-props"

echo ""
echo "Done. Hard-refresh browser. Footer should show Build 2026-06-17-props-v4"
