"""Tests for Sprint 3 — wiring of Sprint 2 scans + WS-tick feeds into continuous.py.

Covers:
- continuous.py imports the four Sprint 2 scan functions (regression guard).
- _feed_sprint3_trackers calls VolatilityTracker.record_price when
  MM_VOLATILITY_ADJUSTED_ENABLED is true and skips when false.
- _feed_sprint3_trackers calls LeadLagMM.record_price when
  LEAD_LAG_MM_ENABLED is true and skips when false.
- _feed_sprint3_trackers never raises on bad input (hot-path safety).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Mock heavy SDK-dependent modules before importing continuous (mirrors
# the established pattern in tests/test_continuous.py).
_modules_to_mock = ["kalshi_api", "polymarket_api", "dashboard", "display", "recovery"]
_saved_modules = {name: sys.modules[name] for name in _modules_to_mock if name in sys.modules}
for _mod_name in _modules_to_mock:
    sys.modules[_mod_name] = MagicMock()
sys.modules["dashboard"].state = MagicMock()

import continuous  # noqa: E402

# Restore for any sibling test files
for _mod_name in _modules_to_mock:
    if _mod_name in _saved_modules:
        sys.modules[_mod_name] = _saved_modules[_mod_name]
    elif _mod_name in sys.modules:
        del sys.modules[_mod_name]


class TestContinuousImportsSprintTwoScans:
    """Regression guard — continuous.py must import all four Sprint 2 scans."""

    def test_imports_scan_nway_arb(self):
        assert hasattr(continuous, "scan_nway_arb")

    def test_imports_scan_lead_lag_mm(self):
        assert hasattr(continuous, "scan_lead_lag_mm")

    def test_imports_scan_toxic_flow_pause(self):
        assert hasattr(continuous, "scan_toxic_flow_pause")

    def test_imports_scan_volatility_adjusted_mm(self):
        assert hasattr(continuous, "scan_volatility_adjusted_mm")


class TestFeedSprint3TrackersFlagOff:
    def test_skips_both_trackers_when_flags_off(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_VOLATILITY_ADJUSTED_ENABLED", False)
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", False)

        vol_mock = MagicMock()
        lag_mock = MagicMock()
        import market_maker as mm_mod
        monkeypatch.setattr(mm_mod, "get_volatility_tracker", lambda: vol_mock)
        monkeypatch.setattr(mm_mod, "get_lead_lag_mm", lambda: lag_mock)

        continuous._feed_sprint3_trackers(
            "polymarket", "ticker-x", {"price": 0.50},
        )
        vol_mock.record_price.assert_not_called()
        lag_mock.record_price.assert_not_called()


class TestFeedSprint3TrackersVolFlagOn:
    def test_records_when_vol_flag_on(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_VOLATILITY_ADJUSTED_ENABLED", True)
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", False)

        vol_mock = MagicMock()
        lag_mock = MagicMock()
        import market_maker as mm_mod
        monkeypatch.setattr(mm_mod, "get_volatility_tracker", lambda: vol_mock)
        monkeypatch.setattr(mm_mod, "get_lead_lag_mm", lambda: lag_mock)

        continuous._feed_sprint3_trackers(
            "polymarket", "ticker-x", {"price": 0.50},
        )
        vol_mock.record_price.assert_called_once_with("ticker-x", 0.50)
        lag_mock.record_price.assert_not_called()


class TestFeedSprint3TrackersLeadLagFlagOn:
    def test_records_when_lead_lag_flag_on(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_VOLATILITY_ADJUSTED_ENABLED", False)
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", True)

        vol_mock = MagicMock()
        lag_mock = MagicMock()
        import market_maker as mm_mod
        monkeypatch.setattr(mm_mod, "get_volatility_tracker", lambda: vol_mock)
        monkeypatch.setattr(mm_mod, "get_lead_lag_mm", lambda: lag_mock)

        continuous._feed_sprint3_trackers(
            "kalshi", "ticker-y", {"yes": 0.62},
        )
        lag_mock.record_price.assert_called_once_with("ticker-y", "kalshi", 0.62)
        vol_mock.record_price.assert_not_called()


class TestFeedSprint3TrackersBothFlagsOn:
    def test_records_both_when_both_flags_on(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_VOLATILITY_ADJUSTED_ENABLED", True)
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", True)

        vol_mock = MagicMock()
        lag_mock = MagicMock()
        import market_maker as mm_mod
        monkeypatch.setattr(mm_mod, "get_volatility_tracker", lambda: vol_mock)
        monkeypatch.setattr(mm_mod, "get_lead_lag_mm", lambda: lag_mock)

        continuous._feed_sprint3_trackers(
            "betfair", "race-1", {"yes_price": 0.34},
        )
        vol_mock.record_price.assert_called_once_with("race-1", 0.34)
        lag_mock.record_price.assert_called_once_with("race-1", "betfair", 0.34)


class TestFeedSprint3TrackersHotPathSafety:
    def test_never_raises_on_missing_price(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_VOLATILITY_ADJUSTED_ENABLED", True)
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", True)

        # No price key in the tick payload — must not raise.
        continuous._feed_sprint3_trackers("polymarket", "t", {})
        continuous._feed_sprint3_trackers("polymarket", "t", {"price": None})

    def test_swallows_tracker_exceptions(self, monkeypatch):
        """If the tracker singleton throws, the WS path must not bubble it up."""
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_VOLATILITY_ADJUSTED_ENABLED", True)
        monkeypatch.setattr(config_mod, "LEAD_LAG_MM_ENABLED", False)

        exploding_tracker = MagicMock()
        exploding_tracker.record_price.side_effect = RuntimeError("boom")
        import market_maker as mm_mod
        monkeypatch.setattr(mm_mod, "get_volatility_tracker",
                            lambda: exploding_tracker)

        # Should not raise
        continuous._feed_sprint3_trackers("polymarket", "t", {"price": 0.5})
