#!/usr/bin/env python3
"""Serve the flight dashboard and its live CSV over local HTTPS."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import ssl

PROJECT_DIR = Path(__file__).resolve().parent
DUMP1090_DIR = Path(os.environ.get("DUMP1090_DIR", Path.home() / "dump1090-web")).expanduser()
CSV_FILE = DUMP1090_DIR / "flight_log.csv"
CERT_FILE = PROJECT_DIR / "localhost.crt"
KEY_FILE = PROJECT_DIR / "localhost.key"


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_DIR), **kwargs)

    def translate_path(self, path):
        if path.split("?", 1)[0] == "/flight_log.csv":
            return str(CSV_FILE)
        if path.split("?", 1)[0] == "/flightsdashboard.html":
            return str(PROJECT_DIR / "dashboard.html")
        return super().translate_path(path)


server = ThreadingHTTPServer(("localhost", 1978), DashboardHandler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(CERT_FILE, KEY_FILE)
server.socket = context.wrap_socket(server.socket, server_side=True)

print("Dashboard available at https://localhost:1978/flightsdashboard.html")
server.serve_forever()
