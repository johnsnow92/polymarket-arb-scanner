"""Crash recovery for continuous mode.

On startup, checks for orphaned positions (open positions whose trades
may have been interrupted) and attempts to reconcile with platform APIs.
"""

import logging

from db import TradeDB

logger = logging.getLogger(__name__)


def reconcile_orphaned_positions(
    db: TradeDB,
    kalshi_client=None,
    pm_trader=None,
    predictit_client=None,
    betfair_client=None,
    manifold_client=None,
):
    """Check for orphaned positions and pending trades from a previous crash.

    Orphans are:
    1. Trades stuck in 'pending' status (order may or may not have been placed)
    2. Open positions whose trades have unknown fill status

    For each, query the platform API to determine actual order status,
    then update the DB accordingly.
    """
    # 1. Check for pending trades (interrupted mid-execution)
    pending_trades = db.get_pending_trades()
    if pending_trades:
        logger.info("Found %d pending trades from previous session — reconciling...", len(pending_trades))
        _reconcile_pending_trades(db, pending_trades, kalshi_client, pm_trader,
                                 predictit_client, betfair_client, manifold_client)

    # 2. Check for open positions with no recent activity
    open_positions = db.get_open_positions()
    if open_positions:
        logger.info("Found %d open positions from previous session.", len(open_positions))
    else:
        logger.debug("No orphaned positions found.")


def _reconcile_pending_trades(
    db: TradeDB,
    pending_trades: list[dict],
    kalshi_client=None,
    pm_trader=None,
    predictit_client=None,
    betfair_client=None,
    manifold_client=None,
):
    """Attempt to determine the actual status of pending trades."""
    resolved = 0
    for trade in pending_trades:
        trade_id = trade["id"]
        platform = trade["platform"]
        order_id = trade.get("order_id")

        if not order_id:
            # No order_id means the order was never placed — mark as failed
            db.update_trade_status(trade_id, "failed")
            logger.info("Trade #%d: no order_id, marked as failed.", trade_id)
            resolved += 1
            continue

        status = _check_order_status(
            platform, order_id,
            kalshi_client=kalshi_client,
            pm_trader=pm_trader,
            predictit_client=predictit_client,
            betfair_client=betfair_client,
            manifold_client=manifold_client,
        )

        if status == "filled":
            db.update_trade_status(trade_id, "filled")
            logger.info("Trade #%d on %s: confirmed filled.", trade_id, platform)
            resolved += 1
        elif status == "canceled":
            db.update_trade_status(trade_id, "failed")
            logger.info("Trade #%d on %s: confirmed canceled.", trade_id, platform)
            resolved += 1
        elif status == "unknown":
            # Could not determine — mark as orphaned for manual review
            db.update_trade_status(trade_id, "orphaned")
            logger.warning("Trade #%d on %s: could not determine status, marked orphaned.", trade_id, platform)
            resolved += 1
        # If status is "pending", leave it — the order may still be resting

    if resolved:
        logger.info("Reconciled %d/%d pending trades.", resolved, len(pending_trades))


def _check_order_status(
    platform: str,
    order_id: str,
    kalshi_client=None,
    pm_trader=None,
    predictit_client=None,
    betfair_client=None,
    manifold_client=None,
) -> str:
    """Query a platform API for the status of an order.

    Returns one of: "filled", "pending", "canceled", "unknown".
    """
    try:
        if platform == "polymarket" and pm_trader:
            resp = pm_trader.get_order_status(order_id)
            if resp:
                s = resp.get("status", "")
                if s == "matched":
                    return "filled"
                elif s in ("canceled", "expired"):
                    return "canceled"
                elif s in ("live", "resting"):
                    return "pending"
            return "unknown"

        elif platform == "kalshi" and kalshi_client:
            resp = kalshi_client.get_order_status(order_id)
            if resp:
                s = resp.get("status", "")
                if s == "executed":
                    return "filled"
                elif s in ("canceled", "expired"):
                    return "canceled"
                elif s == "resting":
                    return "pending"
            return "unknown"

        elif platform == "predictit" and predictit_client:
            resp = predictit_client.get_order_status(int(order_id))
            if resp:
                s = resp.get("status", resp.get("tradeStatus", ""))
                if s in ("Filled", "Completed"):
                    return "filled"
                elif s in ("Cancelled", "Expired"):
                    return "canceled"
            return "unknown"

        elif platform == "betfair" and betfair_client:
            resp = betfair_client.get_order_status(order_id)
            if resp:
                s = resp.get("status", "")
                if s == "EXECUTION_COMPLETE":
                    return "filled"
                elif s == "CANCELLED":
                    return "canceled"
                elif s == "EXECUTABLE":
                    return "pending"
            return "unknown"

        elif platform == "manifold" and manifold_client:
            resp = manifold_client.get_order_status(order_id)
            if resp:
                # Manifold bets are filled immediately
                return "filled"
            return "unknown"

    except Exception as e:
        logger.warning("Failed to check order %s on %s: %s", order_id, platform, e)

    return "unknown"
