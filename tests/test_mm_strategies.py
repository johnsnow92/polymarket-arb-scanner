"""Tests for market_maker.py — Strategies #36-#38."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from market_maker import (
    VolatilityTracker,
    LeadLagMM,
    ToxicFlowDetector,
    get_volatility_tracker,
    get_lead_lag_mm,
    get_toxic_flow_detector,
)


class TestVolatilityTracker:
    def test_record_price_and_get_volatility(self):
        tracker = VolatilityTracker(lookback_seconds=3600, min_samples=3)
        tracker.record_price("m1", 0.50)
        tracker.record_price("m1", 0.52)
        tracker.record_price("m1", 0.48)
        tracker.record_price("m1", 0.53)
        tracker.record_price("m1", 0.49)

        vol = tracker.get_volatility("m1")
        assert vol > 0

    def test_insufficient_samples_returns_zero(self):
        tracker = VolatilityTracker(min_samples=10)
        tracker.record_price("m1", 0.50)
        tracker.record_price("m1", 0.52)

        vol = tracker.get_volatility("m1")
        assert vol == 0.0

    def test_no_samples_returns_zero(self):
        tracker = VolatilityTracker()
        vol = tracker.get_volatility("unknown")
        assert vol == 0.0

    def test_get_spread_multiplier_low_vol(self):
        with patch("market_maker.MM_VOLATILITY_ADJUSTED_ENABLED", True):
            with patch("market_maker.MM_VOLATILITY_SPREAD_MULTIPLIER", 10.0):
                tracker = VolatilityTracker(min_samples=2)
                tracker.record_price("m1", 0.50)
                tracker.record_price("m1", 0.50)

                mult = tracker.get_spread_multiplier("m1")
                assert mult == pytest.approx(1.0, abs=0.1)

    def test_get_spread_multiplier_high_vol(self):
        with patch("market_maker.MM_VOLATILITY_ADJUSTED_ENABLED", True):
            with patch("market_maker.MM_VOLATILITY_SPREAD_MULTIPLIER", 10.0):
                tracker = VolatilityTracker(min_samples=3)
                tracker.record_price("m1", 0.30)
                tracker.record_price("m1", 0.50)
                tracker.record_price("m1", 0.70)
                tracker.record_price("m1", 0.40)

                mult = tracker.get_spread_multiplier("m1", max_multiplier=3.0)
                assert mult > 1.0

    def test_get_spread_multiplier_disabled(self):
        with patch("market_maker.MM_VOLATILITY_ADJUSTED_ENABLED", False):
            tracker = VolatilityTracker()
            mult = tracker.get_spread_multiplier("m1", base_multiplier=1.5)
            assert mult == 1.5

    def test_is_high_volatility(self):
        tracker = VolatilityTracker(min_samples=3)
        tracker.record_price("m1", 0.30)
        tracker.record_price("m1", 0.60)
        tracker.record_price("m1", 0.40)

        assert tracker.is_high_volatility("m1", threshold=0.01) is True
        assert tracker.is_high_volatility("m1", threshold=0.50) is False


class TestLeadLagMM:
    def test_record_update(self):
        mm = LeadLagMM()
        mm.record_update("m1", "polymarket", 0.50)
        mm.record_update("m1", "kalshi", 0.52)

        assert mm._prices["m1"]["polymarket"] == 0.50
        assert mm._prices["m1"]["kalshi"] == 0.52

    def test_get_leader(self):
        with patch("market_maker.LEAD_LAG_MM_ENABLED", True):
            mm = LeadLagMM()
            mm.record_update("m1", "polymarket", 0.50)
            time.sleep(0.01)
            mm.record_update("m1", "kalshi", 0.52)

            leader = mm.get_leader("m1")
            assert leader == "kalshi"

    def test_get_leader_disabled(self):
        with patch("market_maker.LEAD_LAG_MM_ENABLED", False):
            mm = LeadLagMM()
            mm.record_update("m1", "polymarket", 0.50)

            leader = mm.get_leader("m1")
            assert leader is None

    def test_get_lag_ms(self):
        with patch("market_maker.LEAD_LAG_MM_ENABLED", True):
            mm = LeadLagMM()
            mm.record_update("m1", "polymarket", 0.50)
            time.sleep(0.1)
            mm.record_update("m1", "kalshi", 0.52)

            lag = mm.get_lag_ms("m1", "polymarket")
            assert lag >= 50

    def test_get_fair_value(self):
        with patch("market_maker.LEAD_LAG_MM_ENABLED", True):
            mm = LeadLagMM()
            mm.record_update("m1", "polymarket", 0.50)
            mm.record_update("m1", "kalshi", 0.52)

            fair = mm.get_fair_value("m1")
            assert fair == 0.52

    def test_should_quote(self):
        with patch("market_maker.LEAD_LAG_MM_ENABLED", True):
            with patch("market_maker.LEAD_LAG_MIN_DELAY_MS", 50.0):
                mm = LeadLagMM()
                mm.record_update("m1", "polymarket", 0.50)
                time.sleep(0.1)
                mm.record_update("m1", "kalshi", 0.52)

                should = mm.should_quote("m1", "polymarket", min_lag_ms=50.0)
                assert should is True

    def test_should_quote_disabled(self):
        with patch("market_maker.LEAD_LAG_MM_ENABLED", False):
            mm = LeadLagMM()
            mm.record_update("m1", "polymarket", 0.50)
            mm.record_update("m1", "kalshi", 0.52)

            should = mm.should_quote("m1", "polymarket")
            assert should is False


class TestToxicFlowDetector:
    def test_record_fill_adverse(self):
        detector = ToxicFlowDetector()
        detector.record_fill("m1", "bid", 0.52, 10.0, mid_at_fill=0.50)

        fills = detector._fills["m1"]
        assert len(fills) == 1
        assert fills[0]["adverse"] is True

    def test_record_fill_not_adverse(self):
        detector = ToxicFlowDetector()
        detector.record_fill("m1", "bid", 0.48, 10.0, mid_at_fill=0.50)

        fills = detector._fills["m1"]
        assert len(fills) == 1
        assert fills[0]["adverse"] is False

    def test_get_toxicity(self):
        detector = ToxicFlowDetector()
        detector.record_fill("m1", "bid", 0.52, 10.0, mid_at_fill=0.50)
        detector.record_fill("m1", "bid", 0.53, 10.0, mid_at_fill=0.50)
        detector.record_fill("m1", "bid", 0.48, 10.0, mid_at_fill=0.50)

        toxicity = detector.get_toxicity("m1")
        assert toxicity == pytest.approx(0.667, abs=0.01)

    def test_get_toxicity_insufficient_data(self):
        detector = ToxicFlowDetector()
        detector.record_fill("m1", "bid", 0.52, 10.0, mid_at_fill=0.50)

        toxicity = detector.get_toxicity("m1")
        assert toxicity == 0.0

    def test_should_pause_high_toxicity(self):
        with patch("market_maker.MM_TOXIC_FLOW_ENABLED", True):
            detector = ToxicFlowDetector(toxicity_threshold=0.60)
            detector.record_fill("m1", "bid", 0.52, 10.0, mid_at_fill=0.50)
            detector.record_fill("m1", "bid", 0.53, 10.0, mid_at_fill=0.50)
            detector.record_fill("m1", "bid", 0.54, 10.0, mid_at_fill=0.50)

            should = detector.should_pause("m1")
            assert should is True

    def test_should_pause_low_toxicity(self):
        with patch("market_maker.MM_TOXIC_FLOW_ENABLED", True):
            detector = ToxicFlowDetector(toxicity_threshold=0.60)
            detector.record_fill("m1", "bid", 0.48, 10.0, mid_at_fill=0.50)
            detector.record_fill("m1", "bid", 0.47, 10.0, mid_at_fill=0.50)
            detector.record_fill("m1", "bid", 0.46, 10.0, mid_at_fill=0.50)

            should = detector.should_pause("m1")
            assert should is False

    def test_should_pause_disabled(self):
        with patch("market_maker.MM_TOXIC_FLOW_ENABLED", False):
            detector = ToxicFlowDetector()
            detector.record_fill("m1", "bid", 0.52, 10.0, mid_at_fill=0.50)
            detector.record_fill("m1", "bid", 0.53, 10.0, mid_at_fill=0.50)
            detector.record_fill("m1", "bid", 0.54, 10.0, mid_at_fill=0.50)

            should = detector.should_pause("m1")
            assert should is False

    def test_trigger_pause(self):
        with patch("market_maker.MM_TOXIC_FLOW_PAUSE_SECONDS", 60.0):
            detector = ToxicFlowDetector()
            detector.record_fill("m1", "bid", 0.52, 10.0, mid_at_fill=0.50)
            detector.record_fill("m1", "bid", 0.53, 10.0, mid_at_fill=0.50)
            detector.record_fill("m1", "bid", 0.54, 10.0, mid_at_fill=0.50)

            detector.trigger_pause("m1", pause_seconds=30.0)
            remaining = detector.get_pause_remaining("m1")
            assert 25 < remaining <= 30

    def test_get_pause_remaining_not_paused(self):
        detector = ToxicFlowDetector()
        remaining = detector.get_pause_remaining("m1")
        assert remaining == 0.0


class TestGetVolatilityTracker:
    def test_returns_instance(self):
        with patch("market_maker.MM_VOLATILITY_LOOKBACK_SECONDS", 300.0):
            tracker = get_volatility_tracker()
            assert isinstance(tracker, VolatilityTracker)


class TestGetLeadLagMM:
    def test_returns_instance(self):
        mm = get_lead_lag_mm()
        assert isinstance(mm, LeadLagMM)


class TestGetToxicFlowDetector:
    def test_returns_instance(self):
        with patch("market_maker.MM_TOXIC_FLOW_THRESHOLD", 0.60):
            detector = get_toxic_flow_detector()
            assert isinstance(detector, ToxicFlowDetector)
