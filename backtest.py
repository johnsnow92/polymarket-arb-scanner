#!/usr/bin/env python3
"""Backtesting engine — replays historical snapshots through simulated execution."""

import argparse
import logging
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Ensure parent directory is importable
sys.path.insert(0, os.path.dirname(__file__))

from config import BACKTEST_INITIAL_BALANCE
from snapshot import SnapshotRecorder

try:
    from fees import (
        net_profit_binary_internal,
        net_profit_negrisk_internal,
        net_profit_cross_platform,
        net_profit_kalshi_binary,
        net_profit_kalshi_multi,
        net_profit_triangular,
        net_profit_gemini_binary,
        net_profit_ibkr_binary,
    )
except ImportError:
    # Graceful fallback if fee functions are unavailable
    pass

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Results of a backtest run."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    initial_balance: float = 0.0
    final_balance: float = 0.0
    trades_by_type: dict = field(default_factory=dict)
    trade_log: list = field(default_factory=list)

    def summary(self) -> str:
        """Return a human-readable summary of the backtest."""
        lines = [
            "=" * 60,
            "BACKTEST RESULTS",
            "=" * 60,
            f"Period: {self.initial_balance:.2f} initial balance",
            f"Final balance:    ${self.final_balance:.2f}",
            f"Total P&L:        ${self.total_pnl:+.2f}",
            f"Return:           {(self.total_pnl / self.initial_balance * 100) if self.initial_balance > 0 else 0:.2f}%",
            f"Total trades:     {self.total_trades}",
            f"Win rate:         {self.win_rate:.1%}",
            f"Max drawdown:     ${self.max_drawdown:.2f}",
            f"Sharpe ratio:     {self.sharpe_ratio:.3f}",
            "",
            "Trades by type:",
        ]
        for opp_type, stats in sorted(self.trades_by_type.items()):
            lines.append(
                f"  {opp_type}: {stats['count']} trades, "
                f"P&L ${stats['pnl']:+.4f}, "
                f"win rate {stats['win_rate']:.1%}"
            )
        lines.append("=" * 60)
        return "\n".join(lines)


class BacktestEngine:
    """Replays historical snapshots to simulate trading and calculate P&L."""

    def __init__(
        self,
        recorder: SnapshotRecorder | None = None,
        initial_balance: float | None = None,
    ):
        self.recorder = recorder or SnapshotRecorder()
        self.initial_balance = initial_balance or BACKTEST_INITIAL_BALANCE

    def run(
        self,
        start_time: str,
        end_time: str,
        min_roi: float = 0.0,
        min_profit: float = 0.0,
        max_trade_size: float = 5.0,
        opp_type_filter: str | None = None,
    ) -> BacktestResult:
        """Run a backtest over historical snapshots.

        Args:
            start_time: ISO format start timestamp.
            end_time: ISO format end timestamp.
            min_roi: Minimum ROI threshold to enter a trade (e.g. 0.05 for 5%).
            min_profit: Minimum absolute net profit to enter a trade.
            max_trade_size: Maximum trade size per leg in dollars.
            opp_type_filter: Optional filter for specific opportunity types.

        Returns:
            BacktestResult with performance metrics.
        """
        snapshots = self.recorder.get_snapshots(start_time, end_time, opp_type_filter)

        if not snapshots:
            logger.warning("No snapshots found for the given time range.")
            result = BacktestResult()
            result.initial_balance = self.initial_balance
            result.final_balance = self.initial_balance
            return result

        logger.info(
            "Backtesting %d snapshots from %s to %s",
            len(snapshots), start_time, end_time,
        )

        balance = self.initial_balance
        peak_balance = balance
        max_drawdown = 0.0
        trade_log = []
        returns = []  # Per-trade returns for Sharpe calculation
        trades_by_type: dict[str, dict] = {}

        for snap in snapshots:
            net_profit = snap.get("net_profit", 0)
            gross_spread = snap.get("gross_spread", 0)
            fees = snap.get("fees", 0)
            opp_t = snap.get("opp_type", "")
            market = snap.get("market", "")
            price_a = snap.get("price_a")
            price_b = snap.get("price_b")

            # Skip if net_profit is non-positive
            if net_profit <= 0:
                continue

            # Calculate total cost from prices
            total_cost = 0.0
            if price_a is not None and price_b is not None:
                total_cost = price_a + price_b
            elif price_a is not None:
                total_cost = price_a
            elif gross_spread > 0:
                total_cost = 1.0 - gross_spread

            if total_cost <= 0:
                continue

            # ROI filter
            roi = net_profit / total_cost if total_cost > 0 else 0
            if roi < min_roi:
                continue

            # Minimum profit filter
            if net_profit < min_profit:
                continue

            # Size the trade
            trade_size = min(max_trade_size, balance * 0.1)  # Max 10% of balance per trade
            if trade_size <= 0:
                continue

            # Scale profit by trade size (snapshots record per-unit profit)
            # Number of contracts we can buy at total_cost per unit
            units = trade_size / total_cost if total_cost > 0 else 0
            realized_profit = net_profit * units

            # Recalculate fees using fee functions if available
            realized_profit = self._recalc_profit_with_fees(
                opp_t, price_a, price_b, units, net_profit,
            )

            # Execute the simulated trade
            balance += realized_profit

            # Track peak and drawdown
            if balance > peak_balance:
                peak_balance = balance
            drawdown = peak_balance - balance
            if drawdown > max_drawdown:
                max_drawdown = drawdown

            # Track returns for Sharpe
            trade_return = realized_profit / trade_size if trade_size > 0 else 0
            returns.append(trade_return)

            # Log the trade
            trade_entry = {
                "timestamp": snap.get("timestamp", ""),
                "market": market,
                "type": opp_t,
                "price_a": price_a,
                "price_b": price_b,
                "net_profit": realized_profit,
                "balance_after": balance,
                "roi": roi,
            }
            trade_log.append(trade_entry)

            # Track by type
            if opp_t not in trades_by_type:
                trades_by_type[opp_t] = {
                    "count": 0, "wins": 0, "pnl": 0.0, "win_rate": 0.0,
                }
            trades_by_type[opp_t]["count"] += 1
            trades_by_type[opp_t]["pnl"] += realized_profit
            if realized_profit > 0:
                trades_by_type[opp_t]["wins"] += 1

        # Compute final stats
        total_trades = len(trade_log)
        winning = sum(1 for t in trade_log if t["net_profit"] > 0)
        losing = total_trades - winning

        # Win rates per type
        for stats in trades_by_type.values():
            stats["win_rate"] = stats["wins"] / stats["count"] if stats["count"] > 0 else 0.0

        # Sharpe ratio (annualized, assuming daily frequency)
        sharpe = 0.0
        if returns:
            avg_return = sum(returns) / len(returns)
            if len(returns) > 1:
                variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
                std_return = math.sqrt(variance)
                if std_return > 0:
                    sharpe = (avg_return / std_return) * math.sqrt(252)

        return BacktestResult(
            total_trades=total_trades,
            winning_trades=winning,
            losing_trades=losing,
            win_rate=winning / total_trades if total_trades > 0 else 0.0,
            total_pnl=balance - self.initial_balance,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            initial_balance=self.initial_balance,
            final_balance=balance,
            trades_by_type=trades_by_type,
            trade_log=trade_log,
        )

    def _recalc_profit_with_fees(
        self,
        opp_type: str,
        price_a: float | None,
        price_b: float | None,
        units: float,
        fallback_profit: float,
    ) -> float:
        """Recalculate profit using fee functions from fees.py.

        Falls back to snapshot net_profit * units if fee functions are
        unavailable or prices are missing.
        """
        if price_a is None or price_b is None:
            return fallback_profit * units

        try:
            if opp_type == "Binary":
                result = net_profit_binary_internal(price_a, price_b)
            elif opp_type.startswith("KalshiBinary"):
                result = net_profit_kalshi_binary(price_a, price_b)
            elif opp_type.startswith("Cross"):
                result = net_profit_cross_platform(price_a, price_b, "yes", "no")
            elif opp_type.startswith("Gemini"):
                result = net_profit_gemini_binary(price_a, price_b)
            elif opp_type.startswith("IBKR"):
                result = net_profit_ibkr_binary(price_a, price_b)
            elif opp_type == "TriangularCross":
                result = net_profit_triangular(price_a, price_b, "", "")
            else:
                return fallback_profit * units

            return result.get("net_profit", fallback_profit) * units
        except (NameError, TypeError, Exception):
            return fallback_profit * units


