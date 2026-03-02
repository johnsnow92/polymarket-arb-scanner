"""Lightweight HTTP dashboard for scanner status.

Serves a single-page trading dashboard at GET / and JSON API endpoints
for positions, trades, opportunities, P&L history, and system health.
Optional HTTP Basic Auth when DASHBOARD_PASS is set.
"""

import base64
import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger(__name__)

# Module start time for uptime calculation
_start_time = time.monotonic()


# ---------------------------------------------------------------------------
# Shared scanner state (updated by cli.py / continuous.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Kill switch — runtime pause/resume (thread-safe)
# ---------------------------------------------------------------------------

_pause_lock = threading.Lock()
_paused = False
_pause_reason = ""
_pause_timestamp: float | None = None


def is_paused() -> bool:
    """Check if the kill switch is engaged (trading paused)."""
    return _paused


def pause(reason: str = "manual") -> dict:
    """Engage the kill switch — stop all trade execution.

    Args:
        reason: Human-readable reason for pausing (logged and returned).

    Returns:
        Dict with pause state info.
    """
    global _paused, _pause_reason, _pause_timestamp
    with _pause_lock:
        _paused = True
        _pause_reason = reason
        _pause_timestamp = time.time()
        logger.warning("KILL SWITCH ENGAGED: trading paused (%s)", reason)
    return get_pause_state()


def resume() -> dict:
    """Disengage the kill switch — allow trade execution to continue.

    Returns:
        Dict with pause state info.
    """
    global _paused, _pause_reason, _pause_timestamp
    with _pause_lock:
        was_paused = _paused
        _paused = False
        _pause_reason = ""
        _pause_timestamp = None
        if was_paused:
            logger.warning("KILL SWITCH DISENGAGED: trading resumed")
    return get_pause_state()


def get_pause_state() -> dict:
    """Return current kill switch state as a dict."""
    return {
        "paused": _paused,
        "reason": _pause_reason,
        "paused_since": _pause_timestamp,
    }


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_auth(handler) -> bool:
    """Verify HTTP Basic Auth credentials if DASHBOARD_PASS is set.

    Returns True if auth passes (or auth is disabled). Sends 401 and
    returns False if auth fails.
    """
    from config import DASHBOARD_USER, DASHBOARD_PASS

    if not DASHBOARD_PASS:
        return True  # Auth disabled

    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        _send_401(handler)
        return False

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        user, pwd = decoded.split(":", 1)
    except Exception:
        _send_401(handler)
        return False

    if user == DASHBOARD_USER and pwd == DASHBOARD_PASS:
        return True

    _send_401(handler)
    return False


