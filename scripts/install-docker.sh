#!/usr/bin/env bash
#
# install-docker.sh — install Docker Engine + Compose plugin for Solar Monitor.
#
# Supports RHEL-family (AlmaLinux/Rocky/CentOS/RHEL) and Debian/Ubuntu.
# Adds the invoking user to the 'docker' group and (on RHEL) opens the Docker
# bridge through firewalld so containers can reach your inverter and the
# internet.
#
#   sudo ./install-docker.sh
#
set -euo pipefail

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!  %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Please run as root (sudo ./install-docker.sh)."

# The non-root user to add to the docker group (the one who ran sudo).
TARGET_USER="${SUDO_USER:-${USER:-root}}"

if [ -r /etc/os-release ]; then . /etc/os-release; else die "Cannot detect OS (no /etc/os-release)."; fi

install_rhel() {
    say "Detected RHEL family: ${PRETTY_NAME:-$ID}"
    say "Removing any old/conflicting packages"
    dnf remove -y docker docker-client docker-client-latest docker-common \
        docker-latest docker-latest-logrotate docker-logrotate docker-engine \
        podman runc 2>/dev/null || true

    say "Adding Docker's official repository"
    dnf -y install dnf-plugins-core
    dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

    say "Installing Docker Engine + CLI + Compose plugin"
    dnf install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin

    say "Enabling and starting Docker"
    systemctl enable --now docker

    if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
        say "Opening the Docker bridge through firewalld (LAN + internet egress)"
        firewall-cmd --permanent --add-masquerade || true
        firewall-cmd --permanent --zone=trusted --add-interface=docker0 2>/dev/null || true
        firewall-cmd --reload || true
        systemctl restart docker
        warn "If your inverter is still unreachable from a container, also trust the"
        warn "compose bridge: ip -o link | grep br-   then add it to the trusted zone."
    fi
}

install_debian() {
    say "Detected Debian/Ubuntu: ${PRETTY_NAME:-$ID}"
    local repo="ubuntu"
    case "${ID:-}" in debian) repo="debian" ;; esac

    say "Removing any old packages"
    for p in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
        apt-get remove -y "$p" 2>/dev/null || true
    done

    say "Adding Docker's official repository + key"
    apt-get update
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${repo}/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/${repo} ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list

    say "Installing Docker Engine + CLI + Compose plugin"
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin

    say "Enabling and starting Docker"
    systemctl enable --now docker
}

case "${ID:-}${ID_LIKE:-}" in
    *rhel*|*fedora*|*centos*|*almalinux*|*rocky*) install_rhel ;;
    *debian*|*ubuntu*)                            install_debian ;;
    *) die "Unsupported distro '${ID:-unknown}'. Install Docker manually: https://docs.docker.com/engine/install/" ;;
esac

if [ "$TARGET_USER" != "root" ]; then
    say "Adding '$TARGET_USER' to the docker group (run docker without sudo)"
    usermod -aG docker "$TARGET_USER" || warn "Could not add $TARGET_USER to docker group."
    warn "Log out and back in (or run: newgrp docker) for group membership to apply."
fi

say "Verifying"
docker --version
docker compose version
docker run --rm hello-world >/dev/null 2>&1 && echo "hello-world OK" || \
    warn "hello-world test could not run yet (try again after re-login)."

say "Docker is installed. Next:  ./build.sh   to build and start Solar Monitor."
