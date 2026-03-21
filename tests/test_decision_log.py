"""Tests for JSONL decision logging in ArbitrageExecutor (HARDEN-03)."""

import json
import os
import sys
import tempfile
import threading
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import TradeDB
from risk_manager import RiskManager


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
    """Force reimport of executor with mocked modules in place."""
    if "executor" in sys.modules:
        del sys.modules["executor"]
    from executor import ArbitrageExecutor
    return ArbitrageExecutor


@pytest.fixture
def ArbitrageExecutor():
    return _import_executor()


@pytest.fixture
def tmp_data_dir():
    """Provide a temporary directory as DATA_DIR for the executor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


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
def executor(ArbitrageExecutor, db, risk_manager, tmp_data_dir):
    """Create an ArbitrageExecutor with DATA_DIR pointing to tmp dir."""
    with patch.dict(os.environ, {"DATA_DIR": tmp_data_dir}):
        if "executor" in sys.modules:
            del sys.modules["executor"]
        ArbitrageExecutor = _import_executor()
        pm_trader = MagicMock()
        kalshi_client = MagicMock()
        exc = ArbitrageExecutor(
            pm_trader=pm_trader,
            kalshi_client=kalshi_client,
            db=db,
            risk_manager=risk_manager,
            dry_run=True,
            max_trade_size=5.0,
        )
        yield exc
        exc.close()


class TestDecisionLog:
    """Tests for JSONL decision logging via _write_decision."""

    def test_write_decision_appends_json_line(self, executor, tmp_data_dir):
        """_write_decision writes a valid JSON line with expected keys."""
        opp = {
            "type": "Binary",
            "market": "TEST-MARKET",
            "prices": "Y=0.5 N=0.5",
            "net_profit": 0.05,
            "net_roi": "5%",
        }
        executor._write_decision(opp, "skip", "test_reason")

        log_path = os.path.join(tmp_data_dir, "decisions.jsonl")
        assert os.path.exists(log_path)

        with open(log_path, "r", encoding="utf-8") as fh:
            line = fh.readline()

        entry = json.loads(line)
        assert "ts" in entry
        assert entry["strategy"] == "Binary"
        assert entry["market"] == "TEST-MARKET"
        assert entry["decision"] == "skip"
        assert entry["reason"] == "test_reason"
        assert "prices" in entry
        assert "expected_profit" in entry
        assert "expected_roi" in entry
        assert "risk_check" in entry

    def test_write_decision_ts_is_numeric(self, executor, tmp_data_dir):
        """Timestamp field is a float epoch value."""
        opp = {"type": "Cross", "market": "M", "prices": "", "net_profit": 0, "net_roi": "0%"}
        executor._write_decision(opp, "execute", "dry_run")

        log_path = os.path.join(tmp_data_dir, "decisions.jsonl")
        with open(log_path, "r", encoding="utf-8") as fh:
            entry = json.loads(fh.readline())

        assert isinstance(entry["ts"], (int, float))
        assert entry["ts"] > 0

    def test_log_skipped_writes_decision(self, executor, tmp_data_dir):
        """_log_skipped calls _write_decision with decision='skip'."""
        opp = {
            "type": "Binary",
            "market": "SKIP-MARKET",
            "prices": "Y=0.5",
            "total_cost": "$5.00",
            "net_profit": 0.05,
            "net_roi": "1%",
            "_clob_depth": 100,
        }
        executor._log_skipped(opp, "test_skip_reason")

        log_path = os.path.join(tmp_data_dir, "decisions.jsonl")
        assert os.path.exists(log_path)

        with open(log_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["decision"] == "skip"
        assert entry["reason"] == "test_skip_reason"
        assert entry["market"] == "SKIP-MARKET"

    def test_dry_run_log_writes_decision(self, executor, tmp_data_dir):
        """_dry_run_log calls _write_decision with decision='execute', reason='dry_run'."""
        opp = {
            "type": "Binary",
            "market": "DRY-MARKET",
            "prices": "Y=0.5 N=0.5",
            "total_cost": "$5.00",
            "net_profit": 0.10,
            "net_roi": "2%",
            "_clob_depth": 200,
        }
        legs = [
            {"platform": "polymarket", "side": "YES", "price": 0.5, "_token_id": "tok1"},
            {"platform": "polymarket", "side": "NO", "price": 0.5, "_token_id": "tok2"},
        ]
        executor._dry_run_log(opp, legs, 5.0)

        log_path = os.path.join(tmp_data_dir, "decisions.jsonl")
        assert os.path.exists(log_path)

        with open(log_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["decision"] == "execute"
        assert entry["reason"] == "dry_run"
        assert entry["market"] == "DRY-MARKET"

    def test_close_releases_handle(self, executor):
        """close() closes the decision file handle."""
        assert not executor._decision_fh.closed
        executor.close()
        assert executor._decision_fh.closed

    def test_thread_safety(self, executor, tmp_data_dir):
        """Concurrent _write_decision calls produce valid JSONL without corruption."""
        opp = {"type": "Cross", "market": "THREAD-MKT", "prices": "", "net_profit": 0, "net_roi": "0%"}
        errors = []

        def worker():
            try:
                for _ in range(10):
                    executor._write_decision(opp, "skip", "concurrent_test")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

        log_path = os.path.join(tmp_data_dir, "decisions.jsonl")
        with open(log_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        # 20 threads * 10 writes = 200 lines
        assert len(lines) == 200
        # All lines should be valid JSON
        for line in lines:
            entry = json.loads(line)  # raises if invalid
            assert entry["decision"] == "skip"

    def test_decision_fh_opens_in_append_mode(self, executor):
        """The decision file handle is opened in append mode."""
        assert not executor._decision_fh.closed
        # 'a' mode means it's writable and position is at end
        assert executor._decision_fh.mode == "a"

    def test_multiple_writes_produce_multiple_lines(self, executor, tmp_data_dir):
        """Multiple _write_decision calls produce multiple JSONL lines."""
        opp = {"type": "Binary", "market": "M", "prices": "", "net_profit": 0, "net_roi": "0%"}
        for i in range(5):
            executor._write_decision(opp, "skip", f"reason_{i}")

        log_path = os.path.join(tmp_data_dir, "decisions.jsonl")
        with open(log_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        assert len(lines) == 5
        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert entry["reason"] == f"reason_{i}"
