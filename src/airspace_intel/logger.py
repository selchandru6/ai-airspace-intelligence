#!/usr/bin/env python3
"""Poll dump1090's ``aircraft.json`` and append every observed aircraft to a
CSV log and a SQLite database.

Run:
    airspace-logger                 # after `pip install -e .`
    python -m airspace_intel.logger

Stop with Ctrl+C. Safe to leave running for hours/days - the CSV rolls over
to a timestamped part once it reaches MAX_CSV_MB (see airspace_intel.config).
"""

import csv
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

from .config import (
    AIRCRAFT_JSON,
    CSV_PATH as LOG_FILE,
    DATA_DIR,
    DB_PATH as DB_FILE,
    MAX_CSV_MB,
    POLL_INTERVAL_SECONDS,
)

FIELDNAMES = [
    "timestamp_utc",
    "receiver_now",
    "receiver_messages",
    "hex",
    "flight",
    "alt_baro",
    "altitude_ft",
    "alt_geom",
    "gs",
    "speed_kt",
    "ias",
    "tas",
    "mach",
    "track",
    "heading",
    "track_rate",
    "roll",
    "mag_heading",
    "true_heading",
    "baro_rate",
    "geom_rate",
    "squawk",
    "emergency",
    "category",
    "nav_qnh",
    "nav_altitude_mcp",
    "nav_altitude_fms",
    "nav_heading",
    "nav_modes",
    "lat",
    "lon",
    "rssi",
    "messages",
    "seen",
    "seen_pos",
    "version",
    "nic",
    "nic_baro",
    "rc",
    "nac_p",
    "nac_v",
    "sil",
    "sil_type",
    "gva",
    "sda",
    "alert",
    "spi",
    "mlat",
    "tisb",
    "dbFlags",
    "receiver_metadata",
    "raw_json",
]


def ensure_log_header():
    """Create or upgrade the CSV header without discarding existing rows."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
        return

    with open(LOG_FILE, "r", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == FIELDNAMES:
            return
        existing_rows = list(reader)

    directory = os.path.dirname(LOG_FILE) or "."
    with tempfile.NamedTemporaryFile("w", newline="", dir=directory, delete=False) as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(existing_rows)
        migrated_path = f.name
    os.replace(migrated_path, LOG_FILE)


def rotate_if_needed():
    """Roll the active CSV over to a timestamped part once it gets large.

    The SQLite database keeps every observation; the CSV parts are a plain
    text mirror, so splitting them keeps any single file easy to open.
    """
    if MAX_CSV_MB <= 0 or not os.path.exists(LOG_FILE):
        return
    if os.path.getsize(LOG_FILE) < MAX_CSV_MB * 1024 * 1024:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived = os.path.join(DATA_DIR, f"flight_log-{stamp}.csv")
    os.replace(LOG_FILE, archived)
    ensure_log_header()
    print(f"  rotated CSV -> {os.path.basename(archived)}")


def json_value(value):
    """Keep nested dump1090 values losslessly inside one CSV cell."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return value


