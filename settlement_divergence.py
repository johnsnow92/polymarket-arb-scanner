"""Settlement-divergence veto gate — deterministic pre-trade VETO (PM-lane safety).

A cross-venue arb (e.g. Polymarket binary + Kalshi binary) is only safe if BOTH
legs settle on the SAME real-world outcome under the SAME rules. If the venues
word their settlement criteria differently (different source, cutoff time, or
edge-case handling), the "arb" can settle one leg YES and the other NO — a
guaranteed loss, not a locked profit. This gate blocks any cross-venue pair whose
settlement rules are not CONFIRMED matching.

Operating-rule compliant: **no LLM in the hot path.** The rule-text comparison is
an OFFLINE pre-compute — an LLM reads the two settlement-rule texts and emits
MATCH / DIVERGE / UNCERTAIN, stored keyed by the market pair. This gate is PURE
and DETERMINISTIC: it only READS the stored verdict and vetoes fail-closed —

    allow ONLY if a fresh MATCH verdict is on file for the pair;
    veto on DIVERGE, UNCERTAIN, a MISSING verdict, or a STALE MATCH.

Phase 1 (this file): the deterministic veto + the offline-precompute plumbing +
tests. Phase 2 (separate PR): call the veto in `executor.py` before a Cross pair
fires, and populate the store from a scheduled rule-comparison job.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol


class SettlementVerdict(str, Enum):
    MATCH = "match"          # rules settle on the same outcome — safe to pair
    DIVERGE = "diverge"      # rules can settle differently — never pair
    UNCERTAIN = "uncertain"  # comparison inconclusive — treat as unsafe


@dataclass(frozen=True)
class RuleComparison:
    """The stored result of one offline settlement-rule comparison."""
    pair_key: str
    verdict: SettlementVerdict
    rationale: str
    compared_at: float       # unix seconds when the comparison was made


def pair_key(venue_a: str, id_a: str, venue_b: str, id_b: str) -> str:
    """Canonical, order-independent key for a cross-venue market pair."""
    a = f"{venue_a.strip().lower()}:{id_a.strip()}"
    b = f"{venue_b.strip().lower()}:{id_b.strip()}"
    lo, hi = sorted((a, b))
    return f"{lo}|{hi}"


class VerdictStore(Protocol):
    """Read side the gate needs; a real store also persists (Supabase/db)."""

    def get(self, key: str) -> RuleComparison | None: ...


@dataclass
class InMemoryVerdictStore:
    """Dict-backed store. The gate uses ``get``; the offline job uses ``put``.

    Persisting to the shared DB / Supabase is a follow-up; the interface is the
    same so the gate code does not change.
    """
    _by_key: dict[str, RuleComparison] = field(default_factory=dict)

    def get(self, key: str) -> RuleComparison | None:
        return self._by_key.get(key)

    def put(self, comparison: RuleComparison) -> None:
        self._by_key[comparison.pair_key] = comparison


def settlement_divergence_veto(
    key: str,
    store: VerdictStore,
    max_age_s: float | None = None,
    now: float | None = None,
) -> tuple[bool, str]:
    """Deterministic fail-closed veto for one cross-venue pair.

    Returns ``(vetoed, reason)``. The pair may fire ONLY when a fresh MATCH
    verdict is on file; everything else vetoes:
      * no verdict on file        -> veto (never pair blind),
      * DIVERGE / UNCERTAIN       -> veto,
      * MATCH older than max_age_s -> veto (settlement rules can change).
    """
    comparison = store.get(key)
    if comparison is None:
        return True, f"no settlement comparison on file for {key} — veto (fail-closed)"
    if comparison.verdict is not SettlementVerdict.MATCH:
        return True, f"settlement {comparison.verdict.value} for {key}: {comparison.rationale}"
    if max_age_s is not None:
        age = (now if now is not None else time.time()) - comparison.compared_at
        if age > max_age_s:
            return True, f"settlement MATCH for {key} is stale ({age:.0f}s > {max_age_s:.0f}s) — re-compare"
    return False, ""


# ----------------------------------------------------------------------
# Offline pre-compute — the ONLY place the (LLM) comparator runs. Never the gate.
# ----------------------------------------------------------------------

# Compares two settlement-rule texts → (verdict, rationale). Backed by an LLM in
# production; injected so the deterministic plumbing is tested without one.
RuleComparator = Callable[[str, str], "tuple[SettlementVerdict, str]"]


def precompute_comparison(
    key: str,
    rules_a: str,
    rules_b: str,
    comparator: RuleComparator,
    store: InMemoryVerdictStore,
    now: float | None = None,
) -> RuleComparison:
    """Run the comparator on two rule texts OFFLINE and store the verdict.

    This is a scheduled pre-trade job, never the execution path — the gate later
    reads the stored verdict deterministically (and never holds a comparator).
    """
    verdict, rationale = comparator(rules_a, rules_b)
    comparison = RuleComparison(
        pair_key=key,
        verdict=verdict,
        rationale=rationale,
        compared_at=now if now is not None else time.time(),
    )
    store.put(comparison)
    return comparison
