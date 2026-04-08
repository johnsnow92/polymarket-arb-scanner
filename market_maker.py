"""Market making engine — provide liquidity and earn bid-ask spreads.

Components:
- QuoteEngine: calculates optimal bid/ask quotes
- InventoryTracker: tracks net position per market per platform
- QuoteManager: places, cancels, and updates resting limit orders
- MarketMaker: orchestrates the full market making loop
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

DEFAULT_MIN_SPREAD = 0.03       # Minimum bid-ask spread ($0.03)
DEFAULT_MAX_INVENTORY = 50.0    # Max position per market ($50)
DEFAULT_MAX_TOTAL_EXPOSURE = 500.0  # Max total MM exposure ($500)
DEFAULT_QUOTE_SIZE = 5.0        # Default size per quote ($5)
DEFAULT_INVENTORY_SKEW = 0.5    # How much inventory skews quotes (0-1)
DEFAULT_REFRESH_INTERVAL = 10.0  # Seconds between quote updates


# ---------------------------------------------------------------------------
# InventoryTracker
# ---------------------------------------------------------------------------

class InventoryTracker:
    """Track net position per market per platform.

    Thread-safe.  Positions are tracked in dollar terms.
    """

    def __init__(self, max_per_market: float = DEFAULT_MAX_INVENTORY,
                 max_total: float = DEFAULT_MAX_TOTAL_EXPOSURE):
        self.max_per_market = max_per_market
        self.max_total = max_total
        self._positions: dict[str, dict[str, float]] = {}  # {market_key: {platform: position}}
        self._lock = threading.Lock()

    def update(self, market_key: str, platform: str, delta: float) -> None:
        """Record a position change.

        Args:
            market_key: Market identifier.
            platform: Platform name.
            delta: Change in position (+buy, -sell) in dollars.
        """
        with self._lock:
            if market_key not in self._positions:
                self._positions[market_key] = {}
            current = self._positions[market_key].get(platform, 0.0)
            self._positions[market_key][platform] = current + delta

    def get_position(self, market_key: str, platform: str = "") -> float:
        """Get net position for a market (optionally filtered by platform).

        Returns:
            Net position in dollars. Positive = long YES, negative = long NO.
        """
        with self._lock:
            if market_key not in self._positions:
                return 0.0
            if platform:
                return self._positions[market_key].get(platform, 0.0)
            return sum(self._positions[market_key].values())

    def get_total_exposure(self) -> float:
        """Get total absolute exposure across all markets."""
        with self._lock:
            total = 0.0
            for market_positions in self._positions.values():
                total += sum(abs(v) for v in market_positions.values())
            return total

    def can_trade(self, market_key: str, size: float) -> bool:
        """Check if a trade would exceed inventory limits.

        Args:
            market_key: Market identifier.
            size: Absolute trade size in dollars.
        """
        current = abs(self.get_position(market_key))
        total = self.get_total_exposure()
        return (current + size <= self.max_per_market and
                total + size <= self.max_total)

    def needs_hedge(self, market_key: str) -> bool:
        """Check if a market position needs hedging (>80% of max)."""
        position = abs(self.get_position(market_key))
        return position > self.max_per_market * 0.8

    def get_all_positions(self) -> dict[str, float]:
        """Get all market positions (net across platforms)."""
        with self._lock:
            return {
                key: sum(plats.values())
                for key, plats in self._positions.items()
                if any(v != 0 for v in plats.values())
            }


# ---------------------------------------------------------------------------
# QuoteEngine
# ---------------------------------------------------------------------------

class QuoteEngine:
    """Calculate optimal bid/ask quotes for market making.

    Spread formula: base_spread + inventory_skew + volatility_adjustment
    """

    def __init__(
        self,
        min_spread: float = DEFAULT_MIN_SPREAD,
        inventory_skew_factor: float = DEFAULT_INVENTORY_SKEW,
    ):
        self.min_spread = min_spread
        self.inventory_skew_factor = inventory_skew_factor

    def calculate_quotes(
        self,
        mid_price: float,
        inventory: float = 0.0,
        max_inventory: float = DEFAULT_MAX_INVENTORY,
        volatility: float = 0.0,
    ) -> dict:
        """Calculate bid and ask prices.

        Args:
            mid_price: Current mid-market price (0-1).
            inventory: Current net position. Positive = long, quotes skew to sell.
            max_inventory: Maximum inventory for skew calculation.
            volatility: Recent price volatility (0-1). Widens spread.

        Returns:
            Dict with ``bid``, ``ask``, ``spread``, ``skew``.
        """
        half_spread = self.min_spread / 2

        # Volatility adjustment: wider spread in volatile markets
        vol_adj = volatility * 0.5  # 10% vol -> 5 cent wider spread

        # Inventory skew: when long, lower bid (buy less) and lower ask (sell faster)
        if max_inventory > 0:
            inventory_ratio = inventory / max_inventory  # -1 to +1
        else:
            inventory_ratio = 0.0
        skew = inventory_ratio * self.inventory_skew_factor * half_spread

        total_half = half_spread + vol_adj

        bid = mid_price - total_half - skew
        ask = mid_price + total_half - skew

        # Clamp to valid price range
        bid = max(0.01, min(0.99, bid))
        ask = max(0.01, min(0.99, ask))

        # Ensure ask > bid
        if ask <= bid:
            mid = (bid + ask) / 2
            bid = mid - 0.01
            ask = mid + 0.01

        return {
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "spread": round(ask - bid, 4),
            "skew": round(skew, 4),
            "mid": round(mid_price, 4),
        }


# ---------------------------------------------------------------------------
# QuoteManager
# ---------------------------------------------------------------------------

class QuoteManager:
    """Place, cancel, and update resting limit orders.

    Wraps platform-specific order placement APIs behind a common interface.
    """

    def __init__(self):
        self._active_orders: dict[str, dict] = {}  # order_id -> order_info
        self._lock = threading.Lock()

    def place_quote(
        self,
        platform: str,
        market_key: str,
        side: str,
        price: float,
        size: float,
        trader=None,
    ) -> str | None:
        """Place a resting limit order (GTC).

        Args:
            platform: Platform name.
            market_key: Market identifier.
            side: "bid" or "ask".
            price: Limit price.
            size: Order size in dollars.
            trader: Platform-specific trader/client instance.

        Returns:
            Order ID or None on failure.
        """
        if trader is None:
            logger.debug("No trader for %s — dry run quote %s %s @ %.4f",
                         platform, side, market_key, price)
            # Dry run: generate a fake order ID for tracking
            order_id = f"dry_{platform}_{market_key}_{side}_{time.time():.0f}"
            with self._lock:
                self._active_orders[order_id] = {
                    "platform": platform,
                    "market_key": market_key,
                    "side": side,
                    "price": price,
                    "size": size,
                    "status": "resting",
                    "placed_at": time.time(),
                }
            return order_id

        # Live order placement would go here, dispatching to platform API
        # For now, log the intended action
        logger.info("MM quote: %s %s %s @ %.4f ($%.2f)",
                     platform, side, market_key, price, size)
        return None

    def cancel_quote(self, order_id: str, trader=None) -> bool:
        """Cancel a resting order.

        Args:
            order_id: Order ID to cancel.
            trader: Platform-specific trader/client instance.

        Returns:
            True if cancelled or already gone.
        """
        with self._lock:
            if order_id in self._active_orders:
                self._active_orders[order_id]["status"] = "cancelled"
                del self._active_orders[order_id]
                return True
        return False

    def cancel_all(self, market_key: str = "", trader=None) -> int:
        """Cancel all active orders, optionally filtered by market.

        Returns number of orders cancelled.
        """
        with self._lock:
            to_cancel = [
                oid for oid, info in self._active_orders.items()
                if not market_key or info["market_key"] == market_key
            ]
            for oid in to_cancel:
                self._active_orders[oid]["status"] = "cancelled"
                del self._active_orders[oid]
            return len(to_cancel)

    def get_active_orders(self, market_key: str = "") -> list[dict]:
        """Get all active orders, optionally filtered by market."""
        with self._lock:
            return [
                {"order_id": oid, **info}
                for oid, info in self._active_orders.items()
                if not market_key or info["market_key"] == market_key
            ]

    def record_fill(self, order_id: str, fill_size: float, fill_price: float) -> None:
        """Record a fill event for an order."""
        with self._lock:
            if order_id in self._active_orders:
                order = self._active_orders[order_id]
                order["status"] = "filled"
                order["fill_size"] = fill_size
                order["fill_price"] = fill_price
                order["filled_at"] = time.time()
                # Remove from active
                del self._active_orders[order_id]


# ---------------------------------------------------------------------------
# MarketMaker
# ---------------------------------------------------------------------------

class MarketMaker:
    """Orchestrates the full market making loop.

    Selects markets, calculates quotes, manages orders and inventory.
    Runs as a background task in continuous mode.
    """

    def __init__(
        self,
        inventory: InventoryTracker | None = None,
        quote_engine: QuoteEngine | None = None,
        quote_manager: QuoteManager | None = None,
        min_spread: float = DEFAULT_MIN_SPREAD,
        quote_size: float = DEFAULT_QUOTE_SIZE,
        max_inventory: float = DEFAULT_MAX_INVENTORY,
        max_total_exposure: float = DEFAULT_MAX_TOTAL_EXPOSURE,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
        dry_run: bool = True,
    ):
        self.inventory = inventory or InventoryTracker(max_inventory, max_total_exposure)
        self.quote_engine = quote_engine or QuoteEngine(min_spread)
        self.quote_manager = quote_manager or QuoteManager()
        self.quote_size = quote_size
        self.max_inventory = max_inventory
        self.refresh_interval = refresh_interval
        self.dry_run = dry_run
        self._running = False
        self._active_markets: dict[str, dict] = {}  # market_key -> market info
        self._lock = threading.Lock()

    def add_market(self, market_key: str, platform: str, mid_price: float,
                   token_id: str = "", ticker: str = "") -> None:
        """Register a market for market making.

        Args:
            market_key: Unique market identifier.
            platform: Platform to make markets on.
            mid_price: Current mid-market price.
            token_id: Polymarket token ID (if applicable).
            ticker: Kalshi ticker (if applicable).
        """
        with self._lock:
            self._active_markets[market_key] = {
                "platform": platform,
                "mid_price": mid_price,
                "token_id": token_id,
                "ticker": ticker,
                "last_update": time.time(),
            }

    def remove_market(self, market_key: str) -> None:
        """Stop making markets on a specific market."""
        self.quote_manager.cancel_all(market_key)
        with self._lock:
            self._active_markets.pop(market_key, None)

    def update_price(self, market_key: str, mid_price: float) -> None:
        """Update the mid price for a market (e.g. from WebSocket feed)."""
        with self._lock:
            if market_key in self._active_markets:
                self._active_markets[market_key]["mid_price"] = mid_price
                self._active_markets[market_key]["last_update"] = time.time()

    def refresh_quotes(self, market_key: str = "", trader=None) -> list[dict]:
        """Recalculate and update quotes for active markets.

        Args:
            market_key: If set, refresh only this market. Otherwise refresh all.
            trader: Platform trader instance for order placement.

        Returns:
            List of new quote dicts placed.
        """
        with self._lock:
            markets = dict(self._active_markets)

        if market_key:
            markets = {k: v for k, v in markets.items() if k == market_key}

        new_quotes = []
        for mkey, info in markets.items():
            platform = info["platform"]
            mid = info["mid_price"]

            # Skip invalid prices
            if mid <= 0.01 or mid >= 0.99:
                continue

            # Get current inventory
            inventory = self.inventory.get_position(mkey)

            # Skip if inventory limit hit and we can't reduce
            if abs(inventory) >= self.max_inventory:
                continue

            # Calculate quotes
            quotes = self.quote_engine.calculate_quotes(
                mid, inventory, self.max_inventory
            )

            # Cancel existing quotes for this market
            self.quote_manager.cancel_all(mkey)

            # Place new bid
            if self.inventory.can_trade(mkey, self.quote_size):
                bid_id = self.quote_manager.place_quote(
                    platform, mkey, "bid", quotes["bid"], self.quote_size,
                    trader=trader if not self.dry_run else None,
                )
                if bid_id:
                    new_quotes.append({
                        "order_id": bid_id,
                        "market": mkey,
                        "side": "bid",
                        "price": quotes["bid"],
                        "size": self.quote_size,
                    })

            # Place new ask
            if self.inventory.can_trade(mkey, self.quote_size):
                ask_id = self.quote_manager.place_quote(
                    platform, mkey, "ask", quotes["ask"], self.quote_size,
                    trader=trader if not self.dry_run else None,
                )
                if ask_id:
                    new_quotes.append({
                        "order_id": ask_id,
                        "market": mkey,
                        "side": "ask",
                        "price": quotes["ask"],
                        "size": self.quote_size,
                    })

        if new_quotes:
            logger.info("MM refreshed %d quotes across %d markets",
                        len(new_quotes), len(markets))
        return new_quotes

    def on_fill(self, order_id: str, market_key: str, platform: str,
                side: str, price: float, size: float) -> None:
        """Handle a fill event: update inventory, record fill, check hedging.

        Args:
            order_id: Filled order ID.
            market_key: Market identifier.
            platform: Platform name.
            side: "bid" or "ask".
            price: Fill price.
            size: Fill size in dollars.
        """
        # Update inventory
        delta = size if side == "bid" else -size
        self.inventory.update(market_key, platform, delta)

        # Record the fill
        self.quote_manager.record_fill(order_id, size, price)

        logger.info("MM fill: %s %s %s @ %.4f ($%.2f) | inventory=%.2f",
                     platform, side, market_key, price, size,
                     self.inventory.get_position(market_key))

        # Check if hedging is needed
        if self.inventory.needs_hedge(market_key):
            logger.warning("MM: inventory on %s exceeds 80%% of max — hedge needed",
                           market_key)

    def generate_opportunities(self) -> list[dict]:
        """Generate market making pseudo-opportunities for the executor.

        Returns a list of opportunity dicts (one per active market) that
        the executor can process in dry-run mode for reporting.
        """
        with self._lock:
            markets = dict(self._active_markets)

        opportunities = []
        for mkey, info in markets.items():
            mid = info["mid_price"]
            if mid <= 0.01 or mid >= 0.99:
                continue

            inventory = self.inventory.get_position(mkey)
            quotes = self.quote_engine.calculate_quotes(
                mid, inventory, self.max_inventory
            )

            spread = quotes["spread"]
            # Estimated profit per round-trip (both sides fill)
            est_profit = spread - (spread * 0.03)  # ~3% fee estimate

            if est_profit <= 0:
                continue

            opportunities.append({
                "type": "MarketMake",
                "_layer": 3,  # Layer 3: market making
                "market": mkey,
                "prices": f"bid={quotes['bid']:.4f} ask={quotes['ask']:.4f} mid={mid:.4f}",
                "total_cost": f"${self.quote_size:.2f}",
                "net_profit": est_profit,
                "net_roi": est_profit / self.quote_size if self.quote_size > 0 else 0,
                "_platform": info["platform"],
                "_bid_price": quotes["bid"],
                "_ask_price": quotes["ask"],
                "_mid_price": mid,
                "_spread": spread,
                "_inventory": inventory,
                "_market_key": mkey,
            })

        return opportunities

    def stop(self) -> None:
        """Stop market making — cancel all outstanding quotes."""
        self._running = False
        cancelled = self.quote_manager.cancel_all()
        logger.info("MM stopped: cancelled %d outstanding quotes", cancelled)

    def get_status(self) -> dict:
        """Get current market maker status for dashboard."""
        with self._lock:
            num_markets = len(self._active_markets)

        active_orders = self.quote_manager.get_active_orders()
        positions = self.inventory.get_all_positions()

        return {
            "active_markets": num_markets,
            "active_orders": len(active_orders),
            "total_exposure": self.inventory.get_total_exposure(),
            "positions": positions,
            "dry_run": self.dry_run,
        }


# ---------------------------------------------------------------------------
# RewardTracker (Polymarket)
# ---------------------------------------------------------------------------

class RewardTracker:
    """Track Polymarket reward program metadata and calculate optimal spreads.

    Thread-safe cache of reward scores per market with TTL-based expiration.
    """

    def __init__(self):
        self._reward_cache: dict[str, dict] = {}  # {market_key: reward_data}
        self._cache_timestamps: dict[str, float] = {}  # {market_key: timestamp}
        self._lock = threading.Lock()

    def update_polymarket_reward(self, market_key: str, reward_data: dict,
                                ttl_seconds: float = 300.0) -> None:
        """Update reward metadata for a market.

        Args:
            market_key: Market identifier (conditionId).
            reward_data: Dict with min_incentive_size, max_incentive_spread, pool_size_usdc, etc.
            ttl_seconds: Cache TTL in seconds.
        """
        with self._lock:
            self._reward_cache[market_key] = reward_data
            self._cache_timestamps[market_key] = time.time() + ttl_seconds

    def get_polymarket_reward(self, market_key: str) -> dict | None:
        """Get cached reward metadata for a market.

        Returns None if not cached, expired, or never set.
        """
        with self._lock:
            if market_key not in self._reward_cache:
                return None
            expiry = self._cache_timestamps.get(market_key, 0)
            if time.time() > expiry:
                # Cache expired
                del self._reward_cache[market_key]
                if market_key in self._cache_timestamps:
                    del self._cache_timestamps[market_key]
                return None
            return self._reward_cache[market_key]

    def calculate_optimal_reward_spread(self, market_key: str, mid_price: float,
                                       inventory: float = 0.0) -> dict | None:
        """Calculate bid/ask spread optimized for reward qualification.

        Takes into account platform-specific constraints and inventory position.

        Args:
            market_key: Market identifier.
            mid_price: Current mid-price (0-1).
            inventory: Current inventory position (positive = long).

        Returns:
            Dict with bid, ask, spread, or None if no reward data.
        """
        reward_data = self.get_polymarket_reward(market_key)
        if not reward_data:
            return None

        max_spread = reward_data.get("max_incentive_spread", 0.05)

        # Target spread: 60% of max for good reward score while staying competitive
        target_spread = max_spread * 0.6
        half_spread = target_spread / 2

        # Apply inventory skew: when long, tighten ask (sell faster)
        skew = 0.0
        if inventory > 0:
            skew = -target_spread * 0.1

        bid = mid_price - half_spread + skew
        ask = mid_price + half_spread + skew

        # Clamp to valid range
        bid = max(0.01, min(0.99, bid))
        ask = max(0.01, min(0.99, ask))

        return {
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "spread": round(ask - bid, 4),
            "reward_optimized": True,
        }


# ---------------------------------------------------------------------------
# KalshiRewardTracker
# ---------------------------------------------------------------------------

class KalshiRewardTracker:
    """Track Kalshi liquidity incentive program participation via local order logging.

    Kalshi has no public reward API, so we track qualifying order metrics locally.
    Thread-safe.
    """

    def __init__(self, db=None):
        """Initialize tracker.

        Args:
            db: TradeDB instance for persisting reward metrics (optional).
        """
        self._db = db
        self._active_orders: dict[str, dict] = {}  # {order_id: order_data}
        self._lock = threading.Lock()

    def log_order_placed(self, order_id: str, market_key: str, size: float,
                        price: float, mid_price: float, side: str) -> None:
        """Log a Kalshi limit order placement.

        Args:
            order_id: Order ID from exchange.
            market_key: Market identifier.
            size: Order size in dollars.
            price: Limit price.
            mid_price: Current mid-price for spread calculation.
            side: "buy" or "sell".
        """
        spread = abs(price - mid_price) / mid_price if mid_price > 0 else 0

        with self._lock:
            self._active_orders[order_id] = {
                "market_key": market_key,
                "size": size,
                "price": price,
                "side": side,
                "spread": spread,
                "placed_at": time.time(),
            }

        # Persist to database if available
        if self._db:
            self._db.log_reward_metric(
                platform="kalshi",
                market_key=market_key,
                order_id=order_id,
                event="placed",
                size=size,
                spread=spread,
                resting_seconds=0,
            )

    def log_order_cancelled(self, order_id: str) -> None:
        """Log a Kalshi order cancellation.

        Args:
            order_id: Order ID that was cancelled.
        """
        with self._lock:
            if order_id not in self._active_orders:
                return

            order = self._active_orders.pop(order_id)
            resting_seconds = int(time.time() - order["placed_at"])

            # Persist cancellation to database if available
            if self._db:
                self._db.log_reward_metric(
                    platform="kalshi",
                    market_key=order["market_key"],
                    order_id=order_id,
                    event="cancelled",
                    size=order["size"],
                    spread=order["spread"],
                    resting_seconds=resting_seconds,
                )

    def get_active_orders(self) -> list[dict]:
        """Get all currently active orders."""
        with self._lock:
            return [
                {"order_id": oid, **info}
                for oid, info in self._active_orders.items()
            ]

    def estimate_daily_reward(self, market_key: str) -> float:
        """Estimate daily reward yield for a market based on resting orders.

        This is a rough estimate only; actual rewards are computed daily by Kalshi.

        Args:
            market_key: Market identifier.

        Returns:
            Estimated daily reward in USDC.
        """
        with self._lock:
            orders = [
                o for o in self._active_orders.values()
                if o["market_key"] == market_key
            ]

        if not orders:
            return 0.0

        # Kalshi reward formula is proprietary; estimate based on resting time + spread
        # Assumption: ~$0.50/day per 24h of resting at mid-spread (3%)
        total_resting = sum(time.time() - o["placed_at"] for o in orders)
        avg_spread = sum(o["spread"] for o in orders) / len(orders) if orders else 0

        # Estimate: reward ∝ resting_time * (1 - spread_tightness)
        estimated_daily = (total_resting / 86400) * (1 - avg_spread * 100) * 0.50
        return max(0.0, estimated_daily)
