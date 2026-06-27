#!/usr/bin/env bash
# One-stop installer for Solar Monitor v1 (Linux/macOS).
set -euo pipefail
echo "== Solar Monitor installer =="
command -v docker >/dev/null || { echo "Docker is required -> https://docs.docker.com/engine/install/"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required (docker compose ...)."; exit 1; }
if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from template (edit passwords if you wish)."; fi
echo "Building images and starting the stack (first run can take a few minutes)..."
docker compose up -d --build
echo
echo "Done. Open:"
echo "  Dashboard     : http://localhost:8080/    sign in: admin / admin"
echo "  phpMyAdmin    : http://localhost:8081/     config DB (server: config-db)"
echo "  mongo-express : http://localhost:8082/     solar data DB"
echo
echo "Next: sign in, change the admin password, then set your inverter IP under Settings."
