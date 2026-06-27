#!/usr/bin/env bash
#
# build.sh — build and (re)start the Solar Monitor stack.
#
# Bakes the current git commit into the image so the in-app updater can tell
# when GitHub has something newer. Safe to re-run; it rebuilds and restarts
# in place.
#
#   ./build.sh            # build + start (detached)
#   ./build.sh --no-cache # force a clean rebuild
#   ./build.sh --logs     # build + start, then follow logs
#
set -euo pipefail

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# Run from the project directory (this script's parent: repo root).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
APP_DIR="${SM_APP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$APP_DIR"

NO_CACHE=""
FOLLOW_LOGS=0
for arg in "$@"; do
    case "$arg" in
        --no-cache) NO_CACHE="--no-cache" ;;
        --logs)     FOLLOW_LOGS=1 ;;
        -h|--help)  sed -n '2,12p' "$SELF"; exit 0 ;;
        *) die "Unknown option: $arg" ;;
    esac
done

command -v docker >/dev/null 2>&1 || die "Docker isn't installed. Run ./scripts/install-docker.sh first."
if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="docker-compose"
else die "Docker Compose plugin not found. Install docker-compose-plugin."; fi
[ -f docker-compose.yml ] || die "docker-compose.yml not found in $APP_DIR."

# Current commit (so the updater knows what's deployed). Falls back gracefully.
if command -v git >/dev/null 2>&1 && git -C "$APP_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    GIT_SHA="$(git -C "$APP_DIR" rev-parse HEAD)"
else
    GIT_SHA="${GIT_SHA:-unknown}"
    warn "Not a git checkout — deployed version will show as '$GIT_SHA'."
fi
export GIT_SHA

say "Project : $APP_DIR"
say "Commit  : $GIT_SHA"

say "Building images${NO_CACHE:+ (no cache)}"
# shellcheck disable=SC2086
$COMPOSE build $NO_CACHE

say "Starting the stack (detached)"
$COMPOSE up -d

# Record what is now deployed so the app reads it even without a rebuild.
mkdir -p "$APP_DIR/.update"
echo "$GIT_SHA" > "$APP_DIR/.update/DEPLOYED_SHA" 2>/dev/null || true

say "Status"
$COMPOSE ps

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Done. Open  http://${IP:-<server-ip>}:8080   (sign in admin / admin, then change it)."
echo    "      phpMyAdmin :8081   mongo-express :8082"

if [ "$FOLLOW_LOGS" -eq 1 ]; then
    say "Following app logs (Ctrl+C to stop)"
    $COMPOSE logs -f app
fi
