"""Volatility-Adjusted Market Making scan (Strategy VolatilityAdjustedMM).

Surfaces markets whose realized volatility currently warrants a widened
spread per the VolatilityTracker. The runtime adjustment is enforced inside
``QuoteEngine.calculate_quotes`` — this scan exposes the same state so
operators can see which markets are paying for the widened spread.

Returns ``[]`` whenever ``MM_VOLATILITY_ADJUSTED_ENABLED`` is false so an
always-on call in the orchestrator is a cheap no-op.
"""

import logging

logger = logging.getLogger(__name__)


def scan_volatility_adjusted_mm(
    market_keys: list[str] | None = None,
    tracker=None,
    base_multiplier: float = 1.0,
    max_multiplier: float = 3.0,
) -> list[dict]:
    """Emit VolatilityAdjustedMM opportunities for high-volatility markets.

    Args:
        market_keys: Markets to inspect. If empty/None, returns ``[]``.
        tracker: Optional ``VolatilityTracker`` instance. Defaults to the
            module singleton ``market_maker.get_volatility_tracker()``.
        base_multiplier: Multiplier floor (markets at this floor are skipped).
        max_multiplier: Multiplier ceiling forwarded to ``get_spread_multiplier``.

    Returns:
        List of VolatilityAdjustedMM opportunity dicts (possibly empty).
    """
    from config import MM_VOLATILITY_ADJUSTED_ENABLED

    if not MM_VOLATILITY_ADJUSTED_ENABLED:
        return []

    if not market_keys:
        return []

    if tracker is None:
        from market_maker import get_volatility_tracker
        tracker = get_volatility_tracker()

    opps: list[dict] = []
    for market_key in market_keys:
        if not market_key:
            continue

        multiplier = tracker.get_spread_multiplier(
            market_key,
            base_multiplier=base_multiplier,
            max_multiplier=max_multiplier,
        )
        if multiplier <= base_multiplier:
            continue

        volatility = tracker.get_volatility(market_key)

        opps.append({
            "type": "VolatilityAdjustedMM",
            "_layer": 3,
            "market": f"{market_key[:60]} (vol-adjusted MM)",
            "prices": f"vol={volatility:.4f} mult={multiplier:.2f}x",
            "total_cost": "$0.00",
            "net_profit": 0.0,
            "net_roi": 0.0,
            "confidence": 0.70,
            "_market_key": market_key,
            "_volatility": volatility,
            "_spread_multiplier": multiplier,
        })

    return opps
