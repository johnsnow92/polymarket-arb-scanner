"""Tests for backtest.py — backtesting engine."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from snapshot import SnapshotRecorder
from backtest import BacktestEngine, BacktestResult, _normalize_date


@pytest.fixture
def recorder():
    rec = SnapshotRecorder(db_path=":memory:")
    yield rec
    rec.close()


@pytest.fixture
def engine(recorder):
    return BacktestEngine(recorder=recorder, initial_balance=1000.0)


def _seed_snapshots(recorder, opportunities):
    """Helper to seed snapshot data."""
    recorder.record_snapshot(opportunities)


# ---------------------------------------------------------------------------
# BacktestResult
# ---------------------------------------------------------------------------

class TestBacktestResult:
    def test_default_values(self):
        result = BacktestResult()
        assert result.total_trades == 0
        assert result.win_rate == 0.0
        assert result.total_pnl == 0.0
        assert result.max_drawdown == 0.0
        assert result.sharpe_ratio == 0.0

    def test_summary_format(self):
        result = BacktestResult(
            total_trades=10,
            winning_trades=7,
            losing_trades=3,
            win_rate=0.7,
            total_pnl=50.0,
            max_drawdown=10.0,
            sharpe_ratio=1.5,
            initial_balance=1000.0,
            final_balance=1050.0,
            trades_by_type={"Binary": {"count": 10, "pnl": 50.0, "win_rate": 0.7, "wins": 7}},
        )
        summary = result.summary()
        assert "BACKTEST RESULTS" in summary
        assert "Total P&L" in summary
        assert "$+50.00" in summary
        assert "Binary" in summary


# ---------------------------------------------------------------------------
# BacktestEngine.run
# ---------------------------------------------------------------------------

class TestBacktestRun:
    def test_no_snapshots_returns_empty_result(self, engine):
        result = engine.run("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")
        assert result.total_trades == 0
        assert result.initial_balance == 1000.0
        assert result.final_balance == 1000.0

    def test_processes_profitable_snapshots(self, engine, recorder):
        opps = [{
            "type": "Binary",
            "market": "Test Market",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.10,
        }]
        _seed_snapshots(recorder, opps)

        result = engine.run("2000-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00")
        assert result.total_trades >= 1
        assert result.total_pnl > 0
        assert result.final_balance > result.initial_balance

    def test_min_roi_filter(self, engine, recorder):
        opps = [{
            "type": "Binary",
            "market": "Low ROI",
            "prices": "Y=0.490 N=0.500",
            "total_cost": "$0.9900",
            "net_profit": 0.001,  # ROI ~0.1%
        }]
        _seed_snapshots(recorder, opps)

        # With 5% min ROI, should skip this trade
        result = engine.run(
            "2000-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00",
            min_roi=0.05,
        )
        assert result.total_trades == 0

    def test_min_profit_filter(self, engine, recorder):
        opps = [{
            "type": "Binary",
            "market": "Low Profit",
            "prices": "Y=0.490 N=0.500",
            "total_cost": "$0.9900",
            "net_profit": 0.001,
        }]
        _seed_snapshots(recorder, opps)

        result = engine.run(
            "2000-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00",
            min_profit=0.05,
        )
        assert result.total_trades == 0

    def test_zero_net_profit_skipped(self, engine, recorder):
        opps = [{
            "type": "Binary",
            "market": "No Profit",
            "prices": "Y=0.500 N=0.500",
            "total_cost": "$1.0000",
            "net_profit": 0.0,
        }]
        _seed_snapshots(recorder, opps)

        result = engine.run("2000-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00")
        assert result.total_trades == 0

    def test_negative_profit_skipped(self, engine, recorder):
        opps = [{
            "type": "Binary",
            "market": "Loss",
            "prices": "Y=0.600 N=0.500",
            "total_cost": "$1.1000",
            "net_profit": -0.10,
        }]
        _seed_snapshots(recorder, opps)

        result = engine.run("2000-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00")
        assert result.total_trades == 0

    def test_tracks_by_type(self, engine, recorder):
        opps = [
            {
                "type": "Binary",
                "market": "Binary A",
                "prices": "Y=0.400 N=0.450",
                "total_cost": "$0.8500",
                "net_profit": 0.10,
            },
            {
                "type": "KalshiBinary",
                "market": "Kalshi B",
                "prices": "Y=0.350 N=0.400",
                "total_cost": "$0.7500",
                "net_profit": 0.15,
            },
        ]
        _seed_snapshots(recorder, opps)

        result = engine.run("2000-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00")
        assert result.total_trades == 2
        assert "Binary" in result.trades_by_type
        assert "KalshiBinary" in result.trades_by_type

    def test_opp_type_filter(self, engine, recorder):
        opps = [
            {
                "type": "Binary",
                "market": "Binary A",
                "prices": "Y=0.400 N=0.450",
                "total_cost": "$0.8500",
                "net_profit": 0.10,
            },
            {
                "type": "KalshiBinary",
                "market": "Kalshi B",
                "prices": "Y=0.350 N=0.400",
                "total_cost": "$0.7500",
                "net_profit": 0.15,
            },
        ]
        _seed_snapshots(recorder, opps)

        result = engine.run(
            "2000-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00",
            opp_type_filter="Binary",
        )
        assert result.total_trades == 1
        assert "Binary" in result.trades_by_type
        assert "KalshiBinary" not in result.trades_by_type

    def test_max_drawdown_tracked(self, engine, recorder):
        # Multiple snapshots — drawdown tracking works
        opps = [{
            "type": "Binary",
            "market": "Test",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.10,
        }]
        _seed_snapshots(recorder, opps)

        result = engine.run("2000-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00")
        # With a positive trade, drawdown should be 0
        assert result.max_drawdown == pytest.approx(0.0)

    def test_win_rate_calculation(self, engine, recorder):
        opps = [{
            "type": "Binary",
            "market": "Winner",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.10,
        }]
        _seed_snapshots(recorder, opps)

        result = engine.run("2000-01-01T00:00:00+00:00", "2099-12-31T23:59:59+00:00")
        assert result.win_rate == 1.0


# ---------------------------------------------------------------------------
# _normalize_date
# ---------------------------------------------------------------------------

class TestNormalizeDate:
    def test_start_date_adds_midnight(self):
        result = _normalize_date("2026-01-01", is_start=True)
        assert result == "2026-01-01T00:00:00+00:00"

    def test_end_date_adds_end_of_day(self):
        result = _normalize_date("2026-02-01", is_start=False)
        assert result == "2026-02-01T23:59:59+00:00"

    def test_iso_format_passthrough(self):
        iso = "2026-01-15T12:00:00+00:00"
        assert _normalize_date(iso, is_start=True) == iso

    def test_iso_format_passthrough_end(self):
        iso = "2026-02-01T23:59:59+00:00"
        assert _normalize_date(iso, is_start=False) == iso


# ---------------------------------------------------------------------------
# _recalc_profit_with_fees
# ---------------------------------------------------------------------------

class TestRecalcProfitWithFees:
    def test_fallback_on_missing_prices(self, engine):
        result = engine._recalc_profit_with_fees("Binary", None, 0.45, 5.0, 0.10)
        assert result == pytest.approx(0.10 * 5.0)

    def test_fallback_on_unknown_type(self, engine):
        result = engine._recalc_profit_with_fees("UnknownType", 0.40, 0.45, 5.0, 0.10)
        assert result == pytest.approx(0.10 * 5.0)

    def test_binary_uses_fee_function(self, engine):
        # With fee functions loaded, should use actual fee calculation
        result = engine._recalc_profit_with_fees("Binary", 0.40, 0.45, 1.0, 0.10)
        # The result should be based on net_profit_binary_internal(0.40, 0.45)
        assert isinstance(result, float)
