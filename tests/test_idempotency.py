"""Tests for idempotency key generation and DB-level duplicate trade prevention (HARDEN-05)."""

import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import TradeDB


# ---------------------------------------------------------------------------
# Helpers — import executor module under mock
# ---------------------------------------------------------------------------

def _get_executor_module():
    """Import executor with external API modules mocked."""
    mocks = {}
    for mod_name in [
        "polymarket_api", "kalshi_api",
        "betfair_api", "smarkets_api", "sxbet_api",
        "matchbook_api", "gemini_api", "ibkr_api",
    ]:
        if mod_name not in sys.modules:
            mocks[mod_name] = MagicMock()
            sys.modules[mod_name] = mocks[mod_name]

    if "executor" in sys.modules:
        del sys.modules["executor"]

    import executor as _exec_mod
    return _exec_mod, mocks


@pytest.fixture(autouse=True)
def cleanup_executor_module():
    """Remove executor from sys.modules after each test to avoid cross-test pollution."""
    yield
    sys.modules.pop("executor", None)


@pytest.fixture
def db():
    """In-memory TradeDB for each test."""
    database = TradeDB(":memory:")
    yield database
    database.close()


@pytest.fixture
def tmpdb():
    """File-based TradeDB for tests that insert rows with real timestamps."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database = TradeDB(tmp.name)
    yield database
    database.close()
    os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# TestIdempotencyKey
# ---------------------------------------------------------------------------

class TestIdempotencyKey:
    def test_deterministic_within_same_minute(self):
        """Same inputs in the same minute bucket produce the same key."""
        mod, _ = _get_executor_module()
        fixed_time = 1_700_000_060.0  # second 60 => minute bucket 28333334

        with patch("time.time", return_value=fixed_time):
            k1 = mod._make_idempotency_key("market1", "buy", 0.55)
        with patch("time.time", return_value=fixed_time + 30):  # still same minute
            k2 = mod._make_idempotency_key("market1", "buy", 0.55)

        assert k1 == k2

    def test_different_price_produces_different_key(self):
        mod, _ = _get_executor_module()
        fixed_time = 1_700_000_000.0

        with patch("time.time", return_value=fixed_time):
            k1 = mod._make_idempotency_key("market1", "buy", 0.55)
            k2 = mod._make_idempotency_key("market1", "buy", 0.56)

        assert k1 != k2

    def test_different_market_produces_different_key(self):
        mod, _ = _get_executor_module()
        fixed_time = 1_700_000_000.0

        with patch("time.time", return_value=fixed_time):
            k1 = mod._make_idempotency_key("market1", "buy", 0.55)
            k2 = mod._make_idempotency_key("market2", "buy", 0.55)

        assert k1 != k2

    def test_different_side_produces_different_key(self):
        mod, _ = _get_executor_module()
        fixed_time = 1_700_000_000.0

        with patch("time.time", return_value=fixed_time):
            k1 = mod._make_idempotency_key("market1", "buy", 0.55)
            k2 = mod._make_idempotency_key("market1", "sell", 0.55)

        assert k1 != k2

    def test_key_is_16_char_hex(self):
        mod, _ = _get_executor_module()
        k = mod._make_idempotency_key("some_market", "buy", 0.42)
        assert len(k) == 16
        assert all(c in "0123456789abcdef" for c in k)

    def test_different_minute_produces_different_key(self):
        """Keys change across minute boundaries."""
        mod, _ = _get_executor_module()
        # Two times 61 seconds apart straddle a minute boundary
        t1 = 1_700_000_059.0  # minute bucket 28333334
        t2 = 1_700_000_121.0  # minute bucket 28333335

        with patch("time.time", return_value=t1):
            k1 = mod._make_idempotency_key("m", "buy", 0.5)
        with patch("time.time", return_value=t2):
            k2 = mod._make_idempotency_key("m", "buy", 0.5)

        assert k1 != k2

    def test_extra_param_changes_key(self):
        mod, _ = _get_executor_module()
        fixed_time = 1_700_000_000.0

        with patch("time.time", return_value=fixed_time):
            k1 = mod._make_idempotency_key("m", "buy", 0.5, extra="")
            k2 = mod._make_idempotency_key("m", "buy", 0.5, extra="leg2")

        assert k1 != k2


# ---------------------------------------------------------------------------
# TestHasRecentTrade
# ---------------------------------------------------------------------------

class TestHasRecentTrade:
    def test_empty_db_returns_false(self, db):
        assert db.has_recent_trade("market1") is False

    def test_returns_true_for_recent_non_skipped_trade(self, db):
        db.log_opportunity(
            opp_type="Binary", market="market1", prices="Y=0.45 N=0.50",
            total_cost=0.95, net_profit=0.05, net_roi=0.05, depth=100.0,
            action="traded",
        )
        assert db.has_recent_trade("market1", window_secs=60.0) is True

    def test_returns_false_for_different_market(self, db):
        db.log_opportunity(
            opp_type="Binary", market="market1", prices="",
            total_cost=0.9, net_profit=0.1, net_roi=0.1, depth=50.0,
            action="traded",
        )
        assert db.has_recent_trade("market2", window_secs=60.0) is False

    def test_skipped_opportunities_are_excluded(self, db):
        """Skipped opportunities (action LIKE 'skipped:%') must not trigger dedup."""
        db.log_opportunity(
            opp_type="Binary", market="market1", prices="",
            total_cost=0.9, net_profit=0.1, net_roi=0.1, depth=50.0,
            action="skipped:risk:too_small",
        )
        assert db.has_recent_trade("market1", window_secs=60.0) is False

    def test_skipped_duplicate_trade_action_is_excluded(self, db):
        """'skipped:duplicate_trade' should also be excluded from dedup window."""
        db.log_opportunity(
            opp_type="Binary", market="market1", prices="",
            total_cost=0.9, net_profit=0.1, net_roi=0.1, depth=50.0,
            action="skipped:duplicate_trade",
        )
        assert db.has_recent_trade("market1", window_secs=60.0) is False

    def test_dry_run_action_triggers_dedup(self, db):
        """dry_run is a legitimate execution — should count as a recent trade."""
        db.log_opportunity(
            opp_type="Binary", market="market1", prices="",
            total_cost=0.9, net_profit=0.1, net_roi=0.1, depth=50.0,
            action="dry_run",
        )
        assert db.has_recent_trade("market1", window_secs=60.0) is True

    def test_window_respected(self):
        """Trade older than window_secs should not trigger dedup."""
        import sqlite3
        from datetime import datetime, timedelta, timezone

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        database = TradeDB(tmp.name)
        try:
            # Insert an opportunity with an old timestamp directly
            old_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
            with database._lock:
                database.conn.execute(
                    """INSERT INTO opportunities
                       (timestamp, type, market, prices, total_cost, net_profit, net_roi, depth, action)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (old_ts, "Binary", "market1", "", 0.9, 0.1, 0.1, 50.0, "traded"),
                )
                database.conn.commit()

            # Window is 60s — record is 120s old, should NOT trigger dedup
            assert database.has_recent_trade("market1", window_secs=60.0) is False
        finally:
            database.close()
            os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# TestDuplicateRejection
