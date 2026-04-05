"""Liquidity rewards scan for Polymarket and Kalshi reward programs."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from polymarket_api import get_clob_prices
from scans.helpers import _fetch_clob_for_market, filter_dust

logger = logging.getLogger(__name__)


def _validate_reward_metadata(reward_dict: dict) -> bool:
    """Validate reward metadata fields are present and sane.

    Args:
        reward_dict: Reward metadata from Markets API.

    Returns:
        True if all fields are valid, False otherwise.
    """
    if not reward_dict:
        return False

    min_size = reward_dict.get("min_incentive_size")
    max_spread = reward_dict.get("max_incentive_spread")
    pool_size = reward_dict.get("pool_size_usdc", 0)

    # Validate ranges
    if min_size is None or min_size <= 0:
        return False
    if max_spread is None or max_spread <= 0 or max_spread > 0.50:
        return False
    if pool_size < 0:
        return False

    return True


def _calculate_optimal_quotes(reward_info: dict, mid_price: float, inventory: float = 0.0) -> dict:
    """Calculate optimal bid/ask quotes for reward qualification.

    Args:
        reward_info: Reward metadata with max_incentive_spread, etc.
        mid_price: Current market mid-price (0-1).
        inventory: Current inventory position (for skew).

    Returns:
        Dict with bid, ask, spread keys.
    """
    max_spread = reward_info.get("max_incentive_spread", 0.05)

    # Target spread: 60% of max for high reward score without competing too hard
    target_spread = max_spread * 0.6
    half_spread = target_spread / 2

    # Inventory skew: when long, encourage selling
    skew = 0.0
    if inventory > 0:
        skew = -target_spread * 0.1

    bid = mid_price - half_spread + skew
    ask = mid_price + half_spread + skew

    # Clamp to valid range
    bid = max(0.01, min(0.99, bid))
    ask = max(0.01, min(0.99, ask))

    return {
        "bid": round(bid, 4),
        "ask": round(ask, 4),
        "spread": round(ask - bid, 4),
    }


def _refine_rewards_with_clob(opportunities: list[dict], markets_by_key: dict,
                              price_cache: dict | None = None) -> list[dict]:
    """Stage 2: Verify optimal quotes are achievable against live CLOB.

    Checks that optimal bids/asks don't cross the market and that depth is sufficient.

    Args:
        opportunities: Candidates from stage 1.
        markets_by_key: Market objects indexed by market key.
        price_cache: Optional WS price cache.

    Returns:
        Refined opportunities (may drop some if CLOB unavailable or crosses).
    """
    if not opportunities:
        return opportunities

    logger.info("Refining %d reward candidates with CLOB depth check...", len(opportunities))

    # Pre-fetch CLOB prices in parallel
    fetch_tasks = {}
    for opp in opportunities:
        market_key = opp.get("_market_key")
        market = markets_by_key.get(market_key) if market_key else None
        if market and market_key not in fetch_tasks:
            fetch_tasks[market_key] = market

    clob_results = {}
    if fetch_tasks:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_clob_for_market, m, price_cache): mk
                       for mk, m in fetch_tasks.items()}
            for future in as_completed(futures):
                mk = futures[future]
                try:
                    _, clob = future.result()
                    clob_results[mk] = clob
                except Exception as e:
                    logger.debug("CLOB fetch failed for reward refinement: %s", e)

    refined = []
    for opp in opportunities:
        market_key = opp.get("_market_key")
        market = markets_by_key.get(market_key) if market_key else None
        if not market:
            refined.append(opp)
            continue

        clob = clob_results.get(market_key)
        if not clob or clob["yes_bid"] is None or clob["yes_ask"] is None:
            # CLOB unavailable: keep opportunity (graceful degradation)
            opp["_clob_refined"] = False
            refined.append(opp)
            continue

        # Check depth: minimum $5 on each side
        min_depth = 5.0
        yes_bid_depth = clob.get("yes_bid_size", 0) or 0
        yes_ask_depth = clob.get("yes_ask_size", 0) or 0

        if yes_bid_depth < min_depth or yes_ask_depth < min_depth:
            logger.debug(
                "Dropping reward opportunity on %s: insufficient depth (bid=%s, ask=%s)",
                market_key,
                yes_bid_depth,
                yes_ask_depth,
            )
            continue

        # Verify optimal quotes don't cross market
        optimal_bid = opp.get("optimal_bid", 0)
        optimal_ask = opp.get("optimal_ask", 0)
        clob_mid = (clob["yes_bid"] + clob["yes_ask"]) / 2

        if optimal_bid > clob["yes_ask"] or optimal_ask < clob["yes_bid"]:
            logger.debug(
                "Dropping reward opportunity on %s: quotes cross market",
                market_key,
            )
            continue

        opp["_clob_refined"] = True
        opp["_clob_depth"] = min(yes_bid_depth, yes_ask_depth)
        refined.append(opp)

    dropped = len(opportunities) - len(refined)
    if dropped:
        logger.info("Dropped %d reward candidates at CLOB validation.", dropped)
    return refined


def scan_polymarket_rewards(markets: list[dict], reward_tracker, min_pool_usdc: float = 10.0,
                            price_cache: dict | None = None) -> list[dict]:
    """Scan for Polymarket reward-eligible markets and generate resting order opportunities.

    Two-stage scan:
    1. Filter markets with active reward programs and sufficient pool size.
    2. Refine with CLOB depth check.

    Args:
        markets: List of Polymarket market objects from Markets API.
        reward_tracker: RewardTracker instance for calculating optimal spreads.
        min_pool_usdc: Minimum reward pool size to include market (default $10).
        price_cache: Optional WS price cache for faster lookup.

    Returns:
        List of reward opportunity dicts.
    """
    opportunities = []
    markets_by_key = {}

    logger.info("Scanning %d Polymarket markets for reward programs...", len(markets))

    filtered_no_incentives = 0
    filtered_small_pool = 0
    filtered_invalid_metadata = 0

    for market in markets:
        market_key = market.get("conditionId", market.get("question", ""))
        if not market_key:
            continue

        # Stage 1: Check for active reward program
        incentives = market.get("incentives", {})
        if not incentives:
            filtered_no_incentives += 1
            continue

        # Validate reward metadata
        if not _validate_reward_metadata(incentives):
            filtered_invalid_metadata += 1
            logger.debug("Invalid reward metadata on %s: %s", market_key, incentives)
            continue

        pool_size = incentives.get("pool_size_usdc", 0)
        if pool_size < min_pool_usdc:
            filtered_small_pool += 1
            continue

        min_size = incentives.get("min_incentive_size", 5.0)

        # Get mid-price from cache or API
        mid_price = None
        if price_cache:
            cached = price_cache.get(market_key, {})
            yes_bid = cached.get("yes_bid")
            yes_ask = cached.get("yes_ask")
            if yes_bid is not None and yes_ask is not None:
                mid_price = (yes_bid + yes_ask) / 2

        # Fall back to market mid-price
        if mid_price is None:
            prices = market.get("outcomePrices", [])
            if prices and len(prices) >= 2:
                yes_price = prices[0]
                mid_price = yes_price
            else:
                continue

        # Calculate optimal quotes using reward tracker
        optimal = _calculate_optimal_quotes(incentives, mid_price, inventory=0.0)

        # Check if single-sided is allowed based on midpoint range
        single_sided_ok = 0.10 <= mid_price <= 0.90

        # Create opportunity
        markets_by_key[market_key] = market
        opportunities.append({
            "type": "PolymarketRewards",
            "_layer": 3,  # Layer 3: market making / liquidity provision
            "market": market.get("question", market.get("title", "Unknown"))[:60],
            "platform": "polymarket",
            "reward_pool_usdc": pool_size,
            "min_size": min_size,
            "optimal_bid": optimal["bid"],
            "optimal_ask": optimal["ask"],
            "optimal_spread": optimal["spread"],
            "single_sided_ok": single_sided_ok,
            "_market_key": market_key,
            "_market_volume": float(market.get("volume", 0) or 0),
        })

    if filtered_no_incentives:
        logger.info("Filtered %d markets without reward programs.", filtered_no_incentives)
    if filtered_small_pool:
        logger.info("Filtered %d markets with reward pool < $%.2f.", filtered_small_pool, min_pool_usdc)
    if filtered_invalid_metadata:
        logger.info("Filtered %d markets with invalid reward metadata.", filtered_invalid_metadata)

    # Stage 2: Refine with CLOB depth check
    opportunities = _refine_rewards_with_clob(opportunities, markets_by_key, price_cache=price_cache)

    # Filter dust (unlikely on rewards, but consistent with other scans)
    opportunities = filter_dust(opportunities, min_amount=0.01)

    logger.info("Found %d Polymarket reward opportunities.", len(opportunities))
    return opportunities


def scan_kalshi_rewards(kalshi_client, reward_tracker, min_pool_usdc: float = 10.0) -> list[dict]:
    """Scan for Kalshi reward-eligible markets and generate resting order opportunities.

    Kalshi has no public reward API, so we generate opportunities for high-volume markets
    where the reward tracker can log qualifying orders.

    Args:
        kalshi_client: KalshiClient instance.
        reward_tracker: KalshiRewardTracker instance for logging orders.
        min_pool_usdc: Minimum pool size (Kalshi doesn't expose this; kept for API compatibility).

    Returns:
        List of reward opportunity dicts for Kalshi markets.
    """
    opportunities = []

    logger.info("Scanning Kalshi markets for reward-eligible opportunities...")

    try:
        # Fetch active Kalshi markets via events → markets
        if not hasattr(kalshi_client, "fetch_all_events"):
            logger.warning("Kalshi client missing fetch_all_events; skipping reward scan")
            return opportunities
        events = kalshi_client.fetch_all_events() or []
        kalshi_markets = []
        for ev in events[:50]:  # Limit to top 50 events for reward scan
            ticker = ev.get("event_ticker", "")
            if ticker:
                try:
                    markets = kalshi_client.fetch_markets_for_event(ticker)
                    kalshi_markets.extend(markets or [])
                except Exception:
                    pass
        if not kalshi_markets:
            logger.info("No Kalshi markets returned.")
            return opportunities

        logger.info("Scanning %d Kalshi markets for liquidity incentive program eligibility...",
                    len(kalshi_markets))

        filtered_low_volume = 0

        for market in kalshi_markets:
            ticker = market.get("ticker")
            if not ticker:
                continue

            # Filter by daily volume (minimum $1000 for reward eligibility)
            min_daily_volume = 1000.0
            volume = float(market.get("volume_24h", 0) or 0)
            if volume < min_daily_volume:
                filtered_low_volume += 1
                continue

            # Get current mid-price from market order book
            last_price = market.get("last_price")
            if last_price is None or last_price <= 0 or last_price >= 1:
                continue

            mid_price = last_price

            # Generate resting order opportunity
            # Kalshi spreads: use 3% of mid as target (conservative)
            target_spread = max(0.02, min(0.05, mid_price * 0.03))
            bid = max(0.01, mid_price - target_spread / 2)
            ask = min(0.99, mid_price + target_spread / 2)

            opportunities.append({
                "type": "KalshiRewards",
                "_layer": 3,  # Layer 3: market making
                "market": market.get("title", ticker)[:60],
                "platform": "kalshi",
                "ticker": ticker,
                "optimal_bid": round(bid, 4),
                "optimal_ask": round(ask, 4),
                "optimal_spread": round(ask - bid, 4),
                "_market_key": ticker,
                "_market_volume": volume,
            })

        if filtered_low_volume:
            logger.info("Filtered %d Kalshi markets with volume < $%.0f.", filtered_low_volume, min_daily_volume)

    except Exception as e:
        logger.error("Kalshi reward scan failed: %s", e)

    logger.info("Found %d Kalshi reward opportunities.", len(opportunities))
    return opportunities
