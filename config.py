"""Centralized configuration — all constants backed by environment variables."""

import logging
import os
import sys
from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.expanduser("~/.claude/.env"))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Environment variable helpers (safe parsing with clear error messages)
# ---------------------------------------------------------------------------

class ConfigError(ValueError):
    """Raised when an environment variable has an invalid value."""


def _env_float(name: str, default: str) -> float:
    """Read an env var as float, raising ConfigError on bad values."""
    raw = os.getenv(name, default)
    try:
        return float(raw)
    except (ValueError, TypeError):
        raise ConfigError(
            f"Environment variable {name}={raw!r} is not a valid float"
        )


def _env_int(name: str, default: str) -> int:
    """Read an env var as int, raising ConfigError on bad values."""
    raw = os.getenv(name, default)
    try:
        return int(raw)
    except (ValueError, TypeError):
        raise ConfigError(
            f"Environment variable {name}={raw!r} is not a valid integer"
        )


def _env_bool(name: str, default: str) -> bool:
    """Read an env var as bool (true/false), raising ConfigError on bad values."""
    raw = os.getenv(name, default).lower().strip()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    raise ConfigError(
        f"Environment variable {name}={raw!r} is not a valid boolean "
        f"(expected true/false, 1/0, or yes/no)"
    )


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
DEFAULT_MIN_PROFIT = _env_float("MIN_PROFIT_THRESHOLD", "0.005")
FUZZY_MATCH_THRESHOLD = _env_int("FUZZY_MATCH_THRESHOLD", "72")
WS_SUBSCRIPTION_LIMIT = _env_int("WS_SUBSCRIPTION_LIMIT", "2000")
WS_TRIGGER_ENABLED = _env_bool("WS_TRIGGER_ENABLED", "true")
WS_TRIGGER_THRESHOLD = _env_float("WS_TRIGGER_THRESHOLD", "0.03")
PARALLEL_WORKERS = _env_int("PARALLEL_WORKERS", "4")
RESCAN_INTERVAL = _env_int("RESCAN_INTERVAL", "30")
MAX_RESOLUTION_DAYS = _env_int("MAX_RESOLUTION_DAYS", "7")

# Kalshi fee parameters
KALSHI_FEE_CAP_CENTS = _env_int("KALSHI_FEE_CAP_CENTS", "175")

# Risk management
BASE_TRADE_SIZE = _env_float("BASE_TRADE_SIZE", "1.0")
MAX_TRADE_SIZE = _env_float("MAX_TRADE_SIZE", "3.0")
DAILY_LOSS_LIMIT = _env_float("DAILY_LOSS_LIMIT", "5.0")
MAX_OPEN_POSITIONS = _env_int("MAX_OPEN_POSITIONS", "10")
MIN_LIQUIDITY = _env_float("MIN_LIQUIDITY", "10.0")
MIN_LIQUIDITY_HIGH_ROI = _env_float("MIN_LIQUIDITY_HIGH_ROI", "5.0")
MIN_NET_ROI = _env_float("MIN_NET_ROI", "0")
ALLOW_BETTER_REENTRY = _env_bool("ALLOW_BETTER_REENTRY", "true")
REENTRY_IMPROVEMENT_THRESHOLD = _env_float("REENTRY_IMPROVEMENT_THRESHOLD", "0.20")

# Dynamic sizing
DYNAMIC_SIZING_ENABLED = _env_bool("DYNAMIC_SIZING_ENABLED", "true")
SIZING_AGGRESSIVENESS = _env_float("SIZING_AGGRESSIVENESS", "0.5")

# Kelly criterion position sizing
KELLY_FRACTION = _env_float("KELLY_FRACTION", "0.5")
KELLY_MAX_FRACTION = _env_float("KELLY_MAX_FRACTION", "0.25")

# Execution
DRY_RUN = _env_bool("DRY_RUN", "true")
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "semi-auto")

# Platform execution whitelist — only these platforms can place live orders.
# Comma-separated list of platform names. Platforms not listed here will still
# be scanned for price data but will never execute trades.
_VALID_PLATFORMS = frozenset([
    "polymarket", "kalshi", "betfair", "smarkets",
    "sxbet", "matchbook", "gemini", "ibkr",
])
_raw_enabled = os.getenv("ENABLED_EXECUTION_PLATFORMS", "polymarket,kalshi")
ENABLED_EXECUTION_PLATFORMS: frozenset[str] = frozenset(
    p.strip().lower() for p in _raw_enabled.split(",") if p.strip()
)

