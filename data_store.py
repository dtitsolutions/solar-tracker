#!/usr/bin/env python3
"""
data_store.py - MongoDB persistence for Solar Monitor (time-series solar data).

Every logged inverter snapshot is written here as a document in the `readings`
collection. System configuration and users live in MySQL (see config_store.py).

Connection parameters are read from the environment (set by docker-compose):

    SM_MONGO_HOST       (default 127.0.0.1)
    SM_MONGO_PORT       (default 27017)
    SM_MONGO_USER       (default solar)
    SM_MONGO_PASSWORD   (default solar)
    SM_MONGO_DB         (default solar_monitor)

Document shape (brand-agnostic, snake_case):
    recorded_at           datetime (UTC)
    inverter_id           str   (reserved for multi-inverter; "default" for now)
    solar_power_w         int
    grid_power_w          int
    battery_power_w       int
    house_power_w         int
    battery_soc_percent   float
    grid_status           str   ("on" / "off" / "unknown")
    temperature_c         float
    energy_today_kwh      float
    energy_total_kwh      float
"""

import os
import time
from datetime import datetime, timezone

MONGO = {
    "host": os.environ.get("SM_MONGO_HOST", "127.0.0.1"),
    "port": int(os.environ.get("SM_MONGO_PORT", "27017")),
    "user": os.environ.get("SM_MONGO_USER", "solar"),
    "password": os.environ.get("SM_MONGO_PASSWORD", "solar"),
    "database": os.environ.get("SM_MONGO_DB", "solar_monitor"),
}

COLLECTION = "readings"
_client = None


def _database():
    import pymongo
    global _client
    if _client is None:
        _client = pymongo.MongoClient(
            host=MONGO["host"], port=MONGO["port"],
            username=(MONGO["user"] or None), password=(MONGO["password"] or None),
            authSource=os.environ.get("SM_MONGO_AUTHDB", "admin"),
            serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    return _client[MONGO["database"]]


def wait_for_database(retries=60, delay=2):
    """Block until MongoDB answers a ping (container start-up)."""
    last_error = None
    for _ in range(retries):
        try:
            _database().command("ping")
            return True
        except Exception as e:           # noqa: BLE001
            last_error = e
            time.sleep(delay)
    raise RuntimeError(f"MongoDB not reachable: {last_error}")


def initialise():
    """Ping and ensure helpful indexes exist."""
    wait_for_database()
    import pymongo
    coll = _database()[COLLECTION]
    coll.create_index([("recorded_at", pymongo.DESCENDING)])
    coll.create_index([("inverter_id", pymongo.ASCENDING),
                       ("recorded_at", pymongo.DESCENDING)])


def _as_int(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def insert_reading(data, grid_status, inverter_id="default"):
    """Write one inverter snapshot. `data` is the normalised sensor dict."""
    doc = {
        "recorded_at": datetime.now(timezone.utc),
        "inverter_id": inverter_id,
        "solar_power_w": _as_int(data.get("ppv")),
        "grid_power_w": _as_int(data.get("active_power")),
        "battery_power_w": _as_int(data.get("pbattery1")),
        "house_power_w": _as_int(data.get("house_consumption")),
        "battery_soc_percent": _as_float(data.get("battery_soc")),
        "grid_status": (grid_status or "unknown")[:16],
        "temperature_c": _as_float(data.get("temperature")),
        "energy_today_kwh": _as_float(data.get("e_day")),
        "energy_total_kwh": _as_float(data.get("e_total")),
    }
    _database()[COLLECTION].insert_one(doc)


def recent(limit=200, inverter_id=None):
    """Return the most recent readings (newest first), for charts/exports."""
    import pymongo
    query = {"inverter_id": inverter_id} if inverter_id else {}
    cursor = (_database()[COLLECTION]
              .find(query, {"_id": 0})
              .sort("recorded_at", pymongo.DESCENDING)
              .limit(int(limit)))
    rows = list(cursor)
    for r in rows:                       # JSON-safe timestamps
        if isinstance(r.get("recorded_at"), datetime):
            r["recorded_at"] = r["recorded_at"].isoformat()
    return rows


def series(inverter_id=None, since=None, until=None, limit=20000):
    """Readings within [since, until] (UTC datetimes), oldest first, for charts."""
    import pymongo
    query = {}
    if inverter_id:
        query["inverter_id"] = inverter_id
    tf = {}
    if since is not None:
        tf["$gte"] = since
    if until is not None:
        tf["$lte"] = until
    if tf:
        query["recorded_at"] = tf
    proj = {"_id": 0, "recorded_at": 1, "solar_power_w": 1, "grid_power_w": 1,
            "battery_power_w": 1, "house_power_w": 1, "battery_soc_percent": 1,
            "energy_today_kwh": 1, "energy_total_kwh": 1}
    cursor = (_database()[COLLECTION]
              .find(query, proj)
              .sort("recorded_at", pymongo.ASCENDING)
              .limit(int(limit)))
    rows = list(cursor)
    for r in rows:
        if isinstance(r.get("recorded_at"), datetime):
            r["recorded_at"] = r["recorded_at"].isoformat()
    return rows


def delete_inverter_data(inverter_id):
    """Delete every reading for one inverter (called when an inverter is removed,
    to reclaim space). Returns the number of documents removed."""
    if not inverter_id:
        return 0
    res = _database()[COLLECTION].delete_many({"inverter_id": inverter_id})
    return getattr(res, "deleted_count", 0)

