#!/usr/bin/env python3
"""
Flight Tracker API + dashboard server.

Runs entirely offline. Aircraft type data comes from the local dump1090
database (public_html/db), not from any web API.

Install once:
    pip install flask --break-system-packages

Run:
    python3 api_server.py

View on this Mac:   http://localhost:5050/
View on your TV:    http://<mac-mini-ip>:5050/   (printed on startup)
"""

import csv
import json
import os
import socket
import sqlite3
import ssl
from collections import defaultdict
from datetime import datetime
from math import radians, sin, cos, asin, sqrt, atan2, degrees

from flask import Flask, jsonify, request, send_from_directory

# ---------------------------------------------------------------- config

BASE_DIR = os.path.expanduser(os.environ.get("DUMP1090_DIR", "~/dump1090-web"))
CSV_PATH = os.path.join(BASE_DIR, "flight_log.csv")
DB_PATH = os.path.join(BASE_DIR, "flight_log.sqlite3")
DB_DIR = os.path.join(BASE_DIR, "public_html", "db")
GEOJSON_DIR = os.path.join(BASE_DIR, "public_html", "geojson")
OL_DIR = os.path.join(BASE_DIR, "public_html", "ol")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 1978
CERT_FILE = os.path.join(APP_DIR, "localhost.crt")
KEY_FILE = os.path.join(APP_DIR, "localhost.key")

# Your receiver's position, used for range/bearing math and the map centre.
# Match these to the --lat/--lon you pass to dump1090. Set via environment:
#   export RECEIVER_LAT=xx.xxxx RECEIVER_LON=-yy.yyyy
RECEIVER_LAT = float(os.environ.get("RECEIVER_LAT", "0.0"))
RECEIVER_LON = float(os.environ.get("RECEIVER_LON", "0.0"))

LIVE_THRESHOLD_SECONDS = 30

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

# ------------------------------------------------- callsign classification

AIRLINE_PREFIXES = {
    "AAL": "American Airlines", "DAL": "Delta Air Lines", "UAL": "United Airlines",
    "SWA": "Southwest Airlines", "JBU": "JetBlue", "ASA": "Alaska Airlines",
    "SKW": "SkyWest", "ENY": "Envoy Air", "RPA": "Republic Airways",
    "SCX": "Sun Country", "NKS": "Spirit Airlines", "FFT": "Frontier Airlines",
    "AAY": "Allegiant Air", "ACA": "Air Canada", "BAW": "British Airways",
    "DLH": "Lufthansa", "AFR": "Air France", "KLM": "KLM",
    "QTR": "Qatar Airways", "UAE": "Emirates", "JIA": "PSA Airlines",
    "EDV": "Endeavor Air", "GJS": "GoJet Airlines", "ASH": "Mesa Airlines",
    "QXE": "Horizon Air", "AWI": "Air Wisconsin", "PDT": "Piedmont Airlines",
    "BRZ": "Breeze Airways", "HAL": "Hawaiian Airlines", "MXY": "Breeze Airways",
    "VOI": "Volaris", "AMX": "Aeromexico", "WJA": "WestJet",
}
CARGO_PREFIXES = {
    "FDX": "FedEx Express", "UPS": "UPS Airlines", "GTI": "Atlas Air",
    "ABX": "ABX Air", "CLX": "Cargolux", "PAC": "Polar Air Cargo",
    "AJT": "Amerijet", "CKS": "Kalitta Air", "MTN": "Mountain Air Cargo",
    "BOX": "AeroLogic", "GEC": "Lufthansa Cargo", "SWN": "West Air",
}
MIL_PREFIXES = {
    "RCH": "US Air Force (AMC)", "CNV": "US Navy", "SAM": "Special Air Mission",
    "PAT": "US Army Priority Air Transport", "EVAC": "Aeromedical Evacuation",
    "SPAR": "USAF Special Air Resources", "DOOM": "USAF Airborne Command",
}

