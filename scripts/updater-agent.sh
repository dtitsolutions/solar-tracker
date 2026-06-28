#!/bin/sh
# updater-agent.sh — runs inside the 'updater' sidecar container.
#
# Watches for the trigger the app writes on "Update now", then updates on the
# host: git pull, rebuild with the new commit baked in, restart app + web.
#
# It writes CLEAN phase markers to .update/update.log (which the dashboard turns
# into a friendly progress bar) and sends the noisy git/docker output to
# .update/update-verbose.log (for troubleshooting only — never shown to users).
set -u

DIR="${SM_APP_DIR:-/opt/solar-tracker}"
BRANCH="${SM_GITHUB_BRANCH:-main}"
TRIG="$DIR/.update/request"
LOG="$DIR/.update/update.log"
VERBOSE="$DIR/.update/update-verbose.log"

command -v git >/dev/null 2>&1 || apk add --no-cache git >/dev/null 2>&1 || true
docker compose version >/dev/null 2>&1 || apk add --no-cache docker-cli-compose >/dev/null 2>&1 || true

mkdir -p "$DIR/.update" 2>/dev/null || true
cd "$DIR" 2>/dev/null || echo "updater: cannot enter $DIR (set SM_APP_DIR)"

mark() { echo "$1" >> "$LOG"; }                 # clean phase marker for the UI

run_update() {
  : > "$VERBOSE"

  mark "@PHASE downloading"
  if ! { git fetch --all --prune && git reset --hard "origin/$BRANCH"; } >>"$VERBOSE" 2>&1; then
    mark "@FAIL download"; return 1; fi
  SHA="$(git rev-parse HEAD 2>>"$VERBOSE")"

  mark "@PHASE installing"
  if ! GIT_SHA="$SHA" docker compose build app web >>"$VERBOSE" 2>&1; then
    mark "@FAIL build"; return 1; fi

  mark "@PHASE restarting"
  if ! GIT_SHA="$SHA" docker compose up -d --no-deps app web >>"$VERBOSE" 2>&1; then
    mark "@FAIL restart"; return 1; fi

  echo "$SHA" > "$DIR/.update/DEPLOYED_SHA"
  mark "@DONE $SHA"
}

echo "updater agent ready in $DIR; watching for update requests"
while true; do
  if [ -f "$TRIG" ]; then
    rm -f "$TRIG"                                # consume once
    run_update || true
  fi
  sleep 3
done