# ---------------------------------------------------------------------------

class TestDuplicateRejection:
    """Test that execute() rejects an opportunity when has_recent_trade returns True."""

    def _make_executor(self, db):
        """Create a minimal ArbitrageExecutor with mocked dependencies."""
        mod, _ = _get_executor_module()
        from risk_manager import RiskManager
        risk = RiskManager({
            "max_trade_size": 5.0,
            "daily_loss_limit": 25.0,
            "max_open_positions": 25,
            "min_net_roi": 0.001,
        })
        executor = mod.ArbitrageExecutor(
            pm_trader=None,
            kalshi_client=None,
            db=db,
            risk_manager=risk,
            dry_run=True,
        )
        return executor

    def test_execute_returns_false_on_duplicate(self, db):
        """execute() returns False and calls _log_skipped when has_recent_trade is True."""
        executor = self._make_executor(db)

        opp = {
            "type": "Binary",
            "market": "Will X?",
            "prices": "Y=0.45 N=0.50",
            "total_cost": 0.95,
            "net_profit": 0.05,
            "net_roi": 0.0526,
            "_clob_depth": 100.0,
        }

        with patch.object(db, "has_recent_trade", return_value=True):
            with patch.object(executor, "_log_skipped") as mock_log:
                result = executor.execute(opp)

        assert result is False
        mock_log.assert_called_once_with(opp, "duplicate_trade")

    def test_execute_proceeds_when_no_duplicate(self, db):
        """execute() does NOT call _log_skipped for duplicate when has_recent_trade returns False."""
        executor = self._make_executor(db)

        opp = {
            "type": "Binary",
            "market": "Will Y?",
            "prices": "Y=0.45 N=0.50",
            "total_cost": 0.95,
            "net_profit": 0.05,
            "net_roi": 0.0526,
            "_clob_depth": 100.0,
        }

        with patch.object(db, "has_recent_trade", return_value=False):
            with patch.object(executor, "_log_skipped") as mock_log:
                # Also patch risk gate and other steps to avoid needing real infra
                with patch.object(executor.risk, "check", return_value=(False, "test_block")):
                    executor.execute(opp)

        # Duplicate-specific log should NOT have been called
        for call in mock_log.call_args_list:
            assert call.args[1] != "duplicate_trade", "Should not log duplicate when no duplicate"


