# Solar Monitor v1

A self-hosted, **brand-agnostic** local dashboard for solar inverters. It polls
your inverter over the LAN, shows a live power-flow view (solar / grid / battery
/ home), and records the time-series to its own database. No cloud, no vendor
account. GoodWe is supported today; the architecture is built so other brands
slot in later.

## Architecture

- **web** (`web/`, Node) — public web server; streams live data from Redis
  (push-based, no refresh) and proxies everything else to the app.
- **app** (`solar_monitor.py`, Python) — collector + API; polls the inverter,
  writes readings to MongoDB, and publishes live state to Redis.
- **redis** — live cache + pub/sub bus between app and web.
- **MySQL** (`config-db`) — system configuration + user accounts.
- **MongoDB** (`data-db`) — time-series solar readings.
- **phpMyAdmin** (:8081) / **mongo-express** (:8082) — browse each database.

Inverter drivers live in **`lib/`** (`lib/inverter.py`, the GoodWe driver);
other brands slot in alongside it behind the same read interface.

## Quick start (Docker)

    ./install.sh        # Linux / macOS
    ./install.ps1       # Windows PowerShell

Or manually:

    cp .env.example .env          # optional: edit passwords
    docker compose up -d --build

Then open **http://localhost:8080/** and sign in with **admin / admin**.
Change the admin password, then open **Settings -> Advanced -> Inverter** and
enter your inverter's IP address.

> Broadcast discovery can't cross Docker's network, so set the inverter IP
> manually. A fixed DHCP lease for the inverter is recommended.

## Ports

| Service       | URL                    | Purpose                          |
|---------------|------------------------|----------------------------------|
| Dashboard     | http://localhost:8080/ | the app                          |
| phpMyAdmin    | http://localhost:8081/ | MySQL config DB (server config-db)|
| mongo-express | http://localhost:8082/ | MongoDB solar-data DB            |
| MySQL         | localhost:3306         | config DB (replication/tools)    |
| MongoDB       | localhost:27017        | solar-data DB                    |

## Roles

- **admin** — change all settings, add/remove users, assign roles, change
  passwords, manage the inverter.
- **viewer** — live dashboard and basic display preferences only; advanced
  settings and user management are hidden and blocked server-side.

## Settings

- **Basic** (any user): summary units, which info tiles to show.
- **Advanced** (admin): dashboard name, inverter IP, **live interval** and
  **database-log interval** (default 30 s, so logging doesn't overload the live
  view), plus an optional **Remote MySQL (replication)** test for a future
  external dashboard.

## Multiple inverters

Open **Settings → Advanced → Inverters (plants)** to add each inverter as its
own entity: a **nickname**, a **brand** (GoodWe today; Solis/Deye appear but are
greyed out as "Not Yet Supported"), and its **IP address**. Each inverter gets a
unique id, and its readings are stored in MongoDB tagged with that id.

When two or more inverters exist, a switcher appears under the dashboard title:
use the ‹ › arrows (or swipe left/right on the flow card) to choose which
inverter you're viewing. Any signed-in user can switch the view; only admins can
add, edit, or remove inverters.

> v1 polls the **active** inverter (the one selected). Continuous background
> logging of every inverter at once is the next increment.

## Database access (phpMyAdmin / mongo-express)

- **phpMyAdmin** (http://localhost:8081/): the server is already `config-db`.
  Sign in with **`solar` / `solar`** (app user, scoped to `solar_monitor`) or
  **`root` / `<SM_DB_ROOT_PASSWORD>`** for full access.
- **mongo-express** (http://localhost:8082/): no login by default; browse the
  `solar_monitor` database → `readings` collection.

**"Access denied / credentials out of sync":** MariaDB only applies the
`SM_DB_*` passwords the **first time** its data volume is created. If you changed
`.env` after the first `up`, the database still has the original password. Fix on
a dev box (this wipes the config DB; the app re-seeds `admin/admin`):

    docker compose down -v
    docker compose up -d --build

To keep the data instead, reset the user with the current root password:

    docker compose exec config-db mariadb -uroot -p'<current-root-pw>' \
      -e "ALTER USER 'solar'@'%' IDENTIFIED BY 'solar'; FLUSH PRIVILEGES;"

## First run

On a fresh install, after you sign in as **admin / admin** a short setup wizard
asks for a **dashboard name**, **timezone**, and a **new admin password**. Finish
it and you're on the live dashboard.

## Logs

Two logfiles are written (and also streamed to `docker compose logs app`):

- `solar-monitor-action.log` — user actions (logins, settings, user/inverter changes)
- `solar-monitor-system.log` — system events, errors, and on/off-grid transitions

Timestamps use `MM/DD/YY` 24-hour in the configured timezone. In Docker they live
in the `app_logs` volume at `/var/log/solar-monitor/`.

## Updating

    git pull                      # or copy the new files over
    docker compose up -d --build

Hard-refresh the browser (Ctrl+Shift+R). The `config_db_data` and `data_db_data`
volumes persist across rebuilds.

## Environment (.env)

| Variable               | Default          | Purpose                |
|------------------------|------------------|------------------------|
| `SM_DB_NAME/USER/PASSWORD` | solar_monitor / solar / solar | MySQL config DB |
| `SM_DB_ROOT_PASSWORD`  | change-me-root   | MySQL root password    |
| `SM_MONGO_DB/USER/PASSWORD` | solar_monitor / solar / solar | MongoDB data DB |

## Security note

Plain HTTP — fine on a trusted LAN, but the login is a gate, not transport
encryption. Put HTTPS (a reverse proxy) in front before any internet exposure,
and change all default passwords first.

## Notes

- MySQL is required at start (config + users). MongoDB is best-effort: if it's
  briefly down the live view keeps working and logging resumes when it returns.
- Built for a single-phase GoodWe ES hybrid (e.g. GW6000ES). Multi-brand and
  multi-inverter support are on the roadmap.