def ensure_observation_db():
    """Create the indexed store and backfill it once from the existing CSV."""
    with sqlite3.connect(DB_FILE) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY,
            timestamp_epoch REAL NOT NULL,
            timestamp_utc TEXT NOT NULL,
            hex TEXT,
            flight TEXT,
            altitude_ft REAL,
            speed_kt REAL,
            lat REAL,
            lon REAL,
            messages REAL,
            payload TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_observations_time ON observations(timestamp_epoch)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_observations_hex ON observations(hex)")
        existing = db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        if existing or not os.path.exists(LOG_FILE):
            return
        with open(LOG_FILE, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    timestamp = datetime.fromisoformat(row["timestamp_utc"])
                except (KeyError, ValueError):
                    continue
                db.execute("""INSERT INTO observations
                    (timestamp_epoch, timestamp_utc, hex, flight, altitude_ft, speed_kt, lat, lon, messages, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                        timestamp.timestamp(), row.get("timestamp_utc", ""), row.get("hex", ""),
                        row.get("flight", ""), row.get("altitude_ft") or 0, row.get("speed_kt") or 0,
                        row.get("lat") or None, row.get("lon") or None, row.get("messages") or 0,
                        json.dumps(row, separators=(",", ":")),
                    ))


def poll_once():
    """Read the current aircraft.json snapshot and append rows to the log."""
    try:
        with open(AIRCRAFT_JSON, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # dump1090 may be mid-write, or not running yet - just skip this cycle
        return 0

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows_written = 0

    receiver_metadata = {
        key: value for key, value in data.items() if key != "aircraft"
    }

    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        db = sqlite3.connect(DB_FILE)
        for ac in data.get("aircraft", []):
            row = {
                "timestamp_utc": timestamp,
                "receiver_now": data.get("now", ""),
                "receiver_messages": json_value(data.get("messages")),
                "hex": ac.get("hex", ""),
                "flight": ac.get("flight", "").strip(),
                "alt_baro": ac.get("alt_baro", ""),
                "altitude_ft": ac.get("alt_baro", ac.get("altitude", "")),
                "alt_geom": ac.get("alt_geom", ""),
                "gs": ac.get("gs", ""),
                "speed_kt": ac.get("gs", ac.get("speed", "")),
                "ias": ac.get("ias", ""),
                "tas": ac.get("tas", ""),
                "mach": ac.get("mach", ""),
                "track": ac.get("track", ""),
                "heading": ac.get("track", ac.get("heading", "")),
                "track_rate": ac.get("track_rate", ""),
                "roll": ac.get("roll", ""),
                "mag_heading": ac.get("mag_heading", ""),
                "true_heading": ac.get("true_heading", ""),
                "baro_rate": ac.get("baro_rate", ""),
                "geom_rate": ac.get("geom_rate", ""),
                "squawk": ac.get("squawk", ""),
                "emergency": ac.get("emergency", ""),
                "category": ac.get("category", ""),
                "nav_qnh": ac.get("nav_qnh", ""),
                "nav_altitude_mcp": ac.get("nav_altitude_mcp", ""),
                "nav_altitude_fms": ac.get("nav_altitude_fms", ""),
                "nav_heading": ac.get("nav_heading", ""),
                "nav_modes": json_value(ac.get("nav_modes")),
                "lat": ac.get("lat", ""),
                "lon": ac.get("lon", ""),
                "rssi": ac.get("rssi", ""),
                "messages": ac.get("messages", ""),
                "seen": ac.get("seen", ""),
                "seen_pos": ac.get("seen_pos", ""),
                "version": ac.get("version", ""),
                "nic": ac.get("nic", ""),
                "nic_baro": ac.get("nic_baro", ""),
                "rc": ac.get("rc", ""),
                "nac_p": ac.get("nac_p", ""),
                "nac_v": ac.get("nac_v", ""),
                "sil": ac.get("sil", ""),
                "sil_type": ac.get("sil_type", ""),
                "gva": ac.get("gva", ""),
                "sda": ac.get("sda", ""),
                "alert": ac.get("alert", ""),
                "spi": ac.get("spi", ""),
                "mlat": json_value(ac.get("mlat")),
                "tisb": json_value(ac.get("tisb")),
                "dbFlags": ac.get("dbFlags", ""),
                "receiver_metadata": json.dumps(receiver_metadata, separators=(",", ":")),
                "raw_json": json.dumps(ac, separators=(",", ":")),
            }
            writer.writerow(row)
            db.execute("""INSERT INTO observations
                (timestamp_epoch, timestamp_utc, hex, flight, altitude_ft, speed_kt, lat, lon, messages, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                    datetime.fromisoformat(timestamp).timestamp(), timestamp, row["hex"], row["flight"],
                    row["altitude_ft"] or 0, row["speed_kt"] or 0, row["lat"] or None, row["lon"] or None,
                    row["messages"] or 0, json.dumps(row, separators=(",", ":")),
                ))
            rows_written += 1
        db.commit()
        db.close()

    return rows_written


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    ensure_log_header()
    ensure_observation_db()
    print(f"Logging flights to: {LOG_FILE}")
    print(f"Reading from:       {AIRCRAFT_JSON}")
    print(f"Rotating CSV at:    {MAX_CSV_MB} MB" if MAX_CSV_MB > 0 else "CSV rotation: off")
    print(f"Polling every {POLL_INTERVAL_SECONDS}s - Ctrl+C to stop.\n")

    total = 0
    try:
        while True:
            rotate_if_needed()
            n = poll_once()
            total += n
            if n:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] logged {n} aircraft (total: {total})")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print(f"\nStopped. {total} total rows logged to {LOG_FILE}")


if __name__ == "__main__":
    main()
