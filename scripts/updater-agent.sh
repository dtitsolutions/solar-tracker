#!/bin/sh
# updater-agent.sh — runs inside the 'updater' sidecar container.
#
# Watches for the trigger the app writes on "Update now", then updates on the
# host: git pull, rebuild with the new commit baked in, restart app + web.
#
# Everything is logged to .update/update.log:
#   - @PHASE / @DONE / @FAIL markers  -> drive the dashboard progress bar
#   - readable lines + real command output -> shown under "Show logs"
set -u

DIR="${SM_APP_DIR:-/opt/solar-tracker}"
BRANCH="${SM_GITHUB_BRANCH:-main}"
TRIG="$DIR/.update/request"
LOG="$DIR/.update/update.log"

command -v git >/dev/null 2>&1 || apk add --no-cache git >/dev/null 2>&1 || true
docker compose version >/dev/null 2>&1 || apk add --no-cache docker-cli-compose >/dev/null 2>&1 || true

mkdir -p "$DIR/.update" 2>/dev/null || true
cd "$DIR" 2>/dev/null || echo "updater: cannot enter $DIR (set SM_APP_DIR)"

mark() { echo "$1" >> "$LOG"; }                              # marker for the progress bar
log()  { echo "$(date '+%H:%M:%S')  $*" >> "$LOG"; }         # readable line for Show logs

run_update() {
  log "Update requested. Target branch: $BRANCH"

  mark "@PHASE downloading"
  log "Downloading latest code from origin/$BRANCH …"
  if git fetch --all --prune >>"$LOG" 2>&1 && git reset --hard "origin/$BRANCH" >>"$LOG" 2>&1; then
    SHA="$(git rev-parse HEAD)"; log "Downloaded $SHA"
  else
    mark "@FAIL download"; log "ERROR: download failed (check git credentials for a private repo)"; return 1
  fi

  mark "@PHASE installing"
  log "Building the new version ($SHA) — this can take a few minutes …"
  if GIT_SHA="$SHA" docker compose build app web >>"$LOG" 2>&1; then
    log "Build finished"
  else
    mark "@FAIL build"; log "ERROR: build failed (see output above)"; return 1
  fi

  mark "@PHASE restarting"
  log "Restarting the application …"
  if GIT_SHA="$SHA" docker compose up -d --no-deps app web >>"$LOG" 2>&1; then
    log "Application restarted"
  else
    mark "@FAIL restart"; log "ERROR: restart failed (see output above)"; return 1
  fi

  echo "$SHA" > "$DIR/.update/DEPLOYED_SHA"
  mark "@DONE $SHA"
  log "Update complete. Now running $SHA"
}

echo "updater agent ready in $DIR; watching for update requests"
while true; do
  if [ -f "$TRIG" ]; then
    rm -f "$TRIG"
    run_update || true
  fi
  sleep 3
done
