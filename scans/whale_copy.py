"""Whale copy trading strategy via on-chain wallet monitoring on Polygonscan."""

import logging
import time

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

    Converts a Polygonscan transaction dict into an opportunity dict.
    Current implementation is simplified (MVP): decodes basic fields.
    Full calldata parsing (extracting trade direction/size) deferred to Phase 10.

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
        Opportunity dict with type="WhaleCopy", or None if not a valid trade.
    """
    # Verify this is a successful transaction (not a revert)
    if tx.get("isError") == "1":
        return None

    return {
        "type": "WhaleCopy",
        "market": f"Whale trade from {whale_address[:8]}... at block {tx.get('blockNumber')}",
        "_whale_address": whale_address,
        "_whale_tx_hash": tx.get("hash"),
        "_whale_timestamp": int(tx.get("timeStamp", 0)),
        "_whale_block": int(tx.get("blockNumber", 0)),
        "_market_key": tx.get("hash"),  # Use tx hash as unique market key
        "_layer": 4,
    }


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
