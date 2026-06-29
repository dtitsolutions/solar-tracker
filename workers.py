#!/usr/bin/env python3
"""
workers.py - one OS process per inverter (crash isolation, no Docker socket).

Each enabled inverter is polled by its own multiprocessing.Process. A worker:
  * polls its inverter (real driver, or simulated in demo mode),
  * writes its latest snapshot into a shared Manager dict (read by the main
    process to drive the live view of whichever inverter is active),
  * logs its readings to the shared MongoDB at the log interval.

A crash in one worker cannot take down the others or the web/API process; the
supervisor simply respawns it on the next sync(). No Docker socket is required,
and docker compose still owns the single app container's lifecycle.
"""

import multiprocessing as mp
from datetime import datetime, timezone

_mgr = None
_live = None            # Manager().dict(): inverter_id -> latest snapshot
_health = None          # Manager().dict(): inverter_id -> health beat
_procs = {}             # inverter_id -> {"proc": Process, "stop": Event, "sig": tuple}


def _ensure():
    global _mgr, _live, _health
    if _mgr is None:
        _mgr = mp.Manager()
        _live = _mgr.dict()
        _health = _mgr.dict()
    return _live


def live_snapshot(inverter_id):
    """Latest snapshot for one inverter, or None."""
    if _live is None or not inverter_id:
        return None
    try:
        v = _live.get(inverter_id)
        return dict(v) if v else None
    except Exception:
        return None


def health_snapshot(inverter_id):
    """Health beat for one inverter: {last_success, last_attempt, errors, connected}."""
    if _health is None or not inverter_id:
        return None
    try:
        v = _health.get(inverter_id)
        return dict(v) if v else None
    except Exception:
        return None


def _sig(inv):
    return (inv.get("ip_address") or "", inv.get("brand") or "goodwe",
            bool(inv.get("is_enabled")), inv.get("nickname") or "")