# Platform minimum order sizes (USD). Orders below these are rejected
# client-side to prevent API rejections and costly partial-fill hedging.
PLATFORM_MIN_ORDER_SIZE: dict[str, float] = {
    "polymarket": 0.01,
    "kalshi": 0.01,
    "sxbet": 1.00,
    "gemini": 0.01,
    "ibkr": 0.01,
    "betfair": 2.50,
    "smarkets": 6.25,
    "matchbook": 5.50,
}

# Polygon gas cost estimate (per transaction, in dollars)
POLYGON_GAS_ESTIMATE = _env_float("POLYGON_GAS_ESTIMATE", "0.005")

# Revalidation
REVALIDATION_MIN_FLOOR = _env_float("REVALIDATION_MIN_FLOOR", "0.003")
REVALIDATION_ADAPTIVE = _env_bool("REVALIDATION_ADAPTIVE", "true")

# API rate limits (seconds between requests)
PM_RATE_LIMIT = _env_float("PM_RATE_LIMIT", "0.01")
KALSHI_RATE_LIMIT = _env_float("KALSHI_RATE_LIMIT", "0.05")

# Dust trade filter — minimum profit to execute (avoids wasting gas)
MIN_PROFIT_AMOUNT = _env_float("MIN_PROFIT_AMOUNT", "0.02")

# Fill polling (Polymarket only; Kalshi FOK fills instantly)
FILL_POLL_INTERVAL = _env_float("FILL_POLL_INTERVAL", "0.1")
FILL_POLL_TIMEOUT = _env_float("FILL_POLL_TIMEOUT", "5.0")

# Partial fill hedging
HEDGE_ENABLED = _env_bool("HEDGE_ENABLED", "true")
HEDGE_MAX_ATTEMPTS = _env_int("HEDGE_MAX_ATTEMPTS", "5")
HEDGE_MAX_SPREAD_LOSS_PCT = _env_float("HEDGE_MAX_SPREAD_LOSS_PCT", "0.15")

# Betfair commission rate (2-5%, default 3% for moderate-volume users)
BETFAIR_COMMISSION_RATE = _env_float("BETFAIR_COMMISSION_RATE", "0.03")

# Betfair Streaming API (TLS TCP socket)
BETFAIR_STREAM_HOST = os.getenv("BETFAIR_STREAM_HOST", "stream-api.betfair.com")
BETFAIR_STREAM_PORT = _env_int("BETFAIR_STREAM_PORT", "443")

# Smarkets commission rate (fixed 2% for most users)
SMARKETS_COMMISSION_RATE = _env_float("SMARKETS_COMMISSION_RATE", "0.02")

# Proxy configuration
POLYMARKET_PROXY_URL = os.getenv("POLYMARKET_PROXY_URL")
KALSHI_PROXY_URL = os.getenv("KALSHI_PROXY_URL")
BETFAIR_PROXY_URL = os.getenv("BETFAIR_PROXY_URL")
SMARKETS_PROXY_URL = os.getenv("SMARKETS_PROXY_URL")
SXBET_PROXY_URL = os.getenv("SXBET_PROXY_URL")
MATCHBOOK_PROXY_URL = os.getenv("MATCHBOOK_PROXY_URL")
GEMINI_PROXY_URL = os.getenv("GEMINI_PROXY_URL")

# Platform credentials (presence-checked, not stored)
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
POLYMARKET_CHAIN_ID = _env_int("POLYMARKET_CHAIN_ID", "137")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS")
POLYMARKET_SIGNATURE_TYPE = _env_int("POLYMARKET_SIGNATURE_TYPE", "0")
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

# Gemini Predictions
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_SECRET = os.getenv("GEMINI_API_SECRET")
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://api.gemini.com")
GEMINI_FEE_RATE = _env_float("GEMINI_FEE_RATE", "0.05")  # 5% taker / 1% maker
GEMINI_ORDER_TYPE = os.getenv("GEMINI_ORDER_TYPE", "ioc")  # "ioc" or "gtc"
GEMINI_RATE_LIMIT = _env_float("GEMINI_RATE_LIMIT", "0.1")

