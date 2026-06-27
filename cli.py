#!/usr/bin/env python3
"""
Solar Monitor — standalone inverter CLI
=======================================

Talk to a single GoodWe inverter directly, with no Docker, DB or web stack in
the way. Great for isolating "unable to connect": if this works on the host but
the container doesn't, the problem is Docker networking, not the inverter.

Setup (host, once):
    python3 -m venv .venv
    .venv/bin/pip install goodwe

Usage:
    .venv/bin/python cli.py read 192.168.10.51
    .venv/bin/python cli.py read 192.168.10.51 --family ES
    .venv/bin/python cli.py families 192.168.10.51      # try every family
    .venv/bin/python cli.py probe 192.168.10.51         # raw UDP, no goodwe
    .venv/bin/python cli.py discover                    # broadcast discovery

Inside the container it also works:
    docker compose exec app python cli.py read 192.168.10.51 --family ES

Options: --family ES|ET|EH|DT|BP|EM  --port 8899  --timeout 2  --retries 3  --json
Env fallbacks: SM_GOODWE_FAMILY, SM_GOODWE_TIMEOUT
"""
import argparse
import asyncio
import json as _json
import os
import socket
import sys

FAMILIES = ["ES", "ET", "EH", "DT", "BP", "EM"]


def _p(msg=""):
    print(msg, flush=True)


def _import_goodwe():
    try:
        import goodwe  # noqa: F401
        return goodwe
    except Exception:
        _p("! The 'goodwe' package isn't installed in this Python.")
        _p("  Install it first:  .venv/bin/pip install goodwe")
        sys.exit(3)


async def _connect_family(goodwe, ip, family, port, timeout, retries):
    """Connect using one explicit family (or None for auto-discovery)."""
    if family is None:
        return await goodwe.connect(host=ip, port=port, timeout=timeout, retries=retries)
    try:
        return await goodwe.connect(host=ip, port=port, family=family,
                                    timeout=timeout, retries=retries)
    except TypeError:
        # older goodwe without keyword args
        return await goodwe.connect(ip, port, family, None, timeout, retries)


async def _robust_connect(goodwe, ip, hint, port, timeout, retries, debug=False):
    """Hint -> auto -> sweep ES/ET/EH/DT/BP/EM. Returns (inverter, family_used)."""
    order = []
    if hint:
        order.append(hint)
    order.append(None)
    for f in FAMILIES:
        if f != hint:
            order.append(f)
    last = None
    for fam in order:
        label = fam or "auto-detect"
        try:
            r = retries if (fam == hint or fam is None) else 1
            inv = await _connect_family(goodwe, ip, fam, port, timeout, r)
            return inv, label
        except Exception as e:  # noqa: BLE001
            last = e
            detail = ("%s: %s" % (e.__class__.__name__, e)) if debug else _short(e)
            _p("  - %-11s failed: %s" % (label, detail))
    raise last or ConnectionError("no family worked")


def _short(e):
    s = str(e) or e.__class__.__name__
    return s if len(s) <= 90 else s[:87] + "..."


def _getattr(inv, *names):
    for n in names:
        v = getattr(inv, n, None)
        if v:
            return v
    return None