def _run(params, live, health, stop):
    """Worker entrypoint (separate process)."""
    import time
    import data_store
    import logsetup
    import lib
    from solar_monitor import compute_grid_status, _coerce, _demo_data

    inv_id = params["id"]
    brand = params["brand"]
    ip = params["ip"]
    nick = params["nickname"]
    demo = params["demo"]
    poll = max(1, params["poll"])
    logiv = max(1, params["log"])
    batt_cap = params.get("battery_capacity_kwh") or 0
    panel_cap = params.get("panel_capacity_w") or 0
    try:
        logsetup.set_timezone(params.get("tz", "UTC"))
    except Exception:
        pass
    last_log = [0.0]
    errors = [0]

    try:
        from zoneinfo import ZoneInfo
    except Exception:
        ZoneInfo = None
    # today's values are computed from the inverter's lifetime counters against a
    # baseline. On worker START the baseline is seeded from the inverter's own day
    # counters (lifetime - today) so a freshly-added inverter immediately reflects
    # generation/consumption since *its* midnight. At the configured-timezone
    # midnight rollover the baseline is reset to the current lifetime value, so the
    # numbers reset to 0 at local midnight. No data -> the value stays 0.
    _daily = {"date": None, "base": {}, "grid": {"imp": 0.0, "exp": 0.0, "last": None}}
    # tkey -> (lifetime counter, inverter's own "today" counter). Import/export are
    # NOT taken from the inverter here: on ES hybrids the grid energy registers are
    # unreliable (they report export with no real export). Instead imp_today/exp_today
    # are integrated from the *measured* grid power below.
    _LIFE = {"gen_today": ("e_total", "e_day"),
             "cons_today": ("e_load_total", "e_load_day")}

    def _local_date():
        name = params.get("tz", "UTC")
        try:
            if ZoneInfo:
                return datetime.now(ZoneInfo(name)).strftime("%Y-%m-%d")
        except Exception:
            pass
        return datetime.now().strftime("%Y-%m-%d")

    def _num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def _seed_grid_today(ga):
        # Recover today's import/export from already-logged readings so a mid-day
        # restart doesn't lose the day's grid totals.
        name = params.get("tz", "UTC") or "UTC"
        try:
            if ZoneInfo:
                midnight = datetime.now(ZoneInfo(name)).replace(hour=0, minute=0, second=0, microsecond=0)
                since = midnight.astimezone(timezone.utc)
            else:
                since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            rows = data_store.series(inv_id, since, None, limit=20000)
        except Exception:
            return
        prev = None
        for r in rows:
            ra = r.get("recorded_at")
            try:
                t = datetime.fromisoformat(ra) if isinstance(ra, str) else ra
            except Exception:
                continue
            gw = r.get("grid_power_w")
            if prev is not None and isinstance(gw, (int, float)):
                dt = (t - prev).total_seconds()
                if 0 < dt <= 600:
                    if gw > 0:
                        ga["imp"] += gw * dt / 3600.0
                    elif gw < 0:
                        ga["exp"] += (-gw) * dt / 3600.0
            prev = t

    def daily(data):
        today = _local_date()
        first = _daily["date"] is None                   # worker just started
        rollover = (not first) and _daily["date"] != today
        if first or rollover:
            _daily["date"] = today
            _daily["base"] = {}
            _daily["grid"] = {"imp": 0.0, "exp": 0.0, "last": None}
            if first:
                _seed_grid_today(_daily["grid"])
            for tkey, (life, dayfld) in _LIFE.items():
                lv = data.get(life)
                if not _num(lv):
                    continue
                if first:                                 # seed so today == inverter's own day value
                    dv = data.get(dayfld)
                    _daily["base"][tkey] = float(lv) - (float(dv) if _num(dv) else 0.0)
                else:                                     # local midnight -> reset today to 0
                    _daily["base"][tkey] = float(lv)
        for tkey, (life, dayfld) in _LIFE.items():
            dv = data.get(dayfld)
            if _num(dv):                                  # OLD METRIC: inverter's own daily
                data[tkey] = round(max(0.0, float(dv)), 3) # counter (matches the GoodWe portal)
                continue
            cur = data.get(life)                          # fallback: derive from lifetime counter
            if not _num(cur):                             # (only for inverters with no day field)
                continue
            cur = float(cur)
            base = _daily["base"].get(tkey)
            if base is None:                              # appeared mid-day: start from now
                _daily["base"][tkey] = base = cur
            if cur < base:                                # counter rolled over / was cleared
                _daily["base"][tkey] = base = cur
            data[tkey] = round(max(0.0, cur - base), 3)
        # Grid import/export today: integrate the measured grid power (active_power,
        # + = importing / - = exporting) rather than trusting the inverter's daily
        # grid-energy register. No real grid flow -> stays at 0. Note: this counts
        # from when the monitor started today; a mid-day restart resets it.
        gw = data.get("active_power")
        ga = _daily["grid"]
        tnow = time.monotonic()
        if _num(gw) and ga["last"] is not None:
            dt = tnow - ga["last"]
            if 0 < dt <= 600:                             # ignore long stalls / clock jumps
                if gw > 0:
                    ga["imp"] += gw * dt / 3600.0
                elif gw < 0:
                    ga["exp"] += (-gw) * dt / 3600.0
        ga["last"] = tnow
        data["imp_today"] = round(ga["imp"] / 1000.0, 3)
        data["exp_today"] = round(ga["exp"] / 1000.0, 3)
        if "cons_today" not in data or not _num(data.get("cons_today")):
            g, im, ex = data.get("gen_today"), data.get("imp_today"), data.get("exp_today")
            if all(_num(x) for x in (g, im, ex)):
                data["cons_today"] = round(max(0.0, g + im - ex), 3)
        return data

    def beat(success, connected):
        h = {"last_attempt": time.time(), "errors": errors[0], "connected": connected}
        if success:
            h["last_success"] = time.time()
        else:
            prev = None
            try:
                prev = health.get(inv_id)
            except Exception:
                prev = None
            h["last_success"] = (prev or {}).get("last_success")
        try:
            health[inv_id] = h
        except Exception:
            pass

    def emit(data, status, reason, model, serial, units, meta=None):
        daily(data)
        if isinstance(units, dict):
            for _k in ("gen_today", "imp_today", "exp_today", "cons_today"):
                if _k in data:
                    units.setdefault(_k, "kWh")
        meta = meta or {}
        snap = {"ok": True, "error": None, "inverter_id": inv_id, "inverter_nickname": nick,
                "inverter_brand": brand, "inverter_ip": ("demo" if demo else ip),
                "model": model, "serial": serial, "data": data, "units": units,
                "firmware": meta.get("firmware"), "rated_power": meta.get("rated_power"),
                "battery_capacity_kwh": batt_cap, "panel_capacity_w": panel_cap,
                "grid_status": status, "grid_status_src": reason, "demo": demo,
                "poll_interval_seconds": poll, "log_interval_seconds": logiv,
                "ts": time.time(),
                "time": datetime.now().isoformat(timespec="seconds")}
        try:
            live[inv_id] = snap
        except Exception:
            pass
        beat(success=True, connected=True)
        now = time.monotonic()
        if now - last_log[0] >= logiv:
            last_log[0] = now
            try:
                data_store.insert_reading(data, status, inv_id)
            except Exception as e:                       # noqa: BLE001
                try:
                    logsetup.system_error(f"[worker {nick}] reading log failed: {e}")
                except Exception:
                    pass

    if demo:
        soc = 58.0
        tick = 0
        while not stop.is_set():
            tick += 1
            off = ((tick + len(inv_id)) % 41 == 0)
            data = _demo_data(soc, off)
            soc = max(12.0, min(98.0, soc + (0.4 if (tick // 20) % 2 == 0 else -0.4)))
            status, reason = (("off", "demo: off-grid") if off else ("on", "demo: on-grid"))
            emit(data, status, reason, f"DEMO-{brand.upper()}",
                 f"DEMOSN{inv_id[:4].upper()}",
                 {"ppv": "W", "house_consumption": "W", "pbattery1": "W", "battery_soc": "%"},
                 {"firmware": "DEMO-1.0", "rated_power": 6000})
            stop.wait(poll)
        return

    import asyncio
    driver = lib.get_driver(brand)
    while not stop.is_set():
        loop = asyncio.new_event_loop()
        try:
            inverter = loop.run_until_complete(driver.connect(ip, retries=3))
            model = getattr(inverter, "model_name", "") or ""
            serial = getattr(inverter, "serial_number", "") or params.get("serial", "")
            units = {s.id_: (s.unit or "") for s in inverter.sensors()}
            _fw = (getattr(inverter, "firmware", None) or getattr(inverter, "arm_firmware", None)
                   or getattr(inverter, "arm_version", None))
            _rp = getattr(inverter, "rated_power", None)
            meta = {"firmware": (str(_fw) if _fw else None),
                    "rated_power": (_rp if isinstance(_rp, (int, float)) else None)}
            try:
                logsetup.system(f"[worker {nick}] connected to {model or brand} at {ip}")
            except Exception:
                pass
            while not stop.is_set():
                raw = loop.run_until_complete(inverter.read_runtime_data())
                data = {k: _coerce(v) for k, v in raw.items()}
                status, reason = compute_grid_status(data)
                emit(data, status, reason, model, serial, units, meta)
                stop.wait(poll)
        except Exception as e:                           # noqa: BLE001
            errors[0] += 1
            beat(success=False, connected=False)
            try:
                logsetup.system_error(f"[worker {nick}] connect/read failed: {e}")
            except Exception:
                pass
            stop.wait(5)
        finally:
            try:
                loop.close()
            except Exception:
                pass


def _start(inv, demo, poll, logiv, tz):
    _ensure()
    stop_evt = mp.Event()
    params = {"id": inv["id"], "brand": inv.get("brand") or "goodwe",
              "ip": inv.get("ip_address") or "", "serial": inv.get("serial") or "",
              "nickname": inv.get("nickname") or inv["id"],
              "battery_capacity_kwh": inv.get("battery_capacity_kwh") or 0,
              "panel_capacity_w": inv.get("panel_capacity_w") or 0,
              "demo": demo, "poll": poll, "log": logiv, "tz": tz}
    p = mp.Process(target=_run, args=(params, _live, _health, stop_evt), daemon=True,
                   name="inv-" + str(inv["id"])[:8])
    p.start()
    _procs[inv["id"]] = {"proc": p, "stop": stop_evt, "sig": _sig(inv)}


def stop(inverter_id):
    entry = _procs.pop(inverter_id, None)
    if not entry:
        return
    try:
        entry["stop"].set()
    except Exception:
        pass
    try:
        entry["proc"].join(timeout=3)
    except Exception:
        pass
    try:
        if entry["proc"].is_alive():
            entry["proc"].terminate()
    except Exception:
        pass
    try:
        if _live is not None:
            _live.pop(inverter_id, None)
    except Exception:
        pass
    try:
        if _health is not None:
            _health.pop(inverter_id, None)
    except Exception:
        pass


def stop_all():
    for inv_id in list(_procs):
        stop(inv_id)


def sync(inverters, demo, poll, logiv, tz):
    """Ensure exactly one live worker per enabled inverter."""
    _ensure()
    wanted = {inv["id"]: inv for inv in inverters
              if inv.get("is_enabled") and (demo or inv.get("ip_address"))}
    if demo and not wanted:                              # demo with no inverters yet
        synthetic = {"id": "default", "brand": "goodwe", "nickname": "Demo Inverter",
                     "ip_address": "demo", "is_enabled": True}
        wanted = {"default": synthetic}
    for inv_id in list(_procs):
        gone = inv_id not in wanted
        changed = (not gone) and _procs[inv_id]["sig"] != _sig(wanted[inv_id])
        dead = not _procs[inv_id]["proc"].is_alive()
        if gone or changed or dead:
            stop(inv_id)
    for inv_id, inv in wanted.items():
        if inv_id not in _procs:
            _start(inv, demo, poll, logiv, tz)


def count_alive():
    return sum(1 for e in _procs.values() if e["proc"].is_alive())
