#!/bin/sh
# updater-agent.sh — runs inside the 'updater' sidecar container.
#
# Watches for the trigger the app writes on "Update now", then updates on the
# host: git pull (authenticated with the .env token — no host git setup needed),
# rebuild with the new commit baked in, restart app + web.
#
# All progress is logged to .update/update.log:
#   - @PHASE / @DONE / @FAIL markers  -> drive the dashboard progress bar
#   - readable lines + real output    -> shown under "Show logs"
set -u

DIR="${SM_APP_DIR:-/opt/solar-tracker}"
BRANCH="${SM_GITHUB_BRANCH:-main}"
REPO="${SM_GITHUB_REPO:-}"
TOKEN="${SM_GITHUB_TOKEN:-}"
TRIG="$DIR/.update/request"
LOG="$DIR/.update/update.log"
SHAFILE="$DIR/.update/DEPLOYED_SHA"

command -v git >/dev/null 2>&1 || apk add --no-cache git >/dev/null 2>&1 || true
docker compose version >/dev/null 2>&1 || apk add --no-cache docker-cli-compose >/dev/null 2>&1 || true

mkdir -p "$DIR/.update" 2>/dev/null || true
cd "$DIR" 2>/dev/null || echo "updater: cannot enter $DIR (set SM_APP_DIR)"

# Record the running commit if it's not known yet, so the dashboard's version
# check is accurate even before the first in-app update.
if [ ! -s "$SHAFILE" ]; then
  H="$(git rev-parse HEAD 2>/dev/null || true)"
  [ -n "$H" ] && echo "$H" > "$SHAFILE"
fi

mark() { echo "$1" >> "$LOG"; }
log()  { echo "$(date '+%H:%M:%S')  $*" >> "$LOG"; }

# Fetch using the .env token (kept out of the URL/logs via an auth header).
git_fetch() {
  if [ -n "$TOKEN" ] && [ -n "$REPO" ]; then
    basic="$(printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\n')"
    git -c http.extraheader="Authorization: Basic $basic" fetch "https://github.com/${REPO}.git" "$BRANCH"
  else
    git fetch origin "$BRANCH"
  fi
}

run_update() {
  log "Update requested. Target: ${REPO:-origin}@$BRANCH"

  mark "@PHASE downloading"
  log "Downloading the latest version …"
  if git_fetch >>"$LOG" 2>&1 && git reset --hard FETCH_HEAD >>"$LOG" 2>&1; then
    SHA="$(git rev-parse HEAD)"; log "Downloaded $SHA"
  else
    mark "@FAIL download"
    log "ERROR: could not download. The token in .env may be missing or lack access to ${REPO:-the repo}."
    return 1
  fi

  mark "@PHASE installing"
  log "Installing the update (building) — this can take a few minutes …"
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

  echo "$SHA" > "$SHAFILE"
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
