"""Stale price exploitation — trade against slow-updating platforms."""

import logging
import time

from .helpers import capital_efficiency_score

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stale price opportunity detection
# ---------------------------------------------------------------------------

def scan_stale_prices(
    price_tracker,
    matched_markets: list[dict],
    min_move_pct: float = 0.03,
    min_stale_seconds: float = 30.0,
    min_profit: float = 0.005,
) -> list[dict]:
    """Detect stale price opportunities across platforms.

    When a price moves on a liquid platform but a less liquid platform
    hasn't updated, the stale price represents a trading opportunity.

    Args:
        price_tracker: PriceTracker instance with recent price data.
        matched_markets: List of cross-platform matched market dicts,
            each with ``market_key``, ``platforms`` dict mapping
            platform names to market identifiers.
        min_move_pct: Minimum price move on the fresh platform (0-1).
        min_stale_seconds: Minimum age of the stale price to qualify.
        min_profit: Minimum estimated profit to report.

    Returns:
        List of opportunity dicts sorted by estimated profit.
    """
    opportunities = []

    for match in matched_markets:
        market_key = match.get("market_key", "")
        if not market_key:
            continue

        stale_opps = price_tracker.detect_stale_opportunities(market_key)
        for opp in stale_opps:
            if opp["stale_age_seconds"] < min_stale_seconds:
                continue
            if abs(opp["price_delta"]) < min_move_pct:
                continue

            # Estimate profit: delta minus estimated fees (~2-5%)
            estimated_fee_pct = 0.03
            gross_profit = abs(opp["price_delta"])
            net_profit = gross_profit - (gross_profit * estimated_fee_pct)

            if net_profit < min_profit:
                continue

            total_cost = opp["stale_price"]
            net_roi = net_profit / total_cost if total_cost > 0 else 0

            opportunity = {
                "type": "StalePriceOpp",
                "market": market_key,
                "prices": (
                    f"stale_{opp['stale_platform']}={opp['stale_price']:.4f} "
                    f"fresh_{opp['fresh_platform']}={opp['fresh_price']:.4f}"
                ),
                "total_cost": f"${total_cost:.4f}",
                "net_profit": net_profit,
                "net_roi": net_roi,
                "confidence": _stale_confidence(opp),
                "_stale_platform": opp["stale_platform"],
                "_fresh_platform": opp["fresh_platform"],
                "_stale_price": opp["stale_price"],
                "_fresh_price": opp["fresh_price"],
                "_stale_age": opp["stale_age_seconds"],
                "_direction": opp["direction"],
                "_market_key": market_key,
                "_platforms": match.get("platforms", {}),
            }
            opportunity["_efficiency"] = capital_efficiency_score(opportunity)
            opportunities.append(opportunity)

    opportunities.sort(key=lambda o: o["net_profit"], reverse=True)
    logger.info("Stale price scan: found %d opportunities", len(opportunities))
    return opportunities


def _stale_confidence(opp: dict) -> float:
    """Estimate confidence (0-1) based on staleness age and price delta.

    Higher staleness age and larger delta = lower confidence (more likely
    the stale platform has already updated but we haven't seen it).
    """
    age = opp.get("stale_age_seconds", 0)
    delta = abs(opp.get("price_delta", 0))

    # Base confidence: high for moderate staleness, decreasing with age
    if age < 15:
        age_factor = 0.95
    elif age < 30:
        age_factor = 0.85
    elif age < 60:
        age_factor = 0.70
    elif age < 120:
        age_factor = 0.50
    else:
        age_factor = 0.30

    # Larger delta = slightly lower confidence (more likely to revert)
    delta_factor = max(0.5, 1.0 - delta * 2)

    return round(age_factor * delta_factor, 3)
