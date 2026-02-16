"""Shared helpers used across scan modules."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from polymarket_api import get_clob_prices
from kalshi_api import KalshiClient

logger = logging.getLogger(__name__)


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

    Favors high ROI opportunities with adequate order book depth.
    Returns 0 for invalid data.
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
        depth = 1  # Avoid zeroing out — still rank by ROI

    roi = net_profit / total_cost
    return roi * min(depth, 50)
