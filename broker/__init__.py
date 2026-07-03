"""Out-of-band policy broker (docs/plans/09-policy-broker.md).

Deterministic authority module: the autonomous loop proposes intents
(flip lane / move capital / rotate secret); the broker validates each one
against a policy config stored OUTSIDE this repo and executes only if every
rulebook check passes. No LLM anywhere in this package.
"""

from .policy import PolicyConfig, PolicyError, compute_gate_hash, load_policy
from .queue import Intent, IntentError, IntentQueue
from .validator import BrokerValidator, CheckResult, LiveSources
from .secrets import SecretRotationError, rotate_secret_via_stdin
from .broker import (
    BrokerDecision,
    ExecutionResult,
    IntentExecutors,
    PolicyBroker,
    TwoFactorWallError,
)

__all__ = [
    "BrokerDecision",
    "BrokerValidator",
    "CheckResult",
    "ExecutionResult",
    "Intent",
    "IntentError",
    "IntentExecutors",
    "IntentQueue",
    "LiveSources",
    "PolicyBroker",
    "PolicyConfig",
    "PolicyError",
    "SecretRotationError",
    "TwoFactorWallError",
    "compute_gate_hash",
    "load_policy",
    "rotate_secret_via_stdin",
]