# Common ICAO type codes -> readable names. The local db gives us the code
# (e.g. "C172"); this turns it into something worth reading on a TV.
TYPE_NAMES = {
    "A319": "Airbus A319", "A320": "Airbus A320", "A321": "Airbus A321",
    "A20N": "Airbus A320neo", "A21N": "Airbus A321neo", "A332": "Airbus A330-200",
    "A333": "Airbus A330-300", "A339": "Airbus A330-900neo", "A343": "Airbus A340-300",
    "A359": "Airbus A350-900", "A35K": "Airbus A350-1000", "A388": "Airbus A380-800",
    "B712": "Boeing 717-200", "B733": "Boeing 737-300", "B735": "Boeing 737-500",
    "B736": "Boeing 737-600", "B737": "Boeing 737-700", "B738": "Boeing 737-800",
    "B739": "Boeing 737-900", "B38M": "Boeing 737 MAX 8", "B39M": "Boeing 737 MAX 9",
    "B744": "Boeing 747-400", "B748": "Boeing 747-8", "B752": "Boeing 757-200",
    "B753": "Boeing 757-300", "B762": "Boeing 767-200", "B763": "Boeing 767-300",
    "B764": "Boeing 767-400", "B772": "Boeing 777-200", "B77L": "Boeing 777-200LR",
    "B77W": "Boeing 777-300ER", "B788": "Boeing 787-8", "B789": "Boeing 787-9",
    "B78X": "Boeing 787-10",
    "E135": "Embraer ERJ-135", "E145": "Embraer ERJ-145", "E170": "Embraer E170",
    "E175": "Embraer E175", "E190": "Embraer E190", "E195": "Embraer E195",
    "E75L": "Embraer E175", "E55P": "Embraer Phenom 300", "E50P": "Embraer Phenom 100",
    "CRJ2": "Bombardier CRJ-200", "CRJ7": "Bombardier CRJ-700",
    "CRJ9": "Bombardier CRJ-900", "CRJX": "Bombardier CRJ-1000",
    "DH8A": "Dash 8-100", "DH8C": "Dash 8-300", "DH8D": "Dash 8-400",
    "AT72": "ATR 72", "AT76": "ATR 72-600",
    "C172": "Cessna 172 Skyhawk", "C152": "Cessna 152", "C182": "Cessna 182 Skylane",
    "C206": "Cessna 206 Stationair", "C208": "Cessna 208 Caravan", "C210": "Cessna 210",
    "C25A": "Cessna Citation CJ2", "C25B": "Cessna Citation CJ3",
    "C25C": "Cessna Citation CJ4", "C56X": "Cessna Citation Excel",
    "C550": "Cessna Citation II", "C680": "Cessna Citation Sovereign",
    "C68A": "Cessna Citation Latitude", "C700": "Cessna Citation Longitude",
    "C414": "Cessna 414", "C421": "Cessna 421", "C310": "Cessna 310",
    "C77R": "Cessna Cardinal RG", "C185": "Cessna 185 Skywagon",
    "C195": "Cessna 195", "T206": "Cessna T206 Turbo Stationair",
    "P28A": "Piper PA-28 Cherokee", "P28R": "Piper PA-28R Arrow",
    "P28B": "Piper PA-28 Cherokee", "PA32": "Piper PA-32 Cherokee Six",
    "PA34": "Piper PA-34 Seneca", "PA46": "Piper PA-46 Malibu",
    "PA18": "Piper PA-18 Super Cub", "PA24": "Piper Comanche",
    "BE20": "Beechcraft King Air 200", "BE9L": "Beechcraft King Air 90",
    "BE30": "Beechcraft King Air 350", "B350": "Beechcraft King Air 350",
    "BE33": "Beechcraft Debonair", "BE35": "Beechcraft Bonanza V-tail",
    "BE36": "Beechcraft Bonanza 36", "BE58": "Beechcraft Baron 58",
    "BE40": "Beechcraft Premier", "BT36": "Beechcraft Bonanza 36",
    "AEST": "Piper Aerostar",
    "SR20": "Cirrus SR20", "SR22": "Cirrus SR22", "S22T": "Cirrus SR22T",
    "SF50": "Cirrus Vision Jet",
    "DA40": "Diamond DA40", "DA42": "Diamond DA42", "DA62": "Diamond DA62",
    "M20P": "Mooney M20", "M600": "Piper M600", "AA5": "Grumman AA-5",
    "GLF4": "Gulfstream IV", "GLF5": "Gulfstream V", "GLF6": "Gulfstream G650",
    "GL7T": "Gulfstream G700", "G280": "Gulfstream G280",
    "CL30": "Bombardier Challenger 300", "CL35": "Bombardier Challenger 350",
    "CL60": "Bombardier Challenger 600", "GLEX": "Bombardier Global Express",
    "LJ35": "Learjet 35", "LJ45": "Learjet 45", "LJ60": "Learjet 60",
    "F2TH": "Dassault Falcon 2000", "FA7X": "Dassault Falcon 7X",
    "H25B": "Hawker 800", "PC12": "Pilatus PC-12", "PC24": "Pilatus PC-24",
    "TBM7": "Daher TBM 700", "TBM9": "Daher TBM 900",
    "HDJT": "Honda HA-420 HondaJet",
    "R44": "Robinson R44", "R66": "Robinson R66", "EC30": "Airbus H130",
    "EC35": "Airbus H135", "EC45": "Airbus H145", "AS50": "Airbus AS350",
    "B06": "Bell 206 JetRanger", "B407": "Bell 407", "B429": "Bell 429",
    "S76": "Sikorsky S-76", "A109": "Leonardo A109", "A139": "Leonardo AW139",
    "MD11": "McDonnell Douglas MD-11", "MD83": "McDonnell Douglas MD-83",
    "C130": "Lockheed C-130 Hercules", "C17": "Boeing C-17 Globemaster",
    "K35R": "Boeing KC-135 Stratotanker", "A10": "Fairchild A-10 Thunderbolt",
    "F16": "General Dynamics F-16", "F18": "Boeing F/A-18 Hornet",
    "T38": "Northrop T-38 Talon", "T6": "Beechcraft T-6 Texan II",
    "T28": "North American T-28 Trojan", "J3": "Piper J-3 Cub",
    "UH60": "Sikorsky UH-60 Black Hawk", "H60": "Sikorsky H-60",
    "ULAC": "Ultralight", "AT5T": "Air Tractor AT-500", "AT3T": "Air Tractor AT-300",
}

