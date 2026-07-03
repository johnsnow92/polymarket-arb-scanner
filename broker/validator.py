"""The deterministic validation rulebook (docs/plans/09-policy-broker.md §4).

Every rule re-validates against LIVE sources (injected callables — never
stale state). Any exception raised by a live source fails that check closed.
ALL applicable checks must pass or the intent is rejected.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from .policy import PolicyConfig, compute_gate_hash
from .queue import HALT_SCOPE_CAPITAL, Intent, IntentQueue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Live sources — the broker never trusts a cached number
# ---------------------------------------------------------------------------


@dataclass
class LiveSources:
    """Callables the broker uses to read live state at validation time."""

    portfolio_value_usd: Callable[[], float]
    realized_pnl_usd: Callable[[], float]
    ledger_balances: Callable[[], dict[str, float]]
    venue_balances: Callable[[], dict[str, float]]
    gate_config: Callable[[str], dict]
    input_ages_seconds: Callable[[], dict[str, float]]
    heartbeat_ages_seconds: Callable[[], dict[str, float]]
    seconds_since_last_flip: Callable[[], float]
    kill_switch_dry_run: Callable[[], bool]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    reason: str = ""


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class BrokerValidator:
    """Runs the fail-closed rulebook for one intent."""

    def __init__(self, policy: PolicyConfig, sources: LiveSources, queue: IntentQueue):
        self.policy = policy
        self.sources = sources
        self.queue = queue

    # -- public entry ---------------------------------------------------------

    def validate(self, intent: Intent) -> list[CheckResult]:
        checks: list[Callable[[Intent], CheckResult]] = [
            self._check_allowlist,
            self._check_gate_hashes,
            self._check_kill_state,
            self._check_freshness,
        ]
        if intent.intent_type == "move_capital":
            checks += [self._check_capital_halt, self._check_caps,
                       self._check_reconciliation]
        if intent.intent_type == "flip_lane":
            checks.append(self._check_cooldown)
            if intent.payload.get("action") == "enable":
                checks += [self._check_kill_switch_dry_run, self._check_micro_entry]
        return [self._run(check, intent) for check in checks]

    @staticmethod
    def passed(results: list[CheckResult]) -> bool:
        return all(r.ok for r in results)

    @staticmethod
    def failures(results: list[CheckResult]) -> str:
        return "; ".join(f"{r.name}: {r.reason}" for r in results if not r.ok)

    # -- fail-closed wrapper ----------------------------------------------------

    def _run(self, check: Callable[[Intent], CheckResult], intent: Intent) -> CheckResult:
        name = check.__name__.removeprefix("_check_")
        try:
            return check(intent)
        except Exception as exc:  # any live-source error ⇒ fail closed
            logger.error("Check %s fail-closed: %s", name, exc)
            return CheckResult(name, False, f"fail-closed: live source unreadable ({exc})")

    # -- individual rules ---------------------------------------------------------

    def _intent_venues(self, intent: Intent) -> list[str]:
        p = intent.payload
        keys = ("venue", "from_venue", "to_venue")
        return [str(p[k]).lower() for k in keys if p.get(k)]

    def _check_allowlist(self, intent: Intent) -> CheckResult:
        venues = self._intent_venues(intent)
        if not venues:
            return CheckResult("allowlist", False, "intent names no venue")
        for venue in venues:
            if venue in self.policy.sportsbook_venues:
                return CheckResult(
                    "allowlist", False,
                    f"'{venue}' is a sportsbook — auto-placement is never permitted",
                )
            if venue not in self.policy.venue_allowlist:
                return CheckResult("allowlist", False, f"'{venue}' not on venue allowlist")
        return CheckResult("allowlist", True)

    def _check_gate_hashes(self, intent: Intent) -> CheckResult:
        for gate, registered in self.policy.gate_hashes.items():
            live = compute_gate_hash(self.sources.gate_config(gate))
            if live != registered:
                return CheckResult(
                    "gate_hashes", False,
                    f"gate '{gate}' config hash mismatch (registered {registered[:12]}…, "
                    f"live {live[:12]}…) — possible merged threshold edit",
                )
        return CheckResult("gate_hashes", True)

    def _check_kill_state(self, intent: Intent) -> CheckResult:
        if self.policy.kill_global:
            return CheckResult("kill_state", False, "global kill switch is set")
        if self.policy.any_lane_halted():
            halted = sorted(
                lane for lane, is_halted in self.policy.kill_lanes.items() if is_halted
            )
            return CheckResult("kill_state", False, f"lanes in kill halt: {halted}")
        return CheckResult("kill_state", True)

    def _check_freshness(self, intent: Intent) -> CheckResult:
        ttl = self.policy.freshness_ttl_seconds
        inputs = self.sources.input_ages_seconds()
        heartbeats = self.sources.heartbeat_ages_seconds()
        if not inputs or not heartbeats:
            return CheckResult("freshness", False,
                               "no gate inputs/heartbeats reported — cannot verify freshness")
        for name, age in {**inputs, **heartbeats}.items():
            if age > ttl:
                return CheckResult("freshness", False,
                                   f"'{name}' is stale ({age:.0f}s > TTL {ttl:.0f}s)")
        return CheckResult("freshness", True)

    def _check_capital_halt(self, intent: Intent) -> CheckResult:
        if self.queue.halt_active(HALT_SCOPE_CAPITAL):
            return CheckResult("capital_halt", False,
                               "capital moves are halted (operator clear required)")
        return CheckResult("capital_halt", True)

    def _check_caps(self, intent: Intent) -> CheckResult:
        p = intent.payload
        amount = float(p.get("amount_usd", 0))
        if amount <= 0:
            return CheckResult("caps", False, "amount_usd must be > 0")
        if p.get("source") == "principal":
            return CheckResult("caps", False,
                               "new principal is operator-gated — broker only moves earned gains")
        ceiling = self.policy.principal_cap_usd + self.sources.realized_pnl_usd()
        portfolio = self.sources.portfolio_value_usd()
        if portfolio > ceiling:
            return CheckResult(
                "caps", False,
                f"portfolio ${portfolio:,.2f} exceeds working ceiling ${ceiling:,.2f} "
                f"({self.policy.tranche} principal + realized P&L)",
            )
        if amount > ceiling:
            return CheckResult("caps", False,
                               f"amount ${amount:,.2f} exceeds working ceiling ${ceiling:,.2f}")
        market = p.get("market")
        if market:
            if amount > self.policy.per_market_cap_usd:
                return CheckResult(
                    "caps", False,
                    f"amount ${amount:,.2f} exceeds per-market cap "
                    f"${self.policy.per_market_cap_usd:,.2f} for '{market}'",
                )
            depth = p.get("book_depth_usd")
            if depth is None:
                return CheckResult("caps", False,
                                   f"market-scoped move for '{market}' has no book_depth_usd — "
                                   "cannot verify size ≤ depth")
            if amount > float(depth):
                return CheckResult("caps", False,
                                   f"amount ${amount:,.2f} exceeds book depth ${float(depth):,.2f}")
        return CheckResult("caps", True)

    def _check_reconciliation(self, intent: Intent) -> CheckResult:
        ledger = self.sources.ledger_balances()
        live = self.sources.venue_balances()
        tolerance = self.policy.recon_tolerance_usd
        for venue in sorted(set(ledger) | set(live)):
            diff = abs(ledger.get(venue, 0.0) - live.get(venue, 0.0))
            if diff > tolerance:
                reason = (
                    f"reconciliation break on '{venue}': ledger "
                    f"${ledger.get(venue, 0.0):,.2f} vs live ${live.get(venue, 0.0):,.2f} "
                    f"(diff ${diff:,.2f} > ${tolerance:,.2f})"
                )
                # A break halts ALL capital moves until an operator clears it.
                self.queue.record_halt(HALT_SCOPE_CAPITAL, reason)
                return CheckResult("reconciliation", False, reason)
        return CheckResult("reconciliation", True)

    def _check_cooldown(self, intent: Intent) -> CheckResult:
        elapsed = self.sources.seconds_since_last_flip()
        if elapsed < self.policy.cooldown_seconds:
            return CheckResult(
                "cooldown", False,
                f"last lane flip {elapsed:.0f}s ago < cooldown "
                f"{self.policy.cooldown_seconds:.0f}s",
            )
        return CheckResult("cooldown", True)

    def _check_kill_switch_dry_run(self, intent: Intent) -> CheckResult:
        if not self.sources.kill_switch_dry_run():
            return CheckResult("kill_switch_dry_run", False,
                               "kill-switch dry-run halt FAILED — no order may be placed")
        return CheckResult("kill_switch_dry_run", True)

    def _check_micro_entry(self, intent: Intent) -> CheckResult:
        micro = self.policy.micro_entry
        try:
            if float(micro["max_first_order_usd"]) <= 0:
                return CheckResult("micro_entry", False, "max_first_order_usd must be > 0")
            if int(micro["first_n_fills"]) < 1:
                return CheckResult("micro_entry", False, "first_n_fills must be >= 1")
            if float(micro["max_fill_deviation_pct"]) <= 0:
                return CheckResult("micro_entry", False, "max_fill_deviation_pct must be > 0")
        except (KeyError, TypeError, ValueError) as exc:
            return CheckResult("micro_entry", False, f"invalid micro-entry config: {exc}")
        return CheckResult("micro_entry", True)
