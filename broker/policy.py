"""Policy config loading + gate-hash registry for the policy broker.

The policy config MUST live outside this repo (docs/plans/09-policy-broker.md):
the loop can auto-merge this repo, so a merge must never be able to widen the
loop's own money authority. The loader fail-closes on any missing, unreadable,
malformed, or in-repo config.
"""

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_POLICY_PATH = (
    Path.home() / "Financial Markets with AI" / "policy" / "broker-policy.json"
)

REQUIRED_KEYS = frozenset([
    "tranche",
    "principal_cap_usd",
    "per_market_cap_usd",
    "venue_allowlist",
    "sportsbook_venues",
    "gate_hashes",
    "kill_state",
    "cooldown_seconds",
    "freshness_ttl_seconds",
    "recon_tolerance_usd",
    "micro_entry",
])

REQUIRED_MICRO_ENTRY_KEYS = frozenset([
    "max_first_order_usd",
    "first_n_fills",
    "max_fill_deviation_pct",
])


class PolicyError(ValueError):
    """Policy config missing, malformed, or unsafe. Always fail-closed."""


# ---------------------------------------------------------------------------
# Gate-config hashing
# ---------------------------------------------------------------------------

def compute_gate_hash(gate_config: dict) -> str:
    """SHA-256 of the canonical-JSON form of a gate's live config values.

    Key order never affects the hash, so the registered hash only changes when
    a gate value actually changes (e.g. a merged edit to a threshold).
    """
    canonical = json.dumps(gate_config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Policy config
# ---------------------------------------------------------------------------

class PolicyConfig:
    """Validated, read-only view of the out-of-band policy file."""

    def __init__(self, data: dict, source_path: Path):
        self.source_path = source_path
        self.tranche: str = data["tranche"]
        self.principal_cap_usd: float = float(data["principal_cap_usd"])
        self.per_market_cap_usd: float = float(data["per_market_cap_usd"])
        self.venue_allowlist: frozenset[str] = frozenset(
            str(v).lower() for v in data["venue_allowlist"]
        )
        self.sportsbook_venues: frozenset[str] = frozenset(
            str(v).lower() for v in data["sportsbook_venues"]
        )
        self.gate_hashes: dict[str, str] = dict(data["gate_hashes"])
        kill_state = data["kill_state"]
        self.kill_global: bool = bool(kill_state.get("global", False))
        self.kill_lanes: dict[str, bool] = {
            str(k).lower(): bool(v) for k, v in kill_state.get("lanes", {}).items()
        }
        self.cooldown_seconds: float = float(data["cooldown_seconds"])
        self.freshness_ttl_seconds: float = float(data["freshness_ttl_seconds"])
        self.recon_tolerance_usd: float = float(data["recon_tolerance_usd"])
        self.micro_entry: dict = dict(data["micro_entry"])

    def lane_halted(self, lane: str) -> bool:
        return self.kill_lanes.get(lane.lower(), False)

    def any_lane_halted(self) -> bool:
        return any(self.kill_lanes.values())


def _validate(data: dict, path: Path) -> None:
    missing = REQUIRED_KEYS - data.keys()
    if missing:
        raise PolicyError(
            f"policy config {path} missing required keys: {sorted(missing)}"
        )
    if not isinstance(data["venue_allowlist"], list) or not data["venue_allowlist"]:
        raise PolicyError("venue_allowlist must be a non-empty list")
    if not isinstance(data["sportsbook_venues"], list):
        raise PolicyError("sportsbook_venues must be a list")
    if not isinstance(data["gate_hashes"], dict) or not data["gate_hashes"]:
        raise PolicyError("gate_hashes must be a non-empty dict (pre-registered)")
    if not isinstance(data["kill_state"], dict):
        raise PolicyError("kill_state must be a dict")
    micro = data["micro_entry"]
    if not isinstance(micro, dict):
        raise PolicyError("micro_entry must be a dict")
    micro_missing = REQUIRED_MICRO_ENTRY_KEYS - micro.keys()
    if micro_missing:
        raise PolicyError(f"micro_entry missing keys: {sorted(micro_missing)}")
    for key in ("principal_cap_usd", "per_market_cap_usd"):
        if float(data[key]) <= 0:
            raise PolicyError(f"{key} must be > 0")
    for key in ("cooldown_seconds", "freshness_ttl_seconds", "recon_tolerance_usd"):
        if float(data[key]) < 0:
            raise PolicyError(f"{key} must be >= 0")
    if float(micro["max_first_order_usd"]) <= 0:
        raise PolicyError("micro_entry.max_first_order_usd must be > 0")
    if int(micro["first_n_fills"]) < 1:
        raise PolicyError("micro_entry.first_n_fills must be >= 1")
    if float(micro["max_fill_deviation_pct"]) <= 0:
        raise PolicyError("micro_entry.max_fill_deviation_pct must be > 0")


def load_policy(path: str | os.PathLike | None = None) -> PolicyConfig:
    """Load and validate the policy config. Fail-closed on every error path.

    Refuses any config located inside this repo: a merge to the loop-mergeable
    repo must never be able to alter (or smuggle in) the broker's policy.
    """
    if path is None:
        path = os.getenv("BROKER_POLICY_PATH", str(DEFAULT_POLICY_PATH))
    resolved = Path(path).expanduser().resolve()

    if resolved.is_relative_to(REPO_ROOT):
        raise PolicyError(
            f"policy config {resolved} is INSIDE the loop-mergeable repo "
            f"({REPO_ROOT}) — refusing (config isolation is non-negotiable)"
        )
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"policy config unreadable at {resolved}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyError(f"policy config {resolved} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"policy config {resolved} must be a JSON object")

    _validate(data, resolved)
    logger.info("Loaded broker policy from %s (tranche=%s)", resolved, data["tranche"])
    return PolicyConfig(data, resolved)
