#!/usr/bin/env python3
"""
config_store.py - MySQL persistence for Solar Monitor (system configuration).

Stores application settings and user accounts in MySQL. Time-series solar
readings are NOT here - those live in MongoDB (see data_store.py).

Connection parameters are read from the environment (set by docker-compose):

    SM_DB_HOST       (default 127.0.0.1)
    SM_DB_PORT       (default 3306)
    SM_DB_USER       (default solar)
    SM_DB_PASSWORD   (default solar)
    SM_DB_NAME       (default solar_monitor)

Tables (all snake_case, utf8mb4, InnoDB):
    users            - login accounts, roles and profile names
    app_settings     - key/value scalar settings
    tile_visibility  - which info tiles are shown
"""

import hashlib
import os
import secrets
import threading
import time
import uuid

LOCAL_DB = {
    "host": os.environ.get("SM_DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("SM_DB_PORT", "3306")),
    "user": os.environ.get("SM_DB_USER", "solar"),
    "password": os.environ.get("SM_DB_PASSWORD", "solar"),
    "database": os.environ.get("SM_DB_NAME", "solar_monitor"),
}

HASH_ITERATIONS = 100_000
_write_lock = threading.Lock()

# Inverter brands. Only GoodWe has a working driver today; the others are
# reserved so the UI can show them greyed out as "Not Yet Supported".
SUPPORTED_BRANDS = ("goodwe",)
KNOWN_BRANDS = ("goodwe", "solis", "deye")

DEFAULT_SETTINGS = {
    "dashboard_name": "Solar Flow",
    "summary_units": "auto",
    "inverter_ip": "",
    "active_inverter_id": "",
    "timezone": "UTC",
    "setup_complete": "0",
    "debug_mode": "0",
    "poll_interval_seconds": "5",
    "log_interval_seconds": "30",
    "remote_db_host": "",
    "remote_db_port": "3306",
    "remote_db_user": "",
    "remote_db_password": "",
    "remote_db_name": "",
}

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id              INT AUTO_INCREMENT PRIMARY KEY,
        username        VARCHAR(64)  NOT NULL UNIQUE,
        password_hash   VARCHAR(255) NOT NULL,
        password_salt   VARCHAR(64)  NOT NULL,
        first_name      VARCHAR(120) NOT NULL DEFAULT '',
        last_name       VARCHAR(120) NOT NULL DEFAULT '',
        role            VARCHAR(16)  NOT NULL DEFAULT 'user',
        created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS app_settings (
        setting_key     VARCHAR(64) PRIMARY KEY,
        setting_value   TEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS tile_visibility (
        tile_id         VARCHAR(64) PRIMARY KEY,
        is_visible      TINYINT(1)  NOT NULL DEFAULT 1
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS inverters (
        id              VARCHAR(36) PRIMARY KEY,
        nickname        VARCHAR(80)  NOT NULL,
        brand           VARCHAR(24)  NOT NULL DEFAULT 'goodwe',
        ip_address      VARCHAR(64)  NOT NULL DEFAULT '',
        is_enabled      TINYINT(1)   NOT NULL DEFAULT 1,
        sort_order      INT          NOT NULL DEFAULT 0,
        created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def _connect(database=True):
    import pymysql
    import pymysql.cursors
    params = dict(host=LOCAL_DB["host"], port=LOCAL_DB["port"], user=LOCAL_DB["user"],
                  password=LOCAL_DB["password"], charset="utf8mb4", autocommit=True,
                  connect_timeout=5, cursorclass=pymysql.cursors.DictCursor)
    if database:
        params["database"] = LOCAL_DB["database"]
    return pymysql.connect(**params)


def wait_for_database(retries=60, delay=2):
    """Block until the local MySQL accepts connections (container start-up)."""
    last_error = None
    for _ in range(retries):
        try:
            conn = _connect()
            conn.close()
            return True
        except Exception as e:           # noqa: BLE001
            last_error = e
            time.sleep(delay)
    raise RuntimeError(f"local database not reachable: {last_error}")


def _hash_password(password, salt_bytes):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, HASH_ITERATIONS).hex()


def initialise():
    """Create tables and seed defaults (admin/admin, default settings)."""
    wait_for_database()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for statement in SCHEMA_STATEMENTS:
                cur.execute(statement)
            for key, value in DEFAULT_SETTINGS.items():
                cur.execute(
                    "INSERT IGNORE INTO app_settings (setting_key, setting_value) VALUES (%s, %s)",
                    (key, value))
            cur.execute("SELECT COUNT(*) AS total FROM users")
            if cur.fetchone()["total"] == 0:
                salt = secrets.token_bytes(16)
                cur.execute(
                    "INSERT INTO users (username, password_hash, password_salt, role) "
                    "VALUES (%s, %s, %s, %s)",
                    ("admin", _hash_password("admin", salt), salt.hex(), "admin"))

            # One-time migration: fold a legacy single inverter_ip into the
            # inverters table and mark it active.
            cur.execute("SELECT COUNT(*) AS total FROM inverters")
            if cur.fetchone()["total"] == 0:
                cur.execute("SELECT setting_value FROM app_settings WHERE setting_key='inverter_ip'")
                row = cur.fetchone()
                legacy_ip = (row["setting_value"] if row else "") or ""
                if legacy_ip:
                    inv_id = str(uuid.uuid4())
                    cur.execute(
                        "INSERT INTO inverters (id, nickname, brand, ip_address, is_enabled, sort_order) "
                        "VALUES (%s, %s, 'goodwe', %s, 1, 0)",
                        (inv_id, "Inverter 1", legacy_ip))
                    cur.execute(
                        "INSERT INTO app_settings (setting_key, setting_value) VALUES ('active_inverter_id', %s) "
                        "ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)",
                        (inv_id,))
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #
def _coerce_int(value, fallback):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def get_settings():
    """Return the full settings object used by the API/UI."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT setting_key, setting_value FROM app_settings")
            raw = {row["setting_key"]: row["setting_value"] for row in cur.fetchall()}
            cur.execute("SELECT tile_id, is_visible FROM tile_visibility")
            tiles = {row["tile_id"]: bool(row["is_visible"]) for row in cur.fetchall()}
    finally:
        conn.close()
    return {
        "dashboard_name": raw.get("dashboard_name", "Solar Flow"),
        "summary_units": raw.get("summary_units", "auto"),
        "inverter_ip": raw.get("inverter_ip", "") or "",
        "active_inverter_id": raw.get("active_inverter_id", "") or "",
        "timezone": raw.get("timezone", "UTC") or "UTC",
        "setup_complete": (raw.get("setup_complete", "0") == "1"),
        "debug_mode": (raw.get("debug_mode", "0") == "1"),
        "poll_interval_seconds": _coerce_int(raw.get("poll_interval_seconds"), 5),
        "log_interval_seconds": _coerce_int(raw.get("log_interval_seconds"), 30),
        "remote_db": {
            "host": raw.get("remote_db_host", "") or "",
            "port": _coerce_int(raw.get("remote_db_port"), 3306),
            "user": raw.get("remote_db_user", "") or "",
            "password": raw.get("remote_db_password", "") or "",
            "name": raw.get("remote_db_name", "") or "",
        },
        "updates": {
            "repo": raw.get("github_repo", "") or "",
            "branch": raw.get("github_branch", "") or "",
            "token_set": bool((raw.get("github_token") or "").strip()),
        },
        "tiles": tiles,
    }


def get_update_source():
    """Raw update source for the backend (includes the token). Never sent to UI."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT setting_key, setting_value FROM app_settings "
                        "WHERE setting_key IN ('github_repo','github_branch','github_token')")
            raw = {row["setting_key"]: row["setting_value"] for row in cur.fetchall()}
    finally:
        conn.close()
    return {
        "repo": (raw.get("github_repo") or "").strip(),
        "branch": (raw.get("github_branch") or "").strip(),
        "token": (raw.get("github_token") or "").strip(),
    }


def update_settings(updates):
    """Apply a partial settings update (same shape as get_settings)."""
    scalar = {}
    if "dashboard_name" in updates:
        scalar["dashboard_name"] = str(updates["dashboard_name"])[:60]
    if "summary_units" in updates:
        scalar["summary_units"] = str(updates["summary_units"])[:8]
    if "timezone" in updates:
        scalar["timezone"] = str(updates["timezone"])[:64]
    if "setup_complete" in updates:
        scalar["setup_complete"] = "1" if updates["setup_complete"] else "0"
    if "debug_mode" in updates:
        scalar["debug_mode"] = "1" if updates["debug_mode"] else "0"
    if "inverter_ip" in updates:
        scalar["inverter_ip"] = str(updates["inverter_ip"] or "")
    if "poll_interval_seconds" in updates:
        scalar["poll_interval_seconds"] = str(max(1, min(3600, _coerce_int(updates["poll_interval_seconds"], 5))))
    if "log_interval_seconds" in updates:
        scalar["log_interval_seconds"] = str(max(1, min(86400, _coerce_int(updates["log_interval_seconds"], 30))))
    if isinstance(updates.get("remote_db"), dict):
        remote = updates["remote_db"]
        column_map = {"host": "remote_db_host", "port": "remote_db_port", "user": "remote_db_user",
                      "password": "remote_db_password", "name": "remote_db_name"}
        for field, column in column_map.items():
            if field in remote:
                scalar[column] = str(remote[field])
    if isinstance(updates.get("updates"), dict):
        up = updates["updates"]
        if "repo" in up:
            scalar["github_repo"] = str(up["repo"] or "").strip()[:120]
        if "branch" in up:
            scalar["github_branch"] = str(up["branch"] or "").strip()[:80]
        # Token: only write when a value is supplied; "" with clear_token clears it.
        if up.get("clear_token"):
            scalar["github_token"] = ""
        elif up.get("token"):
            scalar["github_token"] = str(up["token"]).strip()[:255]

    tiles = updates.get("tiles") if isinstance(updates.get("tiles"), dict) else None

    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                for key, value in scalar.items():
                    cur.execute(
                        "INSERT INTO app_settings (setting_key, setting_value) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)",
                        (key, value))
                if tiles is not None:
                    for tile_id, is_visible in tiles.items():
                        cur.execute(
                            "INSERT INTO tile_visibility (tile_id, is_visible) VALUES (%s, %s) "
                            "ON DUPLICATE KEY UPDATE is_visible = VALUES(is_visible)",
                            (str(tile_id)[:64], 1 if is_visible else 0))
        finally:
            conn.close()
    return get_settings()


# --------------------------------------------------------------------------- #
#  Users
# --------------------------------------------------------------------------- #
def get_user(username):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, first_name, last_name, role FROM users WHERE username = %s",
                (username,))
            return cur.fetchone()
    finally:
        conn.close()


def get_role(username):
    user = get_user(username)
    return (user or {}).get("role", "user")


def is_admin(username):
    return get_role(username) == "admin"


def list_users():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username, first_name, last_name, role, created_at "
                        "FROM users ORDER BY username")
            rows = list(cur.fetchall())
            for r in rows:                      # JSON-safe timestamps
                if r.get("created_at") is not None:
                    r["created_at"] = str(r["created_at"])
            return rows
    finally:
        conn.close()


