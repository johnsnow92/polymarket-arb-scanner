"""Lightweight HTTP dashboard for scanner status."""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger(__name__)


class _DashboardState:
    """Shared mutable state updated by the scanner loop."""

    def __init__(self):
        self.scan_count = 0
        self.last_scan_time = None
        self.open_positions = 0
        self.daily_pnl = 0.0
        self.ws_connections = 0
        self.opportunities_found = 0
        self.last_opportunities: list[dict] = []

    def to_dict(self) -> dict:
        return {
            "scan_count": self.scan_count,
            "last_scan_time": self.last_scan_time,
            "open_positions": self.open_positions,
            "daily_pnl": round(self.daily_pnl, 4),
            "ws_connections": self.ws_connections,
            "opportunities_found": self.opportunities_found,
            "last_opportunities": self.last_opportunities[:20],
        }


# Module-level singleton so scanner can update it and the server can read it
state = _DashboardState()


class _Handler(BaseHTTPRequestHandler):
    """Simple JSON endpoint handler."""

    def do_GET(self):
        if self.path in ("/", "/status"):
            body = json.dumps(state.to_dict(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default stderr logging from BaseHTTPRequestHandler
        logger.debug("Dashboard request: %s", args[0] if args else "")


def start_dashboard(port: int) -> HTTPServer | None:
    """Start the dashboard HTTP server on a background thread.

    Args:
        port: TCP port to listen on. If 0 or negative, returns None.

    Returns:
        The HTTPServer instance (call .shutdown() to stop) or None.
    """
    if port <= 0:
        return None

    try:
        server = HTTPServer(("0.0.0.0", port), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info("Dashboard running on http://0.0.0.0:%d/status", port)
        return server
    except OSError as e:
        logger.warning("Failed to start dashboard on port %d: %s", port, e)
        return None
