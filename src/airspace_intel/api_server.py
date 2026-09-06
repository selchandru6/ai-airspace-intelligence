#!/usr/bin/env python3
"""Flight Tracker API + dashboard server.

Runs entirely offline. Aircraft type data comes from the local dump1090
database (public_html/db), not from any web API.

Run:
    airspace-api                    # after `pip install -e .`
    python -m airspace_intel.api_server

Configuration comes from the environment / .env - see airspace_intel.config.
"""

import csv
import json
import os
import socket
import sqlite3
from collections import defaultdict
from datetime import datetime
from math import radians, sin, cos, asin, sqrt, atan2, degrees

from flask import Flask, jsonify, request, send_from_directory

from .config import (
    CONFIG,
    CERT_FILE,
    CSV_PATH,
    DB_PATH,
    GEOJSON_DIR,
    HOST,
    KEY_FILE,
    LIVE_THRESHOLD_SECONDS,
    MAX_ROWS,
    OL_DIR,
    PORT,
    RECEIVER_CONFIGURED,
    RECEIVER_LAT,
    RECEIVER_LON,
    STATIC_DIR,
    VALID_RANGES,
)
from .classify import (
    classify,
    load_databases,
    lookup_aircraft,
    resolve_category,
)


def haversine_nm(lat1, lon1, lat2, lon2):
    r = 3440.065
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _f(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def bearing_deg(lat1, lon1, lat2, lon2):
    """Return the initial bearing from the receiver to an aircraft."""
    y = sin(radians(lon2 - lon1)) * cos(radians(lat2))
    x = (cos(radians(lat1)) * sin(radians(lat2))
         - sin(radians(lat1)) * cos(radians(lat2)) * cos(radians(lon2 - lon1)))
    return (degrees(atan2(y, x)) + 360) % 360


def flight_state(baro_rate, geom_rate=0):
    rate = baro_rate if abs(baro_rate) >= abs(geom_rate) else geom_rate
    if rate > CONFIG["vertical_rate_tolerance"]:
        return "climbing"
    if rate < -CONFIG["vertical_rate_tolerance"]:
        return "descending"
    return "level"


def quality_label(row):
    if row.get("seen_pos", 99) <= 1 and row.get("nic", 0) >= 8 and row.get("nac_p", 0) >= 8:
        return "Excellent"
    if row.get("seen_pos", 99) <= 3 and row.get("nic", 0) >= 5:
        return "Good"
    if row.get("seen_pos", 99) <= 10:
        return "Fair"
    return "Stale"


def squawk_alert(squawk, emergency, alert):
    code = str(squawk or "").strip()
    emergency = str(emergency or "").lower()
    if code == "7700" or emergency == "general":
        return {"level": "CRITICAL", "title": "MAYDAY / GENERAL EMERGENCY", "code": code}
    if code == "7500":
        return {"level": "CRITICAL", "title": "HIJACK / UNLAWFUL INTERFERENCE", "code": code}
    if code == "7600":
        return {"level": "WARNING", "title": "LOST COMMUNICATIONS", "code": code}
    if str(alert).lower() in {"1", "true"}:
        return {"level": "NOTICE", "title": "TRANSPONDER ALERT", "code": code}
    return None


def wind_vector(gs, track, tas, true_heading):
    if not gs or not tas or track is None or true_heading is None:
        return None
    track_rad, heading_rad = radians(track), radians(true_heading)
    u_w = gs * sin(track_rad) - tas * sin(heading_rad)
    v_w = gs * cos(track_rad) - tas * cos(heading_rad)
    speed = sqrt(u_w * u_w + v_w * v_w)
    direction = (degrees(atan2(-u_w, -v_w)) + 360) % 360
    return {"speed": round(speed, 1), "directionFrom": round(direction, 0)}


def derive_flight_sessions(rows):
    """Build observed operation sessions from one aircraft's receiver history."""
    by_hex = defaultdict(list)
    for row in rows:
        if row["hex"]:
            by_hex[row["hex"]].append(row)

    sessions = []
    for hex_code, points in by_hex.items():
        points.sort(key=lambda row: row["ts"])
        groups, current = [], []
        for point in points:
            if current and (point["ts"] - current[-1]["ts"]).total_seconds() > CONFIG["session_gap_minutes"] * 60:
                groups.append(current)
                current = []
            current.append(point)
        if current:
            groups.append(current)

        for group in groups:
            callsign = next((p["flight"] for p in reversed(group) if p["flight"]), "")
            airborne = [p for p in group if p["alt"] > CONFIG["ground_altitude_ft"] or p["spd"] > CONFIG["ground_speed_kt"]]
            if not airborne:
                continue
            first_airborne = airborne[0]
            last_airborne = airborne[-1]
            close_to_ground = [p for p in group if p.get("lat") is not None and p.get("lon") is not None and
                               p["alt"] <= CONFIG["ground_altitude_ft"] and p["spd"] <= CONFIG["ground_speed_kt"] and
                               haversine_nm(RECEIVER_LAT, RECEIVER_LON, p["lat"], p["lon"]) <= CONFIG["ground_distance_nm"]]
            departure = next((p for p in group if p["ts"] >= first_airborne["ts"]), None)
            arrival = close_to_ground[-1] if close_to_ground and close_to_ground[-1]["ts"] > first_airborne["ts"] else None
            sessions.append({
                "hex": hex_code,
                "flight": callsign,
                "started": group[0]["ts"].isoformat(),
                "ended": group[-1]["ts"].isoformat(),
                "departure": departure["ts"].isoformat() if departure else None,
                "arrival": arrival["ts"].isoformat() if arrival else None,
                "durationMinutes": round((last_airborne["ts"] - first_airborne["ts"]).total_seconds() / 60, 1),
                "observations": len(group),
                "status": "returned" if arrival else "observed airborne",
            })
    return sorted(sessions, key=lambda session: session["started"], reverse=True)[:100]


def relative_traffic_direction(distance, bearing_from_receiver, heading):
    """Classify movement relative to the receiver, not flight route intent."""
    if not distance or heading is None:
        return "unknown"
    toward_receiver = (bearing_from_receiver + 180) % 360
    difference = abs((heading - toward_receiver + 180) % 360 - 180)
    if difference <= 60:
        return "incoming"
    if difference >= 120:
        return "outgoing"
    return "crossing"


def range_start_epoch(range_filter):
    now = datetime.now().astimezone()
    if range_filter == "hour":
        return now.timestamp() - 3600
    if range_filter == "today":
        return datetime.combine(now.date(), datetime.min.time(), tzinfo=now.tzinfo).timestamp()
    if range_filter == "week":
        return now.timestamp() - 7 * 86400
    return None


def read_rows(range_filter):
    if not os.path.exists(CSV_PATH) and not os.path.exists(DB_PATH):
        return []
    start_epoch = range_start_epoch(range_filter)
    rows = []
    payload_rows = []
    if os.path.exists(DB_PATH):
        with sqlite3.connect(DB_PATH) as db:
            if start_epoch is None:
                payload_rows = [json.loads(row[0]) for row in db.execute(
                    "SELECT payload FROM observations ORDER BY timestamp_epoch DESC LIMIT ?",
                    (MAX_ROWS,),
                )]
                payload_rows.reverse()
            else:
                payload_rows = [json.loads(row[0]) for row in db.execute(
                    "SELECT payload FROM observations WHERE timestamp_epoch >= ? "
                    "ORDER BY timestamp_epoch DESC LIMIT ?",
                    (start_epoch, MAX_ROWS),
                )]
                payload_rows.reverse()
    if not payload_rows:
        # No database yet - fall back to the CSV log and every rotated part
        # (flight_log-20260906T210000Z.csv, ..., flight_log.csv) in order.
        for part in sorted(CSV_PATH.parent.glob("flight_log*.csv")):
            with open(part, newline="") as fh:
                payload_rows.extend(csv.DictReader(fh))
    for r in payload_rows:
            try:
                ts = datetime.fromisoformat(r["timestamp_utc"]).astimezone()
            except (KeyError, ValueError):
                continue
            if start_epoch is not None and ts.timestamp() < start_epoch:
                continue
            lat, lon = _f(r.get("lat")), _f(r.get("lon"))
            rssi_raw = r.get("rssi")
            rows.append({
                "ts": ts,
                "hex": (r.get("hex") or "").strip(),
                "flight": (r.get("flight") or "").strip(),
                "alt": _f(r.get("altitude_ft")),
                "alt_geom": _f(r.get("alt_geom")),
                "spd": _f(r.get("speed_kt")),
                "ias": _f(r.get("ias")),
                "tas": _f(r.get("tas")),
                "mach": _f(r.get("mach")),
                "hdg": _f(r.get("heading")),
                "true_heading": _f(r.get("true_heading")),
                "nav_alt_mcp": _f(r.get("nav_altitude_mcp")),
                "nav_alt_fms": _f(r.get("nav_altitude_fms")),
                "nav_heading": _f(r.get("nav_heading")),
                "nav_qnh": _f(r.get("nav_qnh")),
                "nav_modes": (r.get("nav_modes") or "").strip(),
                "baro_rate": _f(r.get("baro_rate")),
                "geom_rate": _f(r.get("geom_rate")),
                "squawk": (r.get("squawk") or "").strip(),
                "emergency": (r.get("emergency") or "").strip(),
                "category_code": (r.get("category") or "").strip(),
                "nic": _f(r.get("nic")),
                "nac_p": _f(r.get("nac_p")),
                "nac_v": _f(r.get("nac_v")),
                "sil": _f(r.get("sil")),
                "sda": _f(r.get("sda")),
                "gva": _f(r.get("gva")),
                "lat": lat if lat else None,
                "lon": lon if lon else None,
                "rssi": _f(rssi_raw) if rssi_raw else None,
                "seen": _f(r.get("seen")),
                "seen_pos": _f(r.get("seen_pos")),
                "messages": _f(r.get("messages")),
                "alert": (r.get("alert") or "").strip(),
                "spi": (r.get("spi") or "").strip(),
                })
    return rows


def build_events(rows):
    """
    Collapse raw 5-second polling rows into meaningful events.

    A line every 5 seconds repeating the same altitude is noise. Emit an
    event only when something happens: an aircraft is acquired, climbs or
    descends past a threshold, or is lost.
    """
    ALT_STEP = 500

    by_ac = defaultdict(list)
    for r in rows:
        by_ac[r["hex"]].append(r)

    events = []
    for hexid, pts in by_ac.items():
        pts.sort(key=lambda p: p["ts"])
        first, last = pts[0], pts[-1]
        callsign = next((p["flight"] for p in reversed(pts) if p["flight"]), "")
        tinfo = lookup_aircraft(hexid)
        cat = resolve_category(callsign, tinfo["typeCode"])[0]
        type_label = tinfo["typeName"] or tinfo["typeCode"]

        def mk(pt, kind, detail):
            return {
                "t": pt["ts"].isoformat(), "hex": hexid, "flight": callsign,
                "kind": kind, "category": cat, "type": type_label, "detail": detail,
            }

        if first["alt"] <= 0 or first["spd"] <= 0:
            continue
        events.append(mk(first, "acquired", f"{int(first['alt']):,} ft · {int(first['spd'])} kt"))

        last_alt = first["alt"]
        for p in pts[1:]:
            if p["alt"] > 0 and p["spd"] > 0 and last_alt > 0 and abs(p["alt"] - last_alt) >= ALT_STEP:
                kind = "climb" if p["alt"] > last_alt else "descend"
                events.append(mk(p, kind, f"{int(p['alt']):,} ft · {int(p['spd'])} kt"))
                last_alt = p["alt"]

        age = (datetime.now().astimezone() - last["ts"]).total_seconds()
        if len(pts) > 1 and last["alt"] > 0 and last["spd"] > 0 and age >= LIVE_THRESHOLD_SECONDS:
            mins = (last["ts"] - first["ts"]).total_seconds() / 60
            events.append(mk(last, "lost", f"tracked {mins:.1f} min · {len(pts)} hits"))

    events.sort(key=lambda e: e["t"], reverse=True)
    return events[:60]


def build_response(range_filter):
    rows = read_rows(range_filter)
    now = datetime.now().astimezone()

    hourly_counts = defaultdict(int)
    ac_map = {}

    for r in rows:
        hourly_counts[r["ts"].hour] += 1
        key = r["hex"] or "unknown"
        a = ac_map.setdefault(key, {
            "hex": key, "callsign": "", "first": r["ts"], "last": r["ts"],
            "maxAlt": 0.0, "maxSpd": 0.0, "points": 0, "track": [], "path": [],
            "bestRssi": None, "maxDist": 0.0, "lastPos": None, "lastHdg": 0.0,
            "latest": {},
        })
        a["points"] += 1
        a["first"] = min(a["first"], r["ts"])
        a["last"] = max(a["last"], r["ts"])
        a["maxAlt"] = max(a["maxAlt"], r["alt"])
        a["maxSpd"] = max(a["maxSpd"], r["spd"])
        a["latest"] = r
        if r["flight"]:
            a["callsign"] = r["flight"]
        if r["rssi"] is not None:
            a["bestRssi"] = r["rssi"] if a["bestRssi"] is None else max(a["bestRssi"], r["rssi"])
        if len(a["track"]) < 400:
            a["track"].append({"t": r["ts"].isoformat(), "alt": r["alt"], "spd": r["spd"]})
        if r["lat"] is not None:
            if len(a["path"]) < 500:
                a["path"].append({"lat": r["lat"], "lon": r["lon"], "alt": r["alt"]})
            a["lastPos"] = {"lat": r["lat"], "lon": r["lon"]}
            a["lastHdg"] = r["hdg"]
            a["lastDist"] = haversine_nm(RECEIVER_LAT, RECEIVER_LON, r["lat"], r["lon"])
            a["lastBearing"] = bearing_deg(RECEIVER_LAT, RECEIVER_LON, r["lat"], r["lon"])
            a["maxDist"] = max(a["maxDist"],
                               haversine_nm(RECEIVER_LAT, RECEIVER_LON, r["lat"], r["lon"]))

    cat_counts = defaultdict(int)
    type_counts = defaultdict(int)
    operator_counts = defaultdict(int)
    aircraft = []

    for a in ac_map.values():
        t = lookup_aircraft(a["hex"])
        cat, cat_label = resolve_category(a["callsign"], t["typeCode"])
        _, _, resolved_operator = classify(a["callsign"])
        operator = resolved_operator or None
        operator_label = operator or ("Private owner" if cat in {"private", "business"} else "Not identified")
        cat_counts[cat] += 1
        if t["typeCode"]:
            type_counts[t["typeName"] or t["typeCode"]] += 1
        if operator:
            operator_counts[operator] += 1
        else:
            operator_counts["Not identified"] += 1

        aircraft.append({
            "hex": a["hex"], "callsign": a["callsign"],
            "category": cat, "categoryLabel": cat_label, "operator": operator,
            "operatorLabel": operator_label,
            "registration": t["registration"] or (a["callsign"] if a["callsign"].startswith("N") else ""),
            "registeredOwner": t["registeredOwner"] or None,
            "typeCode": t["typeCode"], "typeName": t["typeName"],
            "descText": t["descText"], "wtc": t["wtc"],
            "first": a["first"].isoformat(), "last": a["last"].isoformat(),
            "maxAlt": a["maxAlt"], "maxSpd": a["maxSpd"], "points": a["points"],
            "maxDist": round(a["maxDist"], 1),
            "currentAlt": a["latest"].get("alt", 0),
            "currentSpd": a["latest"].get("spd", 0),
            "currentHdg": a.get("lastHdg", 0),
            "currentRate": a["latest"].get("baro_rate", 0) or a["latest"].get("geom_rate", 0),
            "live": (now - a["last"]).total_seconds() < LIVE_THRESHOLD_SECONDS,
            "track": a["track"], "path": a["path"],
            "lastPos": a["lastPos"], "lastHdg": a["lastHdg"],
            "bestRssi": a["bestRssi"],
            "distance": round(a.get("lastDist", a["maxDist"]), 1),
            "bearing": round(a.get("lastBearing", 0), 0),
            "state": flight_state(a["latest"].get("baro_rate", 0), a["latest"].get("geom_rate", 0)),
            "trafficDirection": relative_traffic_direction(
                a.get("lastDist", 0), a.get("lastBearing", 0), a.get("lastHdg")
            ),
            "quality": quality_label(a["latest"]),
            "telemetry": {
                "altGeom": a["latest"].get("alt_geom", 0),
                "ias": a["latest"].get("ias", 0),
                "tas": a["latest"].get("tas", 0),
                "mach": a["latest"].get("mach", 0),
                "baroRate": a["latest"].get("baro_rate", 0),
                "geomRate": a["latest"].get("geom_rate", 0),
                "squawk": a["latest"].get("squawk", ""),
                "emergency": a["latest"].get("emergency", ""),
                "categoryCode": a["latest"].get("category_code", ""),
                "nic": a["latest"].get("nic", 0),
                "nacP": a["latest"].get("nac_p", 0),
                "nacV": a["latest"].get("nac_v", 0),
                "sil": a["latest"].get("sil", 0),
                "sda": a["latest"].get("sda", 0),
                "gva": a["latest"].get("gva", 0),
                "seen": a["latest"].get("seen", 0),
                "seenPos": a["latest"].get("seen_pos", 0),
                "navAltMcp": a["latest"].get("nav_alt_mcp", 0),
                "navAltFms": a["latest"].get("nav_alt_fms", 0),
                "navHeading": a["latest"].get("nav_heading", 0),
                "navQnh": a["latest"].get("nav_qnh", 0),
                "navModes": a["latest"].get("nav_modes", ""),
            },
            "alert": squawk_alert(a["latest"].get("squawk"), a["latest"].get("emergency"), a["latest"].get("alert")),
            "wind": wind_vector(a["latest"].get("spd"), a["latest"].get("hdg"), a["latest"].get("tas"), a["latest"].get("true_heading")),
        })

    aircraft.sort(key=lambda x: x["last"], reverse=True)

    nearest = min((a for a in aircraft if a["distance"] > 0), key=lambda a: a["distance"], default=None)
    highest = max(aircraft, key=lambda a: a["maxAlt"], default=None)
    fastest = max(aircraft, key=lambda a: a["maxSpd"], default=None)
    climbing = sum(a["state"] == "climbing" for a in aircraft)
    descending = sum(a["state"] == "descending" for a in aircraft)
    incoming = sum(a["live"] and a["trafficDirection"] == "incoming" for a in aircraft)
    critical_alerts = [a for a in aircraft if a["alert"] and a["alert"]["level"] == "CRITICAL"]
    stale_count = sum(not a["live"] for a in aircraft)

    coverage = {"low": [0.0] * 72, "mid": [0.0] * 72, "high": [0.0] * 72}
    for r in rows:
        if r["lat"] is None or r["lon"] is None:
            continue
        distance = haversine_nm(RECEIVER_LAT, RECEIVER_LON, r["lat"], r["lon"])
        bearing = bearing_deg(RECEIVER_LAT, RECEIVER_LON, r["lat"], r["lon"])
        tier = "low" if r["alt"] < 10000 else "mid" if r["alt"] <= 25000 else "high"
        sector = int((bearing % 360) // 5)
        coverage[tier][sector] = max(coverage[tier][sector], round(distance, 1))

    hourly = [{"hour": h, "count": hourly_counts.get(h, 0)} for h in range(24)]
    rssi_vals = [a["bestRssi"] for a in aircraft if a["bestRssi"] is not None]
    durations = [
        (datetime.fromisoformat(a["last"]) - datetime.fromisoformat(a["first"])).total_seconds() / 60
        for a in aircraft
    ]

    return {
        "receiver": {
            "lat": RECEIVER_LAT,
            "lon": RECEIVER_LON,
            "configured": RECEIVER_CONFIGURED,
        },
        "kpis": {
            "unique_aircraft": len(aircraft),
            "live_count": sum(1 for a in aircraft if a["live"]),
            "total_sightings": len(rows),
            "max_range_nm": round(max((a["maxDist"] for a in aircraft), default=0), 1),
            "identified_types": len(type_counts),
            "climbing": climbing,
            "descending": descending,
            "incoming": incoming,
            "observations": len(rows),
            "critical_alerts": len(critical_alerts),
            "stale_count": stale_count,
        },
        "hourly": hourly,
        "categories": dict(cat_counts),
        "top_types": [{"name": n, "count": c}
                      for n, c in sorted(type_counts.items(), key=lambda kv: -kv[1])[:8]],
        "top_operators": [{"name": n, "count": c}
                          for n, c in sorted(operator_counts.items(), key=lambda kv: -kv[1])[:8]],
        "insights": {
            "busiest_hour": max(hourly, key=lambda h: h["count"])["hour"] if rows else None,
            "longest_track_minutes": round(max(durations), 1) if durations else 0,
            "best_rssi": round(max(rssi_vals), 1) if rssi_vals else None,
            "avg_altitude": round(sum(a["maxAlt"] for a in aircraft) / len(aircraft)) if aircraft else 0,
        },
        "leaderboard": {
            "nearest": nearest,
            "highest": highest,
            "fastest": fastest,
        },
        "coverage": [{"bearing": i * 5, "low": coverage["low"][i], "mid": coverage["mid"][i], "high": coverage["high"][i]} for i in range(72)],
        "sessions": derive_flight_sessions(rows),
        "aircraft": aircraft,
        "events": build_events(rows),
        "generated_at": now.isoformat(),
        "meta": {
            "range": range_filter,
            "rows_loaded": len(rows),
            "row_cap": MAX_ROWS,
            "row_cap_hit": len(rows) >= MAX_ROWS,
        },
    }


# ------------------------------------------------------------------ routes

app = Flask(__name__)


@app.route("/api/data")
def api_data():
    rng = request.args.get("range", "today")
    return jsonify(build_response(rng if rng in VALID_RANGES else "today"))


@app.route("/geojson/<path:name>")
def serve_geojson(name):
    return send_from_directory(GEOJSON_DIR, name)


@app.route("/ol/<path:name>")
def serve_ol(name):
    """OpenLayers, served from your local dump1090 checkout - no CDN needed."""
    return send_from_directory(OL_DIR, name)


@app.route("/")
@app.route("/flightsdashboard.html")
def index():
    return send_from_directory(STATIC_DIR, "app.html")


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        raise SystemExit(
            f"TLS certificate not found at {CERT_FILE}. "
            "Run scripts/make-cert.sh first."
        )
    print("Flight Tracker")
    print(f"  data: {CSV_PATH}")
    if not RECEIVER_CONFIGURED:
        print("  ! RECEIVER_LAT / RECEIVER_LON are unset - distances and the "
              "map centre will be wrong until you set them.")
    load_databases()
    print()
    print(f"  this Mac: https://localhost:{PORT}/flightsdashboard.html")
    print(f"  your TV:  https://{lan_ip()}:{PORT}/flightsdashboard.html")
    print()
    app.run(host=HOST, port=PORT, debug=False,
            ssl_context=(str(CERT_FILE), str(KEY_FILE)), threaded=True)


if __name__ == "__main__":
    main()
