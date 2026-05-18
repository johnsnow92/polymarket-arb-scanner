"""Tests for Strategy ToxicFlowPause — adverse-selection guard.

Covers:
- scan_toxic_flow_pause returns [] when MM_TOXIC_FLOW_ENABLED is false.
- scan_toxic_flow_pause returns [] when no market_keys are supplied.
- scan_toxic_flow_pause returns [] when no market is currently paused.
- scan_toxic_flow_pause emits a ToxicFlowPause opp with the expected shape
  when the detector flags a market as paused.
- MarketMaker.refresh_quotes skips a market when ToxicFlowDetector.should_pause
  returns True (integration check that the runtime hook is wired).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _isolate_modules():
    for mod in ("scans.toxic_flow_pause",):
        sys.modules.pop(mod, None)
    yield


class _FakeDetector:
    """In-memory ToxicFlowDetector stand-in."""

    def __init__(self, paused: set[str] | None = None,
                 toxicity: float = 0.85, pause_remaining: float = 45.0):
        self.paused = paused or set()
        self._toxicity = toxicity
        self._pause_remaining = pause_remaining

    def should_pause(self, market_key):
        return market_key in self.paused

    def get_toxicity(self, market_key):
        return self._toxicity

    def get_pause_remaining(self, market_key):
        return self._pause_remaining


class TestScanToxicFlowPauseFlagGate:
    def test_returns_empty_when_flag_disabled(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_TOXIC_FLOW_ENABLED", False)
        from scans.toxic_flow_pause import scan_toxic_flow_pause
        det = _FakeDetector(paused={"toxic-market"})
        assert scan_toxic_flow_pause(["toxic-market"], detector=det) == []


class TestScanToxicFlowPauseEmptyInputs:
    def test_returns_empty_when_no_market_keys(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_TOXIC_FLOW_ENABLED", True)
        from scans.toxic_flow_pause import scan_toxic_flow_pause
        det = _FakeDetector(paused=set())
        assert scan_toxic_flow_pause([], detector=det) == []
        assert scan_toxic_flow_pause(None, detector=det) == []


class TestScanToxicFlowPauseNoPaused:
    def test_returns_empty_when_no_market_paused(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_TOXIC_FLOW_ENABLED", True)
        from scans.toxic_flow_pause import scan_toxic_flow_pause
        det = _FakeDetector(paused=set())
        assert scan_toxic_flow_pause(["clean-1", "clean-2"], detector=det) == []


class TestScanToxicFlowPauseEmitsOpp:
    def test_emits_opp_for_paused_market(self, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(config_mod, "MM_TOXIC_FLOW_ENABLED", True)
        from scans.toxic_flow_pause import scan_toxic_flow_pause
        det = _FakeDetector(paused={"toxic-m"}, toxicity=0.82,
                            pause_remaining=33.0)
        opps = scan_toxic_flow_pause(["clean", "toxic-m"], detector=det)
        assert len(opps) == 1
        opp = opps[0]
        assert opp["type"] == "ToxicFlowPause"
        assert opp["_market_key"] == "toxic-m"
        assert opp["_toxicity"] == 0.82
        assert opp["_pause_remaining_seconds"] == 33.0
        assert opp["_layer"] == 3


class TestMarketMakerToxicFlowHook:
    def test_refresh_quotes_skips_paused_markets(self, monkeypatch):
        """MarketMaker.refresh_quotes must consult should_pause and skip when True."""
        import market_maker as mm_mod

        # Force the singleton detector to a fake that always pauses.
        forced = _FakeDetector(paused={"market-a"})
        monkeypatch.setattr(mm_mod, "_toxic_flow_detector", forced)
        monkeypatch.setattr(mm_mod, "get_toxic_flow_detector",
                            lambda: forced)

        mm = mm_mod.MarketMaker(dry_run=True)
        mm.add_market("market-a", "polymarket", mid_price=0.50)
        mm.add_market("market-b", "polymarket", mid_price=0.50)

        quotes = mm.refresh_quotes()
        quoted_markets = {q.get("market") for q in quotes}
        assert "market-a" not in quoted_markets
