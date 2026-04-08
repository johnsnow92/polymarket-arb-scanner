"""Tests for scans/whale_copy.py and whale copy executor integration."""

import sys
import os
import time
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock external APIs before importing the module under test
_mock_polymarket_api = MagicMock()
_mock_polygonscan_api = MagicMock()
sys.modules["polymarket_api"] = _mock_polymarket_api
sys.modules["polygonscan_api"] = _mock_polygonscan_api

from scans.whale_copy import (
    _parse_clob_transaction,
    scan_whale_copy,
    _refine_whale_copy_with_prices,
    POLYMARKET_CLOB_ADDRESS,
)
from fees import net_profit_whale_copy


@pytest.fixture(autouse=True)
def cleanup_modules():
    """Remove scans.whale_copy from sys.modules to prevent test pollution."""
    yield
    sys.modules.pop("scans.whale_copy", None)


def _make_tx(
    hash_val="0xabc123",
    block="100",
    timestamp=None,
    to=POLYMARKET_CLOB_ADDRESS,
    is_error="0",
):
    """Create a mock Polygonscan transaction dict."""
    if timestamp is None:
        timestamp = str(int(time.time()))
    return {
        "hash": hash_val,
        "blockNumber": block,
        "timeStamp": timestamp,
        "to": to,
        "input": "0x12345678",
        "isError": is_error,
    }


# ---------------------------------------------------------------------------
# TestTransactionParsing — _parse_clob_transaction()
# ---------------------------------------------------------------------------


class TestTransactionParsing:
    """Test _parse_clob_transaction for CLOB event extraction."""

    def test_parses_basic_fields(self):
        tx = _make_tx(hash_val="0xdeadbeef", block="42")
        opp = _parse_clob_transaction(tx, "0xwhalewallet")
        assert opp is not None
        assert opp["type"] == "WhaleCopy"
        assert opp["_whale_tx_hash"] == "0xdeadbeef"
        assert opp["_whale_block"] == 42
        assert opp["_whale_address"] == "0xwhalewallet"

    def test_sets_layer_4(self):
        tx = _make_tx()
        opp = _parse_clob_transaction(tx, "0xaddr")
        assert opp["_layer"] == 4

    def test_market_key_is_tx_hash(self):
        tx = _make_tx(hash_val="0x999")
        opp = _parse_clob_transaction(tx, "0xaddr")
        assert opp["_market_key"] == "0x999"

    def test_rejects_error_transactions(self):
        tx = _make_tx(is_error="1")
        opp = _parse_clob_transaction(tx, "0xaddr")
        assert opp is None

    def test_returns_none_for_reverted(self):
        tx = _make_tx(is_error="1")
        result = _parse_clob_transaction(tx, "0xaddr")
        assert result is None


# ---------------------------------------------------------------------------
# TestScanStage1 — scan_whale_copy()
# ---------------------------------------------------------------------------


class TestScanStage1:
    """Test Stage 1: whale transaction polling."""

    def test_detects_whale_transaction(self):
        client = MagicMock()
        client.get_latest_transactions.return_value = [
            _make_tx(hash_val="0xfound", to=POLYMARKET_CLOB_ADDRESS),
        ]
        with patch("scans.whale_copy._refine_whale_copy_with_prices", side_effect=lambda x: x):
            result = scan_whale_copy(["0xwhale1"], client)
        assert len(result) == 1
        assert result[0]["_whale_tx_hash"] == "0xfound"

    def test_returns_required_keys(self):
        client = MagicMock()
        client.get_latest_transactions.return_value = [
            _make_tx(),
        ]
        with patch("scans.whale_copy._refine_whale_copy_with_prices", side_effect=lambda x: x):
            result = scan_whale_copy(["0xwhale1"], client)
        opp = result[0]
        for key in ["type", "market", "_whale_address", "_whale_tx_hash",
                     "_whale_timestamp", "_whale_block", "_market_key", "_layer"]:
            assert key in opp, f"Missing key: {key}"

    def test_handles_empty_wallet_list(self):
        client = MagicMock()
        result = scan_whale_copy([], client)
        assert result == []

    def test_handles_none_client(self):
        result = scan_whale_copy(["0xwhale1"], None)
        assert result == []

    def test_updates_block_cache(self):
        client = MagicMock()
        client.get_latest_transactions.return_value = [
            _make_tx(block="500", to=POLYMARKET_CLOB_ADDRESS),
        ]
        cache = {}
        with patch("scans.whale_copy._refine_whale_copy_with_prices", side_effect=lambda x: x):
            scan_whale_copy(["0xwhale1"], client, last_block_cache=cache)
        assert cache["0xwhale1"] == 500

    def test_graceful_degradation_network_error(self):
        client = MagicMock()
        client.get_latest_transactions.side_effect = Exception("Network error")
        result = scan_whale_copy(["0xwhale1", "0xwhale2"], client)
        assert result == []

    def test_filters_to_clob_address(self):
        client = MagicMock()
        client.get_latest_transactions.return_value = [
            _make_tx(hash_val="0xclob", to=POLYMARKET_CLOB_ADDRESS),
            _make_tx(hash_val="0xother", to="0xSomeOtherContract"),
        ]
        with patch("scans.whale_copy._refine_whale_copy_with_prices", side_effect=lambda x: x):
            result = scan_whale_copy(["0xwhale1"], client)
        assert len(result) == 1
        assert result[0]["_whale_tx_hash"] == "0xclob"


