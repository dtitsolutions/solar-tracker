# Installing Docker (for Solar Monitor / solar-tracker)

You need **Docker Engine** plus the **Compose plugin** (the `docker compose`
subcommand). Below is the official-repository method — the one to use on a
server, rather than the distro's older `docker.io` package.

Run everything as a user with `sudo`.

---

## AlmaLinux / Rocky / RHEL / CentOS Stream  (your server)

```bash
# 1. Remove any old/conflicting packages (safe if none are installed)
sudo dnf remove -y docker docker-client docker-client-latest docker-common \
  docker-latest docker-latest-logrotate docker-logrotate docker-engine podman runc

# 2. Add Docker's official repo (AlmaLinux/Rocky use the CentOS repo)
sudo dnf -y install dnf-plugins-core
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

# 3. Install Engine + CLI + Compose plugin + Buildx
sudo dnf install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

# 4. Start it now and on every boot
sudo systemctl enable --now docker

# 5. Verify
sudo docker run --rm hello-world
docker compose version
```

---

## Ubuntu / Debian

```bash
# 1. Remove old packages
for p in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  sudo apt-get remove -y $p; done

# 2. Add Docker's APT repo + key
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
# Debian users: change "ubuntu" to "debian" in the line above AND below.
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 3. Install
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

# 4. Start + enable
sudo systemctl enable --now docker

# 5. Verify
sudo docker run --rm hello-world
docker compose version
```

---

## Post-install (recommended on a server)

**Run docker without `sudo`** — add your user to the `docker` group:

```bash
sudo usermod -aG docker "$USER"
# log out and back in (or run: newgrp docker) for it to take effect
docker ps        # should work without sudo now
```

> Note: the `docker` group grants root-equivalent access. Only add trusted users.

**Confirm Compose v2 is the plugin** (one word, `docker compose`, not the old
`docker-compose` hyphenated binary):

```bash
docker compose version      # Docker Compose version v2.x.x
```

---

## AlmaLinux firewall note (important for solar-tracker)

On RHEL-family hosts, **firewalld** often blocks traffic from Docker containers
out to other LAN devices (like your inverter) and to the internet. If the
dashboard can't reach the inverter or GitHub, enable NAT/masquerade for the
Docker bridge:

```bash
sudo firewall-cmd --permanent --add-masquerade
sudo firewall-cmd --permanent --zone=trusted --add-interface=docker0
sudo firewall-cmd --reload
sudo systemctl restart docker
```

---

## Then deploy Solar Monitor

```bash
cd /opt/solar-tracker
GIT_SHA="$(git rev-parse HEAD)" docker compose up -d --build
# open http://<server-ip>:8080   (sign in admin / admin, then change it)
```

Useful checks:

```bash
docker compose ps                 # service status
docker compose logs -f app        # collector logs
docker compose down               # stop the stack
```

---

## Uninstall (if ever needed)

```bash
# RHEL family
sudo dnf remove -y docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin
# Debian/Ubuntu
sudo apt-get purge -y docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin

# Optional: wipe images/volumes/networks (DELETES YOUR DATA)
sudo rm -rf /var/lib/docker /var/lib/containerd
```
