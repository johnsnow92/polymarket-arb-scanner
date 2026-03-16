"""Launch the dashboard server standalone (for local testing).

Usage:
    python run_dashboard.py

Starts the HTTP dashboard on the configured port and keeps running
until Ctrl+C. Set DASHBOARD_PORT, DASHBOARD_USER, DASHBOARD_PASS
as environment variables or they will use defaults below.
"""

import os
import sys
import time

# Set defaults if not already in environment
os.environ.setdefault("DASHBOARD_PORT", "8080")
os.environ.setdefault("DASHBOARD_USER", "admin")
# DASHBOARD_PASS must be set via env var — no hardcoded default
os.environ.setdefault("DASHBOARD_REFRESH_SECONDS", "15")

# Must set env vars BEFORE importing config (module-level reads them)
from config import DASHBOARD_PORT, DASHBOARD_USER, DASHBOARD_PASS, setup_logging
from dashboard import start_dashboard, state

setup_logging("INFO")

import logging
logger = logging.getLogger(__name__)


def main():
    logger.info("Starting dashboard on port %d", DASHBOARD_PORT)
    logger.info("Auth: user=%s, password=%s", DASHBOARD_USER, "*" * len(DASHBOARD_PASS))

    server = start_dashboard(DASHBOARD_PORT)
    if server is None:
        logger.error("Failed to start dashboard (port=%d)", DASHBOARD_PORT)
        sys.exit(1)

    # Seed some demo state so the dashboard isn't completely empty
    state.scan_count = 0
    state.daily_pnl = 0.0
    state.open_positions = 0
    state.opportunities_found = 0
    state.ws_connections = 0

    print(f"\n  Dashboard running at http://localhost:{DASHBOARD_PORT}/")
    print(f"  Login: {DASHBOARD_USER} / {'*' * len(DASHBOARD_PASS)}")
    print(f"  Press Ctrl+C to stop\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