# IBKR ForecastEx (via IB Gateway / TWS socket)
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = _env_int("IBKR_PORT", "4001")
IBKR_CLIENT_ID = _env_int("IBKR_CLIENT_ID", "1")
IBKR_ORDER_RATE_LIMIT = _env_float("IBKR_ORDER_RATE_LIMIT", "5.0")

# Metaculus (read-only signal source, works without API key)
METACULUS_API_KEY = os.getenv("METACULUS_API_KEY")
METACULUS_CACHE_TTL = _env_float("METACULUS_CACHE_TTL", "300")

# ---------------------------------------------------------------------------
# Feature flags — defaults are false for local dev safety.
# Enable in production via Railway env vars:
#   MM_ENABLED=true              — Market making engine
#   SNAPSHOT_ENABLED=true        — Price snapshot recording for backtesting
#   DYNAMIC_FEE_ENABLED=true     — Real-time Polygon gas price monitoring
#   EVENT_MONITOR_ENABLED=true   — Metaculus/Manifold signal aggregation
# ---------------------------------------------------------------------------

# Dynamic fee arbitrage (GasMonitor)
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
DYNAMIC_FEE_ENABLED = _env_bool("DYNAMIC_FEE_ENABLED", "false")
GAS_PRICE_CACHE_TTL = _env_float("GAS_PRICE_CACHE_TTL", "15.0")

# Event-driven trading (Metaculus divergence signals)
EVENT_DIVERGENCE_THRESHOLD = _env_float("EVENT_DIVERGENCE_THRESHOLD", "0.10")
EVENT_MONITOR_ENABLED = _env_bool("EVENT_MONITOR_ENABLED", "false")

# Stale price detection
STALE_PRICE_THRESHOLD = _env_float("STALE_PRICE_THRESHOLD", "30.0")
STALE_PRICE_MOVE_PCT = _env_float("STALE_PRICE_MOVE_PCT", "0.03")

# Market making
MM_ENABLED = _env_bool("MM_ENABLED", "false")
MM_MIN_SPREAD = _env_float("MM_MIN_SPREAD", "0.02")  # 2% minimum spread width
MM_QUOTE_SIZE = _env_float("MM_QUOTE_SIZE", "5.0")
MM_MAX_INVENTORY = _env_float("MM_MAX_INVENTORY", "500.0")  # $500 per market cap
MM_MAX_TOTAL_EXPOSURE = _env_float("MM_MAX_TOTAL_EXPOSURE", "500.0")
MM_REFRESH_INTERVAL = _env_float("MM_REFRESH_INTERVAL", "10.0")

# Convergence detection
CONVERGENCE_MIN_DIVERGENCE = _env_float("CONVERGENCE_MIN_DIVERGENCE", "0.05")
CONVERGENCE_MIN_PLATFORMS = _env_int("CONVERGENCE_MIN_PLATFORMS", "3")

# Signal aggregation
SIGNAL_CACHE_TTL = _env_float("SIGNAL_CACHE_TTL", "300.0")

# Notifications
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # Slack/Discord/generic URL
WEBHOOK_MIN_PROFIT = _env_float("WEBHOOK_MIN_PROFIT", "0.01")

# Data directory (for EFS mount in Fargate)
DATA_DIR = os.getenv("DATA_DIR", ".")

# Concurrent execution (submit both legs simultaneously for supported platforms)
CONCURRENT_EXECUTION = _env_bool("CONCURRENT_EXECUTION", "true")

# Order time-in-force strategy.
# "fok" = Fill-or-Kill (default): taker orders, immediate fill or cancel.
#   Safest for arb — you know instantly if you got filled.
# "gtc" = Good-Til-Cancelled: maker/limit orders that rest on the book.
#   Saves taker fees (e.g. Kalshi 7%), but risk of partial/no fill.
# "gtc_first_leg" = Use GTC for the first leg only (capture maker pricing),
#   then FOK for the hedge leg (guarantee execution).
ORDER_TIME_IN_FORCE = os.getenv("ORDER_TIME_IN_FORCE", "fok").lower()

# Maximum seconds to wait for a GTC order to fill before cancelling.
# Only applies when ORDER_TIME_IN_FORCE is "gtc" or "gtc_first_leg".
GTC_ORDER_TIMEOUT = _env_float("GTC_ORDER_TIMEOUT", "30.0")

# Balance caching (avoids redundant balance API calls within a scan cycle)
BALANCE_CACHE_TTL = _env_float("BALANCE_CACHE_TTL", "10.0")

