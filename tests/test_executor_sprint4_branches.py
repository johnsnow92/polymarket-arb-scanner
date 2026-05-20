"""Tests for Sprint 4 executor branches.

Covers:
- _build_legs returns 2 cross-platform legs for NWayArb (trade-capable).
- _build_legs returns 1 directional quote leg for LeadLagMM (trade-capable).
- _build_legs returns [] for ToxicFlowPause and VolatilityAdjustedMM (defensive).
- _build_legs returns [] when picked platforms aren't in
  ENABLED_EXECUTION_PLATFORMS (NWayArb + LeadLagMM only).
- _revalidate returns the expected (passed, reason) for each new opp type.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import TradeDB
from risk_manager import RiskManager


@pytest.fixture(autouse=True)
def mock_external_modules():
    """Mock external API modules that may not be installed (mirrors test_executor.py)."""
    mock_modules = {}
    for mod_name in [
        "polymarket_api", "kalshi_api",
        "betfair_api", "smarkets_api", "sxbet_api",
    ]:
        if mod_name not in sys.modules:
            mock_modules[mod_name] = MagicMock()
            sys.modules[mod_name] = mock_modules[mod_name]
    yield
    for mod_name in mock_modules:
        del sys.modules[mod_name]


def _import_executor():
    if "executor" in sys.modules:
        del sys.modules["executor"]
    from executor import ArbitrageExecutor
    return ArbitrageExecutor


@pytest.fixture
def ArbitrageExecutor():
    return _import_executor()


@pytest.fixture
def db():
    trade_db = TradeDB(":memory:")
    yield trade_db
    trade_db.close()


@pytest.fixture
def risk_manager():
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
def executor(ArbitrageExecutor, db, risk_manager):
    return ArbitrageExecutor(
        pm_trader=MagicMock(),
        kalshi_client=MagicMock(),
        db=db,
        risk_manager=risk_manager,
        dry_run=True,
        max_trade_size=5.0,
    )


# ---------------------------------------------------------------------------
# _build_legs branches
# ---------------------------------------------------------------------------


class TestBuildLegsNWayArb:
    def test_emits_two_legs_for_poly_kalshi_pair(self, executor, monkeypatch):
        import executor as exec_mod
        monkeypatch.setattr(
            exec_mod, "ENABLED_EXECUTION_PLATFORMS",
            ("polymarket", "kalshi", "betfair", "smarkets", "sxbet", "matchbook", "gemini"),
        )
        opp = {
            "type": "NWayArb(4 platforms)",
            "_platform_a": "polymarket",
            "_platform_b": "kalshi",
            "_price_a": 0.40,
            "_price_b": 0.55,
            "_token_ids": ["tok_yes", "tok_no"],
            "_kalshi_ticker": "TICKER-NWAY",
        }
        legs = executor._build_legs(opp, 5.0)
        assert len(legs) == 2
        assert legs[0]["platform"] == "polymarket"
        assert legs[0]["price"] == pytest.approx(0.40)
        assert legs[1]["platform"] == "kalshi"
        assert legs[1]["price"] == pytest.approx(0.55)

    def test_returns_empty_when_platform_not_enabled(self, executor, monkeypatch):
        import executor as exec_mod
        monkeypatch.setattr(
            exec_mod, "ENABLED_EXECUTION_PLATFORMS", ("polymarket",),
        )
        opp = {
            "type": "NWayArb(5 platforms)",
            "_platform_a": "polymarket",
            "_platform_b": "kalshi",
            "_price_a": 0.40,
            "_price_b": 0.55,
        }
        assert executor._build_legs(opp, 5.0) == []


class TestBuildLegsLeadLagMM:
    def test_emits_single_leg_on_lagger(self, executor, monkeypatch):
        import executor as exec_mod
        monkeypatch.setattr(
            exec_mod, "ENABLED_EXECUTION_PLATFORMS", ("polymarket", "kalshi"),
        )
        opp = {
            "type": "LeadLagMM",
            "_leader": "polymarket",
            "_lagger": "kalshi",
            "_lag_ms": 900.0,
            "_fair_value": 0.62,
            "_market_key": "ticker-laggy",
        }
        legs = executor._build_legs(opp, 5.0)
        assert len(legs) == 1
        assert legs[0]["platform"] == "kalshi"
        assert legs[0]["price"] == pytest.approx(0.62)
        assert legs[0]["_ticker"] == "ticker-laggy"

    def test_returns_empty_when_lagger_not_enabled(self, executor, monkeypatch):
        import executor as exec_mod
        monkeypatch.setattr(
            exec_mod, "ENABLED_EXECUTION_PLATFORMS", ("polymarket",),
        )
        opp = {
            "type": "LeadLagMM",
            "_leader": "polymarket",
            "_lagger": "kalshi",
            "_lag_ms": 900.0,
            "_fair_value": 0.62,
            "_market_key": "ticker-laggy",
        }
        assert executor._build_legs(opp, 5.0) == []


class TestBuildLegsDefensive:
    def test_toxic_flow_pause_returns_empty(self, executor):
        opp = {
            "type": "ToxicFlowPause",
            "_market_key": "tox",
            "_toxicity": 0.85,
            "_pause_remaining_seconds": 60.0,
        }
        assert executor._build_legs(opp, 5.0) == []

    def test_volatility_adjusted_mm_returns_empty(self, executor):
        opp = {
            "type": "VolatilityAdjustedMM",
            "_market_key": "vol",
            "_volatility": 0.05,
            "_spread_multiplier": 2.4,
        }
        assert executor._build_legs(opp, 5.0) == []


# ---------------------------------------------------------------------------
# _revalidate branches
# ---------------------------------------------------------------------------


class TestRevalidateNWayArb:
    def test_revalidate_nway_signal_when_platforms_outside_poly_kalshi(self, executor):
        opp = {
            "type": "NWayArb(4 platforms)",
            "net_profit": 0.02,
            "total_cost": "$0.95",
            "_platform_a": "betfair",
            "_platform_b": "smarkets",
            "_price_a": 0.40,
            "_price_b": 0.55,
        }
        # No mocking needed — the branch sets reason="nway_signal" without
        # calling _revalidate_cross.
        passed = executor._revalidate(opp, None)
        assert passed is True


class TestRevalidateLeadLagMM:
    def test_revalidate_passes_when_lag_persists(self, executor, monkeypatch):
        import market_maker as mm_mod
        sentinel = MagicMock()
        sentinel.should_quote.return_value = True
        monkeypatch.setattr(mm_mod, "get_lead_lag_mm", lambda: sentinel)
        opp = {
            "type": "LeadLagMM",
            "net_profit": 0.01,
            "total_cost": "$5.00",
            "_lagger": "kalshi",
            "_market_key": "laggy",
        }
        assert executor._revalidate(opp, None) is True

    def test_revalidate_fails_when_lag_collapsed(self, executor, monkeypatch):
        import market_maker as mm_mod
        sentinel = MagicMock()
        sentinel.should_quote.return_value = False
        monkeypatch.setattr(mm_mod, "get_lead_lag_mm", lambda: sentinel)
        opp = {
            "type": "LeadLagMM",
            "net_profit": 0.01,
            "total_cost": "$5.00",
            "_lagger": "kalshi",
            "_market_key": "caught-up",
        }
        assert executor._revalidate(opp, None) is False


class TestRevalidateDefensive:
    def test_toxic_flow_pause_fails_revalidation(self, executor):
        opp = {
            "type": "ToxicFlowPause",
            "net_profit": 0.0,
            "total_cost": "$0.00",
        }
        # net_profit=0 — caught by the early-return guard, returns False
        assert executor._revalidate(opp, None) is False

    def test_volatility_adjusted_mm_fails_revalidation_when_profit_nonzero(
        self, executor,
    ):
        # If profit is non-zero (defensive scan emits 0.0, but be defensive):
        # the branch sets passed=False, reason=defensive_observability.
        opp = {
            "type": "VolatilityAdjustedMM",
            "net_profit": 0.01,
            "total_cost": "$5.00",
        }
        assert executor._revalidate(opp, None) is False
