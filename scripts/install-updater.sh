#!/usr/bin/env bash
#
# install-updater.sh — install the host-side watcher that makes the dashboard's
# "Update now" button work. A container can't rebuild its own stack, so this
# systemd path-unit watches for the trigger file the app writes and then runs
# update.sh (git pull + docker compose build + up) on the host.
#
#   sudo ./scripts/install-updater.sh                 # uses /opt/solar-tracker
#   sudo ./scripts/install-updater.sh /srv/solar      # custom deploy dir
#
set -euo pipefail

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root:  sudo ./scripts/install-updater.sh"

APP_DIR="${1:-${SM_APP_DIR:-/opt/solar-tracker}}"
APP_DIR="$(cd "$APP_DIR" 2>/dev/null && pwd || true)"
[ -n "$APP_DIR" ] || die "Deploy directory not found. Pass it: sudo ./scripts/install-updater.sh /path/to/solar-tracker"
BRANCH="${SM_GITHUB_BRANCH:-main}"
UP="$APP_DIR/scripts/update.sh"

say "Deploy dir : $APP_DIR"
[ -f "$UP" ] || die "$UP not found — extract the project there first."
chmod +x "$UP"
mkdir -p "$APP_DIR/.update"

# Sanity: is this a git checkout the updater can pull?
if git -C "$APP_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    say "git remote: $(git -C "$APP_DIR" remote get-url origin 2>/dev/null || echo '(none)')"
    if ! git -C "$APP_DIR" ls-remote --heads origin >/dev/null 2>&1; then
        warn "git can't reach the remote non-interactively. For a PRIVATE repo, give the"
        warn "checkout credentials, e.g. embed a token in the remote URL:"
        warn "  git -C $APP_DIR remote set-url origin \\"
        warn "      https://USER:TOKEN@github.com/dtitsolutions/solar-tracker.git"
    fi
else
    warn "$APP_DIR is NOT a git checkout — 'Update now' can't pull here."
    warn "Re-deploy as a clone:  git clone https://github.com/dtitsolutions/solar-tracker.git"
fi

say "Writing systemd units (deploy dir baked in)"
cat > /etc/systemd/system/solar-tracker-update.service <<EOF
[Unit]
Description=Apply Solar Monitor update (git pull + docker build + restart)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=SM_APP_DIR=${APP_DIR}
Environment=SM_GITHUB_BRANCH=${BRANCH}
ExecStart=${UP}
EOF

cat > /etc/systemd/system/solar-tracker-update.path <<EOF
[Unit]
Description=Watch for Solar Monitor update requests

[Path]
PathExists=${APP_DIR}/.update/request
Unit=solar-tracker-update.service

[Install]
WantedBy=multi-user.target
EOF

say "Enabling the watcher"
systemctl daemon-reload
systemctl enable --now solar-tracker-update.path

say "Status"
systemctl --no-pager status solar-tracker-update.path | sed -n '1,4p' || true

say "Installed. Test it end-to-end:"
echo "  1) In the dashboard: Settings -> Updates -> Update now (then 'Show logs')."
echo "  2) Or from the host:  touch $APP_DIR/.update/request"
echo "     watch it run:      journalctl -u solar-tracker-update.service -f"
echo "     and the app log:   tail -f $APP_DIR/.update/update.log"
