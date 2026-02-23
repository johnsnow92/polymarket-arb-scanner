"""Tests for snapshot.py — historical price snapshot recording."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from snapshot import SnapshotRecorder


@pytest.fixture
def recorder():
    rec = SnapshotRecorder(db_path=":memory:")
    yield rec
    rec.close()


# ---------------------------------------------------------------------------
# SnapshotRecorder basic recording
# ---------------------------------------------------------------------------

class TestRecordSnapshot:
    def test_records_single_opportunity(self, recorder):
        opps = [{
            "type": "Binary",
            "market": "Will it rain tomorrow?",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.138,
            "net_roi": "16.2%",
            "_clob_depth": 100.0,
        }]
        count = recorder.record_snapshot(opps)
        assert count == 1
        assert recorder.get_snapshot_count() == 1

    def test_records_multiple_opportunities(self, recorder):
        opps = [
            {
                "type": "Binary",
                "market": "Market A",
                "prices": "Y=0.400 N=0.450",
                "total_cost": "$0.8500",
                "net_profit": 0.10,
            },
            {
                "type": "KalshiBinary",
                "market": "Market B",
                "prices": "Y=0.350 N=0.400",
                "total_cost": "$0.7500",
                "net_profit": 0.20,
            },
        ]
        count = recorder.record_snapshot(opps)
        assert count == 2
        assert recorder.get_snapshot_count() == 2

    def test_empty_list_records_nothing(self, recorder):
        count = recorder.record_snapshot([])
        assert count == 0
        assert recorder.get_snapshot_count() == 0

    def test_snapshot_stores_correct_data(self, recorder):
        opps = [{
            "type": "Cross",
            "market": "Test Cross Market",
            "prices": "PM_Y=0.300 K_N=0.350",
            "total_cost": "$0.6500",
            "net_profit": 0.30,
            "_platform_a": "polymarket",
            "_platform_b": "kalshi",
        }]
        recorder.record_snapshot(opps)
        snapshots = recorder.get_snapshots("2000-01-01", "2099-12-31")
        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap["market"] == "Test Cross Market"
        assert snap["opp_type"] == "Cross"
        assert snap["net_profit"] == pytest.approx(0.30)
        assert snap["platform_a"] == "polymarket"
        assert snap["platform_b"] == "kalshi"


# ---------------------------------------------------------------------------
# Platform extraction
# ---------------------------------------------------------------------------

class TestExtractPlatforms:
    def test_binary_uses_polymarket(self, recorder):
        opp = {"type": "Binary", "prices": "Y=0.400 N=0.450"}
        pa, pb, _, _ = recorder._extract_platforms(opp)
        assert pa == "polymarket"
        assert pb == "polymarket"

    def test_kalshi_binary(self, recorder):
        opp = {"type": "KalshiBinary", "prices": "Y=0.400 N=0.450"}
        pa, pb, _, _ = recorder._extract_platforms(opp)
        assert pa == "kalshi"
        assert pb == "kalshi"

    def test_cross_with_platform_metadata(self, recorder):
        opp = {
            "type": "Cross",
            "prices": "polymarket_Y=0.300 smarkets_N=0.350",
            "_platform_a": "polymarket",
            "_platform_b": "smarkets",
        }
        pa, pb, _, _ = recorder._extract_platforms(opp)
        assert pa == "polymarket"
        assert pb == "smarkets"

    def test_event_divergence(self, recorder):
        opp = {
            "type": "EventDivergence",
            "prices": "platform=0.400 metaculus=0.600",
            "_platform": "kalshi",
        }
        pa, pb, _, _ = recorder._extract_platforms(opp)
        assert pa == "kalshi"
        assert pb == "metaculus"

    def test_betfair_type(self, recorder):
        opp = {"type": "BetfairBackAll", "prices": ""}
        pa, pb, _, _ = recorder._extract_platforms(opp)
        assert pa == "betfair"
        assert pb == "betfair"

    def test_gemini_type(self, recorder):
        opp = {"type": "GeminiBinary", "prices": "Y=0.400 N=0.450"}
        pa, pb, _, _ = recorder._extract_platforms(opp)
        assert pa == "gemini"
        assert pb == "gemini"

    def test_ibkr_type(self, recorder):
        opp = {"type": "IBKRBinary", "prices": "Y=0.400 N=0.450"}
        pa, pb, _, _ = recorder._extract_platforms(opp)
        assert pa == "ibkr"
        assert pb == "ibkr"


# ---------------------------------------------------------------------------
# Price parsing
# ---------------------------------------------------------------------------

class TestParsePricesStr:
    def test_parses_yes_no_format(self):
        a, b = SnapshotRecorder._parse_prices_str("Y=0.400 N=0.450")
        assert a == pytest.approx(0.400)
        assert b == pytest.approx(0.450)

    def test_parses_cross_format(self):
        a, b = SnapshotRecorder._parse_prices_str("PM_Y=0.300 K_N=0.350")
        assert a == pytest.approx(0.300)
        assert b == pytest.approx(0.350)

    def test_parses_comma_separated(self):
        a, b = SnapshotRecorder._parse_prices_str("0.20, 0.30, 0.25")
        assert a == pytest.approx(0.20)
        assert b == pytest.approx(0.30)

    def test_empty_string(self):
        a, b = SnapshotRecorder._parse_prices_str("")
        assert a is None
        assert b is None

    def test_single_price(self):
        a, b = SnapshotRecorder._parse_prices_str("Y=0.500")
        assert a == pytest.approx(0.500)
        assert b is None


# ---------------------------------------------------------------------------
# get_snapshots filtering
# ---------------------------------------------------------------------------

class TestGetSnapshots:
    def test_filter_by_time_range(self, recorder):
        opps = [{
            "type": "Binary",
            "market": "Test",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.10,
        }]
        recorder.record_snapshot(opps)
        # Should find the snapshot (wide range)
        results = recorder.get_snapshots("2000-01-01", "2099-12-31")
        assert len(results) == 1

    def test_filter_by_opp_type(self, recorder):
        opps = [
            {
                "type": "Binary",
                "market": "A",
                "prices": "Y=0.400 N=0.450",
                "total_cost": "$0.8500",
                "net_profit": 0.10,
            },
            {
                "type": "KalshiBinary",
                "market": "B",
                "prices": "Y=0.350 N=0.400",
                "total_cost": "$0.7500",
                "net_profit": 0.20,
            },
        ]
        recorder.record_snapshot(opps)
        results = recorder.get_snapshots("2000-01-01", "2099-12-31", opp_type="Binary")
        assert len(results) == 1
        assert results[0]["opp_type"] == "Binary"

    def test_empty_range_returns_empty(self, recorder):
        opps = [{
            "type": "Binary",
            "market": "Test",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.10,
        }]
        recorder.record_snapshot(opps)
        results = recorder.get_snapshots("2000-01-01", "2000-01-02")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Numeric total_cost handling
# ---------------------------------------------------------------------------

class TestNumericTotalCost:
    def test_numeric_total_cost(self, recorder):
        opps = [{
            "type": "Binary",
            "market": "Test",
            "prices": "Y=0.400 N=0.450",
            "total_cost": 0.85,
            "net_profit": 0.10,
        }]
        count = recorder.record_snapshot(opps)
        assert count == 1
        snapshots = recorder.get_snapshots("2000-01-01", "2099-12-31")
        assert snapshots[0]["gross_spread"] == pytest.approx(0.15)
