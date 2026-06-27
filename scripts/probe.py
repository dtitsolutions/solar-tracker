#!/usr/bin/env python3
"""Reachability probe — run INSIDE the app container to see the network the way
the collector sees it (not the host). Usage: python probe.py <inverter-ip>"""
import socket, sys, urllib.request

ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.10.51"
print("Probing from inside the container...\n")

# 1) UDP to the inverter on 8899 (GoodWe local protocol)
print("[1] UDP %s:8899 (GoodWe) ... " % ip, end="", flush=True)
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    # GoodWe ES/ET discovery-style request; we only care if ANY reply returns
    s.sendto(b"\x7f\x03\x75\x94\x00\x49\xd5\xc2", (ip, 8899))
    data, addr = s.recvfrom(1024)
    print("REPLY from %s (%d bytes) -> container CAN reach the inverter" % (addr, len(data)))
except socket.timeout:
    print("NO REPLY (timeout) -> container CANNOT reach the inverter (firewall/route)")
except OSError as e:
    print("ERROR %s -> no route from the container to that subnet" % e)
finally:
    try: s.close()
    except Exception: pass

# 2) raw TCP to the inverter IP (proves L3 path to the LAN host at all)
print("[2] TCP %s:8899 connect ... " % ip, end="", flush=True)
try:
    c = socket.create_connection((ip, 8899), timeout=3); c.close()
    print("open")
except Exception as e:
    print("%s (UDP-only device may refuse TCP; the UDP test above is what matters)" % e.__class__.__name__)

# 3) internet egress (GitHub)
print("[3] HTTPS api.github.com ... ", end="", flush=True)
try:
    urllib.request.urlopen("https://api.github.com", timeout=6)
    print("reachable -> container has internet")
except Exception as e:
    print("FAILED (%s) -> container has no outbound internet" % e.__class__.__name__)

print("\nIf [1] and [3] both fail, it's the host firewall/bridge, not the inverter.")