# ICAO descriptor: <category><engine count><engine type>
CAT_LETTER = {"L": "Landplane", "S": "Seaplane", "A": "Amphibian",
              "H": "Helicopter", "G": "Gyrocopter", "T": "Tiltrotor"}
ENGINE_LETTER = {"P": "piston", "T": "turboprop", "J": "jet",
                 "E": "electric", "R": "rocket"}
WTC_NAMES = {"L": "Light", "M": "Medium", "H": "Heavy", "J": "Super"}

_AIRCRAFT_DB = {}
_TYPE_DB = {}


def load_databases():
    """Load the local dump1090 aircraft database into memory once at startup."""
    global _AIRCRAFT_DB, _TYPE_DB

    types_path = os.path.join(DB_DIR, "aircraft_types", "icao_aircraft_types.json")
    if os.path.exists(types_path):
        try:
            with open(types_path) as f:
                _TYPE_DB = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    if not os.path.isdir(DB_DIR):
        print(f"  ! aircraft db not found at {DB_DIR} - type lookup disabled")
        return

    count = 0
    for name in sorted(os.listdir(DB_DIR)):
        if not name.endswith(".json"):
            continue
        prefix = name[:-5].upper()
        try:
            with open(os.path.join(DB_DIR, name)) as f:
                chunk = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for suffix, info in chunk.items():
            if isinstance(info, dict):
                _AIRCRAFT_DB[(prefix + suffix).upper()] = info
                count += 1
    print(f"  loaded {count:,} aircraft records, {len(_TYPE_DB):,} type definitions")


def decode_desc(desc):
    """'L2J' -> 'Landplane, 2 jet engines'."""
    if not desc or len(desc) < 3:
        return ""
    cat = CAT_LETTER.get(desc[0], "")
    if not cat:
        return ""
    try:
        n = int(desc[1])
    except ValueError:
        return cat
    eng = ENGINE_LETTER.get(desc[2], "")
    return f"{cat}, {n} {eng} {'engine' if n == 1 else 'engines'}".strip()


def lookup_aircraft(hex_code):
    if not hex_code:
        return {"registration": "", "registeredOwner": "", "typeCode": "", "typeName": "", "descText": "", "wtc": ""}
    info = _AIRCRAFT_DB.get(hex_code.upper(), {})
    type_code = (info.get("t") or "").upper()
    desc = info.get("desc") or ""
    wtc = ""
    if type_code and type_code in _TYPE_DB:
        t = _TYPE_DB[type_code]
        desc = desc or t.get("desc", "")
        wtc = t.get("wtc", "")
    return {
        "registration": (info.get("r") or "").strip(),
        "registeredOwner": (info.get("owner") or info.get("o") or "").strip(),
        "typeCode": type_code,
        "typeName": TYPE_NAMES.get(type_code, ""),
        "descText": decode_desc(desc),
        "wtc": WTC_NAMES.get(wtc, ""),
    }


