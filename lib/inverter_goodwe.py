#!/usr/bin/env python3
"""
inverter.py - Read live data from a GoodWe inverter over the LOCAL network.

Talks straight to the inverter's WiFi/LAN module (UDP, port 8899). No cloud,
no SEMS portal, no internet required - just run it on a device on the same
network as the inverter. Works on Windows, macOS and Linux.

Examples:
    python inverter.py --selftest                 # offline check it runs (no inverter needed)
    python inverter.py --discover                 # find the inverter's IP
    python inverter.py --ip 192.168.1.50          # one snapshot
    python inverter.py --ip 192.168.1.50 --all    # snapshot, every sensor
    python inverter.py --ip 192.168.1.50 --watch  # live, refreshes in place
    python inverter.py --ip 192.168.1.50 --log solar.csv --interval 60
    python inverter.py --ip 192.168.1.50 --benchmark 20   # time 20 reads

Needs (for live reads): pip install goodwe
"""

import argparse
import asyncio
import csv
import os
import socket
import sys
import time
from datetime import datetime


# The handful of readings that matter most for a solar + battery setup.
# The script only prints the ones your specific inverter actually reports,
# so this same list works across ET / EH / ES / DT / MS and other families.
SUMMARY = [
    ("ppv",                      "Solar power (PV)"),
    ("pv1_power",                "  - PV string 1"),
    ("pv2_power",                "  - PV string 2"),
    ("house_consumption",        "House load"),
    ("grid_mode_label",          "Grid status"),
    ("active_power",             "Grid power (+in / -out)"),
    ("meter_active_power_total", "Grid power (meter)"),
    ("pbattery1",                "Battery power"),
    ("battery_mode_label",       "Battery mode"),
    ("battery_soc",              "Battery charge"),
    ("e_day",                    "Solar generated today"),
    ("e_load_day",               "Load consumed today"),
    ("e_day_exp",                "Grid exported today"),
    ("e_day_imp",                "Grid imported today"),
    ("e_bat_charge_day",         "Battery charged today"),
    ("e_bat_discharge_day",      "Battery discharged today"),
    ("e_total",                  "Solar generated (lifetime)"),
    ("temperature",              "Inverter temperature"),
]


def _import_goodwe():
    """Import the goodwe library lazily, with a friendly message if missing.
    Keeping it lazy means --selftest works before the library is installed."""
    try:
        import goodwe
        return goodwe
    except ImportError:
        sys.exit("The 'goodwe' library isn't installed. Run:  pip install goodwe")


