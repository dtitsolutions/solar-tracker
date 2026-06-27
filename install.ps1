# One-stop installer for Solar Monitor v1 (Windows PowerShell).
$ErrorActionPreference = "Stop"
Write-Host "== Solar Monitor installer =="
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Error "Docker Desktop is required: https://docs.docker.com/desktop/"; exit 1 }
try { docker compose version | Out-Null } catch { Write-Error "Docker Compose v2 is required."; exit 1 }
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env from template." }
Write-Host "Building images and starting the stack (first run can take a few minutes)..."
docker compose up -d --build
Write-Host ""
Write-Host "Done. Open:"
Write-Host "  Dashboard     : http://localhost:8080/    sign in: admin / admin"
Write-Host "  phpMyAdmin    : http://localhost:8081/     config DB (server: config-db)"
Write-Host "  mongo-express : http://localhost:8082/     solar data DB"
Write-Host ""
Write-Host "Next: sign in, change the admin password, then set your inverter IP under Settings."
