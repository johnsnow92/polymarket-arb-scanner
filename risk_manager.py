"""Risk management gates for arbitrage execution."""


class RiskManager:
    """Pure gate: returns (allowed, reason) for each opportunity."""

    def __init__(self, config: dict):
        self.max_trade_size = config.get("max_trade_size", 5.0)
        self.daily_loss_limit = config.get("daily_loss_limit", 25.0)
        self.max_open_positions = config.get("max_open_positions", 10)
        self.min_liquidity = config.get("min_liquidity", 50.0)
        self.min_net_roi = config.get("min_net_roi", 0.01)

    def check(self, opportunity: dict, db, balances: dict | None = None) -> tuple[bool, str]:
        """Check if an opportunity passes all risk gates.

        Args:
            opportunity: The arbitrage opportunity dict from scanner
            db: TradeDB instance for querying state
            balances: Optional dict with "polymarket" and/or "kalshi" balance floats

        Returns:
            (allowed, reason) tuple
        """
        # 1. Daily P&L within limit
        daily_pnl = db.get_daily_pnl()
        if daily_pnl < -self.daily_loss_limit:
            return False, f"Daily loss limit hit (P&L: ${daily_pnl:.2f}, limit: -${self.daily_loss_limit:.2f})"

        # 2. Open positions limit
        open_count = db.get_open_positions_count()
        if open_count >= self.max_open_positions:
            return False, f"Max open positions reached ({open_count}/{self.max_open_positions})"

        # 3. Balance check
        if balances:
            opp_type = opportunity.get("type", "")
            # Parse total cost from string like "$0.9500"
            total_cost_str = opportunity.get("total_cost", "$0")
            total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
            trade_cost = min(self.max_trade_size, total_cost)

            if "Cross" in opp_type:
                pm_balance = balances.get("polymarket", 0)
                k_balance = balances.get("kalshi", 0)
                if pm_balance is not None and pm_balance < trade_cost / 2:
                    return False, f"Insufficient Polymarket balance (${pm_balance:.2f})"
                if k_balance is not None and k_balance < trade_cost / 2:
                    return False, f"Insufficient Kalshi balance (${k_balance:.2f})"
            else:
                pm_balance = balances.get("polymarket", 0)
                if pm_balance is not None and pm_balance < trade_cost:
                    return False, f"Insufficient Polymarket balance (${pm_balance:.2f})"

        # 4. Order book depth check
        depth = opportunity.get("_clob_depth", 0)
        if depth < self.min_liquidity:
            return False, f"Insufficient depth ({depth:.0f} < {self.min_liquidity:.0f})"

        # 5. Net ROI check
        net_profit = opportunity.get("net_profit", 0)
        total_cost_str = opportunity.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        if total_cost > 0:
            roi = net_profit / total_cost
            if roi < self.min_net_roi:
                return False, f"ROI too low ({roi:.2%} < {self.min_net_roi:.2%})"

        # 6. Dedup: not already trading this market
        market = opportunity.get("market", "")
        if db.is_market_active(market):
            return False, f"Already trading this market"

        return True, "OK"

    def clamp_size(self, desired_size: float, depth: float, balance: float | None) -> float:
        """Calculate safe trade size given constraints."""
        size = min(desired_size, self.max_trade_size)
        if depth > 0:
            size = min(size, depth)
        if balance is not None and balance > 0:
            size = min(size, balance)
        return max(0, size)
