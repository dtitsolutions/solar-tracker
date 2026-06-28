#!/usr/bin/env python3
"""
logsetup.py - action + system logfiles for Solar Monitor.

Two files (paths overridable via SM_LOG_DIR, default /var/log, falling back to
./logs if that isn't writable):

    solar-monitor-action.log   - user actions (logins, settings, user/inverter changes)
    solar-monitor-system.log   - system events, errors, on/off-grid transitions

Timestamps are MM/DD/YY 24-hour in the configured timezone.
"""

import logging
import os
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except Exception:                       # pragma: no cover
    ZoneInfo = None

ACTION_FILE = "solar-monitor-action.log"
SYSTEM_FILE = "solar-monitor-system.log"

_timezone_name = os.environ.get("SM_TIMEZONE", "UTC")
_action_logger = None
_system_logger = None


def _tzinfo():
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(_timezone_name)
    except Exception:
        return None


class _Formatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, _tzinfo())
        return dt.strftime("%m/%d/%y %H:%M:%S")


def _log_dir():
    candidates = [os.environ.get("SM_LOG_DIR", "/var/log"),
                  os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")]
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            with open(probe, "a"):
                pass
            os.remove(probe)
            return d
        except Exception:
            continue
    return "."


def log_dir():
    """Directory where the action/system logs are written."""
    return _log_dir()


def log_files():
    """Existing log file paths, for the troubleshooting download."""
    d = _log_dir()
    out = []
    for f in (ACTION_FILE, SYSTEM_FILE):
        p = os.path.join(d, f)
        if os.path.exists(p):
            out.append(p)
    return out


def _build(name, filename):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(os.path.join(_log_dir(), filename))
        handler.setFormatter(_Formatter("%(asctime)s | %(levelname)-5s | %(message)s"))
        logger.addHandler(handler)
        # mirror to stdout so `docker compose logs` shows them too
        stream = logging.StreamHandler()
        stream.setFormatter(_Formatter("%(asctime)s | %(levelname)-5s | %(message)s"))
        logger.addHandler(stream)
    return logger


def init():
    global _action_logger, _system_logger
    _action_logger = _build("solar.action", ACTION_FILE)
    _system_logger = _build("solar.system", SYSTEM_FILE)


def set_timezone(name):
    global _timezone_name
    if name:
        _timezone_name = name


def timezone_name():
    return _timezone_name


def action(message):
    if _action_logger is None:
        init()
    _action_logger.info(message)


def system(message):
    if _system_logger is None:
        init()
    _system_logger.info(message)


def system_error(message):
    if _system_logger is None:
        init()
    _system_logger.error(message)
