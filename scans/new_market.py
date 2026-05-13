"""New Market Mispricing — Strategy #34.

Exploit price inefficiency in the first 24-48 hours of new markets.

When markets first launch:
1. Low liquidity leads to wider spreads
2. Price discovery is incomplete
3. Prices may deviate significantly from fair value

Strategy:
1. Detect newly created markets (age < NEW_MARKET_AGE_HOURS)
2. Compare price against multi-source consensus (Metaculus, Manifold, etc.)
3. If divergence exceeds threshold, bet on convergence to fair value

Layer 2: Near-arbitrage — directional bet on price convergence.
"""

import logging
import time
from datetime import datetime, timezone

from config import (
    NEW_MARKET_MISPRICING_ENABLED,
    NEW_MARKET_AGE_HOURS,
    NEW_MARKET_MIN_DIVERGENCE,
    NEW_MARKET_MAX_TRADE_SIZE,
)
from .helpers import capital_efficiency_score, filter_dust

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Market age detection
# ---------------------------------------------------------------------------

def _get_market_age_hours(market: dict, platform: str) -> float | None:
    """Calculate the age of a market in hours.

    Returns:
        Hours since market creation, or None if unknown.
    """
    now = time.time()

    created_at = market.get("created_at") or market.get("createdAt")
    if created_at:
        if isinstance(created_at, str):
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                return (now - dt.timestamp()) / 3600.0
            except ValueError:
                pass
        elif isinstance(created_at, (int, float)):
            if created_at > 1e12:
                created_at = created_at / 1000
            return (now - created_at) / 3600.0

    if platform == "kalshi":
        open_time = market.get("open_time") or market.get("openTime")
        if open_time:
            if isinstance(open_time, str):
                try:
                    dt = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
                    return (now - dt.timestamp()) / 3600.0
                except ValueError:
                    pass

    return None


def _is_new_market(market: dict, platform: str, max_age_hours: float) -> bool:
    """Check if a market is newly created."""
    age = _get_market_age_hours(market, platform)
    if age is None:
        return False
    return age <= max_age_hours


# ---------------------------------------------------------------------------
# Fair value estimation
# ---------------------------------------------------------------------------

def _estimate_fair_value(
    market: dict,
    platform: str,
    signal_aggregator=None,
) -> float | None:
    """Estimate fair value using multi-source signals.

    Uses signal aggregator to get consensus probability from
    Metaculus, Manifold, and other sources.

    Returns:
        Estimated fair value probability (0-1), or None.
    """
    if signal_aggregator is None:
        return None

    title = market.get("title") or market.get("question", "")
    if not title:
        return None

    market_key = market.get("condition_id") or market.get("id") or title

    try:
        signal_aggregator.fetch_external_signals(market_key, title)
        consensus = signal_aggregator.get_consensus(market_key)
        if consensus and "probability" in consensus:
            return consensus["probability"]
    except Exception as e:
        logger.debug("Failed to get fair value for %s: %s", title[:30], e)

    return None


# ---------------------------------------------------------------------------
# Scan function
# ---------------------------------------------------------------------------

def scan_new_market_mispricing(
    markets: list[dict],
    platform: str = "polymarket",
    signal_aggregator=None,
    max_age_hours: float | None = None,
    min_divergence: float | None = None,
    min_profit: float = 0.005,
) -> list[dict]:
    """Scan for mispricing opportunities in new markets.

    Identifies newly created markets where the price diverges
    significantly from multi-source fair value estimates.

    Args:
        markets: List of market dicts.
        platform: Platform name for the markets.
        signal_aggregator: SignalAggregator instance for consensus pricing.
        max_age_hours: Maximum market age to consider (default from config).
        min_divergence: Minimum divergence to flag (default from config).
        min_profit: Minimum net profit threshold.

    Returns:
        List of opportunity dicts sorted by net_profit descending.
    """
    if not NEW_MARKET_MISPRICING_ENABLED:
        return []

    max_age_hours = max_age_hours or NEW_MARKET_AGE_HOURS
    min_divergence = min_divergence or NEW_MARKET_MIN_DIVERGENCE
    opportunities = []

    new_markets = [
        m for m in markets
        if _is_new_market(m, platform, max_age_hours)
    ]

    logger.debug("Found %d new markets (age < %.1fh) to analyze", len(new_markets), max_age_hours)

    for market in new_markets:
        title = market.get("title") or market.get("question", "")
        market_price = market.get("yes_price") or market.get("yes_mid", 0)

        if not market_price or market_price <= 0 or market_price >= 1:
            continue

        fair_value = _estimate_fair_value(market, platform, signal_aggregator)
        if fair_value is None:
            continue

        divergence = abs(fair_value - market_price)
        if divergence < min_divergence:
            continue

        if fair_value > market_price:
            direction = "BUY_YES"
            entry_price = market_price
        else:
            direction = "BUY_NO"
            entry_price = 1.0 - market_price

        from fees import net_profit_new_market
        result = net_profit_new_market(
            market_price=entry_price,
            fair_value=fair_value if direction == "BUY_YES" else (1.0 - fair_value),
            platform=platform,
        )

        if result["net_profit"] < min_profit:
            continue

        age_hours = _get_market_age_hours(market, platform) or 0

        opp = {
            "type": "NewMarketMispricing",
            "_layer": 2,
            "market": f"{title[:40]}... (new market, {age_hours:.1f}h old)",
            "prices": f"market={market_price:.3f} fair={fair_value:.3f} (div={divergence:.3f})",
            "total_cost": f"${entry_price:.4f}",
            "net_profit": result["net_profit"],
            "net_roi": result.get("net_roi", 0),
            "confidence": min(0.80, 0.50 + divergence),
            "_market_key": market.get("condition_id") or market.get("id", ""),
            "_platform": platform,
            "_market": market,
            "_market_price": market_price,
            "_fair_value": fair_value,
            "_divergence": divergence,
            "_age_hours": age_hours,
            "_direction": direction,
        }
        opp["_efficiency"] = capital_efficiency_score(opp)
        opportunities.append(opp)

    opportunities = filter_dust(opportunities, min_amount=min_profit)
    opportunities.sort(key=lambda o: o["net_profit"], reverse=True)

    logger.info("New market mispricing scan: found %d opportunities", len(opportunities))
    return opportunities
