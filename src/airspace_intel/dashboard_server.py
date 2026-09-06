#!/usr/bin/env python3
"""Serve the lightweight chart dashboard and the live CSV over local HTTPS.

Run:
    airspace-dashboard              # after `pip install -e .`
    python -m airspace_intel.dashboard_server

This is the minimal alternative to ``airspace-api``; it only needs the CSV
log, not Flask or the dump1090 aircraft database. Both servers listen on the
same port, so run one at a time.
"""

import ssl
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .config import CERT_FILE, CSV_PATH, KEY_FILE, PORT, STATIC_DIR


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def translate_path(self, path):
        clean = path.split("?", 1)[0]
        if clean == "/flight_log.csv":
            return str(CSV_PATH)
        if clean in ("/", "/flightsdashboard.html"):
            return str(STATIC_DIR / "dashboard.html")
        return super().translate_path(path)


def main():
    if not CERT_FILE.exists() or not KEY_FILE.exists():
        raise SystemExit(
            f"TLS certificate not found at {CERT_FILE}. Run scripts/make-cert.sh first."
        )
    server = ThreadingHTTPServer(("localhost", PORT), DashboardHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"Dashboard available at https://localhost:{PORT}/flightsdashboard.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
