"""Tests for per-strategy metrics wiring in executor.py (MONITOR-02)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Mock external modules before importing executor
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_external_modules():
    """Mock external API modules that may not be installed."""
    mock_modules = {}
    for mod_name in [
        "polymarket_api", "kalshi_api",
        "betfair_api", "smarkets_api", "sxbet_api",
        "matchbook_api", "gemini_api", "ibkr_api",
    ]:
        if mod_name not in sys.modules:
            mock_modules[mod_name] = MagicMock()
            sys.modules[mod_name] = mock_modules[mod_name]
    yield
    for mod_name in mock_modules:
        if mod_name in sys.modules:
            del sys.modules[mod_name]


def _import_executor():
    if "executor" in sys.modules:
        del sys.modules["executor"]
    import executor as _executor_module
    return _executor_module


@pytest.fixture
def executor_module():
    return _import_executor()


@pytest.fixture
def ArbitrageExecutor(executor_module):
    return executor_module.ArbitrageExecutor


@pytest.fixture
def db():
    from db import TradeDB
    trade_db = TradeDB(":memory:")
    yield trade_db
    trade_db.close()


@pytest.fixture
def risk_manager():
    from risk_manager import RiskManager
    return RiskManager({
        "max_trade_size": 5.0,
        "daily_loss_limit": 25.0,
        "max_open_positions": 25,
        "min_liquidity": 25.0,
        "min_liquidity_high_roi": 10.0,
        "min_net_roi": 0,
        "allow_better_reentry": True,
        "reentry_improvement_threshold": 0.20,
    })


@pytest.fixture
def mock_metrics():
    """Return a mock MetricsCollector that can assert on label arguments."""
    return MagicMock()


@pytest.fixture
def executor(ArbitrageExecutor, db, risk_manager):
    pm_trader = MagicMock()
    kalshi_client = MagicMock()
    return ArbitrageExecutor(
        pm_trader=pm_trader,
        kalshi_client=kalshi_client,
        db=db,
        risk_manager=risk_manager,
        dry_run=True,
        max_trade_size=5.0,
    )


# ---------------------------------------------------------------------------
# TestPerStrategyMetrics
# ---------------------------------------------------------------------------

class TestPerStrategyMetrics:
    """Verify that executor.py uses 'strategy' as the primary label key."""

    def _binary_opp(self, opp_type: str = "Binary") -> dict:
        return {
            "type": opp_type,
            "market": "Test Market",
            "prices": "Y=0.40 N=0.45",
            "_token_ids": ["tok_yes", "tok_no"],
            "net_profit": 0.15,
            "net_roi": 0.15,
            "_clob_depth": 100.0,
        }

    def test_trades_executed_uses_strategy_label_on_dry_run(
        self, executor, executor_module
    ):
        """DRY RUN: trades_executed counter must use 'strategy' label key."""
        mock_m = MagicMock()
        executor_module._metrics = mock_m

        # Return None balances so risk manager skips balance checks
        executor._get_cached_balances = lambda opp_type: None

        opp = self._binary_opp("Binary")
        executor.execute(opp)

        # Find an inc("trades_executed", ...) call
        inc_calls = [c for c in mock_m.inc.call_args_list
                     if c.args and c.args[0] == "trades_executed"]
        assert inc_calls, "trades_executed was never incremented"

        # The labels dict must contain 'strategy' key
        for c in inc_calls:
            labels_arg = c.args[1] if len(c.args) > 1 else c.kwargs.get("labels")
            assert labels_arg is not None, "No labels dict passed to trades_executed"
            assert "strategy" in labels_arg, (
                f"'strategy' label missing from trades_executed call. Got: {labels_arg}"
            )
            assert "platform" not in labels_arg, (
                f"Old 'platform' label still present in trades_executed. Got: {labels_arg}"
            )

    def test_trades_executed_strategy_value_matches_opp_type(
        self, executor, executor_module
    ):
        """The 'strategy' label value must equal the opportunity type string."""
        mock_m = MagicMock()
        executor_module._metrics = mock_m

        # Return None balances so risk manager skips balance checks
        executor._get_cached_balances = lambda opp_type: None

        opp = self._binary_opp("Binary")
        executor.execute(opp)

        inc_calls = [c for c in mock_m.inc.call_args_list
                     if c.args and c.args[0] == "trades_executed"]
        for c in inc_calls:
            labels_arg = c.args[1] if len(c.args) > 1 else c.kwargs.get("labels")
            assert labels_arg["strategy"] == "Binary"

    def test_execution_latency_uses_strategy_label(
        self, executor, executor_module
    ):
        """execution_latency_seconds histogram must use 'strategy' label key.

        This fires only on live execution (dry_run=False). We test by
        inspecting executor source for the label key, since running live
        execution requires real platform clients.
        """
        import executor as ex_mod
        import inspect
        src = inspect.getsource(ex_mod.ArbitrageExecutor.execute)
        assert '"strategy"' in src or "'strategy'" in src, (
            "execute() method does not contain 'strategy' label string"
        )
        # Specifically verify latency observe uses strategy, not type
        assert 'observe("execution_latency_seconds"' in src
        # Confirm 'strategy' appears near the observe call (within method)
        idx_observe = src.find('observe("execution_latency_seconds"')
        surrounding = src[max(0, idx_observe - 50):idx_observe + 200]
        assert "strategy" in surrounding, (
            f"'strategy' not found near execution_latency_seconds observe call. Context: {surrounding!r}"
        )

    def test_risk_rejections_uses_strategy_label(
        self, executor, executor_module
    ):
        """risk_rejections counter must include 'strategy' label key."""
        import executor as ex_mod
        import inspect
        src = inspect.getsource(ex_mod.ArbitrageExecutor.execute)
        idx = src.find('inc("risk_rejections"')
        assert idx != -1, "risk_rejections not found in execute()"
        surrounding = src[idx:idx + 150]
        assert "strategy" in surrounding, (
            f"'strategy' label missing from risk_rejections inc call. Context: {surrounding!r}"
        )

    def test_trades_failed_uses_strategy_label(
        self, executor, executor_module
    ):
        """trades_failed counter (live execution path) must use 'strategy' label."""
        import executor as ex_mod
        import inspect
        src = inspect.getsource(ex_mod.ArbitrageExecutor.execute)
        idx = src.find('inc("trades_failed"')
        assert idx != -1, "trades_failed not found in execute()"
        surrounding = src[idx:idx + 150]
        assert "strategy" in surrounding, (
            f"'strategy' label missing from trades_failed inc call. Context: {surrounding!r}"
        )
