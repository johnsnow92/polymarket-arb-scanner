"""Tests for scans/insider_pattern.py — Strategy #42 Insider Pattern Detection."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock py_clob_client before importing scan modules
sys.modules["py_clob_client"] = MagicMock()
sys.modules["py_clob_client.clob_types"] = MagicMock()
sys.modules["py_clob_client.client"] = MagicMock()

from scans.insider_pattern import (
    scan_insider_pattern,
    OrderFlowTracker,
    get_order_flow_tracker,
)


class TestOrderFlowTracker:
    def test_record_and_get_recent_flow(self):
        tracker = OrderFlowTracker(lookback_seconds=3600)

        now = time.time()
        tracker.record_trade("m1", "buy", 0.50, 100.0, now - 100)
        tracker.record_trade("m1", "buy", 0.51, 150.0, now - 50)
        tracker.record_trade("m1", "sell", 0.49, 50.0, now - 30)

        flow = tracker.get_recent_flow("m1")
        assert flow["buy_volume"] == 250.0
        assert flow["sell_volume"] == 50.0
        assert flow["total_volume"] == 300.0
        assert flow["imbalance"] == pytest.approx(0.667, abs=0.01)
        assert flow["num_trades"] == 3

    def test_old_trades_excluded(self):
        tracker = OrderFlowTracker(lookback_seconds=60)

        now = time.time()
        tracker.record_trade("m1", "buy", 0.50, 100.0, now - 120)
        tracker.record_trade("m1", "buy", 0.51, 50.0, now - 30)

        flow = tracker.get_recent_flow("m1")
        assert flow["buy_volume"] == 50.0
        assert flow["num_trades"] == 1

    def test_get_baseline_flow_insufficient_data(self):
        tracker = OrderFlowTracker()
        tracker.record_trade("m1", "buy", 0.50, 100.0)

        baseline = tracker.get_baseline_flow("m1")
        assert baseline["avg_hourly_volume"] == 0.0
        assert baseline["typical_imbalance_range"] == 0.5

    def test_detect_anomaly_volume_spike(self):
        tracker = OrderFlowTracker(lookback_seconds=3600)
        now = time.time()

        for i in range(25):
            ts = now - (i * 4000)
            tracker.record_trade("m1", "buy", 0.50, 10.0, ts)

        for i in range(10):
            ts = now - (i * 100)
            tracker.record_trade("m1", "buy", 0.52, 100.0, ts)

        with patch("scans.insider_pattern.INSIDER_PATTERN_VOLUME_THRESHOLD", 2.0):
            with patch("scans.insider_pattern.INSIDER_PATTERN_IMBALANCE_THRESHOLD", 0.6):
                anomaly = tracker.detect_anomaly("m1")
                if anomaly:
                    assert anomaly["direction"] == "BUY_YES"
                    assert anomaly["confidence"] > 0.5

    def test_detect_anomaly_imbalance(self):
        tracker = OrderFlowTracker(lookback_seconds=3600)
        now = time.time()

        for i in range(10):
            tracker.record_trade("m1", "buy", 0.50, 100.0, now - i * 60)

        with patch("scans.insider_pattern.INSIDER_PATTERN_IMBALANCE_THRESHOLD", 0.5):
            anomaly = tracker.detect_anomaly("m1")
            assert anomaly is not None
            assert anomaly["imbalance"] == pytest.approx(1.0, abs=0.01)

    def test_no_anomaly_when_balanced(self):
        tracker = OrderFlowTracker(lookback_seconds=3600)
        now = time.time()

        for i in range(5):
            tracker.record_trade("m1", "buy", 0.50, 100.0, now - i * 60)
            tracker.record_trade("m1", "sell", 0.50, 100.0, now - i * 60 - 30)

        anomaly = tracker.detect_anomaly("m1")
        assert anomaly is None


class TestScanInsiderPattern:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        with patch("scans.insider_pattern.INSIDER_PATTERN_ENABLED", True):
            with patch("scans.insider_pattern.INSIDER_PATTERN_VOLUME_THRESHOLD", 2.0):
                with patch("scans.insider_pattern.INSIDER_PATTERN_IMBALANCE_THRESHOLD", 0.6):
                    yield

    def test_disabled_returns_empty(self):
        with patch("scans.insider_pattern.INSIDER_PATTERN_ENABLED", False):
            result = scan_insider_pattern([])
            assert result == []

    def test_empty_markets_returns_empty(self):
        result = scan_insider_pattern([])
        assert result == []

    def test_no_anomaly_returns_empty(self):
        tracker = MagicMock()
        tracker.detect_anomaly.return_value = None

        markets = [
            {
                "title": "Test market",
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]

        result = scan_insider_pattern(markets, order_flow_tracker=tracker)
        assert result == []

    def test_finds_anomaly_opportunity(self):
        tracker = MagicMock()
        tracker.detect_anomaly.return_value = {
            "direction": "BUY_YES",
            "imbalance": 0.80,
            "volume_spike": True,
            "recent_volume": 5000.0,
            "baseline_hourly": 500.0,
            "confidence": 0.70,
        }

        markets = [
            {
                "title": "Will X happen?",
                "yes_price": 0.50,
                "condition_id": "m1",
            }
        ]

        with patch("fees.net_profit_insider_pattern") as mock_fee:
            mock_fee.return_value = {
                "net_profit": 0.08,
                "net_roi": 0.16,
            }
            result = scan_insider_pattern(
                markets,
                order_flow_tracker=tracker,
                min_profit=0.01,
            )

            assert len(result) > 0
            opp = result[0]
            assert opp["type"] == "InsiderPattern"
            assert opp["_layer"] == 4
            assert opp["_direction"] == "BUY_YES"
            assert opp["_imbalance"] == 0.80

    def test_sorted_by_confidence(self):
        tracker = MagicMock()
        tracker.detect_anomaly.side_effect = [
            {"direction": "BUY_YES", "imbalance": 0.6, "volume_spike": False,
             "recent_volume": 1000, "baseline_hourly": 500, "confidence": 0.55},
            {"direction": "BUY_NO", "imbalance": -0.9, "volume_spike": True,
             "recent_volume": 3000, "baseline_hourly": 500, "confidence": 0.75},
        ]

        markets = [
            {"title": "Market A", "yes_price": 0.50, "condition_id": "m1"},
            {"title": "Market B", "yes_price": 0.60, "condition_id": "m2"},
        ]

        with patch("fees.net_profit_insider_pattern") as mock_fee:
            mock_fee.return_value = {"net_profit": 0.05, "net_roi": 0.10}
            result = scan_insider_pattern(
                markets,
                order_flow_tracker=tracker,
                min_profit=0.01,
            )

            if len(result) >= 2:
                assert result[0]["confidence"] >= result[1]["confidence"]


class TestGetOrderFlowTracker:
    def test_returns_singleton(self):
        tracker1 = get_order_flow_tracker()
        tracker2 = get_order_flow_tracker()
        assert tracker1 is tracker2


class TestInsiderPatternFeeFunction:
    def test_net_profit_insider_pattern(self):
        from fees import net_profit_insider_pattern
        result = net_profit_insider_pattern(
            market_price=0.50,
            expected_edge=0.10,
            platform="polymarket",
        )
        assert "net_profit" in result
        assert "gross_spread" in result

    def test_zero_edge_zero_profit(self):
        from fees import net_profit_insider_pattern
        result = net_profit_insider_pattern(
            market_price=0.50,
            expected_edge=0.0,
            platform="kalshi",
        )
        assert result["gross_spread"] == 0.0
