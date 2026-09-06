"""Central configuration.

Values are read from the process environment, with a project-level ``.env``
file (git-ignored) loaded first as a convenience for local development. The
real environment always wins over ``.env``.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# src/airspace_intel/config.py -> <project root> is two parents up from the package
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]

# Load <project root>/.env if it exists; never override real env vars.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --- Project layout --------------------------------------------------------
STATIC_DIR = PROJECT_ROOT / "static"
CERT_DIR = Path(os.environ.get("CERT_DIR", PROJECT_ROOT / "certs")).expanduser()
CERT_FILE = CERT_DIR / "localhost.crt"
KEY_FILE = CERT_DIR / "localhost.key"

# --- Logged flight data (this project writes here) ---------------------
# Kept out of the dump1090 tree so the log and its rotated parts live with
# the project. Override with DATA_DIR.
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data")).expanduser()
CSV_PATH = DATA_DIR / "flight_log.csv"
DB_PATH = DATA_DIR / "flight_log.sqlite3"

# Roll flight_log.csv over to a timestamped part once it reaches this size.
MAX_CSV_MB = int(os.environ.get("MAX_CSV_MB", "100"))

# --- dump1090 data source (this project only reads here) --------------
DUMP1090_DIR = Path(os.environ.get("DUMP1090_DIR", "~/dump1090-web")).expanduser()
AIRCRAFT_JSON = DUMP1090_DIR / "public_html" / "data" / "aircraft.json"
DB_DIR = DUMP1090_DIR / "public_html" / "db"
GEOJSON_DIR = DUMP1090_DIR / "public_html" / "geojson"
OL_DIR = DUMP1090_DIR / "public_html" / "ol"

# --- Receiver position --------------------------------------------------
# Match these to the --lat/--lon you pass to dump1090.
RECEIVER_LAT = float(os.environ.get("RECEIVER_LAT", "0.0"))
RECEIVER_LON = float(os.environ.get("RECEIVER_LON", "0.0"))
RECEIVER_CONFIGURED = not (RECEIVER_LAT == 0.0 and RECEIVER_LON == 0.0)

# --- Server ------------------------------------------------------------
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "1978"))

# --- Logger ----------------------------------------------------------
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))

# --- Dashboard tuning ------------------------------------------------
LIVE_THRESHOLD_SECONDS = 30
VALID_RANGES = ("hour", "today", "week", "all")

CONFIG = {
    "vertical_rate_tolerance": 100,
    "rapid_climb_threshold": 2500,
    "rapid_descent_threshold": -2500,
    "nearby_distance_nm": 10,
    "low_aircraft_altitude_ft": 5000,
    "event_cooldown_seconds": 120,
    "range_rings_nm": [25, 50, 100, 150, 200],
    "session_gap_minutes": 30,
    "ground_altitude_ft": 1500,
    "ground_speed_kt": 80,
    "ground_distance_nm": 5,
}
