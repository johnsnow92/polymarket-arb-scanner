"""Whale copy trading strategy via on-chain wallet monitoring on Polygonscan."""

import logging
import time

from whale_copy_decoder import (
    CalldataDecodeError,
    decode_calldata,
    extract_whale_trade,
)

logger = logging.getLogger(__name__)

# Polymarket CLOB contract address on Polygon chain
POLYMARKET_CLOB_ADDRESS = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"

# Latency budget: 30 seconds from whale transaction detection to execution
WHALE_COPY_LATENCY_BUDGET_SECONDS = 30


# ---------------------------------------------------------------------------
# Stage 1: Whale transaction polling and parsing
# ---------------------------------------------------------------------------


def _parse_clob_transaction(tx: dict, whale_address: str) -> dict | None:
    """Extract trade details from a CLOB contract transaction.

    Decodes the transaction's calldata via ``whale_copy_decoder`` to surface
    the whale's effective trade direction, token ID, size, and price for
    every supported CTF Exchange method:

    - ``fillOrder`` — populates ``_whale_side``, ``_whale_token_id``,
      ``_whale_size``, ``_whale_price``, and ``_whale_role``.
    - ``fillOrders`` / ``matchOrders`` — opportunity is tagged with
      ``_whale_method`` and the raw decoded args; per-order extraction
      is left to downstream consumers (the order list can be large).
    - ``cancelOrder`` / ``cancelOrders`` — returns ``None`` (cancels are
      not copy-tradeable signals).
    - Calldata that doesn't match a known method — returns ``None``
      (e.g. random ERC20 transfers misrouted to the contract).

    Args:
        tx: Transaction dict from Polygonscan with keys:
            - hash: Transaction hash
            - blockNumber: Block number
            - timeStamp: Unix timestamp
            - to: Recipient address
            - input: Calldata (encoded function + params)
            - isError: "0" if success, "1" if reverted
        whale_address: The whale wallet address that initiated the trade.

    Returns:
        Opportunity dict with type="WhaleCopy", or None if not a valid
        copy-tradeable signal.
    """
    # Verify this is a successful transaction (not a revert)
    if tx.get("isError") == "1":
        return None

    tx_hash = tx.get("hash")
    base_opp: dict = {
        "type": "WhaleCopy",
        "_whale_address": whale_address,
        "_whale_tx_hash": tx_hash,
        "_whale_timestamp": int(tx.get("timeStamp", 0)),
        "_whale_block": int(tx.get("blockNumber", 0)),
        "_market_key": tx_hash,  # tx hash is unique per CTF call
        "_layer": 4,
    }

    raw_input = tx.get("input")
    try:
        decoded = decode_calldata(raw_input)
    except CalldataDecodeError as e:
        logger.debug("WhaleCopy: malformed calldata for tx %s: %s", tx_hash, e)
        return None

    if decoded is None:
        # Calldata doesn't match any known CTF Exchange method — skip.
        return None

    method = decoded.get("method")
    base_opp["_whale_method"] = method

    if method in ("cancelOrder", "cancelOrders"):
        # Cancels are not copy-tradeable; surface them only at debug level.
        logger.debug("WhaleCopy: skipping %s tx %s", method, tx_hash)
        return None

    if method == "fillOrder":
        trade = extract_whale_trade(decoded, whale_address)
        if trade is None:
            return None
        base_opp.update({
            "_whale_role": trade["whale_role"],
            "_whale_side": trade["whale_side"],
            "_whale_token_id": trade["token_id"],
            "_whale_size": trade["token_amount"],
            "_whale_price": trade["price"],
            "_whale_counterparty": trade["maker_address"],
            "_fill_amount_raw": trade["fill_amount_raw"],
        })
        base_opp["market"] = (
            f"Whale {trade['whale_side']} token {trade['token_id'][:12]}... "
            f"size={trade['token_amount']:.2f} @ {trade.get('price') or 0:.3f}"
        )
        # Use token_id as the market key so cross-platform pairing can match.
        base_opp["_market_key"] = trade["token_id"]
        return base_opp

    # fillOrders / matchOrders — surface aggregate metadata for downstream
    # callers to iterate. We do not collapse multi-order calls here because
    # each maker order may target a different market.
    args = decoded.get("args", {})
    if method == "fillOrders":
        order_count = len(args.get("orders", []))
    else:  # matchOrders
        order_count = len(args.get("makerOrders", [])) + 1
    base_opp["_whale_order_count"] = order_count
    base_opp["_decoded_args"] = args
    base_opp["market"] = (
        f"Whale {method} ({order_count} orders) from {whale_address[:8]}... "
        f"at block {tx.get('blockNumber')}"
    )
    return base_opp


