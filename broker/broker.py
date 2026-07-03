"""PolicyBroker orchestrator: dedupe → hard-stop screen → validate → execute.

Loop proposes, broker disposes. The broker is deterministic code enforcing a
pre-registered rulebook — no LLM, no judgment. Anything that fails, is
unverifiable, or is a hard-stop is escalated to the operator and NEVER retried
(the idempotency key makes a resubmit a no-op).
"""

import logging
from dataclasses import dataclass, field
from typing import Callable

from .policy import PolicyConfig
from .queue import (
    HALT_SCOPE_CAPITAL,
    STATUS_EXECUTED,
    STATUS_HARD_STOP,
    STATUS_IN_DOUBT,
    STATUS_PENDING,
    STATUS_REJECTED,
    Intent,
    IntentQueue,
)
from .validator import BrokerValidator, LiveSources

logger = logging.getLogger(__name__)


class TwoFactorWallError(RuntimeError):
    """A 2FA/KYC wall was hit. Escalate to the operator — never bypass."""


@dataclass(frozen=True)
class ExecutionResult:
    verified: bool
    detail: str = ""


@dataclass
class IntentExecutors:
    """Side-effect callables, injected. Each returns an ExecutionResult."""

    flip_lane: Callable[[Intent], ExecutionResult]
    move_capital: Callable[[Intent], ExecutionResult]
    rotate_secret: Callable[[Intent], ExecutionResult]

    def for_type(self, intent_type: str) -> Callable[[Intent], ExecutionResult]:
        return getattr(self, intent_type)


@dataclass(frozen=True)
class BrokerDecision:
    intent_id: int
    status: str
    reason: str = ""
    duplicate: bool = False
    micro_entry: dict = field(default_factory=dict)


def _default_escalate(message: str) -> None:
    logger.critical("BROKER ESCALATION: %s", message)


class PolicyBroker:
    """Validates queued intents against the out-of-band policy and executes."""

    def __init__(
        self,
        policy: PolicyConfig,
        queue: IntentQueue,
        sources: LiveSources,
        executors: IntentExecutors,
        escalate: Callable[[str], None] | None = None,
    ):
        self.policy = policy
        self.queue = queue
        self.executors = executors
        self.escalate = escalate or _default_escalate
        self.validator = BrokerValidator(policy, sources, queue)

    # ------------------------------------------------------------------

    def process(self, intent: Intent) -> BrokerDecision:
        intent_id, created = self.queue.submit(intent)
        if not created:
            # Repeat of a consequential action = no-op. Never re-execute,
            # never retry an IN_DOUBT or REJECTED outcome.
            return BrokerDecision(
                intent_id,
                self.queue.current_status(intent_id),
                reason=self.queue.last_reason(intent_id) or "duplicate idempotency key — no-op",
                duplicate=True,
            )

        hard_stop = self._hard_stop_reason(intent)
        if hard_stop:
            return self._finish(intent_id, intent, STATUS_HARD_STOP, hard_stop)

        results = self.validator.validate(intent)
        if not self.validator.passed(results):
            return self._finish(
                intent_id, intent, STATUS_REJECTED, self.validator.failures(results)
            )

        return self._execute(intent_id, intent)

    # ------------------------------------------------------------------

    def _hard_stop_reason(self, intent: Intent) -> str:
        """Conditions the broker never decides on its own (spec §5)."""
        p = intent.payload
        if intent.intent_type == "flip_lane" and p.get("action") == "enable":
            lane = str(p.get("lane", "")).lower()
            if lane and self.policy.lane_halted(lane):
                return (f"restart of lane '{lane}' after a kill-switch halt "
                        "requires operator approval")
        if intent.intent_type == "move_capital" and p.get("tranche_advance"):
            return "tranche advance requires operator approval"
        return ""

    def _execute(self, intent_id: int, intent: Intent) -> BrokerDecision:
        try:
            result = self.executors.for_type(intent.intent_type)(intent)
        except TwoFactorWallError as exc:
            return self._finish(intent_id, intent, STATUS_HARD_STOP,
                                f"2FA/KYC wall: {exc} — never bypassed")
        except Exception as exc:
            return self._in_doubt(intent_id, intent, f"executor raised: {exc}")

        if not isinstance(result, ExecutionResult) or not result.verified:
            detail = getattr(result, "detail", "") or "outcome unverifiable"
            return self._in_doubt(intent_id, intent, detail)

        self.queue.append_event(intent_id, STATUS_EXECUTED, result.detail)
        micro = (
            dict(self.policy.micro_entry)
            if intent.intent_type == "flip_lane"
            and intent.payload.get("action") == "enable"
            else {}
        )
        logger.info("Intent %d (%s) EXECUTED: %s",
                    intent_id, intent.intent_type, result.detail)
        return BrokerDecision(intent_id, STATUS_EXECUTED, result.detail,
                              micro_entry=micro)

    def _in_doubt(self, intent_id: int, intent: Intent, reason: str) -> BrokerDecision:
        # An in-doubt capital move also freezes all further capital moves
        # until an operator reconciles and clears the halt.
        if intent.intent_type == "move_capital":
            self.queue.record_halt(
                HALT_SCOPE_CAPITAL, f"in-doubt capital move (intent {intent_id}): {reason}"
            )
        return self._finish(intent_id, intent, STATUS_IN_DOUBT,
                            f"{reason} — marked IN-DOUBT, will never retry")

    def _finish(self, intent_id: int, intent: Intent, status: str,
                reason: str) -> BrokerDecision:
        self.queue.append_event(intent_id, status, reason)
        if status != STATUS_PENDING:
            self.escalate(
                f"[broker] intent {intent_id} ({intent.intent_type}) {status}: {reason}"
            )
        return BrokerDecision(intent_id, status, reason)