async def cmd_read(args):
    goodwe = _import_goodwe()
    hint = (args.family or os.environ.get("SM_GOODWE_FAMILY") or "").strip().upper() or None
    _p("Connecting to %s:%d (timeout %ss, retries %d)%s ..."
       % (args.ip, args.port, args.timeout, args.retries,
          ", family=" + hint if hint else ""))
    try:
        inv, used = await _robust_connect(goodwe, args.ip, hint, args.port,
                                          args.timeout, args.retries, args.debug)
    except Exception as e:  # noqa: BLE001
        _p("\nFAILED to connect: %s" % _short(e))
        _p("\nNext steps:")
        _p("  * Run on the HOST (not the container). If it works here but not in")
        _p("    the container, it's Docker networking — see TROUBLESHOOT-inverter.md.")
        _p("  * Close the GoodWe SEMS app / any other poller (only ONE local")
        _p("    client is allowed at a time).")
        _p("  * Try a single family explicitly:  cli.py read %s --family ES" % args.ip)
        sys.exit(1)

    model = _getattr(inv, "model_name") or "?"
    serial = _getattr(inv, "serial_number") or "?"
    fw = _getattr(inv, "firmware", "software_version", "arm_version") or "?"
    rated = _getattr(inv, "rated_power") or "?"
    _p("\nCONNECTED  (family: %s)" % used)
    _p("  model    : %s" % model)
    _p("  serial   : %s" % serial)
    _p("  firmware : %s" % fw)
    _p("  rated    : %s W" % rated)

    try:
        data = await inv.read_runtime_data()
    except Exception as e:  # noqa: BLE001
        _p("\nConnected, but read_runtime_data() failed: %s" % _short(e))
        sys.exit(2)

    def g(k):
        return data.get(k)

    if args.json:
        out = {k: (str(v) if not isinstance(v, (int, float, str, type(None))) else v)
               for k, v in data.items()}
        out["_meta"] = {"model": model, "serial": serial, "firmware": str(fw), "family": used}
        _p(_json.dumps(out, indent=2, default=str))
        return

    _p("\nLive readings:")
    rows = [
        ("PV / solar", "ppv", "W"),
        ("House load", "house_consumption", "W"),
        ("Grid power (+imp/-exp)", "active_power", "W"),
        ("Battery power", "pbattery1", "W"),
        ("Battery SoC", "battery_soc", "%"),
        ("Today generation", "e_day", "kWh"),
        ("Today load", "e_load_day", "kWh"),
        ("Total generation", "e_total", "kWh"),
        ("Temperature", "temperature", "C"),
    ]
    for label, key, unit in rows:
        v = g(key)
        if v is not None:
            _p("  %-24s %s %s" % (label + ":", v, unit))
    if args.all:
        _p("\nAll sensors:")
        for k in sorted(data.keys()):
            _p("  %-26s %s" % (k, data[k]))
    _p("\nOK — this inverter is reachable and returning data from here.")


async def cmd_families(args):
    goodwe = _import_goodwe()
    _p("Trying each protocol family against %s:%d ...\n" % (args.ip, args.port))
    ok = []
    for fam in FAMILIES:
        try:
            inv = await _connect_family(goodwe, args.ip, fam, args.port, args.timeout, 1)
            model = _getattr(inv, "model_name") or "?"
            _p("  %-4s OK   -> %s" % (fam, model))
            ok.append(fam)
        except Exception as e:  # noqa: BLE001
            _p("  %-4s no   (%s)" % (fam, _short(e)))
    _p("")
    if ok:
        _p("Use:  cli.py read %s --family %s" % (args.ip, ok[0]))
        _p("And set in .env:  SM_GOODWE_FAMILY=%s" % ok[0])
    else:
        _p("No family connected — likely a network path issue (run `probe`), or")
        _p("another client holds the inverter's single local connection.")
        sys.exit(1)


def _aa55(cmd_bytes):
    """Build a GoodWe ES-protocol (AA55) request frame with checksum."""
    frame = bytes([0xAA, 0x55, 0xC0, 0x7F]) + bytes(cmd_bytes)
    chk = sum(frame) & 0xFFFF
    return frame + chk.to_bytes(2, "big")


def cmd_probe(args):
    """Raw UDP reachability — needs no goodwe package. Sends the actual ES (AA55)
    and ET (Modbus) request frames so an ES inverter like the GW####ES replies."""
    probes = [
        ("ES device-info (AA55)", _aa55([0x01, 0x02, 0x00])),
        ("ES running-data (AA55)", _aa55([0x01, 0x06, 0x00])),
        ("ET/EH discovery (Modbus)", b"\x7f\x03\x88\xb8\x00\x21\x3a\xc1"),
    ]
    _p("Raw UDP probe to %s:%d (from THIS host/container)." % (args.ip, args.port))
    _p("nc -u always says 'Connected' for UDP — ignore it; a real REPLY is what counts.\n")
    got = False
    for name, payload in probes:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(args.timeout if args.timeout > 1 else 3)
        try:
            s.sendto(payload, (args.ip, args.port))
            data, addr = s.recvfrom(2048)
            _p("  %-26s REPLY %d bytes from %s" % (name, len(data), addr))
            if args.debug:
                _p("        %s" % data[:32].hex())
            got = True
        except socket.timeout:
            _p("  %-26s no reply (timeout)" % name)
        except OSError as e:
            _p("  %-26s ERROR %s (no route)" % (name, e.__class__.__name__))
        finally:
            s.close()
    _p("")
    if got:
        _p("The inverter REPLIES on UDP 8899 — the network path is fine.")
        _p("If `read` still fails it's the protocol/family or a busy session; try:")
        _p("  cli.py read %s --family ES --debug" % args.ip)
    else:
        _p("NO replies to any protocol. Most likely one of:")
        _p("  * something else holds the inverter's single local session")
        _p("    -> stop the stack: `docker compose down`, and close the SEMS app")
        _p("  * a firewall on THIS machine drops the inbound UDP reply")
        _p("    -> test: `sudo systemctl stop firewalld` then re-run")
        _p("  * the module's local protocol is off, or the IP is wrong")
        sys.exit(1)


