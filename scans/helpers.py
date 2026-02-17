"""Shared helpers used across scan modules."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

from polymarket_api import get_clob_prices
from kalshi_api import KalshiClient
from config import MAX_RESOLUTION_DAYS, MIN_PROFIT_AMOUNT

logger = logging.getLogger(__name__)


def filter_dust(opportunities: list[dict], min_amount: float = None) -> list[dict]:
    """Remove opportunities with net_profit below the dust threshold."""
    if min_amount is None:
        min_amount = MIN_PROFIT_AMOUNT
    before = len(opportunities)
    filtered = [o for o in opportunities if o.get("net_profit", 0) >= min_amount]
    removed = before - len(filtered)
    if removed:
        logger.info("Filtered %d dust trades below $%.2f profit.", removed, min_amount)
    return filtered


def _days_to_resolution(market: dict, platform: str = "polymarket") -> float | None:
    """Calculate days until market resolution. Returns None if no date available."""
    if platform == "kalshi":
        date_str = market.get("close_time") or market.get("expected_expiration_time")
    else:
        date_str = market.get("endDateIso")

    if not date_str:
        return None

    try:
        if date_str.endswith("Z"):
            date_str = date_str[:-1] + "+00:00"
        resolve_dt = datetime.fromisoformat(date_str)
        if resolve_dt.tzinfo is None:
            resolve_dt = resolve_dt.replace(tzinfo=timezone.utc)
        days = (resolve_dt - datetime.now(timezone.utc)).total_seconds() / 86400
        return max(days, 0.01)  # Floor at 0.01 to avoid division by zero
    except (ValueError, TypeError):
        return None


def _within_resolution_window(market: dict, max_days: int = None, platform: str = "polymarket") -> bool:
    """Check if a market resolves within max_days from now.

    Returns True if the market resolves within the window (keep it),
    False if it resolves too far out or has no date (skip it).
    """
    if max_days is None:
        max_days = MAX_RESOLUTION_DAYS
    if max_days <= 0:
        return True  # 0 = disabled

    cutoff = datetime.now(timezone.utc) + timedelta(days=max_days)

    if platform == "kalshi":
        date_str = market.get("close_time") or market.get("expected_expiration_time")
    else:  # polymarket
        date_str = market.get("endDateIso")

    if not date_str:
        return False  # No date = skip (conservative)

    try:
        # Parse ISO 8601 (handles both "Z" and "+00:00" suffixes)
        if date_str.endswith("Z"):
            date_str = date_str[:-1] + "+00:00"
        resolve_dt = datetime.fromisoformat(date_str)
        if resolve_dt.tzinfo is None:
            resolve_dt = resolve_dt.replace(tzinfo=timezone.utc)
        return resolve_dt <= cutoff
    except (ValueError, TypeError):
        return False  # Unparseable date = skip


def _extract_token_ids(market: dict) -> list[str]:
    """Extract CLOB token IDs from a Polymarket market dict."""
    token_ids_raw = market.get("clobTokenIds")
    if not token_ids_raw:
        return []
    try:
        if isinstance(token_ids_raw, str):
            return json.loads(token_ids_raw)
        return list(token_ids_raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return []


def _parallel_fetch_kalshi(kalshi_client: KalshiClient, tickers: list[str], max_workers: int = 4) -> dict:
    """Pre-fetch Kalshi markets for multiple event tickers in parallel."""
    results = {}
    if not tickers:
        return results

    unique_tickers = list(set(t for t in tickers if t))
    logger.info("Fetching Kalshi markets for %d events (parallel, %d workers)...", len(unique_tickers), max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(kalshi_client.fetch_markets_for_event, t): t
            for t in unique_tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results[ticker] = future.result()
            except Exception as e:
                logger.warning("Failed to fetch Kalshi markets for %s: %s", ticker, e)
                results[ticker] = []

    return results


def _fetch_clob_for_market(market: dict) -> tuple[dict, dict | None]:
    """Fetch CLOB prices for a single market. Returns (market, clob_data)."""
    return market, get_clob_prices(market)


def capital_efficiency_score(opp: dict) -> float:
    """Score an opportunity by capital efficiency: (net_profit / total_cost) * min(depth, 50).

    When _days_to_resolution is present, divides the base score by days
    to favor fast-resolving markets. Fallback: original score when no date.
    """
    net_profit = opp.get("net_profit", 0)
    if net_profit <= 0:
        return 0.0

    total_cost_str = opp.get("total_cost", "$0")
    try:
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
    except (ValueError, TypeError):
        return 0.0

    if total_cost <= 0:
        return 0.0

    depth = opp.get("_clob_depth", 0)
    if depth <= 0:
        depth = 1

    roi = net_profit / total_cost
    base_score = roi * min(depth, 50)

    days = opp.get("_days_to_resolution")
    if days is not None and days > 0:
        return base_score / days

    return base_score
