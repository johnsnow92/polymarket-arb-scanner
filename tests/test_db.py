"""Tests for db.py — SQLite persistence for trades, opportunities, and positions."""

import os
import tempfile
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import TradeDB


@pytest.fixture
def db():
    """Create a fresh in-memory TradeDB for each test."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database = TradeDB(tmp.name)
    yield database
    database.close()
    os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# Opportunity logging
# ---------------------------------------------------------------------------

class TestLogOpportunity:
    def test_returns_id(self, db):
        opp_id = db.log_opportunity(
            opp_type="Binary", market="Will X?", prices="Y=0.45 N=0.50",
            total_cost=0.95, net_profit=0.05, net_roi=0.0526, depth=100.0, action="dry_run",
        )
        assert opp_id >= 1

    def test_sequential_ids(self, db):
        id1 = db.log_opportunity("Binary", "M1", "", 0.9, 0.1, 0.1, 50, "dry_run")
        id2 = db.log_opportunity("Binary", "M2", "", 0.9, 0.1, 0.1, 50, "dry_run")
        assert id2 == id1 + 1

    def test_get_recent_opportunities(self, db):
        for i in range(5):
            db.log_opportunity("Binary", f"Market {i}", "", 0.9, 0.1, 0.1, 50, "dry_run")
        recent = db.get_recent_opportunities(limit=3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0]["market"] == "Market 4"

    def test_get_recent_opportunities_empty(self, db):
        assert db.get_recent_opportunities() == []


# ---------------------------------------------------------------------------
# Trade logging
# ---------------------------------------------------------------------------

class TestLogTrade:
    def test_returns_trade_id(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        trade_id = db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "pending")
        assert trade_id >= 1

    def test_get_trades_for_opportunity(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "filled")
        db.log_trade(opp_id, "polymarket", "BUY", 0.50, 5.0, "filled")
        trades = db.get_trades_for_opportunity(opp_id)
        assert len(trades) == 2

    def test_update_trade_status(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        trade_id = db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "pending")
        db.update_trade_status(trade_id, "filled", fill_price=0.44)
        trades = db.get_trades_for_opportunity(opp_id)
        assert trades[0]["status"] == "filled"
        assert trades[0]["fill_price"] == pytest.approx(0.44)

    def test_update_trade_status_without_fill_price(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        trade_id = db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "pending")
        db.update_trade_status(trade_id, "failed")
        trades = db.get_trades_for_opportunity(opp_id)
        assert trades[0]["status"] == "failed"
        assert trades[0]["fill_price"] is None


# ---------------------------------------------------------------------------
# Position lifecycle
# ---------------------------------------------------------------------------

class TestPositions:
    def test_create_position(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        pos_id = db.create_position(opp_id, "market-abc", "polymarket", 0.05)
        assert pos_id >= 1

    def test_open_positions_count(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        assert db.get_open_positions_count() == 0
        db.create_position(opp_id, "m1", "polymarket", 0.05)
        assert db.get_open_positions_count() == 1
        db.create_position(opp_id, "m2", "polymarket", 0.03)
        assert db.get_open_positions_count() == 2

    def test_settle_position_reduces_count(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        pos_id = db.create_position(opp_id, "m1", "polymarket", 0.05)
        assert db.get_open_positions_count() == 1
        db.settle_position(pos_id, realized_pnl=0.04)
        assert db.get_open_positions_count() == 0

    def test_get_open_positions(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        db.create_position(opp_id, "m1", "polymarket", 0.05)
        db.create_position(opp_id, "m2", "kalshi", 0.03)
        pos_id3 = db.create_position(opp_id, "m3", "polymarket", 0.02)
        db.settle_position(pos_id3, 0.01)
        open_pos = db.get_open_positions()
        assert len(open_pos) == 2
        assert all(p["status"] == "open" for p in open_pos)

    def test_is_market_active(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        assert db.is_market_active("my-market") is False
        pos_id = db.create_position(opp_id, "my-market", "polymarket", 0.05)
        assert db.is_market_active("my-market") is True
        db.settle_position(pos_id, 0.04)
        assert db.is_market_active("my-market") is False

    def test_settle_with_expired_status(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        pos_id = db.create_position(opp_id, "m1", "polymarket", 0.05)
        db.settle_position(pos_id, realized_pnl=-0.02, status="expired")
        open_pos = db.get_open_positions()
        assert len(open_pos) == 0


# ---------------------------------------------------------------------------
# Daily P&L
# ---------------------------------------------------------------------------

class TestDailyPnl:
    def test_zero_with_no_positions(self, db):
        assert db.get_daily_pnl() == pytest.approx(0.0)

    def test_open_positions_use_expected_pnl(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        db.create_position(opp_id, "m1", "polymarket", 0.05)
        db.create_position(opp_id, "m2", "polymarket", 0.03)
        assert db.get_daily_pnl() == pytest.approx(0.08)

    def test_settled_positions_use_realized_pnl(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        pos_id = db.create_position(opp_id, "m1", "polymarket", 0.05)
        db.settle_position(pos_id, realized_pnl=0.04)
        assert db.get_daily_pnl() == pytest.approx(0.04)

    def test_mixed_open_and_settled(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        pos1 = db.create_position(opp_id, "m1", "polymarket", 0.05)
        db.create_position(opp_id, "m2", "polymarket", 0.03)
        db.settle_position(pos1, realized_pnl=0.04)
        # settled(0.04) + open(0.03) = 0.07
        assert db.get_daily_pnl() == pytest.approx(0.07)


# ---------------------------------------------------------------------------
# get_active_market_expected_pnl
# ---------------------------------------------------------------------------

class TestGetActiveMarketExpectedPnl:
    def test_returns_none_when_no_positions(self, db):
        assert db.get_active_market_expected_pnl("nonexistent") is None

    def test_returns_expected_pnl_for_open_position(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        db.create_position(opp_id, "my-market", "polymarket", 0.05)
        assert db.get_active_market_expected_pnl("my-market") == pytest.approx(0.05)

    def test_returns_best_pnl_when_multiple_positions(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        db.create_position(opp_id, "my-market", "polymarket", 0.03)
        db.create_position(opp_id, "my-market", "polymarket", 0.07)
        assert db.get_active_market_expected_pnl("my-market") == pytest.approx(0.07)

    def test_ignores_settled_positions(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        pos_id = db.create_position(opp_id, "my-market", "polymarket", 0.05)
        db.settle_position(pos_id, 0.04)
        assert db.get_active_market_expected_pnl("my-market") is None

    def test_returns_none_for_different_market(self, db):
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        db.create_position(opp_id, "other-market", "polymarket", 0.05)
        assert db.get_active_market_expected_pnl("my-market") is None


# ---------------------------------------------------------------------------
# Slippage tracking
# ---------------------------------------------------------------------------

class TestSlippage:
    def test_slippage_column_exists(self, db):
        """The slippage column should exist after table creation."""
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        trade_id = db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "pending")
        # Write slippage directly
        db.conn.execute("UPDATE trades SET slippage = ? WHERE id = ?", (0.01, trade_id))
        db.conn.commit()
        trades = db.get_trades_for_opportunity(opp_id)
        assert trades[0]["slippage"] == pytest.approx(0.01)

    def test_slippage_migration_safe_rerun(self, db):
        """Calling _create_tables again should not fail (ALTER TABLE idempotent)."""
        db._create_tables()  # Should not raise

    def test_slippage_null_by_default(self, db):
        """Trades without slippage set should have NULL slippage."""
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        trade_id = db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "filled")
        trades = db.get_trades_for_opportunity(opp_id)
        assert trades[0]["slippage"] is None

    def test_get_avg_slippage_empty(self, db):
        """Average slippage with no data should return 0."""
        assert db.get_avg_slippage() == pytest.approx(0.0)

    def test_get_avg_slippage_with_data(self, db):
        """Average slippage should compute correctly from trade data."""
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        t1 = db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "filled")
        t2 = db.log_trade(opp_id, "polymarket", "BUY", 0.50, 5.0, "filled")
        db.conn.execute("UPDATE trades SET slippage = ? WHERE id = ?", (0.01, t1))
        db.conn.execute("UPDATE trades SET slippage = ? WHERE id = ?", (-0.005, t2))
        db.conn.commit()
        avg = db.get_avg_slippage()
        assert avg == pytest.approx(0.0025)

    def test_get_avg_slippage_ignores_null(self, db):
        """Average slippage should ignore trades without slippage data."""
        opp_id = db.log_opportunity("Binary", "M", "", 0.9, 0.1, 0.1, 50, "traded")
        t1 = db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "filled")
        db.log_trade(opp_id, "polymarket", "BUY", 0.50, 5.0, "filled")  # No slippage
        db.conn.execute("UPDATE trades SET slippage = ? WHERE id = ?", (0.02, t1))
        db.conn.commit()
        avg = db.get_avg_slippage()
        assert avg == pytest.approx(0.02)  # Only counts the one with data


# ---------------------------------------------------------------------------
# Strategy P&L — no double-count across trade legs (audit M-1/B28)
# ---------------------------------------------------------------------------

class TestStrategyPnl:
    def test_net_profit_counted_once_per_opportunity_not_per_leg(self, db):
        # One opportunity, two trade legs (a cross-platform arb). total_pnl must
        # equal the opportunity's net_profit ONCE, not doubled by the leg count.
        opp_id = db.log_opportunity(
            "CrossPlatform", "M", "", 0.95, 0.05, 0.0526, 100.0, "traded",
        )
        db.log_trade(opp_id, "polymarket", "BUY", 0.45, 5.0, "filled")
        db.log_trade(opp_id, "kalshi", "BUY", 0.50, 5.0, "filled")

        rows = db.get_strategy_pnl()
        cross = next(r for r in rows if r["strategy"] == "CrossPlatform")
        assert cross["total_pnl"] == pytest.approx(0.05)   # once, not 0.10
        assert cross["win_count"] == 1                     # one opportunity, not two legs
        assert cross["trade_count"] == 2                   # two legs counted
