"""Tests for the settlement-divergence veto — deterministic, fail-closed PM-lane gate."""
from __future__ import annotations

from settlement_divergence import (
    InMemoryVerdictStore,
    RuleComparison,
    SettlementVerdict,
    pair_key,
    precompute_comparison,
    settlement_divergence_veto,
)


def _store(verdict=None, rationale="", at=1000.0, key="k"):
    s = InMemoryVerdictStore()
    if verdict is not None:
        s.put(RuleComparison(key, verdict, rationale, at))
    return s


# ---------------------------------------------------------------------------
# Pair key
# ---------------------------------------------------------------------------

def test_pair_key_is_order_independent():
    assert pair_key("polymarket", "0xABC", "kalshi", "KXBTC-Y") == pair_key(
        "kalshi", "KXBTC-Y", "polymarket", "0xABC"
    )


# ---------------------------------------------------------------------------
# Fail-closed veto
# ---------------------------------------------------------------------------

def test_veto_when_no_verdict_on_file():
    vetoed, reason = settlement_divergence_veto("k", _store())
    assert vetoed is True
    assert "no settlement comparison" in reason


def test_veto_on_diverge():
    vetoed, reason = settlement_divergence_veto("k", _store(SettlementVerdict.DIVERGE, "diff source"))
    assert vetoed is True
    assert "diverge" in reason


def test_veto_on_uncertain():
    assert settlement_divergence_veto("k", _store(SettlementVerdict.UNCERTAIN, "ambiguous"))[0] is True


def test_allows_only_on_match():
    vetoed, reason = settlement_divergence_veto("k", _store(SettlementVerdict.MATCH, "identical"))
    assert vetoed is False
    assert reason == ""


# ---------------------------------------------------------------------------
# Staleness (settlement rules can change)
# ---------------------------------------------------------------------------

def test_stale_match_is_vetoed():
    s = _store(SettlementVerdict.MATCH, "ok", at=0.0)
    vetoed, reason = settlement_divergence_veto("k", s, max_age_s=3600.0, now=10_000.0)
    assert vetoed is True
    assert "stale" in reason


def test_fresh_match_within_max_age_allowed():
    s = _store(SettlementVerdict.MATCH, "ok", at=9_000.0)
    assert settlement_divergence_veto("k", s, max_age_s=3600.0, now=10_000.0)[0] is False


# ---------------------------------------------------------------------------
# Offline pre-compute populates the store the gate reads
# ---------------------------------------------------------------------------

def test_precompute_stores_verdict_and_gate_then_allows():
    store = InMemoryVerdictStore()

    def fake_llm(a, b):
        return (SettlementVerdict.MATCH if a == b else SettlementVerdict.DIVERGE), "stub"

    c = precompute_comparison("k", "rules X", "rules X", fake_llm, store, now=1234.0)
    assert c.verdict is SettlementVerdict.MATCH
    assert store.get("k").verdict is SettlementVerdict.MATCH
    assert settlement_divergence_veto("k", store)[0] is False


def test_precompute_diverge_keeps_gate_vetoing():
    store = InMemoryVerdictStore()
    precompute_comparison("k", "a", "b", lambda a, b: (SettlementVerdict.DIVERGE, "x"), store)
    assert settlement_divergence_veto("k", store)[0] is True