def _send_401(handler):
    """Send a 401 Unauthorized response with WWW-Authenticate header."""
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="Arb Scanner Dashboard"')
    handler.send_header("Content-Type", "text/plain")
    body = b"Unauthorized"
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _send_json(handler, data, status: int = 200):
    """Send a JSON response."""
    body = json.dumps(data, indent=2, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_html(handler, html: str, status: int = 200):
    """Send an HTML response."""
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


# ---------------------------------------------------------------------------
# Database helper (lazy import to avoid circular deps)
# ---------------------------------------------------------------------------

def _get_db():
    """Get a TradeDB instance. Returns None on error."""
    try:
        from db import TradeDB
        return TradeDB()
    except Exception as e:
        logger.debug("Error creating TradeDB for dashboard: %s", e)
        return None


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    """HTTP handler for dashboard UI and JSON API endpoints."""

    def do_GET(self):
        path = self.path.split("?")[0]  # Strip query string

        # Health check endpoint — no auth required (for ECS/ALB probes)
        if path == "/healthz":
            _send_json(self, {"status": "ok"})
            return

        if not _check_auth(self):
            return

        # Route dispatch
        routes = {
            "/": self._handle_dashboard,
            "/dashboard": self._handle_dashboard,
            "/status": self._handle_status,
            "/metrics": self._handle_metrics,
            "/alerts": self._handle_alerts,
            "/api/health": self._handle_health,
            "/api/positions": self._handle_positions,
            "/api/platforms": self._handle_platforms,
            "/api/trades": self._handle_trades,
            "/api/opportunities": self._handle_opportunities,
            "/api/strategies": self._handle_strategies,
            "/api/history": self._handle_history,
            "/api/slippage": self._handle_slippage,
            "/api/pause": self._handle_pause_get,
            "/api/db-stats": self._handle_db_stats,
        }

        handler_fn = routes.get(path)
        if handler_fn:
            try:
                handler_fn()
            except Exception as e:
                logger.warning("Dashboard handler error on %s: %s", path, e)
                _send_json(self, {"error": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]

        # Read the full request body upfront to avoid connection resets
        post_body = b""
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            if content_len > 0:
                post_body = self.rfile.read(content_len)
        except Exception:
            pass

        if not _check_auth(self):
            return

        post_routes = {
            "/api/pause": self._handle_pause_post,
            "/api/resume": self._handle_resume_post,
            "/api/purge": self._handle_purge_post,
        }

        handler_fn = post_routes.get(path)
        if handler_fn:
            try:
                handler_fn(post_body)
            except Exception as e:
                logger.warning("Dashboard POST handler error on %s: %s", path, e)
                _send_json(self, {"error": str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()

    # -------------------------------------------------------------------
    # Existing endpoints (preserved)
    # -------------------------------------------------------------------

    def _handle_dashboard(self):
        """Serve the single-page HTML dashboard."""
        from config import DASHBOARD_REFRESH_SECONDS
        from dashboard_ui import get_dashboard_html
        html = get_dashboard_html(refresh_seconds=DASHBOARD_REFRESH_SECONDS)
        _send_html(self, html)

    def _handle_status(self):
        """Scanner state JSON (existing endpoint, preserved for compatibility)."""
        _send_json(self, state.to_dict())

    def _handle_metrics(self):
        """Prometheus text format metrics."""
        try:
            from metrics import metrics
            body = metrics.get_prometheus_text().encode("utf-8")
        except Exception as e:
            logger.debug("Error loading metrics: %s", e)
            body = b"# metrics unavailable\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_alerts(self):
        """Recent alerts as JSON."""
        try:
            from alerting import alert_manager
            alerts = alert_manager.get_recent_alerts(50)
        except Exception as e:
            logger.debug("Error loading alerts: %s", e)
            alerts = []
        _send_json(self, alerts)

    # -------------------------------------------------------------------
    # New API endpoints
    # -------------------------------------------------------------------

    def _handle_health(self):
        """System health: mode, uptime, metrics, config summary."""
        from config import DRY_RUN, EXECUTION_MODE, MAX_TRADE_SIZE, BASE_TRADE_SIZE

        uptime = time.monotonic() - _start_time

        metrics_data = {}
        try:
            from metrics import metrics
            metrics_data = metrics.get_all()
        except Exception:
            pass

        cumulative = 0.0
        db = _get_db()
        if db:
            try:
                cumulative = db.get_cumulative_pnl()
            except Exception:
                pass

        _send_json(self, {
            "dry_run": DRY_RUN,
            "execution_mode": EXECUTION_MODE,
            "max_trade_size": MAX_TRADE_SIZE,
            "base_trade_size": BASE_TRADE_SIZE,
            "uptime_seconds": round(uptime, 1),
            "cumulative_pnl": cumulative,
            "metrics": metrics_data,
            "paused": _paused,
        })

    def _handle_positions(self):
        """Open positions with trade details."""
        db = _get_db()
        if not db:
            _send_json(self, [])
            return
        try:
            positions = db.get_open_positions()
        except Exception:
            positions = []
        _send_json(self, positions)

    def _handle_platforms(self):
        """Open positions grouped by platform."""
        db = _get_db()
        if not db:
            _send_json(self, [])
            return
        try:
            platforms = db.get_positions_by_platform()
        except Exception:
            platforms = []
        _send_json(self, platforms)

    def _handle_trades(self):
        """Recent trades with opportunity context."""
        db = _get_db()
        if not db:
            _send_json(self, [])
            return
        try:
            trades = db.get_recent_trades(limit=100)
        except Exception:
            trades = []
        _send_json(self, trades)

    def _handle_opportunities(self):
        """Recent opportunities."""
        db = _get_db()
        if not db:
            _send_json(self, [])
            return
        try:
            opps = db.get_recent_opportunities(limit=100)
        except Exception:
            opps = []
        _send_json(self, opps)

    def _handle_strategies(self):
        """Opportunity statistics grouped by strategy type."""
        db = _get_db()
        if not db:
            _send_json(self, [])
            return
        try:
            stats = db.get_opportunity_stats_by_type()
        except Exception:
            stats = []
        _send_json(self, stats)

    def _handle_history(self):
        """Daily P&L history for charting (last 30 days)."""
        db = _get_db()
        if not db:
            _send_json(self, [])
            return
        try:
            history = db.get_daily_pnl_history(days=30)
        except Exception:
            history = []
        _send_json(self, history)

    def _handle_slippage(self):
        """Average slippage across all trades."""
        db = _get_db()
        if not db:
            _send_json(self, {"avg_slippage": 0.0})
            return
        try:
            avg = db.get_avg_slippage()
        except Exception:
            avg = 0.0
        _send_json(self, {"avg_slippage": avg})

    # -------------------------------------------------------------------
    # Kill switch endpoints
    # -------------------------------------------------------------------

    def _handle_db_stats(self):
        """GET /api/db-stats — row counts for all tables."""
        db = _get_db()
        if not db:
            _send_json(self, {"error": "database unavailable"}, 500)
            return
        try:
            stats = db.get_db_stats()
        except Exception as e:
            _send_json(self, {"error": str(e)}, 500)
            return
        _send_json(self, stats)

    def _handle_pause_get(self):
        """GET /api/pause — return current kill switch state."""
        _send_json(self, get_pause_state())

    def _handle_pause_post(self, body: bytes = b""):
        """POST /api/pause — engage the kill switch."""
        reason = "dashboard"
        try:
            if body:
                parsed = json.loads(body)
                reason = parsed.get("reason", "dashboard")
        except Exception:
            pass
        _send_json(self, pause(reason))

    def _handle_resume_post(self, body: bytes = b""):
        """POST /api/resume — disengage the kill switch."""
        _send_json(self, resume())

    def _handle_purge_post(self, body: bytes = b""):
        """POST /api/purge — delete all opportunities/trades for a given type.

        Request body: {"type": "SpreadKalshi"}
        """
        try:
            if not body:
                _send_json(self, {"error": "Request body required with 'type' field"}, 400)
                return
            parsed = json.loads(body)
            opp_type = parsed.get("type", "")
            if not opp_type:
                _send_json(self, {"error": "'type' field is required"}, 400)
                return
        except json.JSONDecodeError:
            _send_json(self, {"error": "Invalid JSON"}, 400)
            return

        db = _get_db()
        if not db:
            _send_json(self, {"error": "database unavailable"}, 500)
            return
        try:
            result = db.purge_opportunities_by_type(opp_type)
            _send_json(self, {"purged": result, "type": opp_type})
        except Exception as e:
            _send_json(self, {"error": str(e)}, 500)

    # -------------------------------------------------------------------

    def log_message(self, format, *args):
        # Suppress default stderr logging from BaseHTTPRequestHandler
        logger.debug("Dashboard request: %s", args[0] if args else "")


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

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
        logger.info("Dashboard running on http://0.0.0.0:%d", port)
        return server
    except OSError as e:
        logger.warning("Failed to start dashboard on port %d: %s", port, e)
        return None