def check_login(username, password):
    if not username:
        return False
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT password_hash, password_salt FROM users WHERE username = %s",
                (username,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return False
    try:
        salt = bytes.fromhex(row["password_salt"])
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(_hash_password(password or "", salt), row["password_hash"])


def add_user(username, password, first_name="", last_name="", role="user"):
    salt = secrets.token_bytes(16)
    role = "admin" if role == "admin" else "user"
    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (username, password_hash, password_salt, first_name, last_name, role) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (username, _hash_password(password, salt), salt.hex(),
                     first_name[:120], last_name[:120], role))
        finally:
            conn.close()


def set_role(username, role):
    role = "admin" if role == "admin" else "user"
    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET role = %s WHERE username = %s", (role, username))
        finally:
            conn.close()


def delete_user(username):
    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE username = %s", (username,))
        finally:
            conn.close()


def count_admins():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'admin'")
            return cur.fetchone()["total"]
    finally:
        conn.close()


def update_profile(username, first_name, last_name):
    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET first_name = %s, last_name = %s WHERE username = %s",
                    (first_name[:120], last_name[:120], username))
        finally:
            conn.close()


def set_password(username, new_password):
    salt = secrets.token_bytes(16)
    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET password_hash = %s, password_salt = %s WHERE username = %s",
                    (_hash_password(new_password, salt), salt.hex(), username))
        finally:
            conn.close()