def use_windows_friendly_loop():
    """On Windows, the Selector event loop is the reliable choice for the UDP
    traffic this tool uses and lets Ctrl+C stop it cleanly."""
    if sys.platform.startswith("win"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except AttributeError:
            pass  # very old Python; default loop will still try


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def units(inverter):
    """Map sensor id -> unit string, e.g. {'ppv': 'W', 'battery_soc': '%'}."""
    return {s.id_: (s.unit or "") for s in inverter.sensors()}


async def read_once(ip, retries):
    """Connect and pull one full set of runtime readings."""
    goodwe = _import_goodwe()
    inverter = await goodwe.connect(host=ip, retries=retries)
    data = await inverter.read_runtime_data()
    return inverter, data


def print_summary(inverter, data, unit_map):
    print(f"\n{inverter.model_name}   SN: {inverter.serial_number}")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("  " + "-" * 46)
    for sid, label in SUMMARY:
        if sid in data:
            print(f"  {label:30} {str(data[sid]):>10} {unit_map.get(sid, '')}")
    print()


def print_all(inverter, data):
    print(f"\n{inverter.model_name}   SN: {inverter.serial_number}")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("  " + "-" * 60)
    for s in inverter.sensors():
        if s.id_ in data:
            print(f"  {s.name:34} {str(data[s.id_]):>12} {s.unit or ''}    [{s.id_}]")
    print()


async def snapshot(args):
    inverter, data = await read_once(args.ip, args.retries)
    if args.all:
        print_all(inverter, data)
    else:
        print_summary(inverter, data, units(inverter))


async def watch(args):
    inverter, data = await read_once(args.ip, args.retries)
    unit_map = units(inverter)
    while True:
        clear_screen()
        if args.all:
            print_all(inverter, data)
        else:
            print_summary(inverter, data, unit_map)
        print(f"  refreshing every {args.interval}s - Ctrl+C to stop")
        await asyncio.sleep(args.interval)
        try:
            data = await inverter.read_runtime_data()
        except Exception as e:
            print(f"  (read failed, retrying: {e})")
            await asyncio.sleep(args.interval)


async def log_to_csv(args):
    inverter, data = await read_once(args.ip, args.retries)
    unit_map = units(inverter)

    # Columns = timestamp + whichever summary fields this inverter reports.
    cols = ["timestamp"] + [sid for sid, _ in SUMMARY if sid in data]

    # Reuse an existing file's header so a restart doesn't break the layout.
    new_file = not os.path.exists(args.log) or os.path.getsize(args.log) == 0
    if not new_file:
        with open(args.log, newline="") as f:
            existing = next(csv.reader(f), None)
        if existing:
            cols = existing

    print(f"Logging {len(cols) - 1} fields to {args.log} every {args.interval}s. "
          f"Ctrl+C to stop.")

    with open(args.log, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if new_file:
            writer.writeheader()
            f.flush()
        while True:
            row = {"timestamp": datetime.now().isoformat(timespec="seconds")}
            row.update({k: data.get(k, "") for k in cols if k != "timestamp"})
            writer.writerow(row)
            f.flush()
            print(f"  {row['timestamp']}  PV={data.get('ppv', '?')}"
                  f"{unit_map.get('ppv', '')}  SOC={data.get('battery_soc', '?')}%")
            await asyncio.sleep(args.interval)
            try:
                data = await inverter.read_runtime_data()
            except Exception as e:
                print(f"  (read failed, skipping this interval: {e})")


async def benchmark(args):
    """Time N reads against the real inverter and report latency + success rate."""
    goodwe = _import_goodwe()
    n = args.benchmark
    print(f"Connecting to {args.ip} ...")
    t0 = time.perf_counter()
    inverter = await goodwe.connect(host=args.ip, retries=args.retries)
    connect_ms = (time.perf_counter() - t0) * 1000
    print(f"Connected in {connect_ms:.0f} ms. Running {n} reads...\n")

    times, fails = [], 0
    for i in range(1, n + 1):
        start = time.perf_counter()
        try:
            await inverter.read_runtime_data()
            ms = (time.perf_counter() - start) * 1000
            times.append(ms)
            print(f"  read {i:>3}/{n}   {ms:7.1f} ms")
        except Exception as e:
            fails += 1
            print(f"  read {i:>3}/{n}   FAILED  ({e})")
        await asyncio.sleep(max(args.interval, 0))

    print("\n  " + "-" * 34)
    if times:
        print(f"  ok        {len(times)}/{n}")
        print(f"  min       {min(times):7.1f} ms")
        print(f"  avg       {sum(times) / len(times):7.1f} ms")
        print(f"  max       {max(times):7.1f} ms")
    if fails:
        print(f"  failed    {fails}/{n}")


def discover(timeout=3):
    """
    Broadcast probe that most GoodWe/Solarman WiFi kits answer to.
    Returns {ip: raw_response}. If it finds nothing, use your router's
    DHCP / connected-devices list instead.
    """
    probe = b"WIFIKIT-214028-READ"
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(timeout)
    try:
        s.sendto(probe, ("255.255.255.255", 48899))
        while True:
            try:
                raw, addr = s.recvfrom(1024)
            except socket.timeout:
                break
            text = raw.decode(errors="ignore").strip()
            if text and text != probe.decode():
                found[addr[0]] = text
    finally:
        s.close()
    return found


# --------------------------------------------------------------------------- #
#  Offline self-test: proves the script runs on this machine without an
#  inverter or the goodwe library. Uses fake data through the real code paths.
# --------------------------------------------------------------------------- #
class _FakeSensor:
    def __init__(self, id_, name, unit):
        self.id_, self.name, self.unit = id_, name, unit


class _FakeInverter:
    model_name = "GW-DEMO (self-test)"
    serial_number = "0000DEMO0000"

    def sensors(self):
        return [_FakeSensor(sid, label.strip(" -"), "W") for sid, label in SUMMARY]


def selftest():
    print("Running offline self-test (no inverter, no network needed)...\n")
    ok = True

    fake = _FakeInverter()
    data = {
        "ppv": 3120, "pv1_power": 1600, "pv2_power": 1520,
        "house_consumption": 850, "active_power": -2270, "pbattery1": 0,
        "battery_soc": 87, "e_day": 14.6, "e_load_day": 6.2,
        "meter_e_total_imp": 412.3, "meter_e_total_exp": 1880.7,
        "e_total": 5230.1, "temperature": 38.4,
    }
    unit_map = {sid: ("%" if sid == "battery_soc" else
                      "kWh" if sid.startswith("e_") or "e_total" in sid else
                      "C" if sid == "temperature" else "W")
                for sid, _ in SUMMARY}

    try:
        print_summary(fake, data, unit_map)
        print_all(fake, data)
    except Exception as e:
        ok = False
        print(f"  [FAIL] printing: {e}")

    # Exercise the CSV path end to end.
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "goodwe_selftest.csv")
    try:
        cols = ["timestamp"] + [sid for sid, _ in SUMMARY if sid in data]
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            row = {"timestamp": datetime.now().isoformat(timespec="seconds")}
            row.update({k: data.get(k, "") for k in cols if k != "timestamp"})
            w.writerow(row)
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 2 and rows[0][0] == "timestamp"
        print(f"  [OK]  CSV write/read works  ->  {path}")
    except Exception as e:
        ok = False
        print(f"  [FAIL] CSV: {e}")

    # Confirm the library is importable (warn only - not needed for the test).
    try:
        import goodwe  # noqa: F401
        print("  [OK]  'goodwe' library is installed")
    except ImportError:
        print("  [..]  'goodwe' not installed yet - run: pip install goodwe")

    print(f"\n  Python {sys.version.split()[0]} on {sys.platform}")
    print("  RESULT:", "PASS - the script runs on this machine." if ok else "FAIL")
    return 0 if ok else 1


def parse_args():
    p = argparse.ArgumentParser(description="Read a GoodWe inverter over the local network.")
    p.add_argument("--ip", help="Inverter WiFi/LAN module IP, e.g. 192.168.1.50")
    p.add_argument("--discover", action="store_true", help="Try to find the inverter on the LAN")
    p.add_argument("--selftest", action="store_true", help="Offline check that the script runs here")
    p.add_argument("--benchmark", type=int, metavar="N", help="Time N live reads and report latency")
    p.add_argument("--all", action="store_true", help="Show every sensor, not just the summary")
    p.add_argument("--watch", action="store_true", help="Live view, refreshes in place")
    p.add_argument("--log", metavar="FILE", help="Append readings to a CSV file")
    p.add_argument("--interval", type=int, default=5, help="Seconds between reads. Default 5")
    p.add_argument("--retries", type=int, default=3, help="Connection retries. Default 3")
    return p.parse_args()


def main():
    args = parse_args()

    if args.selftest:
        sys.exit(selftest())

    if args.discover:
        print("Scanning the local network...")
        hits = discover()
        if hits:
            for ip, info in hits.items():
                print(f"  Found: {ip}   {info}")
            print("\nUse one of these:  python inverter.py --ip <IP>")
        else:
            print("Nothing answered the broadcast. Check your router's list of\n"
                  "connected devices for the inverter / 'Solar-WiFi' module instead.")
        return

    if not args.ip:
        sys.exit("Need --ip <address>  (or run --discover, or --selftest).")

    use_windows_friendly_loop()

    try:
        if args.benchmark:
            asyncio.run(benchmark(args))
        elif args.log:
            asyncio.run(log_to_csv(args))
        elif args.watch:
            asyncio.run(watch(args))
        else:
            asyncio.run(snapshot(args))
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        sys.exit(f"\nCouldn't reach the inverter at {args.ip}: {e}\n"
                 "- Same network as the inverter?\n"
                 "- Right IP? (try --discover)\n"
                 "- Port 8899 reachable / not blocked by a firewall?")


if __name__ == "__main__":
    main()


# --- brand driver interface (used by the collector / poller) -----------------
BRAND = "goodwe"


async def connect(ip, retries=3):
    """Connect to a GoodWe inverter and return the live object.

    Transport is auto-detected: a quick Modbus-TCP :502 probe first (newer
    dongles, the kind SEMS+ talks to — fast TCP refusal if absent), then the
    classic UDP :8899 protocol. Force one with SM_GOODWE_TRANSPORT=modbus|udp."""
    transport = (os.environ.get("SM_GOODWE_TRANSPORT") or "auto").strip().lower()
    if transport == "udp":
        return await _connect_udp(ip, retries)
    if transport == "modbus":
        inv = await _connect_modbus_tcp(ip)
        if inv is not None:
            return inv
        raise ConnectionError("Modbus TCP :%d did not respond at %s" % (_MB_PORT, ip))
    # auto
    try:
        inv = await _connect_modbus_tcp(ip)
        if inv is not None:
            return inv
    except Exception:                                     # noqa: BLE001
        pass
    return await _connect_udp(ip, retries)


# --------------------------------------------------------------------------- #
#  Modbus-TCP path (newer GoodWe dongles, port 502)
# --------------------------------------------------------------------------- #
_MB_PORT = int(os.environ.get("SM_GOODWE_MODBUS_PORT", "502") or 502)
_MB_UNIT = int(os.environ.get("SM_GOODWE_MODBUS_UNIT", "247") or 247)   # 0xF7


def _mb_read(ip, port, unit, start, qty, timeout=3):
    """One Modbus-TCP read (function 3). Returns the data bytes or None."""
    import socket
    import struct
    pdu = struct.pack(">BHH", 0x03, start, qty)
    adu = struct.pack(">HHHB", 1, 0, len(pdu) + 1, unit) + pdu
    s = socket.create_connection((ip, port), timeout=timeout)
    try:
        s.sendall(adu)
        buf = b""
        while len(buf) < 7:
            chunk = s.recv(1024)
            if not chunk:
                break
            buf += chunk
        if len(buf) < 7:
            return None
        total = 6 + struct.unpack(">H", buf[4:6])[0]
        while len(buf) < total:
            chunk = s.recv(1024)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    if len(buf) < 9 or (buf[7] & 0x80):
        return None
    n = buf[8]
    return buf[9:9 + n]


def _u16(b, o): return int.from_bytes(b[o:o + 2], "big") if len(b) >= o + 2 else 0
def _s16(b, o): return int.from_bytes(b[o:o + 2], "big", signed=True) if len(b) >= o + 2 else 0
def _u32(b, o): return int.from_bytes(b[o:o + 4], "big") if len(b) >= o + 4 else 0
def _s32(b, o): return int.from_bytes(b[o:o + 4], "big", signed=True) if len(b) >= o + 4 else 0
def _ascii(b, o, n): return b[o:o + n].decode("ascii", "replace").replace("\x00", "").strip() if len(b) >= o + n else ""


def _mb_runtime(ip, port, unit):
    """Read GoodWe ET-family runtime registers over Modbus TCP and normalize to
    the project's data keys. PV power and SoC are high-confidence; grid/battery
    are best-effort ET offsets (verify against SEMS+ with `cli.py tcp --debug`)."""
    blk = _mb_read(ip, port, unit, 0x891C, 0x7D, timeout=3)   # running data (250 bytes)
    data = {}
    if blk:
        ppv1 = _u32(blk, 10)
        ppv2 = _u32(blk, 18)
        data["ppv"] = ppv1 + ppv2
        # best-effort ET offsets — confirm with --debug + SEMS+:
        data["active_power"] = _s32(blk, 80)             # grid power (+import / -export)
        try:
            data["temperature"] = _s16(blk, 96) / 10.0
        except Exception:                                # noqa: BLE001
            pass
        vbat = _u16(blk, 160) / 10.0
        ibat = _s16(blk, 162) / 10.0
        data["pbattery1"] = round(vbat * ibat)           # + charge / - discharge (verify)
    soc = _mb_read(ip, port, unit, 0x908F, 1, timeout=3)     # battery SoC (37007)
    if soc:
        data["battery_soc"] = _u16(soc, 0)
    # House load via the energy balance (avoids an uncertain register):
    if "ppv" in data and "active_power" in data:
        data["house_consumption"] = max(
            0, data["ppv"] + data["active_power"] - data.get("pbattery1", 0))
    return data


class _ModbusInverter:
    """Adapts the Modbus-TCP reader to the same interface the worker expects."""
    def __init__(self, ip, port, unit, model, serial):
        self.ip = ip
        self.port = port
        self.unit = unit
        self.model_name = model or "GoodWe (Modbus TCP)"
        self.serial_number = serial or ""

    def sensors(self):
        return [_FakeSensor(sid, label.strip(" -"), "W") for sid, label in SUMMARY]

    async def read_runtime_data(self):
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _mb_runtime, self.ip, self.port, self.unit)