def main():
    """CLI entry point for backtesting."""
    parser = argparse.ArgumentParser(
        description="Backtest arbitrage strategies on historical price snapshots.",
    )
    parser.add_argument(
        "--start", required=True,
        help="Start date (YYYY-MM-DD or ISO format)",
    )
    parser.add_argument(
        "--end", required=True,
        help="End date (YYYY-MM-DD or ISO format)",
    )
    parser.add_argument(
        "--min-roi", type=float, default=0.0,
        help="Minimum ROI threshold (e.g. 5.0 for 5%%)",
    )
    parser.add_argument(
        "--min-profit", type=float, default=0.0,
        help="Minimum absolute net profit per trade",
    )
    parser.add_argument(
        "--max-trade-size", type=float, default=5.0,
        help="Maximum trade size per leg in dollars",
    )
    parser.add_argument(
        "--balance", type=float, default=None,
        help=f"Initial balance (default: ${BACKTEST_INITIAL_BALANCE:.0f})",
    )
    parser.add_argument(
        "--type", dest="opp_type", default=None,
        help="Filter by opportunity type (e.g. Binary, Cross, KalshiBinary)",
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to snapshots.db file",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print individual trades",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Normalize date inputs to ISO format
    start = _normalize_date(args.start, is_start=True)
    end = _normalize_date(args.end, is_start=False)

    # Convert min_roi from percentage to fraction
    min_roi = args.min_roi / 100.0 if args.min_roi > 1.0 else args.min_roi

    recorder = SnapshotRecorder(db_path=args.db)
    engine = BacktestEngine(recorder=recorder, initial_balance=args.balance)

    result = engine.run(
        start_time=start,
        end_time=end,
        min_roi=min_roi,
        min_profit=args.min_profit,
        max_trade_size=args.max_trade_size,
        opp_type_filter=args.opp_type,
    )

    print(result.summary())

    if args.verbose and result.trade_log:
        print("\nTrade Log:")
        print("-" * 80)
        for i, trade in enumerate(result.trade_log, 1):
            print(
                f"  {i:4d}. {trade['timestamp'][:19]}  "
                f"{trade['type']:20s}  "
                f"P&L ${trade['net_profit']:+.4f}  "
                f"Bal ${trade['balance_after']:.2f}  "
                f"{trade['market'][:40]}"
            )

    recorder.close()


def _normalize_date(date_str: str, is_start: bool = True) -> str:
    """Convert a date string to ISO format.

    Accepts YYYY-MM-DD or full ISO timestamps. For plain dates,
    appends T00:00:00Z (start) or T23:59:59Z (end).
    """
    if "T" in date_str:
        return date_str
    if is_start:
        return f"{date_str}T00:00:00+00:00"
    else:
        return f"{date_str}T23:59:59+00:00"


if __name__ == "__main__":
    main()
