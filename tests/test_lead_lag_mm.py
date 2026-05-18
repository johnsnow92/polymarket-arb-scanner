"""Tests for Strategy LeadLagMM — lead-lag market making detection.

Covers:
- scan_lead_lag_mm returns [] when LEAD_LAG_MM_ENABLED is false (default).
- scan_lead_lag_mm returns [] when no matched pairs are supplied.
- scan_lead_lag_mm returns [] when no platform is lagging.
- scan_lead_lag_mm emits a LeadLagMM opp dict shaped correctly when a
  lagger is detected.
- scan_lead_lag_mm uses the injected detector instead of the singleton when
  one is passed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _isolate_modules():
    for mod in ("scans.lead_lag_mm",):
        sys.modules.pop(mod, None)
    yield


class _FakeDetector:
    """In-memory LeadLagMM stand-in for tests."""

    def __init__(self, leader=None, lag_ms=0.0, fair_value=None, should_quote=False):
        self.leader = leader
        self.lag_ms = lag_ms
        self.fair_value = fair_value
        self._should_quote = should_quote
        self.recorded: list[tuple[str, str, float]] = []

    def record_price(self, market_key, platform, price):
        self.recorded.append((market_key, platform, price))

    def should_quote(self, market_key, platform, min_lag_ms=500.0):
        return self._should_quote and platform != self.leader

    def get_leader(self, market_key):
        return self.leader

    def get_lag_ms(self, market_key, platform):
        return self.lag_ms

    def get_fair_value(self, market_key):
        return self.fair_value


class TestScanLeadLagMMFlagGate:
    def test_returns_empty_when_flag_disabled(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", False)
        from scans.lead_lag_mm import scan_lead_lag_mm

        pairs = [{
            "platform_a": "polymarket", "platform_b": "kalshi",
            "market_key": "k1",
            "market_a": {"yes_ask": 0.50},
            "market_b": {"yes_ask": 0.55},
        }]
        det = _FakeDetector(should_quote=True, leader="polymarket",
                            lag_ms=900.0, fair_value=0.50)
        assert scan_lead_lag_mm(pairs, detector=det) == []
        # Detector must not be touched when flag is off.
        assert det.recorded == []


class TestScanLeadLagMMEmptyInputs:
    def test_returns_empty_when_no_pairs(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", True)
        from scans.lead_lag_mm import scan_lead_lag_mm

        assert scan_lead_lag_mm([], detector=_FakeDetector()) == []


class TestScanLeadLagMMNoLag:
    def test_returns_empty_when_no_platform_lags(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", True)
        from scans.lead_lag_mm import scan_lead_lag_mm

        pairs = [{
            "platform_a": "polymarket", "platform_b": "kalshi",
            "market_key": "no-lag-market",
            "market_a": {"yes_ask": 0.50},
            "market_b": {"yes_ask": 0.50},
        }]
        det = _FakeDetector(should_quote=False, leader="polymarket",
                            lag_ms=0.0, fair_value=0.50)
        assert scan_lead_lag_mm(pairs, detector=det) == []
        # Prices should still have been recorded.
        assert ("no-lag-market", "polymarket", 0.50) in det.recorded


class TestScanLeadLagMMEmitsOpp:
    def test_emits_opp_when_lagger_detected(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", True)
        from scans.lead_lag_mm import scan_lead_lag_mm

        pairs = [{
            "platform_a": "polymarket", "platform_b": "kalshi",
            "market_key": "laggy-market",
            "market_a": {"yes_ask": 0.50},
            "market_b": {"yes_ask": 0.55},
        }]
        det = _FakeDetector(should_quote=True, leader="polymarket",
                            lag_ms=900.0, fair_value=0.50)
        opps = scan_lead_lag_mm(pairs, detector=det)
        assert len(opps) == 1
        opp = opps[0]
        assert opp["type"] == "LeadLagMM"
        assert opp["_layer"] == 3
        assert opp["_leader"] == "polymarket"
        assert opp["_lagger"] == "kalshi"
        assert opp["_lag_ms"] == 900.0
        assert opp["_fair_value"] == 0.50
        assert opp["_market_key"] == "laggy-market"


class TestScanLeadLagMMUsesInjectedDetector:
    def test_passing_detector_skips_singleton_lookup(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", True)
        from scans.lead_lag_mm import scan_lead_lag_mm

        sentinel = _FakeDetector(should_quote=False, leader="polymarket",
                                 lag_ms=0.0, fair_value=0.42)
        # If the scan tried to fall back to the singleton, this assertion
        # would surface as an ImportError (market_maker isn't easily mockable
        # without disturbing other tests). Passing the fake guarantees the
        # injected path is exercised.
        pairs = [{
            "platform_a": "polymarket", "platform_b": "kalshi",
            "market_key": "k",
            "market_a": {"yes_ask": 0.42},
            "market_b": {"yes_ask": 0.45},
        }]
        scan_lead_lag_mm(pairs, detector=sentinel)
        assert any(rec[0] == "k" for rec in sentinel.recorded)
