"""Lead-Lag Market Making scan (Strategy LeadLagMM).

Identifies cross-platform pairs where one platform is leading price discovery
and another is lagging by at least ``LEAD_LAG_MIN_DELAY_MS``. For each lag
detection, emits a directional quote opportunity anchored to the leader's
fair value.

The runtime state (per-market price update timestamps, leader inference) lives
in the module-level ``LeadLagMM`` singleton in ``market_maker.py``. This scan
module is a stateless wrapper that:

1. Calls ``record_price`` for any prices provided in the matched pairs so the
   detector has fresh data to reason about.
2. Asks ``should_quote`` whether each platform in each pair lags enough.
3. Emits standardized opportunity dicts for the cases that pass.

Returns ``[]`` whenever ``LEAD_LAG_MM_ENABLED`` is false so an always-on call
in the orchestrator is a cheap no-op.
"""

import logging

logger = logging.getLogger(__name__)


def _extract_price(market: dict) -> float | None:
    """Best-effort YES-price extractor across the platform market dict shapes."""
    if not isinstance(market, dict):
        return None
    for key in ("yes_ask", "yes_price", "lastTradePrice", "price"):
        val = market.get(key)
        if val is None:
            continue
        try:
            price = float(val)
        except (TypeError, ValueError):
            continue
        if price > 1.0:
            price = price / 100.0
        if 0.0 < price < 1.0:
            return price
    return None


def scan_lead_lag_mm(
    matched_pairs: list[dict],
    detector=None,
    min_lag_ms: float | None = None,
    quote_size: float = 5.0,
) -> list[dict]:
    """Detect lagging platforms and emit LeadLagMM opportunities.

    Args:
        matched_pairs: Output of ``matcher.match_cross_platform`` — each entry
            has ``platform_a``, ``platform_b``, ``market_a``, ``market_b``,
            and a ``market_key``.
        detector: Optional ``LeadLagMM`` instance. Defaults to the module
            singleton ``market_maker.get_lead_lag_mm()`` so callers can share
            state with the live MM loop.
        min_lag_ms: Optional override for the minimum lag threshold (in ms).
            ``None`` defers to ``LEAD_LAG_MIN_DELAY_MS`` inside
            ``LeadLagMM.should_quote``.
        quote_size: Per-leg quote size in dollars to attach to the opp dict.

    Returns:
        List of ``LeadLagMM`` opportunity dicts (possibly empty).
    """
    from config import LEAD_LAG_MM_ENABLED

    if not LEAD_LAG_MM_ENABLED:
        return []

    if not matched_pairs:
        return []

    if detector is None:
        from market_maker import get_lead_lag_mm
        detector = get_lead_lag_mm()

    opps: list[dict] = []
    for pair in matched_pairs:
        market_key = (
            pair.get("market_key")
            or pair.get("_market_key")
            or ""
        )
        if not market_key:
            continue

        platform_a = pair.get("platform_a") or pair.get("platform_yes")
        platform_b = pair.get("platform_b") or pair.get("platform_no")
        if not platform_a or not platform_b:
            continue

        # Seed the detector with the prices on the pair so should_quote has
        # something to reason about even on the first call.
        for platform, market in ((platform_a, pair.get("market_a", {})),
                                 (platform_b, pair.get("market_b", {}))):
            price = _extract_price(market)
            if price is not None:
                detector.record_price(market_key, platform, price)

        # Check each platform for laggard status.
        for lagger in (platform_a, platform_b):
            if not detector.should_quote(market_key, lagger,
                                          min_lag_ms=min_lag_ms or 500.0):
                continue
            leader = detector.get_leader(market_key)
            if not leader or leader == lagger:
                continue
            lag_ms = detector.get_lag_ms(market_key, lagger)
            fair_value = detector.get_fair_value(market_key)
            if fair_value is None:
                continue

            opps.append({
                "type": "LeadLagMM",
                "_layer": 3,
                "market": f"{market_key[:60]} (lead-lag MM)",
                "prices": f"leader={leader}@{fair_value:.3f} → lagger={lagger}",
                "total_cost": f"${quote_size:.2f}",
                "net_profit": 0.0,
                "net_roi": 0.0,
                "confidence": 0.75,
                "_market_key": market_key,
                "_leader": leader,
                "_lagger": lagger,
                "_lag_ms": lag_ms,
                "_fair_value": fair_value,
                "_quote_size": quote_size,
            })

    return opps