def test_remote_connection(remote):
    """Test an arbitrary (remote) MySQL connection for future replication."""
    try:
        import pymysql
    except Exception:
        return False, "pymysql is not installed"
    try:
        conn = pymysql.connect(
            host=(remote.get("host") or "127.0.0.1"),
            port=int(remote.get("port") or 3306),
            user=(remote.get("user") or ""),
            password=(remote.get("password") or ""),
            database=(remote.get("name") or None),
            connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        return True, "Connection successful"
    except Exception as e:           # noqa: BLE001
        return False, str(e)


# --------------------------------------------------------------------------- #
#  Inverters (one entity per physical inverter / plant)
# --------------------------------------------------------------------------- #
def list_inverters():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nickname, brand, ip_address, is_enabled, sort_order, created_at "
                        "FROM inverters ORDER BY sort_order, created_at")
            rows = list(cur.fetchall())
            for r in rows:
                r["is_enabled"] = bool(r["is_enabled"])
                if r.get("created_at") is not None:
                    r["created_at"] = str(r["created_at"])
            return rows
    finally:
        conn.close()


def get_inverter(inverter_id):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nickname, brand, ip_address, is_enabled, sort_order "
                        "FROM inverters WHERE id = %s", (inverter_id,))
            row = cur.fetchone()
            if row:
                row["is_enabled"] = bool(row["is_enabled"])
            return row
    finally:
        conn.close()