# Semantic matching (embedding-based cross-platform market matching)
SEMANTIC_MATCHING_ENABLED = _env_bool("SEMANTIC_MATCHING_ENABLED", "true")
SEMANTIC_MATCH_THRESHOLD = _env_float("SEMANTIC_MATCH_THRESHOLD", "0.70")

# Fee model: "expected_value" uses probability-weighted average fees,
# "worst_case" uses max(case1, case2) — more conservative but overfilters.
FEE_MODEL = os.getenv("FEE_MODEL", "expected_value")

# Snapshot recording (historical price data for backtesting)
SNAPSHOT_ENABLED = _env_bool("SNAPSHOT_ENABLED", "false")
SNAPSHOT_INTERVAL = _env_int("SNAPSHOT_INTERVAL", "60")

# Backtesting
BACKTEST_INITIAL_BALANCE = _env_float("BACKTEST_INITIAL_BALANCE", "1000.0")

# Dashboard
# Railway injects PORT; fall back to it when DASHBOARD_PORT is not set.
_dashboard_port_default = os.getenv("PORT", "0")
DASHBOARD_PORT = _env_int("DASHBOARD_PORT", _dashboard_port_default)
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "")  # empty = no auth
DASHBOARD_REFRESH_SECONDS = _env_int("DASHBOARD_REFRESH_SECONDS", "15")

# ---------------------------------------------------------------------------
# Previously hardcoded constants — extracted for tunability
# ---------------------------------------------------------------------------

# Executor: cooldown (seconds) after a failed trade before retrying same opp
FAILED_TRADE_COOLDOWN = _env_float("FAILED_TRADE_COOLDOWN", "300")

# Continuous mode: max concurrent WS-triggered executions (semaphore count)
MAX_CONCURRENT_WS_EXECUTIONS = _env_int("MAX_CONCURRENT_WS_EXECUTIONS", "5")

# Price cache staleness (seconds) — different thresholds per use-case
PRICE_CACHE_EVICTION_AGE = _env_float("PRICE_CACHE_EVICTION_AGE", "60")
WS_CACHE_MAX_AGE_SCAN = _env_float("WS_CACHE_MAX_AGE_SCAN", "30")
WS_CACHE_MAX_AGE_REVALIDATION = _env_float("WS_CACHE_MAX_AGE_REVALIDATION", "5")

# WS feed stale detection threshold (seconds without any message)
WS_STALE_FEED_SECONDS = _env_float("WS_STALE_FEED_SECONDS", "120")

# Parallel workers for depth/order book fetches (separate from scan workers)
DEPTH_FETCH_WORKERS = _env_int("DEPTH_FETCH_WORKERS", "8")

# Market title truncation length (for display and DB storage)
MARKET_TITLE_MAX_LEN = _env_int("MARKET_TITLE_MAX_LEN", "60")

# Dashboard query limits
DASHBOARD_RECENT_TRADES_LIMIT = _env_int("DASHBOARD_RECENT_TRADES_LIMIT", "100")
DASHBOARD_PNL_HISTORY_DAYS = _env_int("DASHBOARD_PNL_HISTORY_DAYS", "30")

# Metrics & Alerting
METRICS_ENABLED = _env_bool("METRICS_ENABLED", "true")
ALERT_RATE_LIMIT_SECONDS = _env_float("ALERT_RATE_LIMIT_SECONDS", "300")
ALERT_LOSS_STREAK_THRESHOLD = _env_int("ALERT_LOSS_STREAK_THRESHOLD", "5")
ALERT_BALANCE_LOW_THRESHOLD = _env_float("ALERT_BALANCE_LOW_THRESHOLD", "10.0")


# ---------------------------------------------------------------------------
# Validation — called at module load to catch bad configuration early
# ---------------------------------------------------------------------------

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_VALID_EXECUTION_MODES = {"semi-auto", "full-auto"}
_VALID_FEE_MODELS = {"expected_value", "worst_case"}
_VALID_GEMINI_ORDER_TYPES = {"ioc", "gtc"}


