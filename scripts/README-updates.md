# In-app updates (Settings -> Updates)

The dashboard can check GitHub for a newer commit and trigger a self-update.
Because a container can't rebuild and restart its own compose stack, the
"Update now" button just drops a trigger file; a tiny **host-side** systemd
watcher does the actual `git pull` + `docker compose build` + `up -d`.

## One-time host setup

```bash
cd /opt/solar-tracker

# 1. Make sure this is a git checkout of the repo (so the updater can pull):
#    git remote -v   ->  https://github.com/dtitsolutions/solar-tracker

# 2. Install the watcher (runs the rebuild when the app requests it):
sudo cp scripts/solar-tracker-update.service /etc/systemd/system/
sudo cp scripts/solar-tracker-update.path    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now solar-tracker-update.path

# 3. First build with the commit baked in (so "deployed" is known):
GIT_SHA="$(git rev-parse HEAD)" docker compose up -d --build
```

## How it works

- **Check:** `GET /api/update/check` (admin only) asks
  `api.github.com/repos/dtitsolutions/solar-tracker/commits/main` for the latest
  commit and compares it to the deployed SHA (`SM_DEPLOYED_SHA`, baked at build,
  or `.update/DEPLOYED_SHA` written after each update). The server needs outbound
  internet to GitHub.
- **Notify:** if they differ, a banner appears and the Updates panel shows the
  new commit message.
- **Apply:** `POST /api/update/apply` writes `/opt/solar-tracker/.update/request`.
  The `.path` unit sees it and runs `scripts/update.sh`, which pulls, rebuilds
  with `GIT_SHA` set, restarts, records the new SHA, and removes the trigger.

Logs: `/opt/solar-tracker/.update/update.log`.

## Notes / safety

- The compose `app` service bind-mounts `./.update` so the trigger file written
  inside the container appears on the host. No Docker socket is exposed to the
  container.
- Updates run on the host with the privileges of the systemd unit (root by
  default). Review `scripts/update.sh` before enabling if that's a concern.
- `git reset --hard origin/main` discards local uncommitted edits in
  `/opt/solar-tracker`. Keep site-specific config in `.env`, which is git-ignored.

## Private repository

If `dtitsolutions/solar-tracker` is private:

- **Check button:** set a read-only token so the server can query GitHub:
  add `SM_GITHUB_TOKEN=github_pat_xxx` to `/opt/solar-tracker/.env`, then
  `docker compose up -d` to pick it up.
- **Update (git pull):** the checkout in `/opt/solar-tracker` must have working
  git credentials. Easiest is to clone over SSH (deploy key) or embed a token in
  the remote, e.g.:
  `git remote set-url origin https://USER:TOKEN@github.com/dtitsolutions/solar-tracker.git`

## "Unable to connect to inverter"

This is separate from updates. If the IP is pingable and UDP 8899 is open but the
app still can't connect, GoodWe auto-detect is likely failing. Set the protocol
family explicitly in `.env` and restart:

    SM_GOODWE_FAMILY=ES        # GW####ES hybrids; ET/EH/DT for other ranges
    docker compose up -d
