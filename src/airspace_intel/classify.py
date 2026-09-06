"""Callsign and aircraft-type classification.

Everything here is backed by the local dump1090 aircraft database
(``public_html/db``) plus the static lookup tables below - no web API.
"""

import json
import os

from .config import DB_DIR

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

BUSINESS_JET_TYPES = {
    "C25A", "C25B", "C25C", "C56X", "C550", "C680", "C68A", "C700",
    "E50P", "E55P", "GLF4", "GLF5", "GLF6", "GL7T", "G280", "CL30",
    "CL35", "CL60", "GLEX", "LJ35", "LJ45", "LJ60", "F2TH", "FA7X",
    "H25B", "PC12", "PC24", "SF50", "HDJT",
}

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
