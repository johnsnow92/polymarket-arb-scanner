"""Arbitrage trade execution engine."""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import TradeDB
from risk_manager import RiskManager
from polymarket_api import get_clob_prices, PolymarketTrader
from kalshi_api import KalshiClient


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
    ):
        self.pm_trader = pm_trader
        self.kalshi_client = kalshi_client
        self.db = db
        self.risk = risk_manager
        self.dry_run = dry_run
        self.exec_mode = exec_mode
        self.max_trade_size = max_trade_size

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

        print(f"\n{prefix}--- Evaluating: {market} ({opp_type}) ---")

        # 1. Re-validate prices
        if not self.dry_run and not self._revalidate(opportunity):
            self._log_skipped(opportunity, "stale_prices")
            return False

        # 2. Risk gate
        balances = self._fetch_balances(opp_type)
        allowed, reason = self.risk.check(opportunity, self.db, balances)
        if not allowed:
            print(f"  {prefix}Risk blocked: {reason}")
            self._log_skipped(opportunity, f"risk:{reason}")
            return False

        # 3. Size calculation
        depth = opportunity.get("_clob_depth", 0)
        pm_balance = balances.get("polymarket") if balances else None
        size = self.risk.clamp_size(self.max_trade_size, depth, pm_balance)
        if size <= 0:
            print(f"  {prefix}Size 0 after constraints. Skipping.")
            self._log_skipped(opportunity, "zero_size")
            return False

        # 4. Build execution plan
        legs = self._build_legs(opportunity, size)
        if not legs:
            print(f"  {prefix}Could not build execution legs. Skipping.")
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
                print("  Skipped by user.")
                self._log_skipped(opportunity, "user_declined")
                return False

        # 7. Execute or dry-run log
        if self.dry_run:
            return self._dry_run_log(opportunity, legs, size)
        else:
            return self._execute_legs(opportunity, legs, size)

    def _revalidate(self, opportunity: dict) -> bool:
        """Re-fetch current prices and check if the opportunity still exists."""
        # For now, rely on the scanner's recent data. Full re-validation would
        # re-fetch CLOB prices here. Return True to proceed.
        # TODO: Add full re-validation for production use
        return True

    def _fetch_balances(self, opp_type: str) -> dict | None:
        """Fetch balances from relevant platforms."""
        balances = {}
        if self.pm_trader:
            balances["polymarket"] = self.pm_trader.get_balance()
        if self.kalshi_client and "Cross" in opp_type:
            balances["kalshi"] = self.kalshi_client.get_balance()
        return balances if balances else None

    def _build_legs(self, opportunity: dict, size: float) -> list[dict]:
        """Build execution legs based on opportunity type."""
        opp_type = opportunity.get("type", "")
        legs = []

        if opp_type == "Binary":
            # Buy YES + NO on Polymarket
            legs = [
                {"platform": "polymarket", "side": "BUY", "token": "yes",
                 "price": self._parse_price(opportunity, "Y=")},
                {"platform": "polymarket", "side": "BUY", "token": "no",
                 "price": self._parse_price(opportunity, "N=")},
            ]
        elif opp_type.startswith("NegRisk"):
            # Buy YES on each outcome — prices are comma-separated in the opp
            prices_str = opportunity.get("prices", "")
            prices = [float(p.strip()) for p in prices_str.split(",") if p.strip()]
            for i, price in enumerate(prices):
                legs.append({
                    "platform": "polymarket",
                    "side": "BUY",
                    "token": f"yes_{i}",
                    "price": price,
                })
        elif opp_type.startswith("Cross"):
            # One leg on Polymarket, one on Kalshi
            prices_str = opportunity.get("prices", "")
            if "PM_Y=" in prices_str and "K_N=" in prices_str:
                legs = [
                    {"platform": "polymarket", "side": "BUY", "token": "yes",
                     "price": self._parse_price(opportunity, "PM_Y=")},
                    {"platform": "kalshi", "side": "no", "action": "buy",
                     "price": self._parse_price(opportunity, "K_N=")},
                ]
            elif "PM_N=" in prices_str and "K_Y=" in prices_str:
                legs = [
                    {"platform": "polymarket", "side": "BUY", "token": "no",
                     "price": self._parse_price(opportunity, "PM_N=")},
                    {"platform": "kalshi", "side": "yes", "action": "buy",
                     "price": self._parse_price(opportunity, "K_Y=")},
                ]

        return legs

    def _parse_price(self, opportunity: dict, prefix: str) -> float:
        """Extract a price from the opportunity's prices string."""
        prices_str = opportunity.get("prices", "")
        for part in prices_str.split():
            if part.startswith(prefix):
                try:
                    return float(part[len(prefix):])
                except ValueError:
                    pass
        return 0.0

    def _print_plan(self, opportunity: dict, legs: list[dict], size: float, prefix: str):
        """Print the execution plan."""
        print(f"  {prefix}Trade size: ${size:.2f}")
        print(f"  {prefix}Net profit: ${opportunity.get('net_profit', 0):.4f}")
        print(f"  {prefix}ROI: {opportunity.get('net_roi', 'N/A')}")
        for i, leg in enumerate(legs):
            platform = leg["platform"]
            side = leg.get("side", "")
            price = leg.get("price", 0)
            token = leg.get("token", "")
            print(f"  {prefix}Leg {i+1}: {platform} {side} {token} @ ${price:.3f}")

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

        print(f"  [DRY RUN] Logged opportunity #{opp_id} with {len(legs)} legs.")
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

        # Execute legs concurrently
        results = {}
        with ThreadPoolExecutor(max_workers=len(legs)) as executor:
            futures = {}
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
                future = executor.submit(self._execute_single_leg, leg, size, opportunity)
                futures[future] = (i, leg)

            for future in as_completed(futures):
                idx, leg = futures[future]
                try:
                    success, order_id, fill_price = future.result()
                    trade_id = leg["_trade_id"]
                    if success:
                        self.db.update_trade_status(trade_id, "filled", fill_price)
                        results[idx] = True
                        print(f"  Leg {idx+1} FILLED: {leg['platform']} order={order_id}")
                    else:
                        self.db.update_trade_status(trade_id, "failed")
                        results[idx] = False
                        print(f"  Leg {idx+1} FAILED: {leg['platform']}")
                except Exception as e:
                    trade_id = leg["_trade_id"]
                    self.db.update_trade_status(trade_id, "failed")
                    results[idx] = False
                    print(f"  Leg {idx+1} ERROR: {e}")

        # Check if all legs succeeded
        all_filled = all(results.values())
        if not all_filled:
            # Attempt to cancel any filled legs if partial fill
            print("  WARNING: Partial fill detected. Attempting cleanup...")
            # In practice, FOK orders shouldn't partially fill, but handle it
            for i, leg in enumerate(legs):
                if results.get(i) and leg.get("_order_id"):
                    self._cancel_leg(leg)

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
                return True, order_id, price
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
                    return True, order_id, price
            return False, None, None

        return False, None, None

    def _cancel_leg(self, leg: dict):
        """Attempt to cancel a filled/resting order for cleanup."""
        platform = leg["platform"]
        order_id = leg.get("_order_id", "")
        if not order_id:
            return
        if platform == "polymarket" and self.pm_trader:
            self.pm_trader.cancel_order(order_id)
        elif platform == "kalshi" and self.kalshi_client:
            self.kalshi_client.cancel_order(order_id)

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
