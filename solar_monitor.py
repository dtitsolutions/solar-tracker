#!/usr/bin/env python3
"""
solar_monitor.py - Live web dashboard for a GoodWe inverter on the local network.

One process that polls the inverter (UDP 8899), pushes live updates to the
browser over Server-Sent Events, shows a power-flow dashboard, and can log to
CSV. The inverter IP can be set from the dashboard (Settings -> Connect) or
preset with --ip; the last IP set from the UI is saved and reused on restart.

The inverter allows ONE poller at a time - run this OR the standalone logger.

Examples:
    python solar_monitor.py --demo                       # no inverter; http://localhost:8080
    python solar_monitor.py --port 80                    # start, then set IP in the dashboard
    python solar_monitor.py --ip 192.168.1.50 --port 80 --log /opt/solar-monitor/solar.csv

Needs (live reads):  pip install goodwe
"""

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import queue
import random
import secrets
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import urllib.request
import urllib.error

# ---- GitHub auto-update -------------------------------------------------
_UPDATE = {
    "repo": os.environ.get("SM_GITHUB_REPO", "dtitsolutions/solar-tracker"),
    "branch": os.environ.get("SM_GITHUB_BRANCH", "main"),
    "trigger": os.environ.get("SM_UPDATE_TRIGGER", "/app/.update/request"),
    "version_file": os.environ.get("SM_VERSION_FILE", "/app/.update/DEPLOYED_SHA"),
}

