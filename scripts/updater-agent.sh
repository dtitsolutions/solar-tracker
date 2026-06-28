#!/bin/sh
# updater-agent.sh — runs inside the 'updater' sidecar container.
#
# The sidecar mounts the Docker socket and the host project dir (at the same
# path). This agent watches for the trigger file the app writes when an admin
# clicks "Update now", then performs the update on the host: git pull, rebuild
# the image with the new commit baked in, and restart the app + web in place.
#
# It writes progress to .update/update.log, which the dashboard streams live
# via "Show logs". A terminal marker (finished/FAILED) is always written so the
# viewer knows when to stop.
set -u

DIR="${SM_APP_DIR:-/opt/solar-tracker}"
BRANCH="${SM_GITHUB_BRANCH:-main}"
TRIG="$DIR/.update/request"
LOG="$DIR/.update/update.log"

# docker CLI ships in the image; add git + the compose plugin if they're missing.
command -v git >/dev/null 2>&1 || apk add --no-cache git >/dev/null 2>&1 || true
docker compose version >/dev/null 2>&1 || apk add --no-cache docker-cli-compose >/dev/null 2>&1 || true

mkdir -p "$DIR/.update" 2>/dev/null || true
cd "$DIR" 2>/dev/null || { echo "updater: cannot enter $DIR (set SM_APP_DIR)"; }

run_update() {
  (
    set -e
    echo "=== update started $(date -Is) ==="
    echo "fetching origin/$BRANCH …"
    git fetch --all --prune
    git reset --hard "origin/$BRANCH"
    SHA="$(git rev-parse HEAD)"
    echo "building $SHA …"
    GIT_SHA="$SHA" docker compose build app web
    echo "restarting app + web …"
    GIT_SHA="$SHA" docker compose up -d --no-deps app web
    echo "$SHA" > "$DIR/.update/DEPLOYED_SHA"
    echo "deployed $SHA"
    echo "=== update finished $(date -Is) (exit 0) ==="
  ) >>"$LOG" 2>&1
}

echo "updater agent ready in $DIR; watching for update requests"
while true; do
  if [ -f "$TRIG" ]; then
    rm -f "$TRIG"                                  # consume once
    run_update || echo "=== update FAILED $(date -Is) ===" >>"$LOG" 2>&1
  fi
  sleep 3
done
