#!/usr/bin/env bash
# Solar Monitor host updater: pulls the latest code, rebuilds the image with the
# new commit baked in, and restarts the stack. Triggered by the systemd path
# unit when the app writes /opt/solar-tracker/.update/request (from the
# Settings -> Updates -> "Update now" button), or run by hand.
set -euo pipefail

APP_DIR="${SM_APP_DIR:-/opt/solar-tracker}"
BRANCH="${SM_GITHUB_BRANCH:-main}"
UPDATE_DIR="$APP_DIR/.update"
LOG="$UPDATE_DIR/update.log"

mkdir -p "$UPDATE_DIR"
exec >>"$LOG" 2>&1
trap 'rc=$?; if [ "$rc" -eq 0 ]; then echo "=== update finished $(date -Is) (exit 0) ==="; else echo "=== update FAILED $(date -Is) (exit $rc) ==="; fi' EXIT
echo "=== update started $(date -Is) ==="

cd "$APP_DIR"

# Pick the docker compose CLI (plugin or legacy).
if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"; else COMPOSE="docker-compose"; fi

git fetch --all --prune
git reset --hard "origin/${BRANCH}"
SHA="$(git rev-parse HEAD)"
echo "updating to ${SHA}"

# Rebuild with the commit baked in, then restart in place.
GIT_SHA="$SHA" $COMPOSE build
GIT_SHA="$SHA" $COMPOSE up -d

# Record what is now deployed (read back by the app) and clear the trigger.
echo "$SHA" > "$UPDATE_DIR/DEPLOYED_SHA"
rm -f "$UPDATE_DIR/request"
echo "deployed ${SHA}"