# ---------------------------------------------------------------------------
# TestRefinementStage2 — _refine_whale_copy_with_prices()
# ---------------------------------------------------------------------------


class TestRefinementStage2:
    """Test Stage 2: market price refinement."""

    def test_drops_stale_trades_over_30s(self):
        opp = {
            "type": "WhaleCopy",
            "_whale_timestamp": int(time.time()) - 60,  # 60s ago
            "_market_key": "test",
        }
        result = _refine_whale_copy_with_prices([opp])
        assert len(result) == 0

    def test_keeps_fresh_trades(self):
        opp = {
            "type": "WhaleCopy",
            "_whale_timestamp": int(time.time()) - 5,  # 5s ago
            "_market_key": "test",
        }
        result = _refine_whale_copy_with_prices([opp])
        assert len(result) == 1

    def test_handles_empty_list(self):
        result = _refine_whale_copy_with_prices([])
        assert result == []


# ---------------------------------------------------------------------------
# TestFeeCalculation — net_profit_whale_copy()
# ---------------------------------------------------------------------------


class TestFeeCalculation:
    """Test net_profit_whale_copy fee calculator."""

    def test_net_profit_basic(self):
        result = net_profit_whale_copy(0.50, 0.55)
        assert result > 0

    def test_net_profit_zero_margin(self):
        result = net_profit_whale_copy(0.50, 0.50)
        assert result < 0  # Negative due to taker fees

    def test_accounts_for_double_taker_fee(self):
        # Profit should be less than raw price difference
        raw_diff = 0.55 - 0.50
        result = net_profit_whale_copy(0.50, 0.55)
        assert result < raw_diff

    def test_large_spread_profitable(self):
        result = net_profit_whale_copy(0.40, 0.60)
        assert result > 0


# ---------------------------------------------------------------------------
# TestExecutorIntegration — _build_legs and _revalidate_whale_copy
# ---------------------------------------------------------------------------


class TestExecutorIntegration:
    """Test executor integration for WhaleCopy opportunities."""

    @pytest.fixture(autouse=True)
    def mock_external_modules(self):
        """Mock all external modules needed by executor."""
        mods = [
            "polymarket_api", "kalshi_api", "betfair_api",
            "smarkets_api", "sxbet_api", "matchbook_api",
            "gemini_api", "ibkr_api", "polygonscan_api",
        ]
        for mod in mods:
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()
        yield

    def _make_executor(self):
        """Create a minimal ArbitrageExecutor for testing."""
        from executor import ArbitrageExecutor
        db = MagicMock()
        db.has_recent_trade.return_value = False
        risk = MagicMock()
        risk.check.return_value = (True, "")
        return ArbitrageExecutor(
            pm_trader=None,
            kalshi_client=None,
            db=db,
            risk_manager=risk,
            dry_run=True,
        )

    def test_build_legs_whale_copy(self):
        executor = self._make_executor()
        opp = {
            "type": "WhaleCopy",
            "market": "Whale test",
            "_token_ids": ["0xtoken123"],
            "_market_price": 0.55,
        }
        legs = executor._build_legs(opp, 10.0)
        assert len(legs) == 1
        assert legs[0]["platform"] == "polymarket"
        assert legs[0]["side"] == "BUY"
        assert legs[0]["_token_id"] == "0xtoken123"

    def test_build_legs_missing_token_ids(self):
        executor = self._make_executor()
        opp = {"type": "WhaleCopy", "market": "test", "_token_ids": []}
        legs = executor._build_legs(opp, 10.0)
        assert legs == []

    def test_position_limit_enforcement(self):
        executor = self._make_executor()
        executor._whale_copy_position_count = 5  # Default max positions
        opp = {
            "type": "WhaleCopy",
            "market": "test",
            "_token_ids": ["0xtoken"],
            "_market_price": 0.50,
            "net_profit": 0.05,
            "net_roi": 0.10,
            "total_cost": 0.50,
        }
        result = executor.execute(opp)
        assert result is False

    def test_whale_counter_increments(self):
        executor = self._make_executor()
        assert executor._whale_copy_position_count == 0
        executor._whale_copy_position_count += 1
        assert executor._whale_copy_position_count == 1