BUSINESS_JET_TYPES = {
    "C25A", "C25B", "C25C", "C56X", "C550", "C680", "C68A", "C700",
    "E50P", "E55P", "GLF4", "GLF5", "GLF6", "GL7T", "G280", "CL30",
    "CL35", "CL60", "GLEX", "LJ35", "LJ45", "LJ60", "F2TH", "FA7X",
    "H25B", "PC12", "PC24", "SF50", "HDJT",
}


def resolve_category(callsign, type_code):
    """Resolve category independently from operator identity."""
    flight = (callsign or "").strip().upper()
    if flight[:3] in CARGO_PREFIXES:
        return "cargo", "Cargo"
    if flight[:3] in AIRLINE_PREFIXES:
        return "airline", "Airline"
    if any(flight.startswith(prefix) for prefix in MIL_PREFIXES):
        return "military", "Military/Government"
    if type_code in BUSINESS_JET_TYPES:
        return "business", "Business Jet"
    if len(flight) > 1 and flight[0] == "N" and flight[1].isdigit():
        return "private", "Private / GA"
    return "unknown", "Unknown"


def classify(flight):
    f = (flight or "").strip().upper()
    if not f:
        return "unknown", "Unknown", ""
    p3 = f[:3]
    if p3 in AIRLINE_PREFIXES:
        return "airline", "Airline", AIRLINE_PREFIXES[p3]
    if p3 in CARGO_PREFIXES:
        return "cargo", "Cargo", CARGO_PREFIXES[p3]
    for pfx, name in MIL_PREFIXES.items():
        if f.startswith(pfx):
            return "military", "Military/Govt", name
    if len(f) > 1 and f[0] == "N" and f[1].isdigit():
        return "private", "Private/GA", ""
    return "unknown", "Unknown", ""


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


def read_rows(range_filter):
    if not os.path.exists(CSV_PATH) and not os.path.exists(DB_PATH):
        return []
    today = datetime.now().astimezone().date()
    rows = []
    payload_rows = []
    if os.path.exists(DB_PATH):
        with sqlite3.connect(DB_PATH) as db:
            if range_filter == "today":
                start = datetime.combine(today, datetime.min.time(), tzinfo=datetime.now().astimezone().tzinfo).timestamp()
                payload_rows = [json.loads(row[0]) for row in db.execute(
                    "SELECT payload FROM observations WHERE timestamp_epoch >= ? ORDER BY timestamp_epoch", (start,)
                )]
            else:
                payload_rows = [json.loads(row[0]) for row in db.execute(
                    "SELECT payload FROM observations ORDER BY timestamp_epoch"
                )]
    if not payload_rows and os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="") as fh:
            payload_rows = list(csv.DictReader(fh))
    for r in payload_rows:
            try:
                ts = datetime.fromisoformat(r["timestamp_utc"]).astimezone()
            except (KeyError, ValueError):
                continue
            if range_filter == "today" and ts.date() != today:
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

        if len(pts) > 1 and last["alt"] > 0 and last["spd"] > 0:
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
        "receiver": {"lat": RECEIVER_LAT, "lon": RECEIVER_LON},
        "kpis": {
            "unique_aircraft": len(aircraft),
            "live_count": sum(1 for a in aircraft if a["live"]),
            "total_sightings": len(rows),
            "max_range_nm": round(max((a["maxDist"] for a in aircraft), default=0), 1),
            "identified_types": len(type_counts),
            "climbing": climbing,
            "descending": descending,
            "messages_received": sum(int(_f(r.get("messages"))) for r in rows),
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
    }


# ------------------------------------------------------------------ routes

app = Flask(__name__)


@app.route("/api/data")
def api_data():
    rng = request.args.get("range", "today")
    return jsonify(build_response(rng if rng in ("today", "all") else "today"))


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
    return send_from_directory(APP_DIR, "app.html")


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    print("Flight Tracker")
    print(f"  csv: {CSV_PATH}")
    load_databases()
    print()
    print(f"  this Mac: https://localhost:{PORT}/flightsdashboard.html")
    print(f"  your TV:  https://{lan_ip()}:{PORT}/flightsdashboard.html")
    print()
    app.run(host="0.0.0.0", port=PORT, debug=False,
            ssl_context=(CERT_FILE, KEY_FILE), threaded=True)