def _update_source():
    """Effective repo/branch/token: settings in the config DB win, else env."""
    repo = _UPDATE["repo"]
    branch = _UPDATE["branch"]
    token = (os.environ.get("SM_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    try:
        db = config_store.get_update_source()
        if db.get("repo"):
            repo = db["repo"]
        if db.get("branch"):
            branch = db["branch"]
        if db.get("token"):
            token = db["token"]
    except Exception:
        pass
    return repo, branch, token


def _deployed_sha():
    """The commit currently running: build-time env first, then a file the
    host updater writes after each deploy. Empty string if unknown."""
    sha = (os.environ.get("SM_DEPLOYED_SHA") or "").strip()
    if sha:
        return sha
    try:
        with open(_UPDATE["version_file"], "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

def check_for_update():
    """Ask GitHub for the latest commit on the tracked branch and compare it
    to what is deployed. Requires outbound internet to api.github.com. Supports
    private repos when a token is set (config DB or SM_GITHUB_TOKEN)."""
    repo, branch, token = _update_source()
    deployed = _deployed_sha()
    known = bool(deployed)
    url = "https://api.github.com/repos/%s/commits/%s" % (repo, branch)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "solar-monitor-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            hint = ("repository is private or not found — add a token under "
                    "Settings -> Updates (or set SM_GITHUB_TOKEN)"
                    if not token else
                    "token was rejected (check it has read access to %s)" % repo)
            return {"ok": False, "update_available": False, "deployed": deployed,
                    "deployed_known": known, "repo": repo, "branch": branch,
                    "error": "GitHub returned %s — %s" % (e.code, hint)}
        return {"ok": False, "update_available": False, "deployed": deployed,
                "deployed_known": known, "error": "GitHub error HTTP %s" % e.code}
    latest = (data.get("sha") or "")[:40]
    commit = data.get("commit", {}) or {}
    msg = (commit.get("message") or "").split("\n")[0][:200]
    date = ((commit.get("author") or {}).get("date")) or ""
    same = bool(latest) and known and (latest.startswith(deployed) or deployed.startswith(latest))
    return {
        "ok": True, "repo": repo, "branch": branch,
        "deployed": deployed, "deployed_short": deployed[:7], "deployed_known": known,
        "latest": latest, "latest_short": latest[:7],
        "message": msg, "date": date,
        "update_available": bool(latest) and known and not same,
        "html_url": data.get("html_url") or ("https://github.com/%s" % repo),
    }

def update_version():
    """Deployed version only — no internet needed. Lets the browser do the
    GitHub comparison itself when the server can't reach api.github.com."""
    repo, branch, token = _update_source()
    deployed = _deployed_sha()
    return {
        "ok": True, "repo": repo, "branch": branch,
        "deployed": deployed, "deployed_short": deployed[:7],
        "deployed_known": bool(deployed),
        "private": bool(token),
    }

def request_update():
    """Drop a trigger file the host-side updater watches; it does the actual
    git pull + docker build + restart (a container can't rebuild itself).
    Creates the directory if missing and reports a clear error otherwise."""
    trig = _UPDATE["trigger"]
    d = os.path.dirname(trig) or "."
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:                                # noqa: BLE001
        raise RuntimeError(
            "the update directory %s is not available in the container — make sure "
            "docker-compose.yml mounts it (volumes: - ./.update:/app/.update) and "
            "rebuild (%s)" % (d, e))
    if not os.access(d, os.W_OK):
        raise RuntimeError("the update directory %s is not writable by the app" % d)
    with open(trig, "w", encoding="utf-8") as f:
        f.write("update requested at %s\n" % time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    return {"trigger": trig}
# -------------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.inverter_goodwe import SUMMARY  # noqa: E402
import config_store  # noqa: E402
import data_store  # noqa: E402
import logsetup  # noqa: E402
import workers  # noqa: E402

# Active timezone name (kept in sync with settings); drives server time + logs.
_TZ = {"name": os.environ.get("SM_TIMEZONE", "UTC")}

SUMMARY_IDS = [sid for sid, _ in SUMMARY]
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
PAGE = b"<h1>dashboard.html not loaded</h1>"

# In-memory login sessions: token -> {"user", "exp"}. Cleared on restart.
SESSIONS = {}
SESSION_TTL = 7 * 24 * 3600

# Cached settings so the request path keeps working through a brief DB blip.
_settings_lock = threading.Lock()
_settings_cache = {}

_lock = threading.Lock()
_state = {
    "ok": False, "error": "starting up", "time": None,
    "inverter_ip": None, "inverter_id": "default",
    "inverter_nickname": None, "inverter_brand": None,
    "poll_interval_seconds": 5, "log_interval_seconds": 30,
    "model": None, "serial": None, "data": {}, "units": {}, "demo": False,
    "grid_status": "unknown", "grid_status_src": None,
}

# Poller lifecycle: bumping _poll_gen tells the running poller to stop so a new
# one (e.g. a new IP) can take over.
_poll_lock = threading.Lock()
_poll_gen = 0
POLLER = {"poll_interval_seconds": 5, "log_interval_seconds": 30, "retries": 3,
          "override": None, "inverter_id": "default"}


# --------------------------------------------------------------------------- #
#  Live push (Server-Sent Events)
# --------------------------------------------------------------------------- #
class Broadcaster:
    def __init__(self):
        self._subs = set()
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=8)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def publish(self, payload):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except queue.Empty:
                    pass


BROADCAST = Broadcaster()

# Optional Redis: the Node web tier reads live state from here (cache) and
# subscribes to the channel for push updates. All best-effort - if Redis is
# absent the Python server still works on its own.
REDIS = {
    "host": os.environ.get("SM_REDIS_HOST", ""),
    "port": int(os.environ.get("SM_REDIS_PORT", "6379")),
    "key": os.environ.get("SM_REDIS_KEY", "sm:live"),
    "channel": os.environ.get("SM_REDIS_CHANNEL", "sm:live"),
}
_redis_client = None
_redis_ready = False


def _redis():
    global _redis_client, _redis_ready
    if not REDIS["host"]:
        return None
    if _redis_client is None:
        try:
            import redis as _r
            _redis_client = _r.Redis(host=REDIS["host"], port=REDIS["port"],
                                     socket_connect_timeout=3, socket_timeout=3)
            _redis_ready = True
        except Exception as e:                       # noqa: BLE001
            print(f"[redis] unavailable ({e}); continuing without it", flush=True)
            _redis_client = None
    return _redis_client


def _publish():
    with _lock:
        payload = json.dumps(_state)
    BROADCAST.publish(payload)
    r = _redis()
    if r is not None:
        try:
            r.set(REDIS["key"], payload)
            r.publish(REDIS["channel"], payload)
        except Exception as e:                       # noqa: BLE001
            print(f"[redis] publish failed: {e}", flush=True)


def _purge_inverter_redis(inv_id):
    """Remove every Redis key tied to one inverter (live snapshot + namespaced
    keys), called on delete to reclaim space. Best-effort."""
    if not inv_id:
        return
    r = _redis()
    if r is None:
        return
    try:
        keys = set()
        keys.add(f"{REDIS['key']}:{inv_id}")
        try:
            keys.update(r.scan_iter(match=f"sm:*:{inv_id}"))
            keys.update(r.scan_iter(match=f"sm:*:{inv_id}:*"))
        except Exception:
            pass
        for k in keys:
            try:
                r.delete(k)
            except Exception:
                pass
    except Exception as e:                               # noqa: BLE001
        print(f"[redis] purge failed for {inv_id}: {e}", flush=True)


def _coerce(v):
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


# --------------------------------------------------------------------------- #
#  On-grid / off-grid detection
# --------------------------------------------------------------------------- #
VOLT_KEYS = ("vgrid", "grid_voltage", "vline1", "vac1", "vgrid1", "meter_voltage")
FREQ_KEYS = ("fgrid", "grid_frequency", "fac1", "grid_freq", "meter_freq")


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_grid_status(data, override=None):
    if override in ("on", "off"):
        return override, f"forced by --assume-grid {override}"
    wm = data.get("work_mode_label")
    if isinstance(wm, str):
        lw = wm.lower()
        if "off-grid" in lw or "off grid" in lw:
            return "off", f"work mode: {wm}"
        if "on-grid" in lw or "on grid" in lw:
            return "on", f"work mode: {wm}"
    gl = data.get("grid_mode_label")
    if isinstance(gl, str):
        lg = gl.lower()
        if "not connected" in lg or "disconnect" in lg:
            return "off", f"grid mode: {gl}"
        if "connected" in lg:
            return "on", f"grid mode: {gl}"
    gm = data.get("grid_mode")
    if isinstance(gm, (int, float)) and not isinstance(gm, bool):
        return ("off" if int(gm) == 0 else "on"), f"grid mode code {int(gm)}"
    for k in FREQ_KEYS:
        hz = _as_float(data.get(k))
        if hz is not None:
            return ("off" if hz < 10 else "on"), f"grid frequency {hz:g} Hz"
    for k in VOLT_KEYS:
        volts = _as_float(data.get(k))
        if volts is not None:
            return ("off" if volts < 50 else "on"), f"grid voltage {volts:g} V"
    return "unknown", "no grid status sensor reported"


class CSVLogger:
    def __init__(self, path):
        self.path = path
        self.cols = None

    def append(self, data):
        present = [sid for sid in SUMMARY_IDS if sid in data]
        new_file = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
        if self.cols is None:
            if not new_file:
                with open(self.path, newline="") as f:
                    self.cols = next(csv.reader(f), None) or (["timestamp"] + present)
            else:
                self.cols = ["timestamp"] + present
        with open(self.path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.cols, extrasaction="ignore")
            if new_file:
                w.writeheader()
            row = {"timestamp": datetime.now().isoformat(timespec="seconds")}
            row.update({k: data.get(k, "") for k in self.cols if k != "timestamp"})
            w.writerow(row)


def _server_now():
    tz = None
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(_TZ["name"])
    except Exception:
        tz = None
    return datetime.now(tz)


# remembers the last grid status per inverter so we only log transitions
_grid_prev = {}


def _store(data, status, reason, model=None, serial=None, units=None, demo=False):
    now = _server_now()
    # log on/off-grid transitions to the system log
    inv_id = POLLER.get("inverter_id", "default")
    prev = _grid_prev.get(inv_id)
    if status in ("on", "off") and prev != status:
        if prev is not None:
            label = "ON-GRID" if status == "on" else "OFF-GRID"
            nick = _state.get("inverter_nickname") or inv_id
            logsetup.system(f"Inverter '{nick}' went {label} ({reason})")
        _grid_prev[inv_id] = status
    with _lock:
        _state.update(ok=True, error=None, demo=demo, data=data,
                      grid_status=status, grid_status_src=reason,
                      time=now.isoformat(timespec="seconds"),
                      server_time=now.strftime("%m/%d/%y %H:%M:%S"),
                      timezone=_TZ["name"])
        if model is not None:
            _state["model"] = model
        if serial is not None:
            _state["serial"] = serial
        if units is not None:
            _state["units"] = units


def _store_error(msg):
    with _lock:
        _state.update(ok=False, error=msg)
    _publish()


# --------------------------------------------------------------------------- #
#  Settings (stored in MySQL via config_store, cached in memory)
# --------------------------------------------------------------------------- #
def load_config():
    """Return cached settings, refreshing from the database when possible."""
    try:
        cfg = config_store.get_settings()
        with _settings_lock:
            _settings_cache.clear()
            _settings_cache.update(cfg)
        return cfg
    except Exception as e:                       # database blip: serve last known
        print(f"[settings] read failed ({e}); using cache", flush=True)
        with _settings_lock:
            return dict(_settings_cache)


def cached_config():
    with _settings_lock:
        if _settings_cache:
            return dict(_settings_cache)
    return load_config()


def save_config(updates):
    cfg = config_store.update_settings(updates)
    with _settings_lock:
        _settings_cache.clear()
        _settings_cache.update(cfg)
    return cfg


def clamp_interval(v, default=None, hi=3600):
    try:
        return max(1, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
#  Authentication (users in MySQL) + cookie sessions
# --------------------------------------------------------------------------- #
def valid_username(u):
    return bool(u) and len(u) <= 40 and all(c.isalnum() or c in "._-" for c in u)


def check_login(user, pw):
    return config_store.check_login(user, pw)


def new_session(user):
    t = secrets.token_urlsafe(32)
    SESSIONS[t] = {"user": user, "exp": time.time() + SESSION_TTL}
    return t


def session_user(token):
    s = SESSIONS.get(token)
    if not s:
        return None
    if time.time() > s["exp"]:
        SESSIONS.pop(token, None)
        return None
    return s["user"]


def session_valid(token):
    return session_user(token) is not None


def cookie_token(handler):
    for part in (handler.headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "sm_session":
            return v
    return None


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
  font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#0e1320;color:#e8eef7}
.card{width:90%;max-width:340px;background:#161d2e;border:1px solid #26304a;border-radius:16px;
  padding:26px 24px;box-shadow:0 18px 50px rgba(0,0,0,.4)}
h1{font-size:1.1rem;margin:0 0 4px}
p.sub{margin:0 0 18px;color:#8a97ad;font-size:.82rem}
label{display:block;font-size:.74rem;color:#8a97ad;margin:12px 0 5px}
input{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #2c3650;background:#0e1320;
  color:#e8eef7;font-size:.95rem;outline:none}
input:focus{border-color:#3f8ee0;box-shadow:0 0 0 3px rgba(63,142,224,.18)}
button{width:100%;margin-top:18px;padding:11px;border:none;border-radius:10px;background:#3f8ee0;
  color:#fff;font-weight:600;font-size:.92rem;cursor:pointer}
button:hover{filter:brightness(1.07)}
.pw-wrap{position:relative;display:block}
.pw-wrap input{padding-right:46px}
#pw-eye{position:absolute;width:auto;margin:0;top:0;right:0;height:100%;padding:0 12px;
  background:transparent;border:none;border-radius:0;display:flex;align-items:center;
  justify-content:center;color:#8a97ad}
#pw-eye:hover{filter:none;color:#e8eef7}
#pw-eye svg{width:20px;height:20px;display:block}
.err{margin-top:14px;color:#ff6b6b;font-size:.8rem;min-height:1em;text-align:center}
.hint{margin-top:14px;color:#67748c;font-size:.7rem;text-align:center}
</style></head><body>
<form class="card" id="f">
  <h1>Solar Monitor</h1>
  <p class="sub">Please sign in to continue.</p>
  <label for="u">Username</label>
  <input id="u" name="username" autocomplete="username" autocapitalize="off" autofocus>
  <label for="p">Password</label>
  <div class="pw-wrap">
    <input id="p" name="password" type="password" autocomplete="current-password">
    <button type="button" id="pw-eye" aria-label="Show password"></button>
  </div>
  <button type="submit">Sign in</button>
  <div class="err" id="e"></div>
  {login_hint}
</form>
<script>
var EYE='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
var EYEOFF='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
(function(){var b=document.getElementById("pw-eye");b.innerHTML=EYE;
b.addEventListener("click",function(){var p=document.getElementById("p");var on=p.type==="password";
p.type=on?"text":"password";b.innerHTML=on?EYEOFF:EYE;
b.setAttribute("aria-label",(on?"Hide":"Show")+" password");});})();
document.getElementById("f").addEventListener("submit",async ev=>{
  ev.preventDefault();
  const e=document.getElementById("e"); e.textContent="";
  try{
    const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({username:document.getElementById("u").value,password:document.getElementById("p").value})});
    if(r.ok){ location.href="/"; }
    else{ e.textContent="Incorrect username or password."; }
  }catch(_){ e.textContent="Could not reach the server."; }
});
</script></body></html>"""


def _backoff():
    return int(min(max(POLLER["poll_interval_seconds"] * 2, 5), 30))


def _maybe_log(data, status, last_log):
    """Write a reading to the database no more often than the logging interval.
    When per-inverter collector containers are running, they own logging."""
    now = time.monotonic()
    if now - last_log < POLLER["log_interval_seconds"]:
        return last_log
    try:
        data_store.insert_reading(data, status, POLLER.get("inverter_id", "default"))
    except Exception as e:                       # never let logging kill the poller
        print(f"[db] reading log failed: {e}", flush=True)
    return now


# --------------------------------------------------------------------------- #
#  Pollers (restartable via _poll_gen)
# --------------------------------------------------------------------------- #
async def _poll_real(ip, gen):
    import lib
    driver = lib.get_driver(_state.get("inverter_brand") or "goodwe")
    inverter = await driver.connect(ip, retries=POLLER["retries"])
    if gen != _poll_gen:
        return
    units = {s.id_: (s.unit or "") for s in inverter.sensors()}
    with _lock:
        _state["model"] = inverter.model_name
        _state["serial"] = inverter.serial_number
        _state["units"] = units
    last_log = 0.0
    while gen == _poll_gen:
        try:
            raw = await inverter.read_runtime_data()
            data = {k: _coerce(v) for k, v in raw.items()}
            status, reason = compute_grid_status(data, POLLER["override"])
            _store(data, status, reason)
            _publish()
            last_log = _maybe_log(data, status, last_log)
        except Exception as e:
            _store_error(f"read failed: {e}")
        await asyncio.sleep(POLLER["poll_interval_seconds"])


def _run_real(ip, gen):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while gen == _poll_gen:
        try:
            loop.run_until_complete(_poll_real(ip, gen))
        except Exception as e:
            if gen != _poll_gen:
                break
            _store_error(f"connect failed: {e}")
            for _ in range(_backoff()):
                if gen != _poll_gen:
                    break
                time.sleep(1)
    loop.close()


def start_poller(ip, inverter_id="default"):
    global _poll_gen
    with _poll_lock:
        _poll_gen += 1
        gen = _poll_gen
    POLLER["inverter_id"] = inverter_id or "default"
    nickname, brand = None, None
    if inverter_id and inverter_id != "default":
        try:
            inv = config_store.get_inverter(inverter_id)
            if inv:
                nickname, brand = inv.get("nickname"), inv.get("brand")
        except Exception:
            pass
    with _lock:
        _state.update(inverter_ip=ip, inverter_id=(inverter_id or "default"),
                      inverter_nickname=nickname, inverter_brand=brand,
                      ok=False, demo=False, error=f"connecting to {ip}…")
    try:
        logsetup.system(f"Connecting to inverter '{nickname or inverter_id}' at {ip}")
    except Exception:
        pass
    _publish()
    threading.Thread(target=_run_real, args=(ip, gen), daemon=True).start()


def stop_poller_clear(message):
    """Stop all workers and reset the live view (e.g. after the last inverter is
    deleted) so the dashboard stops showing stale data."""
    workers.stop_all()
    _grid_prev.clear()
    with _lock:
        _state.clear()
        _state.update(ok=False, demo=False, error=message, data={}, units={},
                      inverter_ip="", inverter_id="", inverter_nickname=None,
                      inverter_brand=None, grid_status="unknown", grid_status_src=None)
    _publish()


# Whether the whole app is running in demo mode (set in main()).
DEMO = {"on": False}


def resync_workers():
    """Ensure one worker process per enabled inverter; pick an active one if unset."""
    try:
        invs = config_store.list_inverters()
    except Exception:
        invs = []
    try:
        if not (cached_config().get("active_inverter_id") or ""):
            for c in invs:
                if c.get("is_enabled"):
                    config_store.set_active_inverter(c["id"])
                    load_config()
                    break
    except Exception:
        pass
    workers.sync(invs, DEMO["on"], POLLER["poll_interval_seconds"],
                 POLLER["log_interval_seconds"], _TZ["name"])


def _mirror_loop():
    """Surface the active inverter's latest worker snapshot into the live view."""
    while True:
        try:
            active = cached_config().get("active_inverter_id") or "default"
            snap = workers.live_snapshot(active) or workers.live_snapshot("default")
            if snap:
                now = _server_now()
                snap["server_time"] = now.strftime("%m/%d/%y %H:%M:%S")
                snap["timezone"] = _TZ["name"]
                st = snap.get("grid_status")
                prev = _grid_prev.get(active)
                if st in ("on", "off") and prev != st:
                    if prev is not None:
                        lbl = "ON-GRID" if st == "on" else "OFF-GRID"
                        logsetup.system(f"Inverter '{snap.get('inverter_nickname') or active}'"
                                        f" went {lbl} ({snap.get('grid_status_src')})")
                    _grid_prev[active] = st
                with _lock:
                    _state.clear()
                    _state.update(snap)
                _publish()
        except Exception:
            pass
        time.sleep(1)


# ---------------------------------------------------------------------------
# Monitoring: per-inverter Prometheus metrics + JSON health (for Grafana /
# New Relic / blackbox probes). Exposes name, status {ongrid,offgrid,offline}
# and the energy flows for graphing (generation, consumption, import, export).
# ---------------------------------------------------------------------------
_METRICS_TOKEN = os.environ.get("SM_METRICS_TOKEN", "").strip()


def _offline_timeout():
    try:
        return max(30, int(os.environ.get("SM_OFFLINE_TIMEOUT_SECONDS", "120")))
    except Exception:
        return 120


def _esc(v):
    """Escape a Prometheus label value."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _inverter_states():
    """Per-inverter monitoring view: id, name, brand, up, status, data, health."""
    try:
        invs = config_store.list_inverters()
    except Exception:
        invs = []
    now = time.time()
    tmo = _offline_timeout()
    out = []
    for inv in invs:
        if not inv.get("is_enabled"):
            continue                                     # not polled -> not monitored
        name = inv.get("nickname") or inv["id"]
        brand = inv.get("brand") or "goodwe"
        snap = workers.live_snapshot(inv["id"]) or {}
        h = workers.health_snapshot(inv["id"]) or {}
        ls = h.get("last_success")
        up = bool(ls and (now - ls) <= tmo)
        if not up:
            status = "offline"
        else:
            gs = snap.get("grid_status")
            status = "offgrid" if gs == "off" else "ongrid"
        out.append({"id": inv["id"], "name": name, "brand": brand, "enabled": True,
                    "up": up, "status": status, "data": snap.get("data") or {}, "health": h})
    return out


def _render_metrics():
    states = _inverter_states()
    lines = []

    def block(name, typ, helptext, samples):
        if not samples:
            return
        lines.append("# HELP %s %s" % (name, helptext))
        lines.append("# TYPE %s %s" % (name, typ))
        lines.extend(samples)

    def lab(s):
        return 'id="%s",name="%s",brand="%s"' % (_esc(s["id"]), _esc(s["name"]), _esc(s["brand"]))

    block("solar_inverter_up", "gauge",
          "1 if the inverter reported within the offline timeout, else 0",
          ['solar_inverter_up{%s} %d' % (lab(s), 1 if s["up"] else 0) for s in states])

    status_samples = []
    for s in states:
        for st in ("ongrid", "offgrid", "offline"):
            status_samples.append('solar_inverter_status{%s,status="%s"} %d'
                                  % (lab(s), st, 1 if s["status"] == st else 0))
    block("solar_inverter_status", "gauge",
          "Per-inverter status; the active state is 1 (ongrid/offgrid/offline)", status_samples)

    block("solar_inverter_grid_connected", "gauge",
          "1 when on-grid, 0 otherwise (offgrid or offline)",
          ['solar_inverter_grid_connected{%s} %d' % (lab(s), 1 if s["status"] == "ongrid" else 0)
           for s in states])

    block("solar_inverter_last_success_timestamp_seconds", "gauge",
          "Unix time of the last successful read",
          ['solar_inverter_last_success_timestamp_seconds{%s} %s'
           % (lab(s), repr(float(s["health"].get("last_success") or 0.0))) for s in states])

    block("solar_inverter_poll_errors_total", "counter",
          "Cumulative connect/read errors since the worker started",
          ['solar_inverter_poll_errors_total{%s} %d' % (lab(s), int(s["health"].get("errors") or 0))
           for s in states])

    def metric(name, typ, helptext, key, scale=1.0):
        samples = []
        for s in states:
            v = (s.get("data") or {}).get(key)
            if _isnum(v):
                samples.append('%s{%s} %s' % (name, lab(s), repr(float(v) * scale)))
        block(name, typ, helptext, samples)

    metric("solar_generation_watts", "gauge", "Instantaneous PV generation (W)", "ppv")
    metric("solar_consumption_watts", "gauge", "Instantaneous house consumption (W)", "house_consumption")
    metric("solar_battery_power_watts", "gauge", "Battery power (W; sign per inverter convention)", "pbattery1")
    metric("solar_battery_soc_percent", "gauge", "Battery state of charge (%)", "battery_soc")
    metric("solar_temperature_celsius", "gauge", "Inverter temperature (C)", "temperature")

    grid, imp, exp = [], [], []
    for s in states:
        gp = (s.get("data") or {}).get("active_power")
        if _isnum(gp):
            gp = float(gp)
            grid.append('solar_grid_power_watts{%s} %s' % (lab(s), repr(gp)))
            imp.append('solar_grid_import_watts{%s} %s' % (lab(s), repr(gp if gp > 0 else 0.0)))
            exp.append('solar_grid_export_watts{%s} %s' % (lab(s), repr(-gp if gp < 0 else 0.0)))
    block("solar_grid_power_watts", "gauge", "Grid power (W; +import / -export)", grid)
    block("solar_grid_import_watts", "gauge", "Instantaneous grid import (W, >=0)", imp)
    block("solar_grid_export_watts", "gauge", "Instantaneous grid export (W, >=0)", exp)

    metric("solar_energy_generated_kwh_total", "counter", "Lifetime energy generated (kWh)", "e_total")
    metric("solar_energy_grid_imported_kwh_total", "counter", "Lifetime grid import, meter (kWh)", "meter_e_total_imp")
    metric("solar_energy_grid_exported_kwh_total", "counter", "Lifetime grid export, meter (kWh)", "meter_e_total_exp")
    metric("solar_energy_generated_kwh_today", "gauge", "Energy generated today (kWh, resets at local midnight)", "gen_today")
    metric("solar_energy_consumed_kwh_today", "gauge", "Energy consumed today (kWh, resets at local midnight)", "cons_today")
    metric("solar_energy_imported_kwh_today", "gauge", "Energy imported today (kWh, resets at local midnight)", "imp_today")
    metric("solar_energy_exported_kwh_today", "gauge", "Energy exported today (kWh, resets at local midnight)", "exp_today")

    return "\n".join(lines) + "\n"


def _health_item(s):
    d = s.get("data") or {}
    return {"id": s["id"], "name": s["name"], "brand": s["brand"], "enabled": s["enabled"],
            "up": s["up"], "status": s["status"],
            "last_success": s["health"].get("last_success"),
            "errors": int(s["health"].get("errors") or 0),
            "generation_w": d.get("ppv"), "consumption_w": d.get("house_consumption"),
            "generation_today_kwh": d.get("gen_today"),
            "battery_soc_percent": d.get("battery_soc"), "grid_w": d.get("active_power")}


def _health_payload(one_id=None):
    """Return (payload, http_status). payload is None only when one_id is unknown."""
    states = _inverter_states()
    if one_id is not None:
        match = [s for s in states if s["id"] == one_id]
        if not match:
            return None, 404
        item = _health_item(match[0])
        return item, (200 if item["up"] else 503)
    items = [_health_item(s) for s in states]
    all_up = all((not it["enabled"]) or it["up"] for it in items) if items else True
    return ({"status": "ok" if all_up else "degraded", "inverters": items},
            200 if all_up else 503)


def _history(inverter_id, range_key):
    """Bucketed time-series for charts: avg power per bucket over the range."""
    from datetime import timedelta, timezone
    now = datetime.now(timezone.utc)
    if range_key == "today":
        try:
            local = _server_now()
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            since = midnight.astimezone(timezone.utc) if midnight.tzinfo else (now - timedelta(hours=24))
        except Exception:
            since = now - timedelta(hours=24)
        n = 48
    elif range_key == "7d":
        since, n = now - timedelta(days=7), 84
    elif range_key == "30d":
        since, n = now - timedelta(days=30), 90
    else:
        since, n = None, 120
    try:
        rows = data_store.series(inverter_id, since, None)
    except Exception:
        rows = []
    pts = []
    for r in rows:
        ra = r.get("recorded_at")
        try:
            t = datetime.fromisoformat(ra) if isinstance(ra, str) else ra
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        pts.append((t, r))
    if not pts:
        return {"ok": True, "range": range_key, "id": inverter_id, "buckets": []}
    # Bucket over the REQUESTED window so data sits at its true time position and
    # sparse ranges show gaps instead of being stretched across the whole chart.
    if since is not None:
        t0, t1 = since, now
    else:                                                # "all" -> span the data
        t0, t1 = pts[0][0], pts[-1][0]
    width = ((t1 - t0).total_seconds() or 1.0) / n
    agg = [{"solar": 0.0, "house": 0.0, "imp": 0.0, "exp": 0.0, "batt": 0.0, "soc": 0.0, "c": 0}
           for _ in range(n)]
    for t, r in pts:
        idx = int((t - t0).total_seconds() / width)
        if idx < 0 or idx >= n:                          # outside the window
            continue
        a = agg[idx]
        a["c"] += 1
        a["solar"] += (r.get("solar_power_w") or 0)
        a["house"] += (r.get("house_power_w") or 0)
        g = r.get("grid_power_w") or 0
        a["imp"] += g if g > 0 else 0
        a["exp"] += -g if g < 0 else 0
        a["batt"] += (r.get("battery_power_w") or 0)
        a["soc"] += (r.get("battery_soc_percent") or 0)
    out = []
    for i, a in enumerate(agg):
        if not a["c"]:
            continue
        bt = t0 + timedelta(seconds=width * (i + 0.5))
        c = a["c"]
        out.append({"t": bt.isoformat(), "solar": round(a["solar"] / c),
                    "house": round(a["house"] / c), "import": round(a["imp"] / c),
                    "export": round(a["exp"] / c), "battery": round(a["batt"] / c),
                    "soc": round(a["soc"] / c)})
    return {"ok": True, "range": range_key, "id": inverter_id, "buckets": out}


def _metrics_authorized(handler):
    if not _METRICS_TOKEN:
        return True
    auth = handler.headers.get("Authorization", "") or ""
    if auth.lower().startswith("bearer "):
        if auth[7:].strip() == _METRICS_TOKEN:
            return True
    q = parse_qs(urlparse(handler.path).query)
    return (q.get("token", [""])[0] == _METRICS_TOKEN)


DEMO_UNITS = {
    "ppv": "W", "house_consumption": "W", "active_power": "W", "pbattery1": "W",
    "battery_soc": "%", "vgrid": "V", "e_day": "kWh", "e_load_day": "kWh",
    "e_day_exp": "kWh", "e_day_imp": "kWh",
    "meter_e_total_imp": "kWh", "meter_e_total_exp": "kWh", "e_total": "kWh", "temperature": "C",
}


def _demo_data(soc, off_grid):
    now = datetime.now()
    hour = now.hour + now.minute / 60
    sun = max(0.0, math.sin((hour - 6) / 12 * math.pi))
    pv = round(sun * 5200 + random.uniform(-120, 120)) if sun > 0 else 0
    load = round(650 + 400 * abs(math.sin(hour)) + random.uniform(-60, 60))
    surplus = pv - load
    if off_grid:
        batt = max(-2500, min(2500, surplus)); grid = 0; vgrid = 0.0
    else:
        batt = 0
        if surplus > 0 and soc < 98:
            batt = min(surplus, 2500)
        elif surplus < 0 and soc > 20:
            batt = max(surplus, -2500)
        grid = load - pv + batt; vgrid = 230.0
    return {
        "ppv": pv, "house_consumption": load, "active_power": grid,
        "pbattery1": batt, "battery_soc": round(soc, 1), "vgrid": vgrid,
        "battery_mode_label": ("Charge" if batt > 8 else "Discharge" if batt < -8 else "Idle"),
        "grid_in_out_label": ("Idle" if off_grid or abs(grid) <= 8 else "Importing" if grid > 0 else "Exporting"),
        "work_mode_label": "Normal (Off-Grid)" if off_grid else "Normal (On-Grid)",
        "grid_mode": 0 if off_grid else 1,
        "e_day": round(sun * 22 + 2, 1), "e_load_day": round(hour * 0.7 + 1, 1),
        "e_day_exp": round(sun * 16, 1), "e_day_imp": 0.0,
        "meter_e_total_imp": 412.3, "meter_e_total_exp": 1880.7,
        "e_total": 5230.1, "temperature": round(34 + sun * 8 + random.uniform(-1, 1), 1),
    }


async def poll_demo(gen):
    soc = 68.0
    last_log = 0.0
    while gen == _poll_gen:
        off_grid = (int(time.time()) % 90) < 20
        d = _demo_data(soc, off_grid)
        soc = max(15.0, min(99.0, soc + d["pbattery1"] / 60000.0 * POLLER["poll_interval_seconds"]))
        status = "off" if off_grid else "on"
        reason = "demo: grid switched off" if off_grid else "demo: grid voltage 230 V"
        _store(d, status, reason, model="GW-DEMO (demo mode)", serial="0000DEMO0000",
               units=DEMO_UNITS, demo=True)
        _publish()
        last_log = _maybe_log(d, status, last_log)
        await asyncio.sleep(POLLER["poll_interval_seconds"])


def start_demo():
    global _poll_gen
    with _poll_lock:
        _poll_gen += 1
        gen = _poll_gen
    with _lock:
        _state["inverter_ip"] = "demo"

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(poll_demo(gen))
        loop.close()

    threading.Thread(target=run, daemon=True).start()


def valid_ip(s):
    return bool(s) and not any(c.isspace() for c in s) and all(32 < ord(c) < 127 for c in s) and len(s) <= 64


# --------------------------------------------------------------------------- #
#  HTTP server
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    # ---- helpers ----
    def _authed(self):
        return session_valid(cookie_token(self))

    def _user(self):
        return session_user(cookie_token(self))

    def _settings_view(self):
        cfg = load_config()
        if not config_store.is_admin(self._user()):
            cfg = dict(cfg)
            cfg.pop("remote_db", None)        # don't expose replication creds to viewers
        return cfg

    def _json(self, code, obj, cookies=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for c in (cookies or []):
            self.send_header("Set-Cookie", c)
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _redirect(self, loc):
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n > 0 else b""
            return json.loads(raw or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/login":
            hint = ""
            try:
                if cached_config().get("debug_mode"):
                    hint = '<div class="hint">Default login is admin / admin</div>'
            except Exception:
                pass
            page = LOGIN_PAGE.replace("{login_hint}", hint)
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
            return
        if path == "/metrics" or path == "/healthz" or path.startswith("/healthz/"):
            if not _metrics_authorized(self):
                self._send(401, "text/plain; charset=utf-8", b"unauthorized\n")
                return
            if path == "/metrics":
                self._send(200, "text/plain; version=0.0.4; charset=utf-8",
                           _render_metrics().encode("utf-8"))
            elif path == "/healthz":
                payload, code = _health_payload()
                self._json(code, payload)
            else:
                payload, code = _health_payload(path[len("/healthz/"):])
                if payload is None:
                    self._json(404, {"ok": False, "error": "no such inverter"})
                else:
                    self._json(code, payload)
            return
        if not self._authed():
            if path.startswith("/api/"):
                self._json(401, {"ok": False, "error": "auth required"})
            else:
                self._redirect("/login")
            return

        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE)
        elif path == "/api/stream":
            self._stream()
        elif path == "/api/settings":
            self._json(200, self._settings_view())
        elif path == "/api/me":
            me = self._user()
            u = config_store.get_user(me) or {}
            self._json(200, {"username": me, "first_name": u.get("first_name", ""),
                             "last_name": u.get("last_name", ""), "role": u.get("role", "user")})
        elif path == "/api/users":
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            self._json(200, {"ok": True, "users": config_store.list_users()})
        elif path == "/api/inverters":
            with _lock:
                active = _state.get("inverter_id")
            self._json(200, {"ok": True, "inverters": config_store.list_inverters(),
                             "active_inverter_id": active,
                             "supported_brands": list(config_store.SUPPORTED_BRANDS),
                             "known_brands": list(config_store.KNOWN_BRANDS)})
        elif self.path.startswith("/api/connect"):
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            q = parse_qs(urlparse(self.path).query)
            ip = (q.get("ip", [""])[0] or "").strip()
            if not valid_ip(ip):
                self._json(400, {"ok": False, "error": "invalid IP"})
            else:
                save_config({"inverter_ip": ip})
                resync_workers()
                self._json(200, {"ok": True, "inverter_ip": ip})
        elif path == "/api/data":
            with _lock:
                body = json.dumps(_state).encode("utf-8")
            self._send(200, "application/json", body)
        elif path == "/api/history":
            q = parse_qs(urlparse(self.path).query)
            rng = (q.get("range", ["today"])[0] or "today")
            iid = (q.get("id", [""])[0]) or (cached_config().get("active_inverter_id") or "default")
            self._json(200, _history(iid, rng))
        elif path == "/api/update/check":
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            try:
                self._json(200, check_for_update())
            except Exception as e:
                self._json(200, {"ok": False, "update_available": False,
                                 "error": "could not reach GitHub (%s)" % e.__class__.__name__})
        elif path == "/api/update/version":
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            self._json(200, update_version())
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            d = self._read_json()
            user = (d.get("username") or "").strip()
            if check_login(user, d.get("password")):
                t = new_session(user)
                logsetup.action(f"login succeeded for '{user}' from {self.client_address[0]}")
                self._json(200, {"ok": True}, cookies=[
                    f"sm_session={t}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"])
            else:
                logsetup.action(f"login FAILED for '{user}' from {self.client_address[0]}")
                self._json(401, {"ok": False, "error": "invalid credentials"})
            return
        if not self._authed():
            self._json(401, {"ok": False, "error": "auth required"})
            return

        if path == "/api/logout":
            logsetup.action(f"{self._user()} logged out")
            SESSIONS.pop(cookie_token(self), None)
            self._json(200, {"ok": True}, cookies=[
                "sm_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"])
        elif path == "/api/profile":
            d = self._read_json()
            config_store.update_profile(self._user(),
                                    str(d.get("first_name", ""))[:60],
                                    str(d.get("last_name", ""))[:60])
            u = config_store.get_user(self._user()) or {}
            self._json(200, {"ok": True, "first_name": u.get("first_name", ""),
                             "last_name": u.get("last_name", "")})
        elif path == "/api/password":
            d = self._read_json()
            current, new = d.get("current") or "", d.get("new") or ""
            me = self._user()
            if not check_login(me, current):
                self._json(403, {"ok": False, "error": "current password is incorrect"})
                return
            if len(new) < 4:
                self._json(400, {"ok": False, "error": "new password must be at least 4 characters"})
                return
            config_store.set_password(me, new)
            logsetup.action(f"{me} changed their password")
            self._json(200, {"ok": True, "message": "password changed"})
        elif path == "/api/users":                       # admin: add user
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            d = self._read_json()
            username = (d.get("username") or "").strip()
            password = d.get("password") or ""
            role = "admin" if d.get("role") == "admin" else "user"
            if not valid_username(username):
                self._json(400, {"ok": False, "error": "username must be letters/numbers/._-"})
            elif config_store.get_user(username):
                self._json(400, {"ok": False, "error": "that username already exists"})
            elif len(password) < 4:
                self._json(400, {"ok": False, "error": "password must be at least 4 characters"})
            else:
                config_store.add_user(username, password,
                                  str(d.get("first_name", ""))[:60],
                                  str(d.get("last_name", ""))[:60], role)
                logsetup.action(f"{self._user()} added user '{username}' (role {role})")
                self._json(200, {"ok": True, "message": f"user '{username}' added"})
        elif path == "/api/users/role":                  # admin: change a role
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            d = self._read_json()
            target = (d.get("username") or "").strip()
            role = "admin" if d.get("role") == "admin" else "user"
            if not config_store.get_user(target):
                self._json(404, {"ok": False, "error": "no such user"})
            elif role != "admin" and config_store.get_role(target) == "admin" and config_store.count_admins() <= 1:
                self._json(400, {"ok": False, "error": "cannot demote the last admin"})
            else:
                config_store.set_role(target, role)
                logsetup.action(f"{self._user()} set role of '{target}' to {role}")
                self._json(200, {"ok": True, "message": f"role updated for '{target}'"})
        elif path == "/api/users/delete":                # admin: delete a user
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            d = self._read_json()
            target = (d.get("username") or "").strip()
            if target == self._user():
                self._json(400, {"ok": False, "error": "you cannot delete your own account"})
            elif not config_store.get_user(target):
                self._json(404, {"ok": False, "error": "no such user"})
            elif config_store.get_role(target) == "admin" and config_store.count_admins() <= 1:
                self._json(400, {"ok": False, "error": "cannot delete the last admin"})
            else:
                config_store.delete_user(target)
                logsetup.action(f"{self._user()} deleted user '{target}'")
                self._json(200, {"ok": True, "message": f"user '{target}' deleted"})
        elif path == "/api/settings":
            d = self._read_json()
            admin = config_store.is_admin(self._user())
            updates, ip, poll_iv, log_iv = {}, None, None, None
            # Basic display preferences: any signed-in user
            if "summary_units" in d:
                updates["summary_units"] = str(d["summary_units"])[:8]
            if isinstance(d.get("tiles"), dict):
                updates["tiles"] = d["tiles"]
            # Advanced settings: admin only
            advanced_keys = ("dashboard_name", "inverter_ip", "poll_interval_seconds",
                             "log_interval_seconds", "remote_db", "timezone", "debug_mode")
            new_tz = None
            if any(k in d for k in advanced_keys):
                if not admin:
                    self._json(403, {"ok": False, "error": "admin only"})
                    return
                if "dashboard_name" in d:
                    updates["dashboard_name"] = str(d["dashboard_name"])[:60]
                if "debug_mode" in d:
                    updates["debug_mode"] = bool(d["debug_mode"])
                if "timezone" in d:
                    new_tz = str(d["timezone"])[:64]
                    updates["timezone"] = new_tz
                if isinstance(d.get("remote_db"), dict):
                    updates["remote_db"] = d["remote_db"]
                if isinstance(d.get("updates"), dict):
                    src = d["updates"]
                    clean = {}
                    if "repo" in src:
                        clean["repo"] = str(src["repo"] or "").strip()[:120]
                    if "branch" in src:
                        clean["branch"] = str(src["branch"] or "").strip()[:80]
                    if src.get("clear_token"):
                        clean["clear_token"] = True
                    elif src.get("token"):
                        clean["token"] = str(src["token"]).strip()[:255]
                    if clean:
                        updates["updates"] = clean
                if "inverter_ip" in d:
                    ip = (d["inverter_ip"] or "").strip()
                    if ip and not valid_ip(ip):
                        self._json(400, {"ok": False, "error": "invalid IP"})
                        return
                    updates["inverter_ip"] = ip
                if "poll_interval_seconds" in d:
                    poll_iv = clamp_interval(d["poll_interval_seconds"])
                    if poll_iv is None:
                        self._json(400, {"ok": False, "error": "invalid poll interval"})
                        return
                    updates["poll_interval_seconds"] = poll_iv
                if "log_interval_seconds" in d:
                    log_iv = clamp_interval(d["log_interval_seconds"], hi=86400)
                    if log_iv is None:
                        self._json(400, {"ok": False, "error": "invalid log interval"})
                        return
                    updates["log_interval_seconds"] = log_iv
            cfg = save_config(updates)
            if new_tz:
                _TZ["name"] = new_tz
                logsetup.set_timezone(new_tz)
            if poll_iv is not None:
                POLLER["poll_interval_seconds"] = poll_iv
            if log_iv is not None:
                POLLER["log_interval_seconds"] = log_iv
            with _lock:
                if poll_iv is not None:
                    _state["poll_interval_seconds"] = poll_iv
                if log_iv is not None:
                    _state["log_interval_seconds"] = log_iv
            if updates:
                logsetup.action(f"{self._user()} updated settings: {', '.join(sorted(updates.keys()))}")
            if poll_iv is not None or log_iv is not None or ip:
                workers.stop_all()                       # restart with new intervals/IP
                resync_workers()
            else:
                _publish()
            self._json(200, {"ok": True, "config": cfg})
        elif path == "/api/mysql/test":                  # remote replication target (admin)
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            d = self._read_json()
            remote = d.get("remote_db") if isinstance(d.get("remote_db"), dict) else d
            ok, msg = config_store.test_remote_connection(remote)
            self._json(200, {"ok": ok, "message": msg})
        elif path == "/api/inverters":                   # admin: add inverter
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            d = self._read_json()
            ip = (d.get("ip_address") or "").strip()
            inv_id, err = config_store.add_inverter(d.get("nickname"), d.get("brand") or "goodwe", ip)
            if err:
                self._json(400, {"ok": False, "error": err})
                return
            logsetup.action(f"{self._user()} added inverter '{d.get('nickname')}' ({d.get('brand') or 'goodwe'})")
            resync_workers()
            self._json(200, {"ok": True, "id": inv_id, "message": "inverter added"})
        elif path == "/api/inverters/update":            # admin: edit inverter
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            d = self._read_json()
            inv_id = (d.get("id") or "").strip()
            if not config_store.get_inverter(inv_id):
                self._json(404, {"ok": False, "error": "no such inverter"})
                return
            ip = d.get("ip_address")
            err = config_store.update_inverter(
                inv_id, nickname=d.get("nickname"), brand=d.get("brand"),
                ip_address=ip, is_enabled=d.get("is_enabled"))
            if err:
                self._json(400, {"ok": False, "error": err})
                return
            logsetup.action(f"{self._user()} updated inverter {inv_id}")
            resync_workers()
            self._json(200, {"ok": True, "message": "inverter updated"})
        elif path == "/api/inverters/delete":            # admin: remove inverter
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            d = self._read_json()
            inv_id = (d.get("id") or "").strip()
            inv = config_store.get_inverter(inv_id)
            if not inv:
                self._json(404, {"ok": False, "error": "no such inverter"})
                return
            was_active = (cached_config().get("active_inverter_id") == inv_id
                          or POLLER.get("inverter_id") == inv_id)
            config_store.delete_inverter(inv_id)
            logsetup.action(f"{self._user()} deleted inverter '{inv.get('nickname')}' ({inv_id})")
            # reclaim space: readings, Redis keys, and the collector container
            removed = 0
            try:
                removed = data_store.delete_inverter_data(inv_id)
            except Exception as e:                       # noqa: BLE001
                logsetup.system_error(f"could not delete readings for {inv_id}: {e}")
            _purge_inverter_redis(inv_id)
            workers.stop(inv_id)                          # stop its process
            load_config()
            remaining = []
            try:
                remaining = config_store.list_inverters()
            except Exception:
                pass
            if not remaining:
                config_store.set_active_inverter("")
                load_config()
                stop_poller_clear("No inverters configured — add one under Settings \u2192 Inverters.")
            else:
                if was_active:
                    config_store.set_active_inverter(remaining[0]["id"])
                    load_config()
                resync_workers()
            self._json(200, {"ok": True, "message": "inverter deleted",
                             "removed_readings": removed})
        elif path == "/api/inverters/select":            # any user: switch active view
            d = self._read_json()
            inv_id = (d.get("id") or "").strip()
            inv = config_store.get_inverter(inv_id)
            if not inv:
                self._json(404, {"ok": False, "error": "no such inverter"})
                return
            config_store.set_active_inverter(inv_id)
            load_config()
            logsetup.action(f"{self._user()} selected inverter '{inv.get('nickname')}'")
            resync_workers()
            self._json(200, {"ok": True, "id": inv_id, "message": "active inverter set"})
        elif path == "/api/setup":                       # first-run wizard (admin)
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            d = self._read_json()
            name = str(d.get("dashboard_name") or "").strip()[:60]
            tz = str(d.get("timezone") or "UTC").strip()[:64]
            new_pw = d.get("admin_password") or ""
            updates = {"timezone": tz, "setup_complete": True}
            if name:
                updates["dashboard_name"] = name
            save_config(updates)
            _TZ["name"] = tz
            logsetup.set_timezone(tz)
            if new_pw:
                if len(new_pw) < 4:
                    self._json(400, {"ok": False, "error": "password must be at least 4 characters"})
                    return
                config_store.set_password(self._user(), new_pw)
            logsetup.action(f"{self._user()} completed first-run setup (tz {tz})")
            self._json(200, {"ok": True, "message": "setup complete"})
        elif path == "/api/update/apply":                # admin: trigger self-update
            if not config_store.is_admin(self._user()):
                self._json(403, {"ok": False, "error": "admin only"})
                return
            try:
                res = request_update()
                logsetup.action(f"{self._user()} requested a software update")
                self._json(200, {"ok": True, "message":
                                 "Update requested. The host updater will pull the latest code, "
                                 "rebuild and restart — this page may briefly disconnect.", **res})
            except Exception as e:                        # noqa: BLE001
                self._json(500, {"ok": False, "error": "could not write update trigger: %s" % e})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = BROADCAST.subscribe()
        try:
            with _lock:
                first = json.dumps(_state)
            self._event(first)
            while True:
                try:
                    self._event(q.get(timeout=15))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            BROADCAST.unsubscribe(q)

    def _event(self, payload):
        self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


def load_template(args):
    global PAGE
    if not os.path.exists(TEMPLATE_PATH):
        sys.exit(f"Missing {TEMPLATE_PATH} - keep dashboard.html next to solar_monitor.py.")
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()
    cfg = {"flipGrid": bool(args.flip_grid), "flipBatt": bool(args.flip_battery)}
    html = html.replace("__REFRESH_MS__", str(max(1000, args.interval * 1000)))
    html = html.replace("__CONFIG__", json.dumps(cfg))
    PAGE = html.encode("utf-8")


def parse_args():
    p = argparse.ArgumentParser(description="Solar Monitor web dashboard (local network + MySQL).")
    p.add_argument("--ip", help="Inverter IP (optional - can also be set in the dashboard)")
    p.add_argument("--demo", action="store_true", help="Serve simulated data; no inverter needed")
    p.add_argument("--port", type=int, default=8080, help="Web port. Default 8080")
    p.add_argument("--host", default="0.0.0.0", help="Bind address. Default 0.0.0.0")
    p.add_argument("--interval", type=int, default=5, help="Fallback poll seconds if none stored. Default 5")
    p.add_argument("--retries", type=int, default=3, help="Connection retries. Default 3")
    p.add_argument("--flip-grid", action="store_true", help="Reverse grid power sign")
    p.add_argument("--flip-battery", action="store_true", help="Reverse battery power sign")
    p.add_argument("--assume-grid", choices=("auto", "on", "off"), default="auto",
                   help="Force grid status instead of auto-detecting. Default auto")
    return p.parse_args()


def main():
    args = parse_args()
    load_template(args)
    POLLER.update(retries=args.retries,
                  override=None if args.assume_grid == "auto" else args.assume_grid)

    # MySQL holds system config + users and is required: wait, build schema, seed admin.
    try:
        config_store.initialise()
    except Exception as e:
        sys.exit(f"\nCannot reach the configuration database (MySQL): {e}\n"
                 f"Check the SM_DB_* environment variables and that MySQL is running.")

    # MongoDB holds the solar time-series. Best-effort: warn but keep serving live
    # data if it's briefly unavailable (logging resumes once it's back).
    try:
        data_store.initialise()
        mongo_ready = True
    except Exception as e:
        mongo_ready = False
        print(f"[mongo] not ready ({e}); live view still works, logging will retry", flush=True)

    cfg = load_config()
    _TZ["name"] = cfg.get("timezone") or os.environ.get("SM_TIMEZONE", "UTC")
    logsetup.init()
    logsetup.set_timezone(_TZ["name"])
    logsetup.system(f"Solar Monitor starting (timezone {_TZ['name']})")
    poll_interval = clamp_interval(cfg.get("poll_interval_seconds"), default=None) or max(1, args.interval)
    log_interval = clamp_interval(cfg.get("log_interval_seconds"), default=30, hi=86400)
    POLLER["poll_interval_seconds"] = poll_interval
    POLLER["log_interval_seconds"] = log_interval
    with _lock:
        _state["poll_interval_seconds"] = poll_interval
        _state["log_interval_seconds"] = log_interval

    DEMO["on"] = bool(args.demo)
    resync_workers()
    if not args.demo:
        try:
            if not config_store.list_inverters() and not (cfg.get("inverter_ip") or args.ip):
                with _lock:
                    _state.update(ok=False, inverter_ip=None,
                                  error="no inverter configured")
        except Exception:
            pass

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=_mirror_loop, daemon=True).start()
    print(f"Solar Monitor -> http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    print(f"Config (MySQL) {config_store.LOCAL_DB['user']}@{config_store.LOCAL_DB['host']}:"
          f"{config_store.LOCAL_DB['port']}/{config_store.LOCAL_DB['database']}")
    print(f"Data   (Mongo) {data_store.MONGO['host']}:{data_store.MONGO['port']}"
          f"/{data_store.MONGO['database']} {'ready' if mongo_ready else '(waiting)'} "
          f"| poll {poll_interval}s | log {log_interval}s")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    except PermissionError:
        sys.exit(f"\nPort {args.port} needs privilege. Use --port 8080 or grant CAP_NET_BIND_SERVICE.")
    except OSError as e:
        sys.exit(f"\nCouldn't bind {args.host}:{args.port}: {e}")


if __name__ == "__main__":
    main()
