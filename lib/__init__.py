"""
Solar Monitor inverter drivers.

Brand-specific reader code lives here, one module per brand named
``inverter_<brand>.py`` (today: lib/inverter_goodwe.py for GoodWe).

Use ``get_driver(brand)`` to load the right module for an inverter. Each driver
module exposes:

    BRAND                       - the brand id string
    async connect(ip, retries)  - returns a live inverter object
    SUMMARY                     - the curated sensor summary map

Adding a brand later means dropping in ``inverter_<brand>.py`` with the same
interface and listing it in SUPPORTED below.
"""

import importlib

SUPPORTED = {
    "goodwe": "lib.inverter_goodwe",
}
DEFAULT_BRAND = "goodwe"
_cache = {}


def get_driver(brand):
    """Return the driver module for a brand, falling back to the default."""
    key = (brand or DEFAULT_BRAND).lower()
    if key not in SUPPORTED:
        key = DEFAULT_BRAND
    if key not in _cache:
        _cache[key] = importlib.import_module(SUPPORTED[key])
    return _cache[key]


def supported_brands():
    return tuple(SUPPORTED.keys())