def cmd_wifi(args):
    """Find GoodWe WiFi/LAN modules via their UDP 48899 discovery service.
    This answers even when the 8899 data port is silent, and reveals the
    module's CURRENT IP — so it catches a DHCP address change (a stale IP is a
    common cause of total silence on 8899)."""
    targets = []
    if getattr(args, "ip", None):
        targets.append(args.ip)
    targets.append("255.255.255.255")          # LAN broadcast: find ALL modules
    _p("GoodWe module discovery on UDP 48899 (WIFIKIT-214028-READ) ...")
    _p("Listening for any module on the LAN — this finds the real current IP.\n")
    found = {}
    for tgt in targets:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(args.timeout if args.timeout > 1 else 3)
        try:
            s.sendto(b"WIFIKIT-214028-READ", (tgt, 48899))
            deadline_loops = 4
            while deadline_loops > 0:
                try:
                    data, addr = s.recvfrom(1024)
                    txt = data.decode("ascii", "replace").strip()
                    if addr[0] not in found:
                        found[addr[0]] = txt
                        _p("  FOUND %-15s -> %s" % (addr[0], txt))
                except socket.timeout:
                    break
                deadline_loops -= 1
        except OSError as e:
            _p("  (%s on %s: %s)" % (e.__class__.__name__, tgt, e))
        finally:
            s.close()
    _p("")
    if found:
        ips = ", ".join(found.keys())
        _p("Module(s) alive at: %s" % ips)
        _p("If that IP differs from the one you configured, the inverter's DHCP")
        _p("address changed — use the new IP (and set a DHCP reservation), e.g.:")
        first = next(iter(found))
        _p("  cli.py read %s --family ES" % first)
    else:
        _p("No module answered the 48899 discovery either. That usually means:")
        _p("  * you're on a different subnet/VLAN than the inverter (broadcast")
        _p("    doesn't cross subnets) — run this from a host on the inverter's LAN;")
        _p("  * the WiFi/LAN dongle is offline or in cloud-only mode — power-cycle")
        _p("    the inverter (and reseat the dongle); or")
        _p("  * the module is a newer type with no local protocol.")
        sys.exit(1)


def cmd_scan(args):
    """Find which local interface the dongle actually exposes. Newer GoodWe
    dongles (the ones SEMS+ talks to locally) often use Modbus TCP :502 or a
    TCP variant rather than the classic UDP :8899 the goodwe library expects."""
    ip = args.ip
    _p("Scanning %s for known GoodWe local interfaces ...\n" % ip)
    tcp_ports = [(502, "Modbus TCP (newer dongles)"),
                 (8899, "TCP 8899 (some dongles)"),
                 (80, "HTTP (dongle web UI)"),
                 (8000, "HTTP alt"),
                 (6800, "Solarman/IGEN"),
                 (1502, "Modbus TCP alt")]
    open_tcp = []
    for port, label in tcp_ports:
        try:
            c = socket.create_connection((ip, port), timeout=args.timeout if args.timeout > 1 else 2)
            c.close()
            _p("  TCP %-5d OPEN   %s" % (port, label))
            open_tcp.append(port)
        except Exception as e:  # noqa: BLE001
            _p("  TCP %-5d closed (%s)" % (port, e.__class__.__name__))

    # UDP can't be 'connected' meaningfully; we test for a reply.
    udp = [(8899, _aa55([0x01, 0x02, 0x00]), "UDP 8899 GoodWe data"),
           (48899, b"WIFIKIT-214028-READ", "UDP 48899 module discovery")]
    udp_reply = []
    for port, payload, label in udp:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(args.timeout if args.timeout > 1 else 2)
        try:
            s.sendto(payload, (ip, port))
            data, _ = s.recvfrom(1024)
            _p("  UDP %-5d REPLY  %s (%d bytes)" % (port, label, len(data)))
            udp_reply.append(port)
        except socket.timeout:
            _p("  UDP %-5d no reply  %s" % (port, label))
        except OSError as e:
            _p("  UDP %-5d error %s" % (port, e.__class__.__name__))
        finally:
            s.close()

    _p("")
    if 502 in open_tcp or 1502 in open_tcp:
        _p(">> Modbus TCP is open. Your dongle is a newer (Modbus-TCP) type — the")
        _p("   goodwe UDP path won't work. Confirm it:  cli.py modbus %s" % ip)
    elif 8899 in open_tcp and 8899 not in udp_reply:
        _p(">> TCP 8899 is open but UDP 8899 is silent — the dongle uses TCP, not UDP.")
    elif udp_reply:
        _p(">> Something answered UDP — the goodwe library should work; if not, it's")
        _p("   a busy session (close SEMS+) or family. Try: cli.py read %s --family ES" % ip)
    else:
        _p(">> Nothing answered on any known port. Likely a different subnet/VLAN,")
        _p("   or the dongle only exposes its interface to the SEMS+ app while paired.")
        _p("   Run `cli.py wifi` to confirm the IP, and check SEMS+ isn't holding it.")


