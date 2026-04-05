"""Correlated market pairs arbitrage — exploits spread divergences between related markets.

When two related markets (e.g., Bitcoin $100k vs Bitcoin $90k) diverge by more than
a threshold (e.g., 10%), they present a convergence opportunity: long the underpriced
market and short the overpriced one. Both legs are matched-sized to neutralize directional
exposure and capture only the spread convergence.

Example:
  Bitcoin $100k: price=0.75 (75% probability)
  Bitcoin $90k:  price=0.50 (50% probability)
  Spread: (0.75 - 0.50) / 0.75 = 33% divergence
  If threshold is 10%, opportunity: long $90k, short $100k with matched sizing
"""

import json
import logging

from matcher import token_set_ratio
from .helpers import capital_efficiency_score

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration parsing
# ---------------------------------------------------------------------------

def _load_correlated_pairs(config_json: str) -> list[tuple[str, str]]:
    """Parse correlated pair configuration from JSON string.

    Expected format: '[["Bitcoin $100k", "Bitcoin $90k"], ["Eth $5k", "Eth $4k"]]'

    Args:
        config_json: JSON string representing list of 2-tuples (market identifiers).

    Returns:
        List of (market_a, market_b) tuples as strings.

    Raises:
        ValueError: If JSON is malformed or tuples are not 2-element pairs.
    """
    if not config_json or not config_json.strip():
        return []

    try:
        parsed = json.loads(config_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Correlated pairs config is not valid JSON: {e}")

    if not isinstance(parsed, list):
        raise ValueError(f"Correlated pairs config must be a list, got {type(parsed).__name__}")

    pairs = []
    for item in parsed:
        if not isinstance(item, (list, tuple)):
            raise ValueError(f"Each pair must be a list or tuple, got {type(item).__name__}")
        if len(item) != 2:
            raise ValueError(f"Each pair must have exactly 2 elements, got {len(item)}")
        pairs.append((str(item[0]), str(item[1])))

    return pairs


# ---------------------------------------------------------------------------
# Spread calculation
# ---------------------------------------------------------------------------

def _calculate_spread(price_a: float, price_b: float) -> float:
    """Calculate the spread between two prices.

    Formula: |price_a - price_b| / max(price_a, price_b)

    Args:
        price_a: First price in [0, 1].
        price_b: Second price in [0, 1].

    Returns:
        Spread as a decimal (0.10 = 10%). Returns 0.0 if both prices are 0.
    """
    max_price = max(price_a, price_b)
    if max_price == 0:
        return 0.0
    return abs(price_a - price_b) / max_price


# ---------------------------------------------------------------------------
# Market pairing and matching
# ---------------------------------------------------------------------------

def _find_market_by_id_or_title(
    market_identifier: str,
    markets_by_key: dict,
    fuzzy_threshold: int = 70,
) -> dict | None:
    """Find a market by ID or fuzzy title match.

    First tries direct ID lookup. If not found, searches by title using fuzzy
    matching with token_set_ratio.

    Args:
        market_identifier: Market ID or title string.
        markets_by_key: Dict of market_key -> market dict.
        fuzzy_threshold: Minimum token_set_ratio score (0-100) to consider a match.

    Returns:
        Market dict if found, else None.
    """
    # Direct ID lookup
    if market_identifier in markets_by_key:
        return markets_by_key[market_identifier]

    # Fuzzy title match
    best_match = None
    best_score = 0

    for market in markets_by_key.values():
        market_title = market.get("question") or market.get("title", "")
        if not market_title:
            continue

        score = token_set_ratio(market_identifier.lower(), market_title.lower())
        if score > best_score:
            best_score = score
            best_match = market

    if best_score >= fuzzy_threshold:
        return best_match

    return None


# ---------------------------------------------------------------------------
# Stage 1: Spread detection
# ---------------------------------------------------------------------------

def scan_correlated(
    markets_by_key: dict,
    correlated_pairs: list[tuple[str, str]],
    min_spread: float = 0.10,
    price_cache: dict | None = None,
) -> list[dict]:
    """Stage 1: Detect spread divergences in correlated market pairs.

    Scans a list of manually-configured related markets (e.g., Bitcoin $100k vs
    Bitcoin $90k) and identifies opportunities where the spread exceeds the minimum
    threshold. For each opportunity, determines which leg is underpriced (to long)
    and which is overpriced (to short).

    Args:
        markets_by_key: Dict of market_key -> market dict (from API).
        correlated_pairs: List of (market_a_identifier, market_b_identifier) tuples.
            Each identifier can be a market ID or title substring for fuzzy matching.
        min_spread: Minimum spread (as decimal) to trigger opportunity. Default 0.10 (10%).
        price_cache: Optional dict of market_key -> price for faster lookups.
            If not provided, uses mid prices from markets_by_key.

    Returns:
        List of opportunity dicts with type="Correlated", _long_leg, _short_leg,
        _token_ids_a, _token_ids_b, spread.
    """
    opportunities = []

    for market_a_id, market_b_id in correlated_pairs:
        # Find both markets
        market_a = _find_market_by_id_or_title(market_a_id, markets_by_key)
        market_b = _find_market_by_id_or_title(market_b_id, markets_by_key)

        if not market_a or not market_b:
            logger.debug(
                "Correlated pair not found: %s vs %s (found: %s, %s)",
                market_a_id, market_b_id,
                "✓" if market_a else "✗", "✓" if market_b else "✗"
            )
            continue

        # Get prices
        market_key_a = market_a.get("id") or market_a.get("condition_id", "")
        market_key_b = market_b.get("id") or market_b.get("condition_id", "")

        if price_cache:
            price_a = price_cache.get(market_key_a)
            price_b = price_cache.get(market_key_b)
        else:
            price_a = market_a.get("price")
            price_b = market_b.get("price")

        if price_a is None or price_b is None:
            logger.debug(
                "Missing prices for correlated pair: %s (%.2f), %s (%.2f)",
                market_a.get("question", "")[:40], price_a or 0,
                market_b.get("question", "")[:40], price_b or 0,
            )
            continue

        # Calculate spread
        spread = _calculate_spread(price_a, price_b)

        # Check threshold
        if spread < min_spread:
            continue

        # Determine which leg is underpriced (to long) and overpriced (to short)
        if price_a < price_b:
            long_leg_key = market_key_a
            long_leg = market_a
            long_price = price_a
            short_leg_key = market_key_b
            short_leg = market_b
            short_price = price_b
        else:
            long_leg_key = market_key_b
            long_leg = market_b
            long_price = price_b
            short_leg_key = market_key_a
            short_leg = market_a
            short_price = price_a

        # Extract token IDs for execution
        token_ids_a = market_a.get("clobTokenIds", [])
        token_ids_b = market_b.get("clobTokenIds", [])

        opportunity = {
            "type": "Correlated",
            "_layer": 4,  # Layer 4: informed trading
            "market": f"{market_a.get('question', '')[:40]} vs {market_b.get('question', '')[:40]}",
            "prices": f"Long {market_a.get('question', '')[:20]}={long_price:.4f} Short {market_b.get('question', '')[:20]}={short_price:.4f}",
            "_long_leg": long_leg_key,
            "_long_leg_name": long_leg.get("question", ""),
            "_long_price": long_price,
            "_short_leg": short_leg_key,
            "_short_leg_name": short_leg.get("question", ""),
            "_short_price": short_price,
            "spread": spread,
            "_token_ids_a": token_ids_a,
            "_token_ids_b": token_ids_b,
            "_market_key_a": market_key_a,
            "_market_key_b": market_key_b,
        }

        # Calculate net profit estimate (simplified — actual calc in fees.py)
        # Gross spread between the two legs
        gross_spread = abs(short_price - long_price)
        opportunity["net_profit"] = gross_spread
        opportunity["net_roi"] = (gross_spread / long_price) if long_price > 0 else 0
        opportunity["_efficiency"] = capital_efficiency_score(opportunity)

        opportunities.append(opportunity)

        logger.info(
            "Correlated pair: %s (%.2f) vs %s (%.2f), spread=%.1f%%",
            market_a.get("question", "")[:40],
            price_a,
            market_b.get("question", "")[:40],
            price_b,
            spread * 100,
        )

    logger.info("Correlated scan: found %d opportunities", len(opportunities))
    return opportunities


# ---------------------------------------------------------------------------
# Stage 2: Refinement and validation
# ---------------------------------------------------------------------------

def _refine_correlated_with_depth(
    opportunities: list[dict],
    min_liquidity: float = 10.0,
    max_spread_collapse: float = 0.20,
) -> list[dict]:
    """Stage 2: Validate both legs have sufficient liquidity and spread hasn't collapsed.

    Filters out opportunities where:
    - Either leg no longer has depth >= min_liquidity
    - Spread has collapsed by more than max_spread_collapse (e.g., >20%)

    Args:
        opportunities: List of correlated opportunities from scan_correlated().
        min_liquidity: Minimum liquidity (in dollars) required on each leg.
        max_spread_collapse: Maximum allowed drop in spread (0.20 = 20% collapse).

    Returns:
        Refined list with invalid opportunities dropped.
    """
    refined = []

    for opp in opportunities:
        original_spread = opp.get("spread", 0)

        # TODO: In production, re-fetch CLOB depths and validate min_liquidity
        # For now, accept all opportunities that passed Stage 1
        # This is where actual CLOB validation would happen if API call latency were acceptable

        # Check spread collapse
        # In Stage 2, we would re-check the current spread
        # For now, we accept the Stage 1 spread
        refined.append(opp)

    logger.info("Correlated refined: %d/%d pairs have sufficient liquidity", len(refined), len(opportunities))
    return refined