# ---------------------------------------------------------------------------
# TestRecoveryDedup
# ---------------------------------------------------------------------------

class TestRecoveryDedup:
    """Test that _reconcile_pending_trades skips trades that already have filled siblings."""

    def test_skips_trade_with_filled_sibling(self):
        """When get_trades_for_opportunity returns a filled sibling, trade is marked dedup_skipped."""
        from recovery import _reconcile_pending_trades

        db = MagicMock()
        # trade under reconciliation
        trade = {"id": 5, "platform": "polymarket", "order_id": "abc123", "opportunity_id": 10}
        pending_trades = [trade]

        # Sibling: different trade (id 4) for same opportunity, status filled
        db.get_trades_for_opportunity.return_value = [
            {"id": 4, "status": "filled"},
            {"id": 5, "status": "pending"},
        ]

        _reconcile_pending_trades(db, pending_trades)

        # Should call update_trade_status with dedup_skipped
        db.update_trade_status.assert_called_once_with(5, "dedup_skipped")

    def test_proceeds_normally_when_no_filled_sibling(self):
        """When no filled sibling exists, normal reconciliation continues."""
        from recovery import _reconcile_pending_trades

        db = MagicMock()
        trade = {"id": 5, "platform": "polymarket", "order_id": "abc123", "opportunity_id": 10}
        pending_trades = [trade]

        # No filled siblings — all pending
        db.get_trades_for_opportunity.return_value = [
            {"id": 5, "status": "pending"},
        ]

        with patch("recovery._check_order_status", return_value="filled"):
            _reconcile_pending_trades(db, pending_trades)

        # Normal update should be called (filled), not dedup_skipped
        db.update_trade_status.assert_called_once_with(5, "filled")

    def test_proceeds_when_opportunity_id_missing(self):
        """If opportunity_id is missing (no order_id trade), marks as failed normally."""
        from recovery import _reconcile_pending_trades

        db = MagicMock()
        trade = {"id": 7, "platform": "kalshi", "order_id": None, "opportunity_id": 20}
        pending_trades = [trade]

        # Should just mark as failed (no order_id path)
        _reconcile_pending_trades(db, pending_trades)

        db.update_trade_status.assert_called_with(7, "failed")
