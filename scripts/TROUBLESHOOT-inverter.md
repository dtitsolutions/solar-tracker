# "Unable to connect to inverter" — quick diagnosis

Your ping / `nc -u` tests run from the **host**. The collector runs in a
**container** on Docker's bridge network, which is a different path. On
AlmaLinux/Rocky, **firewalld usually blocks traffic forwarded from the Docker
bridge to other LAN hosts**, so the host reaches the inverter but the container
can't. (Same reason GitHub failed from the app.)

## 1. Confirm it (run from the project dir on the host)

UDP to the inverter, *from inside the container*:

    docker compose exec app python -c "import socket;s=socket.socket(2,2);s.settimeout(3);s.sendto(b'\x7f\x03\x75\x94\x00\x49\xd5\xc2',('192.168.10.51',8899));\
import sys;\
print('REPLY -> container CAN reach inverter') if s.recvfrom(1024) else None" \
      2>&1 || echo "NO REPLY -> container blocked from the inverter"

Internet, from inside the container:

    docker compose exec app python -c "import urllib.request as u;u.urlopen('https://api.github.com',timeout=6);print('internet OK')"

Or run the bundled probe:

    docker compose exec app python scripts/probe.py 192.168.10.51

If the UDP test (and GitHub) fail from the container but work from the host,
it's the host firewall/bridge — not the inverter.

## 2a. Fix — allow the Docker bridge through firewalld (recommended)

    # let containers reach the LAN + internet (masquerade/NAT for the bridge)
    sudo firewall-cmd --permanent --add-masquerade
    # put the docker bridge in the trusted zone (covers compose's br-* bridge)
    sudo firewall-cmd --permanent --zone=trusted --add-interface=docker0
    sudo firewall-cmd --reload
    sudo systemctl restart docker
    docker compose up -d

If your compose bridge isn't `docker0`, find it and trust it:

    docker network inspect solar-tracker_default -f '{{range .Options}}{{.}}{{end}}' 2>/dev/null
    ip -o link | grep br-          # shows br-xxxxxxxx
    sudo firewall-cmd --permanent --zone=trusted --add-interface=br-XXXXXXXX
    sudo firewall-cmd --reload

Re-run the probe in step 1 — it should now get a reply.

## 2b. Fix — run the collector on the host network (no firewall changes)

    docker compose -f docker-compose.yml -f docker-compose.hostnet.yml up -d

This puts the `app` container directly on the host's network, so UDP to the
inverter behaves exactly like on the host. (It publishes the DBs/redis on
127.0.0.1 and points the web tier at the host gateway — all handled by the
override file.)

## 3. Still nothing?

- Confirm the inverter IP and that nothing else holds its single local-protocol
  connection (the GoodWe WiFi/LAN module allows only one local client at a time —
  close the SEMS app / any other poller).
- Pin the protocol so discovery is skipped:  `SM_GOODWE_FAMILY=ES` in `.env`.
- Check the worker log:  `docker compose logs --tail=50 app | grep -i connect`