def validate_config() -> list[str]:
    """Validate all configuration values and return a list of warnings.

    Raises:
        ConfigError: If a value is invalid and cannot be safely ignored.

    Returns:
        List of non-fatal warning messages (logged but not raised).
    """
    warnings: list[str] = []

    # --- Enum checks ---
    if EXECUTION_MODE not in _VALID_EXECUTION_MODES:
        raise ConfigError(
            f"EXECUTION_MODE={EXECUTION_MODE!r} is not valid "
            f"(expected one of {_VALID_EXECUTION_MODES})"
        )

    if FEE_MODEL not in _VALID_FEE_MODELS:
        raise ConfigError(
            f"FEE_MODEL={FEE_MODEL!r} is not valid "
            f"(expected one of {_VALID_FEE_MODELS})"
        )

    if GEMINI_ORDER_TYPE not in _VALID_GEMINI_ORDER_TYPES:
        raise ConfigError(
            f"GEMINI_ORDER_TYPE={GEMINI_ORDER_TYPE!r} is not valid "
            f"(expected one of {_VALID_GEMINI_ORDER_TYPES})"
        )

    if LOG_LEVEL not in _VALID_LOG_LEVELS:
        warnings.append(
            f"LOG_LEVEL={LOG_LEVEL!r} is not a standard level "
            f"({_VALID_LOG_LEVELS}); defaulting to INFO"
        )

    # --- Positive-value checks ---
    _positive = {
        "BASE_TRADE_SIZE": BASE_TRADE_SIZE,
        "MAX_TRADE_SIZE": MAX_TRADE_SIZE,
        "DAILY_LOSS_LIMIT": DAILY_LOSS_LIMIT,
        "FILL_POLL_INTERVAL": FILL_POLL_INTERVAL,
        "FILL_POLL_TIMEOUT": FILL_POLL_TIMEOUT,
        "PARALLEL_WORKERS": PARALLEL_WORKERS,
        "HEDGE_MAX_ATTEMPTS": HEDGE_MAX_ATTEMPTS,
        "RESCAN_INTERVAL": RESCAN_INTERVAL,
        "BACKTEST_INITIAL_BALANCE": BACKTEST_INITIAL_BALANCE,
        "GAS_PRICE_CACHE_TTL": GAS_PRICE_CACHE_TTL,
        "IBKR_ORDER_RATE_LIMIT": IBKR_ORDER_RATE_LIMIT,
        "SNAPSHOT_INTERVAL": SNAPSHOT_INTERVAL,
        "BALANCE_CACHE_TTL": BALANCE_CACHE_TTL,
        "ALERT_RATE_LIMIT_SECONDS": ALERT_RATE_LIMIT_SECONDS,
        "ALERT_LOSS_STREAK_THRESHOLD": ALERT_LOSS_STREAK_THRESHOLD,
        "STALE_PRICE_THRESHOLD": STALE_PRICE_THRESHOLD,
    }
    for name, val in _positive.items():
        if val <= 0:
            raise ConfigError(f"{name}={val} must be > 0")

    # --- Non-negative checks ---
    _non_negative = {
        "MIN_LIQUIDITY": MIN_LIQUIDITY,
        "MIN_NET_ROI": MIN_NET_ROI,
        "MIN_PROFIT_AMOUNT": MIN_PROFIT_AMOUNT,
        "DEFAULT_MIN_PROFIT": DEFAULT_MIN_PROFIT,
        "PM_RATE_LIMIT": PM_RATE_LIMIT,
        "KALSHI_RATE_LIMIT": KALSHI_RATE_LIMIT,
        "POLYGON_GAS_ESTIMATE": POLYGON_GAS_ESTIMATE,
        "WEBHOOK_MIN_PROFIT": WEBHOOK_MIN_PROFIT,
        "ALERT_BALANCE_LOW_THRESHOLD": ALERT_BALANCE_LOW_THRESHOLD,
    }
    for name, val in _non_negative.items():
        if val < 0:
            raise ConfigError(f"{name}={val} must be >= 0")

    # --- Range checks ---
    if not (0 <= SIZING_AGGRESSIVENESS <= 1):
        raise ConfigError(
            f"SIZING_AGGRESSIVENESS={SIZING_AGGRESSIVENESS} must be in [0, 1]"
        )
    if not (0 < KELLY_FRACTION <= 1):
        raise ConfigError(
            f"KELLY_FRACTION={KELLY_FRACTION} must be in (0, 1]"
        )
    if not (0 < KELLY_MAX_FRACTION <= 1):
        raise ConfigError(
            f"KELLY_MAX_FRACTION={KELLY_MAX_FRACTION} must be in (0, 1]"
        )
    if not (0 <= BETFAIR_COMMISSION_RATE < 1):
        raise ConfigError(
            f"BETFAIR_COMMISSION_RATE={BETFAIR_COMMISSION_RATE} must be in [0, 1)"
        )
    if not (0 <= SMARKETS_COMMISSION_RATE < 1):
        raise ConfigError(
            f"SMARKETS_COMMISSION_RATE={SMARKETS_COMMISSION_RATE} must be in [0, 1)"
        )
    if not (0 <= GEMINI_FEE_RATE < 1):
        raise ConfigError(
            f"GEMINI_FEE_RATE={GEMINI_FEE_RATE} must be in [0, 1)"
        )
    if not (0 <= DASHBOARD_PORT <= 65535):
        raise ConfigError(
            f"DASHBOARD_PORT={DASHBOARD_PORT} must be in [0, 65535]"
        )
    if not (0 < FUZZY_MATCH_THRESHOLD <= 100):
        raise ConfigError(
            f"FUZZY_MATCH_THRESHOLD={FUZZY_MATCH_THRESHOLD} must be in (0, 100]"
        )
    if not (0 <= EVENT_DIVERGENCE_THRESHOLD <= 1):
        raise ConfigError(
            f"EVENT_DIVERGENCE_THRESHOLD={EVENT_DIVERGENCE_THRESHOLD} "
            f"must be in [0, 1]"
        )
    if not (0 <= HEDGE_MAX_SPREAD_LOSS_PCT <= 1):
        raise ConfigError(
            f"HEDGE_MAX_SPREAD_LOSS_PCT={HEDGE_MAX_SPREAD_LOSS_PCT} "
            f"must be in [0, 1]"
        )
    if not (0 <= REENTRY_IMPROVEMENT_THRESHOLD <= 1):
        raise ConfigError(
            f"REENTRY_IMPROVEMENT_THRESHOLD={REENTRY_IMPROVEMENT_THRESHOLD} "
            f"must be in [0, 1]"
        )
    if not (0 < SEMANTIC_MATCH_THRESHOLD <= 1):
        raise ConfigError(
            f"SEMANTIC_MATCH_THRESHOLD={SEMANTIC_MATCH_THRESHOLD} "
            f"must be in (0, 1]"
        )
    if not (0 < STALE_PRICE_MOVE_PCT < 1):
        raise ConfigError(
            f"STALE_PRICE_MOVE_PCT={STALE_PRICE_MOVE_PCT} "
            f"must be in (0, 1)"
        )

    # --- Relationship checks ---
    if BASE_TRADE_SIZE > MAX_TRADE_SIZE:
        raise ConfigError(
            f"BASE_TRADE_SIZE ({BASE_TRADE_SIZE}) must be <= "
            f"MAX_TRADE_SIZE ({MAX_TRADE_SIZE})"
        )

    if FILL_POLL_TIMEOUT < FILL_POLL_INTERVAL:
        warnings.append(
            f"FILL_POLL_TIMEOUT ({FILL_POLL_TIMEOUT}) < "
            f"FILL_POLL_INTERVAL ({FILL_POLL_INTERVAL}); "
            f"polls may never complete"
        )

    # --- Dashboard checks ---
    if DASHBOARD_PORT > 0 and not DASHBOARD_PASS:
        warnings.append(
            "DASHBOARD_PORT is set but DASHBOARD_PASS is empty — "
            "dashboard has no authentication"
        )

    # --- Platform whitelist validation ---
    if not ENABLED_EXECUTION_PLATFORMS:
        warnings.append(
            "ENABLED_EXECUTION_PLATFORMS is empty — no platforms will execute trades"
        )
    unknown = ENABLED_EXECUTION_PLATFORMS - _VALID_PLATFORMS
    if unknown:
        raise ConfigError(
            f"ENABLED_EXECUTION_PLATFORMS contains unknown platforms: "
            f"{', '.join(sorted(unknown))}. "
            f"Valid: {', '.join(sorted(_VALID_PLATFORMS))}"
        )

    # --- Contradiction warnings ---
    if EXECUTION_MODE == "full-auto" and DRY_RUN:
        warnings.append(
            "EXECUTION_MODE=full-auto but DRY_RUN=true — "
            "no trades will be executed"
        )

    return warnings


# Run validation at import time; log warnings but don't suppress them
_config_warnings = validate_config()