def _valid_ip_or_host(s):
    """Accept a valid IPv4 address, or a plausible hostname. Reject malformed IPs."""
    s = (s or "").strip()
    if not s:
        return False
    # if it looks purely numeric/dotted, it must be a valid IPv4
    if all(c.isdigit() or c == "." for c in s):
        parts = s.split(".")
        if len(parts) != 4:
            return False
        try:
            return all(0 <= int(p) <= 255 for p in parts) and all(p != "" for p in parts)
        except ValueError:
            return False
    # otherwise treat as a hostname (letters allowed)
    return len(s) <= 253 and all(
        ch.isalnum() or ch in "-." for ch in s)


def _find_ip_conflict(cur, ip, exclude_id=None):
    cur.execute("SELECT id, nickname FROM inverters WHERE LOWER(ip_address) = LOWER(%s)", (ip,))
    for row in cur.fetchall():
        if row["id"] != exclude_id:
            return row["nickname"]
    return None


def _find_name_conflict(cur, name, exclude_id=None):
    cur.execute("SELECT id FROM inverters WHERE LOWER(nickname) = LOWER(%s)", (name,))
    for row in cur.fetchall():
        if row["id"] != exclude_id:
            return True
    return False


def add_inverter(nickname, brand, ip_address):
    """Create an inverter. Returns (id, error). brand must be supported."""
    brand = (brand or "goodwe").lower()
    if brand not in SUPPORTED_BRANDS:
        return None, f"{brand} is not yet supported"
    nickname = (nickname or "").strip()[:80] or "Inverter"
    ip_address = (ip_address or "").strip()[:64]
    if not ip_address:
        return None, "IP address is required"
    if not _valid_ip_or_host(ip_address):
        return None, f"'{ip_address}' is not a valid IP address"
    inv_id = str(uuid.uuid4())
    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                clash = _find_ip_conflict(cur, ip_address)
                if clash is not None:
                    return None, f"IP {ip_address} is already used by inverter '{clash}'"
                if _find_name_conflict(cur, nickname):
                    return None, f"An inverter named '{nickname}' already exists"
                cur.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM inverters")
                order = cur.fetchone()["n"]
                cur.execute(
                    "INSERT INTO inverters (id, nickname, brand, ip_address, is_enabled, sort_order) "
                    "VALUES (%s, %s, %s, %s, 1, %s)",
                    (inv_id, nickname, brand, ip_address, order))
        finally:
            conn.close()
    return inv_id, None


def update_inverter(inverter_id, nickname=None, brand=None, ip_address=None, is_enabled=None):
    if brand is not None:
        brand = brand.lower()
        if brand not in SUPPORTED_BRANDS:
            return f"{brand} is not yet supported"
    if nickname is not None:
        nickname = nickname.strip()[:80] or "Inverter"
    if ip_address is not None:
        ip_address = ip_address.strip()[:64]
        if not ip_address:
            return "IP address is required"
        if not _valid_ip_or_host(ip_address):
            return f"'{ip_address}' is not a valid IP address"
    sets, params = [], []
    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                if ip_address is not None:
                    clash = _find_ip_conflict(cur, ip_address, exclude_id=inverter_id)
                    if clash is not None:
                        return f"IP {ip_address} is already used by inverter '{clash}'"
                if nickname is not None and _find_name_conflict(cur, nickname, exclude_id=inverter_id):
                    return f"An inverter named '{nickname}' already exists"
                if nickname is not None:
                    sets.append("nickname = %s"); params.append(nickname)
                if brand is not None:
                    sets.append("brand = %s"); params.append(brand)
                if ip_address is not None:
                    sets.append("ip_address = %s"); params.append(ip_address)
                if is_enabled is not None:
                    sets.append("is_enabled = %s"); params.append(1 if is_enabled else 0)
                if not sets:
                    return None
                params.append(inverter_id)
                cur.execute(f"UPDATE inverters SET {', '.join(sets)} WHERE id = %s", params)
        finally:
            conn.close()
    return None


def delete_inverter(inverter_id):
    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM inverters WHERE id = %s", (inverter_id,))
                # if it was the active one, fall back to the first remaining
                cur.execute("SELECT setting_value AS v FROM app_settings WHERE setting_key='active_inverter_id'")
                row = cur.fetchone()
                if row and row["v"] == inverter_id:
                    cur.execute("SELECT id FROM inverters ORDER BY sort_order, created_at LIMIT 1")
                    nxt = cur.fetchone()
                    cur.execute("UPDATE app_settings SET setting_value=%s WHERE setting_key='active_inverter_id'",
                                ((nxt["id"] if nxt else ""),))
        finally:
            conn.close()


def count_inverters():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM inverters")
            return cur.fetchone()["total"]
    finally:
        conn.close()


def set_active_inverter(inverter_id):
    with _write_lock:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO app_settings (setting_key, setting_value) VALUES ('active_inverter_id', %s) "
                    "ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)",
                    (inverter_id,))
        finally:
            conn.close()
