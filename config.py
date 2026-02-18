"""Centralized configuration — all constants backed by environment variables."""

import logging
import os
import sys
from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.expanduser("~/.claude/.env"))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "")  # Empty = no file logging


def setup_logging(level: str | None = None, log_file: str | None = None):
    """Configure root logger with console and optional file handlers."""
    lvl = getattr(logging, (level or LOG_LEVEL), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]
    handlers[0].setLevel(lvl)

    file_path = log_file if log_file is not None else LOG_FILE
    if file_path:
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        handlers.append(fh)

    logging.basicConfig(level=logging.DEBUG, format=fmt, datefmt=datefmt,
                        handlers=handlers, force=True)


# Scanner defaults
DEFAULT_MIN_PROFIT = float(os.getenv("MIN_PROFIT_THRESHOLD", "0.005"))
FUZZY_MATCH_THRESHOLD = int(os.getenv("FUZZY_MATCH_THRESHOLD", "72"))
WS_SUBSCRIPTION_LIMIT = int(os.getenv("WS_SUBSCRIPTION_LIMIT", "2000"))
WS_TRIGGER_ENABLED = os.getenv("WS_TRIGGER_ENABLED", "true").lower() == "true"
WS_TRIGGER_THRESHOLD = float(os.getenv("WS_TRIGGER_THRESHOLD", "0.003"))
PARALLEL_WORKERS = int(os.getenv("PARALLEL_WORKERS", "4"))
RESCAN_INTERVAL = int(os.getenv("RESCAN_INTERVAL", "30"))
MAX_RESOLUTION_DAYS = int(os.getenv("MAX_RESOLUTION_DAYS", "7"))

# Kalshi fee parameters
KALSHI_FEE_CAP_CENTS = int(os.getenv("KALSHI_FEE_CAP_CENTS", "175"))

# Risk management
MAX_TRADE_SIZE = float(os.getenv("MAX_TRADE_SIZE", "5.0"))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "25.0"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "25"))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "10.0"))
MIN_LIQUIDITY_HIGH_ROI = float(os.getenv("MIN_LIQUIDITY_HIGH_ROI", "10.0"))
MIN_NET_ROI = float(os.getenv("MIN_NET_ROI", "0"))
ALLOW_BETTER_REENTRY = os.getenv("ALLOW_BETTER_REENTRY", "true").lower() == "true"
REENTRY_IMPROVEMENT_THRESHOLD = float(os.getenv("REENTRY_IMPROVEMENT_THRESHOLD", "0.20"))

# Dynamic sizing
DYNAMIC_SIZING_ENABLED = os.getenv("DYNAMIC_SIZING_ENABLED", "true").lower() == "true"
SIZING_AGGRESSIVENESS = float(os.getenv("SIZING_AGGRESSIVENESS", "0.5"))

# Execution
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "semi-auto")

# Polygon gas cost estimate (per transaction, in dollars)
POLYGON_GAS_ESTIMATE = float(os.getenv("POLYGON_GAS_ESTIMATE", "0.03"))

# Revalidation
REVALIDATION_MIN_FLOOR = float(os.getenv("REVALIDATION_MIN_FLOOR", "0.003"))
REVALIDATION_ADAPTIVE = os.getenv("REVALIDATION_ADAPTIVE", "true").lower() == "true"

# API rate limits (seconds between requests)
PM_RATE_LIMIT = float(os.getenv("PM_RATE_LIMIT", "0.01"))
KALSHI_RATE_LIMIT = float(os.getenv("KALSHI_RATE_LIMIT", "0.05"))

# Dust trade filter — minimum profit to execute (avoids wasting gas)
MIN_PROFIT_AMOUNT = float(os.getenv("MIN_PROFIT_AMOUNT", "0.05"))

# Fill polling (Polymarket only; Kalshi FOK fills instantly)
FILL_POLL_INTERVAL = float(os.getenv("FILL_POLL_INTERVAL", "0.1"))
FILL_POLL_TIMEOUT = float(os.getenv("FILL_POLL_TIMEOUT", "2.0"))

# Partial fill hedging
HEDGE_ENABLED = os.getenv("HEDGE_ENABLED", "true").lower() == "true"
HEDGE_MAX_ATTEMPTS = int(os.getenv("HEDGE_MAX_ATTEMPTS", "5"))
HEDGE_MAX_SPREAD_LOSS_PCT = float(os.getenv("HEDGE_MAX_SPREAD_LOSS_PCT", "0.15"))

# Betfair commission rate (2-5%, default 5% for new users)
BETFAIR_COMMISSION_RATE = float(os.getenv("BETFAIR_COMMISSION_RATE", "0.05"))

# Smarkets commission rate (fixed 2% for most users)
SMARKETS_COMMISSION_RATE = float(os.getenv("SMARKETS_COMMISSION_RATE", "0.02"))

# Proxy configuration
POLYMARKET_PROXY_URL = os.getenv("POLYMARKET_PROXY_URL")
KALSHI_PROXY_URL = os.getenv("KALSHI_PROXY_URL")

# Platform credentials (presence-checked, not stored)
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH")
BETFAIR_USERNAME = os.getenv("BETFAIR_USERNAME")
BETFAIR_PASSWORD = os.getenv("BETFAIR_PASSWORD")
BETFAIR_API_KEY = os.getenv("BETFAIR_API_KEY")

# Smarkets
SMARKETS_API_KEY = os.getenv("SMARKETS_API_KEY")

# SX Bet
SXBET_API_KEY = os.getenv("SXBET_API_KEY")
SXBET_PRIVATE_KEY = os.getenv("SXBET_PRIVATE_KEY")

# Matchbook
MATCHBOOK_USERNAME = os.getenv("MATCHBOOK_USERNAME")
MATCHBOOK_PASSWORD = os.getenv("MATCHBOOK_PASSWORD")

# Metaculus (read-only signal source, works without API key)
METACULUS_API_KEY = os.getenv("METACULUS_API_KEY")

# Dynamic fee arbitrage (GasMonitor)
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
DYNAMIC_FEE_ENABLED = os.getenv("DYNAMIC_FEE_ENABLED", "false").lower() == "true"
GAS_PRICE_CACHE_TTL = float(os.getenv("GAS_PRICE_CACHE_TTL", "15.0"))

# Event-driven trading (Metaculus divergence signals)
EVENT_DIVERGENCE_THRESHOLD = float(os.getenv("EVENT_DIVERGENCE_THRESHOLD", "0.10"))
EVENT_MONITOR_ENABLED = os.getenv("EVENT_MONITOR_ENABLED", "false").lower() == "true"

# Notifications
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # Slack/Discord/generic URL
WEBHOOK_MIN_PROFIT = float(os.getenv("WEBHOOK_MIN_PROFIT", "0.01"))

# Data directory (for EFS mount in Fargate)
DATA_DIR = os.getenv("DATA_DIR", ".")

# Dashboard
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "0"))  # 0 = disabled