async def _connect_modbus_tcp(ip):
    """Probe Modbus TCP :502 and, if it answers, return a Modbus inverter object.
    Confirms the link by decoding the device-info block (model + serial)."""
    import asyncio
    loop = asyncio.get_event_loop()

    def _probe():
        di = _mb_read(ip, _MB_PORT, _MB_UNIT, 0x88B8, 0x21, timeout=3)   # device info
        if not di:
            return None
        serial = _ascii(di, 6, 16)
        model = _ascii(di, 22, 10)
        return _ModbusInverter(ip, _MB_PORT, _MB_UNIT, model, serial)

    try:
        inv = await loop.run_in_executor(None, _probe)
    except Exception:                                    # noqa: BLE001
        return None
    return inv


async def _connect_udp(ip, retries=3):
    import goodwe
    try:
        timeout = float(os.environ.get("SM_GOODWE_TIMEOUT", "2") or 2)
    except ValueError:
        timeout = 2.0
    hint = (os.environ.get("SM_GOODWE_FAMILY") or "").strip().upper()

    order = []
    if hint:
        order.append(hint)
    order.append(None)                                    # auto-discovery
    for fam in ("ES", "ET", "EH", "DT", "BP", "EM"):      # ES first: GW####ES hybrids
        if fam != hint:
            order.append(fam)

    last = None
    for fam in order:
        try:
            if fam is None:
                return await goodwe.connect(host=ip, timeout=timeout, retries=retries)
            # explicit family skips discovery; keep the sweep quick
            r = retries if fam == hint else 1
            return await goodwe.connect(host=ip, family=fam, timeout=timeout, retries=r)
        except TypeError:
            # very old goodwe without family/timeout kwargs
            try:
                return await goodwe.connect(host=ip, retries=retries)
            except Exception as e:                        # noqa: BLE001
                last = e
        except Exception as e:                            # noqa: BLE001
            last = e
    raise ConnectionError(
        "could not connect to GoodWe at %s on UDP 8899 — tried auto-detect and "
        "families ES/ET/EH/DT/BP/EM (last error: %s). If it stays unreachable, "
        "set SM_GOODWE_FAMILY (e.g. ES) in your .env." % (ip, last))
