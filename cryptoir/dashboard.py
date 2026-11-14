"""Loopback-only read-only dashboard; no remote hosting or trading endpoints."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import time

from .domain import DataError
from .monitor import dashboard_snapshot
from .streams import dumps

ASSETS = {"/": ("index.html", "text/html"), "/dashboard.css": ("dashboard.css", "text/css"),
          "/dashboard.js": ("dashboard.js", "text/javascript")}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, directory: Path, port: int):
        if type(port) is not int or not 0 <= port <= 65535:
            raise DataError("invalid local dashboard port")
        if not (directory/"status.json").is_file():
            raise DataError("run a paper session before opening its dashboard")
        self.directory = directory
        super().__init__(("127.0.0.1", port), DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "cryptoIR"

    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, *_):
        pass  # Do not log requests, local paths or financial response content.

    def reply(self, code, body, content_type="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", content_type+"; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host")
        # A link may navigate into the HTML shell. Financial JSON and assets still
        # require same-origin requests; this does not grant cross-origin API access.
        navigation = (self.path == "/" and self.headers.get("Sec-Fetch-Mode") == "navigate"
                      and self.headers.get("Sec-Fetch-Dest") == "document")
        if (host not in hosts or self.headers.get("Origin") not in (None, "http://"+host)
                or (not navigation and self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"))):
            self.reply(403, b"Local same-origin access required.")
            return
        if self.path == "/api/status":
            try:
                body = dumps(dashboard_snapshot(self.server.directory)).encode()
            except (OSError, ValueError, KeyError, TypeError):
                self.reply(503, b'{"error":"paper_status_unavailable"}', "application/json")
                return
            self.reply(200, body, "application/json")
        elif self.path in ASSETS:
            name, kind = ASSETS[self.path]
            self.reply(200, (Path(__file__).parent/"web"/name).read_bytes(), kind)
        else:
            self.reply(404, b"Not found.")

    def do_POST(self):
        self.reply(405, b"Read-only dashboard.")


def serve_dashboard(directory: Path, *, port=8765, seconds=3600):
    if type(seconds) is not int or not 5 <= seconds <= 86400:
        raise DataError("dashboard duration must be 5..86400 seconds")
    with DashboardServer(directory, port) as server:
        server.timeout = 0.5
        print(f"cryptoIR paper dashboard: http://127.0.0.1:{server.server_address[1]}/", flush=True)
        end = time.monotonic()+seconds
        try:
            while time.monotonic() < end:
                server.handle_request()
        except KeyboardInterrupt:
            pass