def _modbus_read(ip, port, unit, start, qty, timeout):
    """Minimal Modbus-TCP read (function 3). Returns ('ok',bytes)|('exc',code)|None.
    Reads the full frame using the MBAP length field (handles big blocks)."""
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
    if len(buf) < 9:
        return None
    func = buf[7]
    if func & 0x80:
        return ("exc", buf[8])
    n = buf[8]
    return ("ok", buf[9:9 + n])


def _u16(b, o):
    return int.from_bytes(b[o:o + 2], "big")


def _s16(b, o):
    return int.from_bytes(b[o:o + 2], "big", signed=True)


def _u32(b, o):
    return int.from_bytes(b[o:o + 4], "big")


def _ascii(b, o, n):
    return b[o:o + n].decode("ascii", "replace").replace("\x00", "").strip()


def _decode_device_info(d):
    """GoodWe ET Modbus device-info block (read 0x88B8, 0x21 regs)."""
    if len(d) < 32:
        return {}
    return {
        "modbus_version": _u16(d, 0),
        "rated_power": _u16(d, 2),
        "serial_number": _ascii(d, 6, 16),
        "model_name": _ascii(d, 22, 10),
    }


def cmd_tcp(args):
    """Read a newer (Modbus-TCP) GoodWe inverter on port 502 — the kind SEMS+
    talks to locally. Decodes device info (verifiable against your serial) plus
    a core set of live values."""
    ip = args.ip
    port = args.port if args.port != 8899 else 502
    unit = 0xF7
    t = args.timeout if args.timeout > 1 else 3
    _p("Reading GoodWe over Modbus TCP %s:%d (unit 0x%02X) ...\n" % (ip, port, unit))

    di = _modbus_read(ip, port, unit, 0x88B8, 0x21, t)
    if not di or di[0] != "ok":
        _p("Could not read device-info block (got %s). Is :502 reachable?" % (di,))
        sys.exit(1)
    info = _decode_device_info(di[1])
    _p("Device info:")
    _p("  model        : %s" % info.get("model_name", "?"))
    _p("  serial       : %s" % info.get("serial_number", "?"))
    _p("  rated power  : %s W" % info.get("rated_power", "?"))
    _p("  ^ if the serial matches your inverter, Modbus decoding is CONFIRMED.\n")

    rd = _modbus_read(ip, port, unit, 0x891C, 0x7D, t)
    if not rd or rd[0] != "ok":
        _p("Could not read running-data block (%s)." % (rd,))
        sys.exit(1)
    d = rd[1]

    def off(reg):
        return (reg - 0x891C) * 2

    _p("Live (GoodWe ET map — values marked * are high-confidence):")
    try:
        ppv1 = _u32(d, off(0x8921)); ppv2 = _u32(d, off(0x8925))
        _p("  PV power*     : %d W  (PV1 %d + PV2 %d)" % (ppv1 + ppv2, ppv1, ppv2))
    except Exception:
        pass
    try:
        _p("  Inverter temp : %.1f C   (verify)" % (_s16(d, off(0x8956)) / 10.0))
    except Exception:
        pass
    soc = _modbus_read(ip, port, unit, 0x908F, 1, t)
    if soc and soc[0] == "ok":
        _p("  Battery SoC   : %d %%   (verify)" % _u16(soc[1], 0))

    if args.debug:
        _p("\nRaw running block (0x891C, 125 regs) for mapping:")
        hexs = d.hex()
        for i in range(0, len(hexs), 64):
            _p("  %04X: %s" % (0x891C + i // 4, hexs[i:i + 64]))

    _p("\nThe serial check above tells us decoding works. Send this output (add")
    _p("--debug) plus your SEMS+ live numbers and I'll lock the full register map")
    _p("and wire a Modbus-TCP driver into the monitor so the dashboard reads it.")


def cmd_modbus(args):
    """Probe Modbus TCP (port 502). Any valid Modbus reply — even an exception —
    proves the dongle speaks Modbus TCP, which is how to read newer GoodWe units."""
    ip = args.ip
    port = args.port if args.port not in (8899,) else 502
    _p("Modbus TCP probe to %s:%d ...\n" % (ip, port))
    works = False
    # GoodWe ET/EH unit id is usually 0xF7 (247); newer setups sometimes use 1.
    for unit in (0xF7, 1, 2, 247):
        for start in (0x88B8, 0x891C, 0x0000):   # device-info / running-data areas
            try:
                r = _modbus_read(ip, port, unit, start, 1, args.timeout if args.timeout > 1 else 3)
            except Exception as e:  # noqa: BLE001
                _p("  unit %3d @ 0x%04X -> %s" % (unit, start, e.__class__.__name__))
                continue
            if r is None:
                _p("  unit %3d @ 0x%04X -> no/short response" % (unit, start))
            elif r[0] == "exc":
                _p("  unit %3d @ 0x%04X -> Modbus EXCEPTION %d (device IS speaking Modbus!)" % (unit, start, r[1]))
                works = True
            else:
                _p("  unit %3d @ 0x%04X -> OK, data: %s" % (unit, start, r[1].hex()))
                works = True
    _p("")
    if works:
        _p(">> This inverter speaks Modbus TCP on :%d. The classic goodwe UDP path" % port)
        _p("   can't read it. Next step: I can add a Modbus-TCP reader to the monitor")
        _p("   (and to this CLI) so it polls your dongle the same way SEMS+ does.")
    else:
        _p(">> No Modbus TCP response. If `scan` showed :502 open it may need a")
        _p("   specific unit id/register — tell me what SEMS+ shows and I'll match it.")


async def cmd_discover(args):
    goodwe = _import_goodwe()
    _p("Running GoodWe discovery (broadcast) ...")
    try:
        if args.ip:
            inv = await goodwe.connect(host=args.ip, timeout=args.timeout, retries=args.retries)
            _p("Found at %s: %s" % (args.ip, _getattr(inv, "model_name") or "?"))
        else:
            res = await goodwe.search_inverters()  # may not exist in all versions
            _p(res)
    except AttributeError:
        _p("This goodwe version has no broadcast search; give an IP:")
        _p("  cli.py discover --ip 192.168.10.51")
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        _p("Discovery failed: %s" % _short(e))
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(prog="cli.py", description="Talk to one GoodWe inverter directly.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, ip_required=True):
        if ip_required:
            p.add_argument("ip", help="inverter IP, e.g. 192.168.10.51")
        p.add_argument("--port", type=int, default=8899)
        p.add_argument("--timeout", type=float, default=float(os.environ.get("SM_GOODWE_TIMEOUT", "2") or 2))
        p.add_argument("--retries", type=int, default=3)
        p.add_argument("--debug", action="store_true", help="show full errors / raw bytes")

    pr = sub.add_parser("read", help="connect and print live readings")
    common(pr)
    pr.add_argument("--family", help="ES|ET|EH|DT|BP|EM (skip auto-detect)")
    pr.add_argument("--all", action="store_true", help="dump every sensor")
    pr.add_argument("--json", action="store_true", help="machine-readable output")

    pf = sub.add_parser("families", help="try every protocol family and report which connect")
    common(pf)

    pp = sub.add_parser("probe", help="raw UDP reachability (no goodwe needed)")
    common(pp)

    pw = sub.add_parser("wifi", help="find GoodWe modules on the LAN (UDP 48899) — reveals the real IP")
    common(pw, ip_required=False)
    pw.add_argument("--ip", help="optional: also probe this IP directly")

    ps = sub.add_parser("scan", help="scan an IP for GoodWe local interfaces (TCP 502/8899, UDP 8899/48899)")
    common(ps)

    pm = sub.add_parser("modbus", help="probe Modbus TCP (port 502) — for newer dongles SEMS+ uses")
    common(pm)

    pt = sub.add_parser("tcp", help="read a newer Modbus-TCP GoodWe (port 502): device info + live values")
    common(pt)

    pd = sub.add_parser("discover", help="GoodWe broadcast discovery")
    common(pd, ip_required=False)
    pd.add_argument("--ip", help="optional IP to probe directly")

    args = ap.parse_args()
    if args.cmd == "read":
        asyncio.run(cmd_read(args))
    elif args.cmd == "families":
        asyncio.run(cmd_families(args))
    elif args.cmd == "probe":
        cmd_probe(args)
    elif args.cmd == "wifi":
        cmd_wifi(args)
    elif args.cmd == "scan":
        cmd_scan(args)
    elif args.cmd == "modbus":
        cmd_modbus(args)
    elif args.cmd == "tcp":
        cmd_tcp(args)
    elif args.cmd == "discover":
        asyncio.run(cmd_discover(args))


if __name__ == "__main__":
    main()
