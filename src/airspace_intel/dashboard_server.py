#!/usr/bin/env python3
"""Serve the lightweight chart dashboard and the live CSV over local HTTPS.

Run:
    airspace-dashboard              # after `pip install -e .`
    python -m airspace_intel.dashboard_server

This is the minimal alternative to ``airspace-api``; it only needs the CSV
log, not Flask or the dump1090 aircraft database. Both servers listen on the
same port, so run one at a time.

``GET /flight_log.csv`` returns the full history: every ``data/flight_log*.csv``
part concatenated (oldest rotated part first, active file last) with the
header row emitted once.
"""

import shutil
import ssl
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .config import CERT_FILE, CSV_PATH, KEY_FILE, PORT, STATIC_DIR


def csv_parts():
    """Every flight-log CSV in chronological order (rotated parts, then active)."""
    return sorted(CSV_PATH.parent.glob("flight_log*.csv"))


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def translate_path(self, path):
        clean = path.split("?", 1)[0]
        if clean in ("/", "/flightsdashboard.html"):
            return str(STATIC_DIR / "dashboard.html")
        return super().translate_path(path)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/flight_log.csv":
            self._serve_full_log()
            return
        super().do_GET()

    def _serve_full_log(self):
        parts = csv_parts()
        if not parts:
            self.send_error(404, "no flight log yet - is the logger running?")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        header_written = False
        for part in parts:
            with open(part, "rb") as fh:
                first_line = fh.readline()
                if not header_written:
                    self.wfile.write(first_line)
                    header_written = True
                # subsequent parts repeat the same header - skip it
                shutil.copyfileobj(fh, self.wfile)


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
