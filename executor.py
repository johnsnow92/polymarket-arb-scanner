"""Arbitrage trade execution engine."""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import TradeDB
from risk_manager import RiskManager
from polymarket_api import get_clob_prices, fetch_order_book, get_best_bid_ask, PolymarketTrader
from kalshi_api import KalshiClient
from predictit_api import PredictItClient
from betfair_api import BetfairClient
from manifold_api import ManifoldClient
from fees import (
    net_profit_binary_internal,
    net_profit_negrisk_internal,
    net_profit_cross_platform,
    net_profit_kalshi_binary,
    net_profit_kalshi_multi,
)

logger = logging.getLogger(__name__)


class ArbitrageExecutor:
    """Executes arbitrage trades with risk controls, dry-run, and semi/full-auto modes."""

    def __init__(
        self,
        pm_trader: PolymarketTrader | None,
        kalshi_client: KalshiClient | None,
        db: TradeDB,
        risk_manager: RiskManager,
        dry_run: bool = True,
        exec_mode: str = "semi-auto",
        max_trade_size: float = 5.0,
        price_cache: dict | None = None,
        predictit_client: PredictItClient | None = None,
        betfair_client: BetfairClient | None = None,
        manifold_client: ManifoldClient | None = None,
        revalidation_adaptive: bool = True,
        revalidation_min_floor: float = 0.003,
        dynamic_sizing: bool = False,
        sizing_aggressiveness: float = 0.5,
    ):
        self.pm_trader = pm_trader
        self.kalshi_client = kalshi_client
        self.predictit_client = predictit_client
        self.betfair_client = betfair_client
        self.manifold_client = manifold_client
        self.db = db
        self.risk = risk_manager
        self.dry_run = dry_run
        self.exec_mode = exec_mode
        self.max_trade_size = max_trade_size
        self.price_cache = price_cache
        self.revalidation_adaptive = revalidation_adaptive
        self.revalidation_min_floor = revalidation_min_floor
        self.dynamic_sizing = dynamic_sizing
        self.sizing_aggressiveness = sizing_aggressiveness

    def execute(self, opportunity: dict, market_data: dict | None = None) -> bool:
        """Execute an arbitrage opportunity through the full pipeline.

        Args:
            opportunity: Opportunity dict from scanner
            market_data: Optional pre-fetched market data for re-validation

        Returns:
            True if trade was executed (or logged as dry_run), False if skipped.
        """
        opp_type = opportunity.get("type", "")
        market = opportunity.get("market", "Unknown")
        prefix = "[DRY RUN] " if self.dry_run else ""

        logger.info(f"{prefix}--- Evaluating: {market} ({opp_type}) ---")

        # 1. Re-validate prices
        if not self.dry_run and not self._revalidate(opportunity, self.price_cache):
            self._log_skipped(opportunity, "stale_prices")
            return False

        # 2. Risk gate
        balances = self._fetch_balances(opp_type)
        allowed, reason = self.risk.check(opportunity, self.db, balances)
        if not allowed:
            logger.info(f"{prefix}Risk blocked: {reason}")
            self._log_skipped(opportunity, f"risk:{reason}")
            return False

        # 3. Size calculation
        depth = opportunity.get("_clob_depth", 0)
        per_leg_budget = self._per_leg_budget(opp_type, opportunity, balances)
        if self.dynamic_sizing:
            desired_size = self.risk.calculate_dynamic_size(opportunity, self.sizing_aggressiveness)
        else:
            desired_size = self.max_trade_size
        size = self.risk.clamp_size(desired_size, depth, per_leg_budget)
        if size <= 0:
            logger.info(f"{prefix}Size 0 after constraints. Skipping.")
            self._log_skipped(opportunity, "zero_size")
            return False

        # 4. Build execution plan
        legs = self._build_legs(opportunity, size)
        if not legs:
            logger.info(f"{prefix}Could not build execution legs. Skipping.")
            self._log_skipped(opportunity, "no_legs")
            return False

        # 5. Display plan
        self._print_plan(opportunity, legs, size, prefix)

        # 6. Approval gate (semi-auto)
        if not self.dry_run and self.exec_mode == "semi-auto":
            try:
                answer = input("  Execute? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "y":
                logger.info("Skipped by user.")
                self._log_skipped(opportunity, "user_declined")
                return False

        # 7. Execute or dry-run log
        if self.dry_run:
            return self._dry_run_log(opportunity, legs, size)
        else:
            return self._execute_legs(opportunity, legs, size)

    def _revalidate(self, opportunity: dict, price_cache: dict | None = None) -> bool:
        """Re-fetch current prices and verify the opportunity still exists.

        Returns True if the opportunity is still profitable (>= 90% of original),
        False if stale, degraded, or any API call fails.
        """
        opp_type = opportunity.get("type", "")
        original_profit = opportunity.get("net_profit", 0)
        if original_profit <= 0:
            return False

        try:
            if opp_type == "Binary":
                return self._revalidate_binary(opportunity, original_profit, price_cache)
            elif opp_type.startswith("NegRisk"):
                return self._revalidate_negrisk(opportunity, original_profit, price_cache)
            elif opp_type.startswith("Cross"):
                return self._revalidate_cross(opportunity, original_profit, price_cache)
            elif opp_type == "KalshiBinary":
                return self._revalidate_kalshi_binary(opportunity, original_profit)
            elif opp_type.startswith("KalshiMulti"):
                return self._revalidate_kalshi_multi(opportunity, original_profit)
            # Unknown type — proceed cautiously
            return True
        except Exception as e:
            logger.warning(f"Revalidation failed: {e}")
            return False

    def _check_ws_cache(self, price_cache: dict | None, platform: str, key: str) -> dict | None:
        """Check WebSocket price cache for fresh data (< 5s old)."""
        if not price_cache:
            return None
        entry = price_cache.get((platform, key))
        if entry and time.time() - entry.get("_ts", 0) < 5:
            return entry
        return None

    def _get_revalidation_threshold(self, original_profit: float, opp: dict) -> float:
        """Calculate the minimum acceptable profit during revalidation.

        When adaptive revalidation is enabled:
        - ROI >= 5%: strict 90% of original profit
        - ROI 2-5%: moderate 80% of original profit
        - ROI < 2%: lenient — accept if profit > absolute floor
        Partial-CLOB opportunities use 80% max instead of 90%.
        When disabled: always use strict 90% threshold.
        """
        is_partial = opp.get("_partial_clob", False)

        if not self.revalidation_adaptive:
            return original_profit * (0.8 if is_partial else 0.9)

        total_cost_str = opp.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        roi = original_profit / total_cost if total_cost > 0 else 0

        if roi >= 0.05:
            return original_profit * (0.8 if is_partial else 0.9)
        elif roi >= 0.02:
            return original_profit * (0.7 if is_partial else 0.8)
        else:
            return self.revalidation_min_floor

    def _revalidate_binary(self, opp: dict, original_profit: float, price_cache: dict | None) -> bool:
        """Revalidate a Polymarket binary opportunity."""
        token_ids = opp.get("_token_ids", [])
        if len(token_ids) < 2:
            logger.warning("Revalidation: missing token IDs for binary")
            return False

        # Try WS cache first, then API
        yes_ask = no_ask = None
        cached_yes = self._check_ws_cache(price_cache, "polymarket", token_ids[0])
        cached_no = self._check_ws_cache(price_cache, "polymarket", token_ids[1])
        if cached_yes and cached_no:
            yes_ask = cached_yes.get("price")
            no_ask = cached_no.get("price")

        if yes_ask is None or no_ask is None:
            yes_book = fetch_order_book(token_ids[0])
            no_book = fetch_order_book(token_ids[1])
            if not yes_book or not no_book:
                return False
            yes_data = get_best_bid_ask(yes_book)
            no_data = get_best_bid_ask(no_book)
            yes_ask = yes_data["ask"]
            no_ask = no_data["ask"]

        if yes_ask is None or no_ask is None:
            return False

        result = net_profit_binary_internal(yes_ask, no_ask)
        threshold = self._get_revalidation_threshold(original_profit, opp)
        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False
        # Update opportunity with fresh prices
        opp["prices"] = f"Y={yes_ask:.3f} N={no_ask:.3f}"
        opp["net_profit"] = result["net_profit"]
        return True

    def _revalidate_negrisk(self, opp: dict, original_profit: float, price_cache: dict | None) -> bool:
        """Revalidate a Polymarket NegRisk opportunity."""
        token_ids = opp.get("_token_ids", [])
        if not token_ids:
            return False

        yes_asks = []
        for tid in token_ids:
            if not tid:
                return False
            cached = self._check_ws_cache(price_cache, "polymarket", tid)
            if cached and cached.get("price") is not None:
                yes_asks.append(cached["price"])
            else:
                book = fetch_order_book(tid)
                if not book:
                    return False
                data = get_best_bid_ask(book)
                if data["ask"] is None:
                    return False
                yes_asks.append(data["ask"])

        result = net_profit_negrisk_internal(yes_asks)
        threshold = self._get_revalidation_threshold(original_profit, opp)
        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False
        opp["net_profit"] = result["net_profit"]
        return True

    def _revalidate_cross(self, opp: dict, original_profit: float, price_cache: dict | None) -> bool:
        """Revalidate a cross-platform opportunity."""
        token_ids = opp.get("_token_ids", [])
        kalshi_ticker = opp.get("_kalshi_ticker", "")

        # Re-fetch PM prices
        pm_yes = pm_no = None
        if len(token_ids) >= 2:
            for i, tid in enumerate(token_ids[:2]):
                cached = self._check_ws_cache(price_cache, "polymarket", tid)
                if cached and cached.get("price") is not None:
                    if i == 0:
                        pm_yes = cached["price"]
                    else:
                        pm_no = cached["price"]
            if pm_yes is None or pm_no is None:
                yes_book = fetch_order_book(token_ids[0])
                no_book = fetch_order_book(token_ids[1])
                if not yes_book or not no_book:
                    return False
                yes_data = get_best_bid_ask(yes_book)
                no_data = get_best_bid_ask(no_book)
                pm_yes = yes_data["ask"]
                pm_no = no_data["ask"]

        # Re-fetch Kalshi prices
        k_yes = k_no = None
        if kalshi_ticker and self.kalshi_client:
            cached_k = self._check_ws_cache(price_cache, "kalshi", kalshi_ticker)
            if cached_k:
                k_yes = cached_k.get("yes_price")
                k_no = cached_k.get("no_price")
            if k_yes is None or k_no is None:
                book = self.kalshi_client.fetch_order_book(kalshi_ticker)
                if not book:
                    return False
                # Parse Kalshi order book for best prices
                orderbook = book.get("orderbook", book)
                yes_entries = orderbook.get("yes", [])
                no_entries = orderbook.get("no", [])
                if yes_entries:
                    entry = yes_entries[0]
                    k_yes = float(entry[0]) / 100 if isinstance(entry, list) else float(entry.get("price", 0)) / 100
                if no_entries:
                    entry = no_entries[0]
                    k_no = float(entry[0]) / 100 if isinstance(entry, list) else float(entry.get("price", 0)) / 100

        if pm_yes is None or pm_no is None or k_yes is None or k_no is None:
            return False

        result1 = net_profit_cross_platform(pm_yes, k_no, "yes", "no")
        result2 = net_profit_cross_platform(pm_no, k_yes, "no", "yes")

        if result1["net_profit"] >= result2["net_profit"]:
            best = result1["net_profit"]
            opp["prices"] = f"PM_Y={pm_yes:.3f} K_N={k_no:.3f}"
        else:
            best = result2["net_profit"]
            opp["prices"] = f"PM_N={pm_no:.3f} K_Y={k_yes:.3f}"

        threshold = self._get_revalidation_threshold(original_profit, opp)
        if best < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {best:.4f}")
            return False
        opp["net_profit"] = best
        return True

    def _revalidate_kalshi_binary(self, opp: dict, original_profit: float) -> bool:
        """Revalidate a Kalshi binary opportunity."""
        ticker = opp.get("_kalshi_ticker", "")
        if not ticker or not self.kalshi_client:
            return False
        book = self.kalshi_client.fetch_order_book(ticker)
        if not book:
            return False
        orderbook = book.get("orderbook", book)
        yes_entries = orderbook.get("yes", [])
        no_entries = orderbook.get("no", [])
        if not yes_entries or not no_entries:
            return False
        y_entry = yes_entries[0]
        n_entry = no_entries[0]
        k_yes = float(y_entry[0]) / 100 if isinstance(y_entry, list) else float(y_entry.get("price", 0)) / 100
        k_no = float(n_entry[0]) / 100 if isinstance(n_entry, list) else float(n_entry.get("price", 0)) / 100

        result = net_profit_kalshi_binary(k_yes, k_no)
        threshold = self._get_revalidation_threshold(original_profit, opp)
        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False
        opp["net_profit"] = result["net_profit"]
        return True

    def _revalidate_kalshi_multi(self, opp: dict, original_profit: float) -> bool:
        """Revalidate a Kalshi multi-outcome opportunity."""
        tickers = opp.get("_kalshi_tickers", [])
        if not tickers or not self.kalshi_client:
            return False
        yes_prices = []
        for ticker in tickers:
            book = self.kalshi_client.fetch_order_book(ticker)
            if not book:
                return False
            orderbook = book.get("orderbook", book)
            yes_entries = orderbook.get("yes", [])
            if not yes_entries:
                return False
            entry = yes_entries[0]
            price = float(entry[0]) / 100 if isinstance(entry, list) else float(entry.get("price", 0)) / 100
            yes_prices.append(price)
        result = net_profit_kalshi_multi(yes_prices)
        threshold = self._get_revalidation_threshold(original_profit, opp)
        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False
        opp["net_profit"] = result["net_profit"]
        return True

    def _per_leg_budget(self, opp_type: str, opportunity: dict, balances: dict | None) -> float | None:
        """Calculate per-leg budget based on platform balance and number of legs.

        For multi-leg arbs on the same platform, divides the available balance
        by the number of legs so the total trade fits within the balance.
        """
        if not balances:
            return None

        # Determine relevant balance
        if "Kalshi" in opp_type and "Cross" not in opp_type:
            balance = balances.get("kalshi")
        elif "Cross" in opp_type:
            # Cross arbs have 1 leg per platform — use the smaller balance
            bal_values = [v for v in balances.values() if v is not None and v > 0]
            balance = min(bal_values) if bal_values else None
        else:
            balance = balances.get("polymarket")

        if balance is None or balance <= 0:
            return 0.0

        # Estimate number of legs on the same platform
        if opp_type == "Binary" or opp_type == "KalshiBinary":
            num_legs = 2
        elif opp_type.startswith("KalshiMulti") or opp_type.startswith("NegRisk"):
            # Parse count from type like "KalshiMulti(3)" or "NegRisk(5)"
            import re
            m = re.search(r'\((\d+)\)', opp_type)
            num_legs = int(m.group(1)) if m else len(opportunity.get("_token_ids", [])) or 2
        elif "Cross" in opp_type:
            num_legs = 1  # 1 leg per platform
        else:
            num_legs = 2

        return balance / num_legs

    def _fetch_balances(self, opp_type: str) -> dict | None:
        """Fetch balances from relevant platforms."""
        balances = {}
        if "Kalshi" in opp_type or "Cross" in opp_type:
            if self.kalshi_client:
                balances["kalshi"] = self.kalshi_client.get_balance()
        if "Kalshi" not in opp_type:
            if self.pm_trader:
                balances["polymarket"] = self.pm_trader.get_balance()
        if "Cross" in opp_type:
            if self.predictit_client and hasattr(self.predictit_client, "get_balance"):
                bal = self.predictit_client.get_balance()
                if bal is not None:
                    balances["predictit"] = bal
            if self.betfair_client and hasattr(self.betfair_client, "get_balance"):
                bal = self.betfair_client.get_balance()
                if bal is not None:
                    balances["betfair"] = bal
            if self.manifold_client and hasattr(self.manifold_client, "get_balance"):
                bal = self.manifold_client.get_balance()
                if bal is not None:
                    balances["manifold"] = bal
        return balances if balances else None

    def _build_legs(self, opportunity: dict, size: float) -> list[dict]:
        """Build execution legs based on opportunity type."""
        opp_type = opportunity.get("type", "")
        token_ids = opportunity.get("_token_ids", [])
        legs = []

        if opp_type == "KalshiBinary":
            # Buy YES + NO on same Kalshi market
            legs = [
                {"platform": "kalshi", "side": "yes", "action": "buy",
                 "price": opportunity["_kalshi_yes"],
                 "_ticker": opportunity["_kalshi_ticker"]},
                {"platform": "kalshi", "side": "no", "action": "buy",
                 "price": opportunity["_kalshi_no"],
                 "_ticker": opportunity["_kalshi_ticker"]},
            ]
        elif opp_type.startswith("KalshiMulti"):
            # Buy YES on every outcome in a Kalshi event
            legs = [
                {"platform": "kalshi", "side": "yes", "action": "buy",
                 "price": price, "_ticker": ticker}
                for ticker, price in zip(
                    opportunity["_kalshi_tickers"],
                    opportunity["_kalshi_prices"],
                )
            ]
        elif opp_type == "Binary":
            # Buy YES + NO on Polymarket
            yes_token = token_ids[0] if len(token_ids) > 0 else ""
            no_token = token_ids[1] if len(token_ids) > 1 else ""
            yes_price = self._parse_price(opportunity, "Y=")
            no_price = self._parse_price(opportunity, "N=")
            if yes_price is None or no_price is None:
                return []
            legs = [
                {"platform": "polymarket", "side": "BUY", "token": "yes",
                 "price": yes_price, "_token_id": yes_token},
                {"platform": "polymarket", "side": "BUY", "token": "no",
                 "price": no_price, "_token_id": no_token},
            ]
        elif opp_type.startswith("NegRisk"):
            # Buy YES on each outcome — prices are comma-separated in the opp
            prices_str = opportunity.get("prices", "")
            prices = []
            for p in prices_str.split(","):
                p = p.strip()
                # Skip trailing summary like "... (N total)"
                if not p or p.startswith("(") or p == "...":
                    continue
                try:
                    prices.append(float(p))
                except ValueError:
                    continue
            # Validate price count matches token ID count
            if token_ids and len(prices) != len(token_ids):
                logger.warning(f"NegRisk price count ({len(prices)}) != token ID count ({len(token_ids)}). Skipping.")
                return []
            for i, price in enumerate(prices):
                legs.append({
                    "platform": "polymarket",
                    "side": "BUY",
                    "token": f"yes_{i}",
                    "price": price,
                    "_token_id": token_ids[i] if i < len(token_ids) else "",
                })
        elif opp_type.startswith("Cross"):
            # One leg on Polymarket, one on Kalshi
            prices_str = opportunity.get("prices", "")
            kalshi_ticker = opportunity.get("_kalshi_ticker", "")
            if "PM_Y=" in prices_str and "K_N=" in prices_str:
                yes_token = token_ids[0] if len(token_ids) > 0 else ""
                pm_price = self._parse_price(opportunity, "PM_Y=")
                k_price = self._parse_price(opportunity, "K_N=")
                if pm_price is None or k_price is None:
                    return []
                legs = [
                    {"platform": "polymarket", "side": "BUY", "token": "yes",
                     "price": pm_price, "_token_id": yes_token},
                    {"platform": "kalshi", "side": "no", "action": "buy",
                     "price": k_price, "_ticker": kalshi_ticker},
                ]
            elif "PM_N=" in prices_str and "K_Y=" in prices_str:
                no_token = token_ids[1] if len(token_ids) > 1 else ""
                pm_price = self._parse_price(opportunity, "PM_N=")
                k_price = self._parse_price(opportunity, "K_Y=")
                if pm_price is None or k_price is None:
                    return []
                legs = [
                    {"platform": "polymarket", "side": "BUY", "token": "no",
                     "price": pm_price, "_token_id": no_token},
                    {"platform": "kalshi", "side": "yes", "action": "buy",
                     "price": k_price, "_ticker": kalshi_ticker},
                ]
            elif "_platform_a" in opportunity:
                # Generic cross-platform handler for cross-all opportunities
                legs = self._build_cross_all_legs(opportunity, size)

        return legs

    def _build_cross_all_legs(self, opportunity: dict, size: float) -> list[dict]:
        """Build execution legs for generic cross-platform opportunities."""
        prices_str = opportunity.get("prices", "")
        platform_a = opportunity.get("_platform_a", "")
        platform_b = opportunity.get("_platform_b", "")
        token_ids = opportunity.get("_token_ids", [])

        # Parse prices: format is "{platform}_Y={price} {platform}_N={price}"
        parts = prices_str.split()
        if len(parts) != 2:
            return []

        leg_a = self._build_single_cross_leg(
            parts[0], platform_a, platform_b, opportunity, token_ids, is_first=True)
        leg_b = self._build_single_cross_leg(
            parts[1], platform_b, platform_a, opportunity, token_ids, is_first=False)

        if leg_a is None or leg_b is None:
            return []
        return [leg_a, leg_b]

    def _build_single_cross_leg(
        self, price_part: str, expected_platform: str, other_platform: str,
        opportunity: dict, token_ids: list, is_first: bool,
    ) -> dict | None:
        """Parse a single price part and build a leg dict."""
        # price_part format: "{platform}_Y={price}" or "{platform}_N={price}"
        if "=" not in price_part:
            return None

        label, val = price_part.split("=", 1)
        try:
            price = float(val)
        except ValueError:
            return None
        if price <= 0 or price >= 1:
            return None

        # Determine platform and side from label
        if label.endswith("_Y"):
            platform_name = label[:-2]
            side = "yes"
        elif label.endswith("_N"):
            platform_name = label[:-2]
            side = "no"
        else:
            return None

        leg = {"price": price}

        if platform_name == "polymarket":
            token_idx = 0 if side == "yes" else 1
            leg["platform"] = "polymarket"
            leg["side"] = "BUY"
            leg["token"] = side
            leg["_token_id"] = token_ids[token_idx] if token_idx < len(token_ids) else ""
        elif platform_name == "kalshi":
            leg["platform"] = "kalshi"
            leg["side"] = side
            leg["action"] = "buy"
            leg["_ticker"] = opportunity.get("_kalshi_ticker", "")
        elif platform_name == "predictit":
            leg["platform"] = "predictit"
            leg["side"] = side
            leg["_contract_id"] = opportunity.get("_contract_id")
        elif platform_name == "betfair":
            leg["platform"] = "betfair"
            leg["side"] = side
            leg["_market_id"] = opportunity.get("_market_id", "")
            leg["_selection_id"] = opportunity.get("_selection_id")
        elif platform_name == "manifold":
            leg["platform"] = "manifold"
            leg["side"] = side
            leg["_market_id"] = opportunity.get("_manifold_market_id", "")
        else:
            return None

        return leg

    def _parse_price(self, opportunity: dict, prefix: str) -> float | None:
        """Extract a price from the opportunity's prices string. Returns None on failure."""
        prices_str = opportunity.get("prices", "")
        for part in prices_str.split():
            if part.startswith(prefix):
                try:
                    value = float(part[len(prefix):])
                    if value <= 0 or value >= 1:
                        logger.warning(f"Price out of range for {prefix}: {value}")
                        return None
                    return value
                except ValueError:
                    logger.warning(f"Failed to parse price for {prefix}: {part}")
                    return None
        logger.warning(f"Price prefix '{prefix}' not found in: {prices_str}")
        return None

    def _print_plan(self, opportunity: dict, legs: list[dict], size: float, prefix: str):
        """Print the execution plan."""
        logger.info(f"{prefix}Trade size: ${size:.2f}")
        logger.info(f"{prefix}Net profit: ${opportunity.get('net_profit', 0):.4f}")
        logger.info(f"{prefix}ROI: {opportunity.get('net_roi', 'N/A')}")
        for i, leg in enumerate(legs):
            platform = leg["platform"]
            side = leg.get("side", "")
            price = leg.get("price", 0)
            token = leg.get("token", "")
            logger.info(f"{prefix}Leg {i+1}: {platform} {side} {token} @ ${price:.3f}")

    def _dry_run_log(self, opportunity: dict, legs: list[dict], size: float) -> bool:
        """Log a dry-run execution."""
        total_cost_str = opportunity.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        roi_str = opportunity.get("net_roi", "0%")
        roi = float(roi_str.replace("%", "")) / 100 if isinstance(roi_str, str) else 0

        opp_id = self.db.log_opportunity(
            opp_type=opportunity.get("type", ""),
            market=opportunity.get("market", ""),
            prices=opportunity.get("prices", ""),
            total_cost=total_cost,
            net_profit=opportunity.get("net_profit", 0),
            net_roi=roi,
            depth=opportunity.get("_clob_depth", 0),
            action="dry_run",
        )

        for leg in legs:
            self.db.log_trade(
                opportunity_id=opp_id,
                platform=leg["platform"],
                side=leg.get("side", leg.get("token", "")),
                price=leg.get("price", 0),
                size=size,
                status="dry_run",
            )

        logger.info(f"[DRY RUN] Logged opportunity #{opp_id} with {len(legs)} legs.")
        return True

    def _execute_legs(self, opportunity: dict, legs: list[dict], size: float) -> bool:
        """Execute trade legs concurrently on both platforms."""
        total_cost_str = opportunity.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        roi_str = opportunity.get("net_roi", "0%")
        roi = float(roi_str.replace("%", "")) / 100 if isinstance(roi_str, str) else 0

        opp_id = self.db.log_opportunity(
            opp_type=opportunity.get("type", ""),
            market=opportunity.get("market", ""),
            prices=opportunity.get("prices", ""),
            total_cost=total_cost,
            net_profit=opportunity.get("net_profit", 0),
            net_roi=roi,
            depth=opportunity.get("_clob_depth", 0),
            action="traded",
        )

        # Determine if legs span multiple platforms (cross-platform arbs)
        platforms = set(leg["platform"] for leg in legs)
        cross_platform = len(platforms) > 1

        # Log all trades as pending
        for i, leg in enumerate(legs):
            trade_id = self.db.log_trade(
                opportunity_id=opp_id,
                platform=leg["platform"],
                side=leg.get("side", leg.get("token", "")),
                price=leg.get("price", 0),
                size=size,
                status="pending",
            )
            leg["_trade_id"] = trade_id

        results = {}
        if cross_platform:
            # Cross-platform: execute legs concurrently (different exchanges)
            with ThreadPoolExecutor(max_workers=len(legs)) as pool:
                futures = {}
                for i, leg in enumerate(legs):
                    future = pool.submit(self._execute_single_leg, leg, size, opportunity)
                    futures[future] = (i, leg)
                for future in as_completed(futures):
                    idx, leg = futures[future]
                    try:
                        success, order_id, fill_price = future.result()
                        trade_id = leg["_trade_id"]
                        if success:
                            slippage = fill_price - leg.get("price", 0) if fill_price else 0
                            self.db.update_trade_status(trade_id, "filled", fill_price,
                                                        slippage=slippage)
                            results[idx] = True
                            logger.info(f"Leg {idx+1} FILLED: {leg['platform']} order={order_id}")
                        else:
                            self.db.update_trade_status(trade_id, "failed")
                            results[idx] = False
                            logger.error(f"Leg {idx+1} FAILED: {leg['platform']}")
                    except Exception as e:
                        trade_id = leg["_trade_id"]
                        self.db.update_trade_status(trade_id, "failed")
                        results[idx] = False
                        logger.error(f"Leg {idx+1} ERROR: {e}")
        else:
            # Same-platform: execute legs sequentially, abort on first failure
            for i, leg in enumerate(legs):
                try:
                    success, order_id, fill_price = self._execute_single_leg(leg, size, opportunity)
                    trade_id = leg["_trade_id"]
                    if success:
                        slippage = fill_price - leg.get("price", 0) if fill_price else 0
                        self.db.update_trade_status(trade_id, "filled", fill_price,
                                                    slippage=slippage)
                        results[i] = True
                        logger.info(f"Leg {i+1} FILLED: {leg['platform']} order={order_id}")
                    else:
                        self.db.update_trade_status(trade_id, "failed")
                        results[i] = False
                        logger.error(f"Leg {i+1} FAILED: {leg['platform']}")
                        # Abort remaining legs — no point continuing
                        for j in range(i + 1, len(legs)):
                            self.db.update_trade_status(legs[j]["_trade_id"], "aborted")
                        logger.warning("Aborting remaining legs after leg %d failure.", i + 1)
                        break
                except Exception as e:
                    trade_id = leg["_trade_id"]
                    self.db.update_trade_status(trade_id, "failed")
                    results[i] = False
                    logger.error(f"Leg {i+1} ERROR: {e}")
                    for j in range(i + 1, len(legs)):
                        self.db.update_trade_status(legs[j]["_trade_id"], "aborted")
                    break

        # Check if all legs succeeded
        all_filled = len(results) == len(legs) and all(results.values())
        if all_filled:
            # Create position in DB for lifecycle tracking
            market = opportunity.get("market", "Unknown")
            opp_type = opportunity.get("type", "")
            platform = "polymarket"
            if "Kalshi" in opp_type:
                platform = "kalshi"
            elif "Cross" in opp_type:
                platform = "cross"
            self.db.create_position(
                opportunity_id=opp_id,
                market_identifier=market,
                platform=platform,
                expected_pnl=opportunity.get("net_profit", 0),
            )
        else:
            # Attempt to cancel any filled legs if partial fill
            logger.warning("Partial fill detected. Attempting cleanup...")
            for i, leg in enumerate(legs):
                if results.get(i) and leg.get("_order_id"):
                    cancel_ok = self._cancel_leg(leg)
                    if not cancel_ok:
                        trade_id = leg.get("_trade_id")
                        if trade_id:
                            self.db.update_trade_status(trade_id, "orphaned")
                        logger.warning(f"Leg {i+1} cancel failed -- marked as orphaned.")

        return all_filled

    def _execute_single_leg(
        self, leg: dict, size: float, opportunity: dict
    ) -> tuple[bool, str | None, float | None]:
        """Execute a single trade leg. Returns (success, order_id, fill_price)."""
        platform = leg["platform"]
        price = leg.get("price", 0)

        if platform == "polymarket":
            if not self.pm_trader:
                return False, None, None
            token_id = leg.get("_token_id", "")
            if not token_id:
                logger.error("Missing token_id for Polymarket leg")
                return False, None, None
            side = leg.get("side", "BUY")
            neg_risk = "NegRisk" in opportunity.get("type", "")
            resp = self.pm_trader.place_order(
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                neg_risk=neg_risk,
            )
            if resp and resp.get("success"):
                order_id = resp.get("orderID", resp.get("order_id", ""))
                leg["_order_id"] = order_id
                # Poll for fill confirmation
                fill_price = self._confirm_fill_pm(order_id, price)
                return True, order_id, fill_price
            return False, None, None

        elif platform == "kalshi":
            if not self.kalshi_client:
                return False, None, None
            ticker = leg.get("_ticker", "")
            side = leg.get("side", "yes")
            action = leg.get("action", "buy")
            # Convert dollar size to contracts (1 contract = $1 payout)
            count = max(1, int(size / price)) if price > 0 else 1
            resp = self.kalshi_client.place_order(
                ticker=ticker,
                side=side,
                action=action,
                count=count,
                price_dollars=price,
            )
            if resp:
                order = resp.get("order", resp)
                order_id = order.get("order_id", "")
                leg["_order_id"] = order_id
                status = order.get("status", "")
                if status in ("resting", "executed"):
                    # Poll for fill confirmation
                    fill_price = self._confirm_fill_kalshi(order_id, price)
                    return True, order_id, fill_price
                logger.warning("Kalshi order not filled: status=%s ticker=%s resp=%s",
                               status, ticker, str(resp)[:300])
            else:
                logger.warning("Kalshi place_order returned None for %s %s @ $%.3f (count=%d)",
                               side, ticker, price, count)
            return False, None, None

        elif platform == "predictit":
            if not self.predictit_client or not self.predictit_client.authenticated:
                return False, None, None
            contract_id = leg.get("_contract_id")
            if not contract_id:
                return False, None, None
            side = leg.get("side", "yes")
            quantity = max(1, int(size / price)) if price > 0 else 1
            resp = self.predictit_client.place_order(
                contract_id=contract_id, side=side,
                price=price, quantity=quantity,
            )
            if resp:
                order_id = str(resp.get("id", resp.get("orderId", "")))
                leg["_order_id"] = order_id
                return True, order_id, price
            return False, None, None

        elif platform == "betfair":
            if not self.betfair_client or not self.betfair_client.authenticated:
                return False, None, None
            market_id = leg.get("_market_id", "")
            selection_id = leg.get("_selection_id")
            if not market_id or not selection_id:
                return False, None, None
            side = "BACK" if leg.get("side", "").lower() in ("yes", "back") else "LAY"
            # Convert probability price to decimal odds
            decimal_odds = round(1.0 / price, 2) if price > 0 else 2.0
            instructions = [{
                "selectionId": selection_id,
                "side": side,
                "orderType": "LIMIT",
                "limitOrder": {
                    "size": round(size, 2),
                    "price": decimal_odds,
                    "persistenceType": "LAPSE",
                },
            }]
            resp = self.betfair_client.place_orders(market_id, instructions)
            if resp and resp.get("status") == "SUCCESS":
                results = resp.get("instructionReports", [])
                if results:
                    bet_id = results[0].get("betId", "")
                    leg["_order_id"] = bet_id
                    return True, bet_id, price
            return False, None, None

        elif platform == "manifold":
            if not self.manifold_client or not self.manifold_client.api_key:
                return False, None, None
            market_id = leg.get("_market_id", "")
            if not market_id:
                return False, None, None
            outcome = "YES" if leg.get("side", "").lower() in ("yes", "buy") else "NO"
            resp = self.manifold_client.place_bet(
                market_id=market_id, outcome=outcome, amount=size,
            )
            if resp:
                bet_id = str(resp.get("id", resp.get("betId", "")))
                leg["_order_id"] = bet_id
                return True, bet_id, price
            return False, None, None

        return False, None, None

    def _confirm_fill_pm(self, order_id: str, expected_price: float) -> float:
        """Poll Polymarket for fill confirmation. Returns actual fill price."""
        if not self.pm_trader or not order_id:
            return expected_price
        # Poll every 100ms for up to 2s
        for _ in range(20):
            status = self.pm_trader.get_order_status(order_id)
            if status:
                order_status = status.get("status", "")
                if order_status == "matched":
                    return float(status.get("price", expected_price))
                elif order_status in ("canceled", "expired"):
                    return expected_price
            time.sleep(0.1)
        return expected_price

    def _confirm_fill_kalshi(self, order_id: str, expected_price: float) -> float:
        """Poll Kalshi for fill confirmation. Returns actual fill price."""
        if not self.kalshi_client or not order_id:
            return expected_price
        # Poll every 100ms for up to 2s
        for _ in range(20):
            status = self.kalshi_client.get_order_status(order_id)
            if status:
                order_status = status.get("status", "")
                if order_status == "executed":
                    avg_price = status.get("avg_price")
                    if avg_price is not None:
                        return float(avg_price) / 100.0
                    return expected_price
                elif order_status in ("canceled", "expired"):
                    return expected_price
            time.sleep(0.1)
        return expected_price

    def _cancel_leg(self, leg: dict) -> bool:
        """Attempt to cancel a filled/resting order for cleanup. Returns True if successful."""
        platform = leg["platform"]
        order_id = leg.get("_order_id", "")
        if not order_id:
            return False
        if platform == "polymarket" and self.pm_trader:
            return self.pm_trader.cancel_order(order_id)
        elif platform == "kalshi" and self.kalshi_client:
            return self.kalshi_client.cancel_order(order_id)
        return False

    def _log_skipped(self, opportunity: dict, reason: str):
        """Log a skipped opportunity."""
        total_cost_str = opportunity.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        roi_str = opportunity.get("net_roi", "0%")
        roi = float(roi_str.replace("%", "")) / 100 if isinstance(roi_str, str) else 0

        self.db.log_opportunity(
            opp_type=opportunity.get("type", ""),
            market=opportunity.get("market", ""),
            prices=opportunity.get("prices", ""),
            total_cost=total_cost,
            net_profit=opportunity.get("net_profit", 0),
            net_roi=roi,
            depth=opportunity.get("_clob_depth", 0),
            action=f"skipped:{reason}",
        )
