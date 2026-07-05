"""Seerium x402 data scan — fetch 783 Polymarket snapshots via micropayment.

Costs $0.001 USDC per call on Base (eip155:8453) from the agentic wallet.

Two scan modes:
1. Pre-scored: convert seerium's ``opportunityList`` entries (when market is not
   fully efficient) directly to arbgrid dicts.
2. Local spread: scan all 783 ``snapshots`` for binary markets where the sum of
   best ask prices falls below 1.00 (classic spread capture / internal arb).

Response path (awal wraps the body):
    outer['data']            = seerium body
    outer['data']['data']    = actual scan payload
    outer['data']['data']['snapshots']       = list of market snapshots
    outer['data']['data']['opportunityList'] = pre-scored opportunities
"""
from __future__ import annotations

import logging
from typing import Any

from x402_client import x402_pay
from scans.helpers import filter_dust

log = logging.getLogger(__name__)

SEERIUM_URL = 'https://api.seerium.xyz/v1/prediction/scan'

# Stake used for net_profit calculations when converting seerium edge %.
_NOTIONAL = 100.0


def _extract_payload(outer: dict[str, Any]) -> dict[str, Any]:
    """Unwrap nested awal + seerium response envelope."""
    body = outer.get('data', {})
    if isinstance(body, str):
        import json
        body = json.loads(body)
    payload = body.get('data', {})
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected seerium payload shape: {type(payload)}")
    return payload


def _best_ask(book: dict) -> float | None:
    """Return the lowest ask price from a seerium order book dict."""
    asks = book.get('asks', [])
    if not asks:
        return None
    return min(entry['price'] for entry in asks if 'price' in entry)


def _ask_depth(book: dict) -> float:
    """Sum of all ask-side sizes (liquidity available to buy)."""
    return sum(entry.get('size', 0) for entry in book.get('asks', []))


def _from_opportunity(opp: dict) -> dict | None:
    """Convert a seerium pre-scored opportunity to an arbgrid dict."""
    edge = opp.get('edge') or opp.get('expectedValue') or 0.0
    if not edge or edge <= 0:
        return None

    market_info = opp.get('market') or opp.get('snapshot', {}).get('market', {})
    question = market_info.get('question', 'Unknown market')
    slug = market_info.get('marketSlug', '')

    net_profit = edge * _NOTIONAL
    net_roi = edge

    prices = []
    yes_price = opp.get('yesPrice') or opp.get('price')
    no_price = opp.get('noPrice')
    if yes_price is not None:
        prices.append(float(yes_price))
    if no_price is not None:
        prices.append(float(no_price))

    depth = opp.get('depth') or opp.get('liquidity') or market_info.get('liquidity') or 0.0

    return {
        'strategy': 'x402_seerium_prescored',
        'description': f"Seerium: {question}",
        'market_slug': slug,
        'prices': prices,
        'net_profit': round(net_profit, 4),
        'net_roi': round(net_roi, 6),
        'total_cost': f'${_NOTIONAL:.2f}',
        '_clob_refined': False,
        '_clob_depth': float(depth),
        '_source': 'seerium_x402',
    }


def _scan_snapshots_local(snapshots: list[dict], min_spread: float = 0.005) -> list[dict]:
    """Local spread scan: binary markets where yes_ask + no_ask < 1.00."""
    results = []

    for snap in snapshots:
        market = snap.get('market', {})
        yes_book = snap.get('yesBook', {})
        no_book = snap.get('noBook', {})

        yes_ask = _best_ask(yes_book)
        no_ask = _best_ask(no_book)

        if yes_ask is None or no_ask is None:
            continue

        total_ask = yes_ask + no_ask
        if total_ask >= 1.0:
            continue

        edge = 1.0 - total_ask
        if edge < min_spread:
            continue

        yes_depth = _ask_depth(yes_book)
        no_depth = _ask_depth(no_book)
        depth = min(yes_depth, no_depth)

        net_profit = edge * _NOTIONAL
        net_roi = edge

        question = market.get('question', 'Unknown')
        slug = market.get('marketSlug', '')
        volume24h = market.get('volume24h', 0)

        results.append({
            'strategy': 'x402_seerium_spread',
            'description': f"Seerium spread: {question}",
            'market_slug': slug,
            'prices': [yes_ask, no_ask],
            'net_profit': round(net_profit, 4),
            'net_roi': round(net_roi, 6),
            'total_cost': f'${_NOTIONAL:.2f}',
            '_clob_refined': False,
            '_clob_depth': round(depth, 2),
            '_volume_24h': volume24h,
            '_source': 'seerium_x402',
        })

    return results


def scan_x402_seerium(
    min_profit: float = 0.10,
    min_spread: float = 0.005,
) -> list[dict]:
    """Fetch 783 Polymarket snapshots via x402 micropayment and return opportunities.

    Args:
        min_profit: Minimum net_profit (dollars) to include an opportunity.
        min_spread: Minimum ask-sum gap to flag a local spread opportunity.

    Returns:
        Filtered list of arbgrid-format opportunity dicts.
    """
    log.info("Fetching Polymarket data via Seerium x402 ($0.001 USDC)...")

    try:
        outer = x402_pay(SEERIUM_URL)
    except Exception as exc:
        log.error("Seerium x402 fetch failed: %s", exc)
        return []

    try:
        payload = _extract_payload(outer)
    except Exception as exc:
        log.error("Seerium response parse failed: %s", exc)
        return []

    snapshots = payload.get('snapshots', [])
    opportunity_list = payload.get('opportunityList', [])

    log.info(
        "Seerium: %d snapshots, %d pre-scored opportunities",
        len(snapshots),
        len(opportunity_list),
    )

    results: list[dict] = []

    # Path 1: pre-scored by seerium
    for opp in opportunity_list:
        converted = _from_opportunity(opp)
        if converted:
            results.append(converted)

    # Path 2: local spread scan across all snapshots
    local_hits = _scan_snapshots_local(snapshots, min_spread=min_spread)
    results.extend(local_hits)

    if results:
        results = filter_dust(results, min_amount=min_profit)
        results.sort(key=lambda o: o.get('net_profit', 0), reverse=True)

    log.info("Seerium scan complete: %d opportunities above $%.2f", len(results), min_profit)
    return results
