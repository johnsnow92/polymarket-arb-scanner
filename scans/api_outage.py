"""API Outage Arbitrage — Strategy #35.

Exploit stale prices during platform API outages.

When a platform's API goes down or becomes unresponsive:
1. Prices on that platform become stale (not updating)
2. Other platforms continue to receive price updates
3. The stale platform may have mispriced markets relative to fresh prices

Strategy:
1. Detect platform outages via FeedHealthTracker
2. Compare stale prices against fresh prices on other platforms
3. Trade on the stale platform at favorable prices

Layer 2: Near-arbitrage — exploits temporary information asymmetry.

Risk: The stale platform may update before our order fills.
"""

import logging
import time

from config import (
    API_OUTAGE_ARB_ENABLED,
    API_OUTAGE_STALE_THRESHOLD,
    API_OUTAGE_MIN_DIVERGENCE,
)
from .helpers import capital_efficiency_score, filter_dust

logger = logging.getLogger(__name__)


def scan_api_outage_arb(
    cross_matched_markets: list[dict],
    feed_health_tracker=None,
    price_tracker=None,
    min_divergence: float | None = None,
    min_profit: float = 0.005,
) -> list[dict]:
    """Scan for API outage arbitrage opportunities.

    Identifies markets where one platform has stale prices due to an
    outage while other platforms have fresh prices.

    Args:
        cross_matched_markets: List of matched market dicts with markets
            from multiple platforms.
        feed_health_tracker: FeedHealthTracker instance from ws_feeds.py.
        price_tracker: PriceTracker instance for staleness detection.
        min_divergence: Minimum price divergence to flag (default from config).
        min_profit: Minimum net profit threshold.

    Returns:
        List of opportunity dicts sorted by net_profit descending.
    """
    if not API_OUTAGE_ARB_ENABLED:
        return []

    if feed_health_tracker is None:
        logger.debug("API outage scan requires FeedHealthTracker")
        return []

    min_divergence = min_divergence or API_OUTAGE_MIN_DIVERGENCE
    opportunities = []

    outage_platforms = feed_health_tracker.get_outage_opportunities(
        min_outage_seconds=API_OUTAGE_STALE_THRESHOLD,
    )

    if not outage_platforms:
        logger.debug("No platform outages detected")
        return []

    outage_names = {op["platform"] for op in outage_platforms}
    logger.info(
        "API outage detected on %d platform(s): %s",
        len(outage_names), ", ".join(outage_names),
    )

    for match in cross_matched_markets:
        platform_a = match.get("platform_a", "")
        platform_b = match.get("platform_b", "")
        market_a = match.get("market_a") or match.get("a", {})
        market_b = match.get("market_b") or match.get("b", {})

        if not all([platform_a, platform_b, market_a, market_b]):
            continue

        stale_platform = None
        fresh_platform = None
        stale_market = None
        fresh_market = None

        if platform_a in outage_names and platform_b not in outage_names:
            stale_platform = platform_a
            fresh_platform = platform_b
            stale_market = market_a
            fresh_market = market_b
        elif platform_b in outage_names and platform_a not in outage_names:
            stale_platform = platform_b
            fresh_platform = platform_a
            stale_market = market_b
            fresh_market = market_a
        else:
            continue

        stale_yes = stale_market.get("yes_price") or stale_market.get("yes_mid", 0)
        fresh_yes = fresh_market.get("yes_price") or fresh_market.get("yes_mid", 0)

        if not stale_yes or not fresh_yes:
            continue

        divergence = abs(fresh_yes - stale_yes)
        if divergence < min_divergence:
            continue

        if fresh_yes > stale_yes:
            direction = "BUY_YES"
            entry_price = stale_yes
            edge = fresh_yes - stale_yes
        else:
            direction = "BUY_NO"
            stale_no = 1.0 - stale_yes
            fresh_no = 1.0 - fresh_yes
            entry_price = stale_no
            edge = fresh_no - stale_no

        from fees import net_profit_stale_price
        try:
            result = net_profit_stale_price(
                stale_price=entry_price,
                fresh_price=entry_price + edge,
                platform=stale_platform,
            )
        except (ImportError, AttributeError):
            from fees import net_profit_new_market
            result = net_profit_new_market(
                market_price=entry_price,
                fair_value=entry_price + edge,
                platform=stale_platform,
            )

        if result.get("net_profit", 0) < min_profit:
            continue

        title = (
            stale_market.get("title") or
            stale_market.get("question") or
            stale_market.get("ticker", "")
        )

        outage_info = next(
            (op for op in outage_platforms if op["platform"] == stale_platform),
            {},
        )
        outage_duration = outage_info.get("outage_duration", 0)

        opp = {
            "type": "APIOutageArb",
            "_layer": 2,
            "market": f"{title[:40]}... (API outage arb)",
            "prices": (
                f"stale_{stale_platform}={stale_yes:.3f} vs "
                f"fresh_{fresh_platform}={fresh_yes:.3f}"
            ),
            "total_cost": f"${entry_price:.4f}",
            "net_profit": result.get("net_profit", 0),
            "net_roi": result.get("net_roi", 0),
            "confidence": max(0.60, 0.80 - (outage_duration / 600)),
            "_market_key": stale_market.get("condition_id") or stale_market.get("id", ""),
            "_stale_platform": stale_platform,
            "_fresh_platform": fresh_platform,
            "_stale_market": stale_market,
            "_fresh_market": fresh_market,
            "_stale_price": stale_yes,
            "_fresh_price": fresh_yes,
            "_divergence": divergence,
            "_outage_duration": outage_duration,
            "_direction": direction,
        }
        opp["_efficiency"] = capital_efficiency_score(opp)
        opportunities.append(opp)

    opportunities = filter_dust(opportunities, min_amount=min_profit)
    opportunities.sort(key=lambda o: o["net_profit"], reverse=True)

    logger.info("API outage arb scan: found %d opportunities", len(opportunities))
    return opportunities