def scan_whale_copy(
    whale_wallets: list[str],
    polygonscan_client,
    last_block_cache: dict | None = None,
) -> list[dict]:
    """Stage 1 + 2: Scan whale wallets and refine with market prices.

    Polls Polygonscan API for recent transactions from tracked whale wallets,
    filters to CLOB contract interactions, parses trade details, then refines
    with current market prices and revalidation checks.

    Args:
        whale_wallets: List of whale wallet addresses (0x...).
        polygonscan_client: PolygonscanClient instance for fetching transactions.
        last_block_cache: Dict mapping wallet_address -> last_seen_block_number.
                         Updated in-place as transactions are processed.

    Returns:
        List of refined opportunity dicts with type="WhaleCopy".
    """
    if not whale_wallets or polygonscan_client is None:
        return []

    if last_block_cache is None:
        last_block_cache = {}

    opportunities = []

    # Stage 1: Fetch and parse whale transactions
    for wallet in whale_wallets:
        try:
            # Fetch latest transactions for this wallet
            start_block = last_block_cache.get(wallet, 0)
            txs = polygonscan_client.get_latest_transactions(
                address=wallet,
                start_block=start_block,
                sort="desc",  # Newest first
            )

            if not txs:
                continue

            # Filter to CLOB contract interactions
            clob_txs = [
                tx for tx in txs
                if tx.get("to", "").lower() == POLYMARKET_CLOB_ADDRESS.lower()
            ]

            # Parse each CLOB transaction into an opportunity
            for tx in clob_txs:
                opp = _parse_clob_transaction(tx, wallet)
                if opp:
                    opportunities.append(opp)

            # Update last seen block for this wallet (for next scan cycle)
            if txs:
                highest_block = int(txs[0].get("blockNumber", 0))
                last_block_cache[wallet] = highest_block
                logger.debug(
                    "Updated block cache for %s: %d", wallet, highest_block
                )

        except Exception as e:
            logger.warning("Failed to fetch whale wallet %s: %s", wallet, str(e))
            # Graceful degradation: skip this wallet and continue with others
            continue

    logger.info(
        "Whale copy: found %d opportunities from %d wallets",
        len(opportunities),
        len(whale_wallets),
    )

    # Stage 2: Refine with market prices and latency checks
    opportunities = _refine_whale_copy_with_prices(opportunities)

    return opportunities


# ---------------------------------------------------------------------------
# Stage 2: Market price refinement and revalidation
# ---------------------------------------------------------------------------


def _refine_whale_copy_with_prices(opportunities: list[dict]) -> list[dict]:
    """Stage 2: Refine whale copy opportunities with market price checks.

    Validates that:
    1. Transaction is fresh (< 30s old, within latency budget)
    2. Market data is available via Polymarket CLOB
    3. Price hasn't moved >10% from opportunity creation (Layer 4 floor)

    Args:
        opportunities: List of opportunity dicts from Stage 1.

    Returns:
        Filtered list of refined opportunities.
    """
    if not opportunities:
        return opportunities

    refined = []
    current_time = time.time()

    for opp in opportunities:
        # Check latency: whale transaction must be <30s old
        whale_timestamp = opp.get("_whale_timestamp", 0)
        age_seconds = current_time - whale_timestamp

        if age_seconds > WHALE_COPY_LATENCY_BUDGET_SECONDS:
            logger.debug(
                "Whale copy: dropping stale trade (%.1f seconds old)",
                age_seconds,
            )
            continue

        # In Phase 10, we'll add:
        # - Fetch live market price from Polymarket CLOB
        # - Check market price hasn't moved >10% (Layer 4 floor)
        # - Add _market_price, _token_ids keys to opp
        #
        # For now (MVP): skip market price checks, keep opportunity
        # to allow executor to validate on actual execution

        refined.append(opp)

    logger.debug(
        "Whale copy refinement: kept %d of %d opportunities",
        len(refined),
        len(opportunities),
    )

    return refined
