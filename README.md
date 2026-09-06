# ai-airspace-intelligence

Local tools for logging and visualising ADS-B traffic captured by
[dump1090](https://github.com/flightaware/dump1090). Everything runs offline on
your own machine — aircraft type and registration data come from dump1090's
local database, not from any web API.

Maintained by [apexmoonai.com](https://apexmoonai.com/).

## What's in the box

| Component | Console command | What it does |
|-----------|-----------------|--------------|
| `airspace_intel.logger` | `airspace-logger` | Polls dump1090's `aircraft.json` every few seconds and appends every observed aircraft to `data/flight_log.csv` and `data/flight_log.sqlite3`. The CSV rolls over to a timestamped part once it reaches `MAX_CSV_MB`. |
| `airspace_intel.api_server` | `airspace-api` | Flask app serving the full dashboard (`static/app.html`): live map with range rings, category/type/operator breakdowns, coverage polar plot, event feed, per-aircraft telemetry. |
| `airspace_intel.dashboard_server` | `airspace-dashboard` | Minimal static HTTPS server for the lightweight chart-only dashboard (`static/dashboard.html`). No Flask, no aircraft database. |

## Project layout

```
ai-airspace-intelligence/
├── src/airspace_intel/     # the Python package
│   ├── config.py           # all configuration (env + .env)
│   ├── classify.py         # callsign / aircraft-type lookup tables
│   ├── logger.py           # the flight logger
│   ├── api_server.py       # Flask dashboard API
│   └── dashboard_server.py # lightweight dashboard server
├── static/                 # app.html, dashboard.html
├── scripts/                # make-cert.sh, run-*.sh helpers
├── data/                   # flight_log.csv (+ rotated parts) and .sqlite3  — git-ignored
├── certs/                  # localhost.crt / localhost.key                  — git-ignored
├── images/                 # screenshots for the docs
├── .env.example            # copy to .env and edit
└── pyproject.toml
```

## Requirements

- Python 3.11+
- A working dump1090 install writing `public_html/data/aircraft.json`
- `openssl` (to generate the local TLS certificate)

## Setup

```sh
git clone https://github.com/selchandru6/ai-airspace-intelligence.git
cd ai-airspace-intelligence

# 1. Install (editable) — pulls in flask + python-dotenv
pip install -e .

# 2. Self-signed cert for local HTTPS -> certs/localhost.{crt,key}
./scripts/make-cert.sh

# 3. Local config
cp .env.example .env
$EDITOR .env          # set RECEIVER_LAT / RECEIVER_LON and DUMP1090_DIR
```

`.env` is git-ignored, so your receiver location never lands in version
control. If you skip it, `RECEIVER_LAT`/`RECEIVER_LON` default to `0.0` — the
dashboards still run but distance, bearing and the map centre are meaningless.

## Running

```sh
airspace-logger        # collect data — leave running; Ctrl+C to stop
airspace-api           # full dashboard  -> https://localhost:1978/flightsdashboard.html
airspace-dashboard     # lightweight dashboard (same port — run one server at a time)
```

No install? The `scripts/run-*.sh` wrappers put `src/` on `PYTHONPATH` and work
straight from a clone.

Your browser will warn about the self-signed certificate the first time —
accept it.

## Configuration

All settings come from the environment, with `.env` loaded first as a
convenience (real environment variables win).

| Variable | Default | Purpose |
|----------|---------|---------|
| `DUMP1090_DIR` | `~/dump1090-web` | dump1090 checkout — **read** for `aircraft.json`, the aircraft db, geojson and OpenLayers |
| `DATA_DIR` | `./data` | where the logger **writes** the CSV log, its rotated parts and the SQLite db |
| `RECEIVER_LAT` / `RECEIVER_LON` | `0.0` | receiver position; match your dump1090 `--lat`/`--lon` |
| `MAX_CSV_MB` | `100` | roll `flight_log.csv` to `flight_log-<timestamp>.csv` at this size (`0` disables) |
| `MAX_ROWS` | `500000` | max observation rows one `/api/data` response loads from the DB (newest first) |
| `HOST` / `PORT` | `0.0.0.0` / `1978` | HTTPS bind address and port |
| `POLL_INTERVAL_SECONDS` | `5` | logger poll cadence |
| `CERT_DIR` | `./certs` | TLS cert/key location |

Deeper tuning (vertical-rate thresholds, range rings, session gaps) lives in
the `CONFIG` dict in `src/airspace_intel/config.py`.

## Where the dashboards read their data

Both dashboards read from `DATA_DIR` (`./data` by default). Nothing is ever
deleted there — the logger only appends and rotates.

| Dashboard | Reads from | History available |
|-----------|-----------|-------------------|
| `airspace-api` (full) | `data/flight_log.sqlite3` via `/api/data`; falls back to every `data/flight_log*.csv` part if the DB is missing | **Entire database.** One response is capped at `MAX_ROWS` rows (newest first) as a safety limit — raise it if you need a longer `all` view. `meta.row_cap_hit` in the response tells you when the cap bites. |
| `airspace-dashboard` (lightweight) | `GET /flight_log.csv`, which concatenates **every** `data/flight_log*.csv` part (oldest rotated part first, active file last, one header row) | **All CSV parts**, including rotated ones. |

The SQLite database is never rotated; it holds every observation since the
logger first ran and comfortably handles hundreds of MB. The CSV is a
plain-text mirror — `MAX_CSV_MB` rolls it over to timestamped parts
(`flight_log-20260906T210000Z.csv`) so no single file gets unwieldy, and the
merge above stitches them back together for the dashboard. Everything under
`data/` is git-ignored.

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 Chandran Sellappan /
[apexmoonai.com](https://apexmoonai.com/).
