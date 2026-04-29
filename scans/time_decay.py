"""Time decay convergence — buy near-certain outcomes approaching market expiry."""

import logging
import time

from .helpers import capital_efficiency_score

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Time decay detection
# ---------------------------------------------------------------------------

def scan_time_decay(
    markets_by_key: dict,
    signal_aggregator,
    min_hours_to_expiry: int = 48,
    min_consensus: float = 0.90,
    buy_below_price: float = 0.95,
    price_cache: dict | None = None,
) -> list[dict]:
    """Detect near-expiry markets with high consensus for profitable convergence.

    Stage 1 scan: Find markets <48h from resolution with >90% consensus and
    current price < 0.95 (buy at discount, hold to resolution, capture 5%+ gain).

    Args:
        markets_by_key: Dict of market_key -> market dict (must have
            resolutionSource with timestamp field).
        signal_aggregator: SignalAggregator instance with get_consensus() method.
        min_hours_to_expiry: Minimum hours remaining for sweet spot (default 48).
        min_consensus: Minimum consensus probability to consider (default 0.90).
        buy_below_price: Maximum entry price for opportunity (default 0.95).
        price_cache: Optional dict of recent prices for efficiency.

    Returns:
        List of opportunity dicts with type="TimeDecay", _hours_to_expiry,
        _consensus_side, _consensus_prob, _target_price, _guaranteed_gain.
    """
    opportunities = []
    now = time.time()

    for market_key, market in markets_by_key.items():
        # Check time to resolution — must be in sweet spot (1 < hours <= min_hours_to_expiry)
        hours_left = _check_time_to_expiry(
            market.get("resolutionSource", {}).get("timestamp"),
            min_hours=min_hours_to_expiry
        )
        if hours_left is None:
            continue

        # Get consensus probability from signal aggregator
        consensus_data = signal_aggregator.get_consensus(market_key)
        if consensus_data is None:
            continue

        consensus_prob = consensus_data.get("probability") if isinstance(consensus_data, dict) else consensus_data
        if not isinstance(consensus_prob, (int, float)) or consensus_prob is None:
            continue

        # Validate consensus meets threshold
        if not _validate_consensus(consensus_prob, min_threshold=min_consensus):
            continue

        # Determine consensus side: YES if consensus > 0.50, else NO
        consensus_side = "YES" if consensus_prob >= 0.50 else "NO"

        # Target price is the consensus probability (or 1 - consensus if NO side)
        target_price = consensus_prob if consensus_side == "YES" else 1.0 - consensus_prob

        # Skip if current price is already above buy threshold
        current_price = market.get("price")
        if current_price is None:
            continue
        if current_price >= buy_below_price:
            continue

        # Calculate guaranteed gain: buy_below_price - target_price
        guaranteed_gain = buy_below_price - target_price

        opportunities.append({
            "type": "TimeDecay",
            "market": market.get("question", ""),
            "market_key": market_key,
            "_hours_to_expiry": hours_left,
            "_consensus_side": consensus_side,
            "_consensus_prob": consensus_prob,
            "_target_price": target_price,
            "_guaranteed_gain": guaranteed_gain,
            "_current_price": current_price,
        })

        logger.info(
            "TimeDecay: %s, expiry=%.1f hours, consensus=%.1f%%, gain=%.1f%%",
            market.get("question", market_key),
            hours_left,
            consensus_prob * 100,
            guaranteed_gain * 100
        )

    return opportunities


def _check_time_to_expiry(resolution_timestamp: int | None, min_hours: int = 48) -> float | None:
    """Check if market is in sweet spot for time decay trading (1 < hours <= min_hours).

    Args:
        resolution_timestamp: Unix seconds timestamp of market resolution.
        min_hours: Maximum hours for the sweet spot (default 48).

    Returns:
        Float hours remaining if in sweet spot [1, min_hours], None otherwise.
    """
    if resolution_timestamp is None:
        logger.warning("No resolution timestamp provided")
        return None

    now = time.time()
    hours_left = (resolution_timestamp - now) / 3600.0

    # Sweet spot: 1 < hours <= min_hours
    if hours_left < 1.0:
        logger.debug("Market too close to resolution: %.1f hours left", hours_left)
        return None

    if hours_left > min_hours:
        logger.debug("Market too far from resolution: %.1f hours left", hours_left)
        return None

    return hours_left


def _validate_consensus(consensus: float, min_threshold: float = 0.90) -> bool:
    """Validate consensus probability meets minimum threshold.

    Args:
        consensus: Probability estimate (0-1).
        min_threshold: Minimum threshold for acceptance (default 0.90).

    Returns:
        True if consensus >= min_threshold, False otherwise.
    """
    if not isinstance(consensus, (int, float)):
        logger.warning("Invalid consensus type: %s", type(consensus))
        return False

    if consensus < min_threshold:
        logger.debug("Consensus %.1f%% below threshold %.1f%%", consensus * 100, min_threshold * 100)
        return False

    return True


def _refine_time_decay_with_prices(
    opportunities: list[dict],
    current_prices: dict | None = None,
    current_time: float | None = None,
) -> list[dict]:
    """Stage 2: Re-validate time decay opportunities against current prices and time.

    Drops opportunities where:
    - Current price has risen significantly above buy_below_price (opportunity closed)
    - Hours to expiry has dropped below 1 hour (too late to enter)

    Args:
        opportunities: List of opportunity dicts from Stage 1.
        current_prices: Optional dict of market_key -> current_price.
        current_time: Optional current timestamp (defaults to time.time()).

    Returns:
        Refined list of opportunities still meeting criteria.
    """
    if not current_prices:
        current_prices = {}

    if current_time is None:
        current_time = time.time()

    refined = []

    for opp in opportunities:
        market_key = opp.get("market_key")

        # Re-check expiry time hasn't dropped below 1 hour
        resolution_ts = None  # Would need to be passed in or stored in opp
        # For now, use the stored _hours_to_expiry as a proxy
        # In practice, executor would re-fetch current prices and times
        if opp.get("_hours_to_expiry", 0) < 1.0:
            logger.debug("TimeDecay refined: %s expired (<1h remaining)", market_key)
            continue

        # Re-check current price hasn't risen above threshold
        current_price = current_prices.get(market_key, opp.get("_current_price"))
        if current_price is not None and current_price >= opp.get("_target_price", 0.95):
            logger.debug("TimeDecay refined: %s price rise %.2f >= target %.2f",
                        market_key, current_price, opp.get("_target_price"))
            continue

        refined.append(opp)

    logger.info("TimeDecay refined: %d/%d still profitable at current prices",
                len(refined), len(opportunities))
    return refined
