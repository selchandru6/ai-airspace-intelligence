# ai-airspace-intelligence

Local tools for logging and visualising ADS-B traffic captured by
[dump1090](https://github.com/flightaware/dump1090). Everything runs offline on
your own machine - aircraft type/registration data comes from dump1090's local
database, not from any web API.

Three pieces:

| File | What it does |
|------|--------------|
| `flight_log.py` | Polls dump1090's `aircraft.json` every 5 s and appends every observed aircraft to a CSV log and a SQLite database. |
| `api_server.py` | Flask app serving a rich dashboard (`app.html`): live map with range rings, category/type/operator breakdowns, coverage polar plot, event feed, per-aircraft telemetry. |
| `dashboard_server.py` | Minimal static HTTPS server for a lightweight chart-only dashboard (`dashboard.html`). |

## Requirements

- Python 3.11+
- A working dump1090 install writing `public_html/data/aircraft.json`
- `pip install flask` (only for `api_server.py`)
- `openssl` (only to generate the local TLS cert)

## Setup

```sh
git clone https://github.com/selchandru6/ai-airspace-intelligence.git
cd ai-airspace-intelligence

# 1. Self-signed cert for local HTTPS (creates localhost.crt / localhost.key)
./make-cert.sh

# 2. Tell the tools where dump1090 lives and where your receiver is.
#    DUMP1090_DIR defaults to ~/dump1090-web if unset.
export DUMP1090_DIR="$HOME/dump1090-web"
export RECEIVER_LAT=33.1500      # your receiver latitude
export RECEIVER_LON=-96.8200     # your receiver longitude
```

`RECEIVER_LAT` / `RECEIVER_LON` default to `0.0` if unset - the dashboards work,
but distance, bearing and the map centre will be wrong until you set them.

## Running

**Collect data** (leave running for hours/days; Ctrl+C to stop):

```sh
python3 flight_log.py
```

Writes `$DUMP1090_DIR/flight_log.csv` and `$DUMP1090_DIR/flight_log.sqlite3`.
Both are created automatically and are git-ignored.

**Full dashboard:**

```sh
pip install flask
python3 api_server.py
# https://localhost:1978/flightsdashboard.html
```

**Lightweight dashboard** (runs on the same port - use one server at a time):

```sh
python3 dashboard_server.py
# https://localhost:1978/flightsdashboard.html
```

## Configuration

| Env var | Default | Used by |
|---------|---------|---------|
| `DUMP1090_DIR` | `~/dump1090-web` | all three |
| `RECEIVER_LAT` | `0.0` | `api_server.py` |
| `RECEIVER_LON` | `0.0` | `api_server.py` |

Other tunables (thresholds, range rings, session gaps) live in the `CONFIG`
dict near the top of `api_server.py`.

## Notes

- `localhost.crt` / `localhost.key` and the generated `flight_log.*` data files
  are intentionally git-ignored. Regenerate the cert with `./make-cert.sh`.
- The dashboards load React, Recharts and OpenLayers from public CDNs, so the
  browser needs internet access even though the data stays local.

## License

Apache License 2.0 - see [LICENSE](LICENSE).
