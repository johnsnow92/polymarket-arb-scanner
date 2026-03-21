# Phase 2: Harden & Test - Research

**Researched:** 2026-03-20
**Domain:** Production hardening — structured logging, rate limiting, idempotency, integration test scripts
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Testing Methodology**
- Per-strategy integration test scripts that call real APIs in dry-run mode — run manually or via CI with credentials
- A strategy "passes" if it finds candidates OR logs "no opportunities" without errors — zero crashes, zero unhandled exceptions, valid API responses
- Fee verification via calculated-vs-actual comparison on paper trades — dry-run logs expected fees, manual spot-check against platform fee pages/docs for 3-5 trades per platform
- Test results stored as markdown report in `.planning/phases/02-harden-test/` — structured per-strategy pass/fail with evidence

**Structured Logging & Observability**
- JSON lines format (one JSON object per decision) to a dedicated log file — machine-parseable, separate from human-readable console logs
- Fields: timestamp, strategy, market, decision (execute/skip/reject), reason, prices, expected_profit, risk_check
- Log destination: `DATA_DIR/decisions.jsonl` alongside `trades.db` — single source of truth for all trade decisions
- Log every opportunity that reaches the executor — whether executed, skipped (risk), or rejected (revalidation). Include reason for skip/reject.
- Keep existing console logging, ADD structured JSON alongside — console for human monitoring, JSONL for analysis

**Rate Limiting & Idempotency**
- Per-platform rate limiters with platform-specific limits: Polymarket 10/s, Kalshi 10/s, Betfair 5/s, others conservative 5/s. Pre-request throttle, not just retry-on-429
- Exponential backoff + circuit breaker per platform — 3 retries via tenacity, then circuit-open for 30s for that platform
- Client-side idempotency key per opportunity — hash of (market_id, side, price, timestamp_minute) passed as client_order_id where platforms support it. Check DB for recent identical trades before placing.
- Crash recovery dedup: reconcile on startup by querying open orders from each platform API — compare against `trades.db` pending records. Extend existing `recovery.py` with order dedup check.

### Claude's Discretion
- Exact rate limit values per platform (can tune based on API docs during planning)
- Circuit breaker implementation details (stdlib vs third-party)
- JSONL rotation policy (size-based vs time-based)
- Idempotency key hash algorithm and exact fields

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HARDEN-01 | Live dry-run test per strategy type | Integration test script structure, pass/fail criteria, per-module run commands |
| HARDEN-02 | Validate fee calculations against actual charges | Fee comparison methodology, platform fee docs locations, tolerance approach |
| HARDEN-03 | Structured logging for trade decisions | JSONL writer pattern, `_log_skipped`/`_dry_run_log` hook points in executor.py |
| HARDEN-04 | Rate-limit awareness per platform | Per-platform limit values, circuit breaker pattern, header parsing for Retry-After |
| HARDEN-05 | Idempotent order placement | Hash key construction, DB dedup lookup, per-platform client_order_id support |
</phase_requirements>

---

## Summary

Phase 2 hardens the existing scanner pipeline for live trading. The codebase is already substantial (1,488 tests, all passing) and has good foundations: `tenacity` retries on all API clients, thread-safe SQLite with WAL mode, per-market execution locks, and a `recovery.py` crash-reconciliation module. What is missing is: (1) structured JSONL decision logs in `executor.py`, (2) circuit breakers on top of existing per-platform throttle globals, (3) idempotency keys in order placement paths, and (4) runnable integration test scripts that exercise real APIs in dry-run mode.

The work is primarily **additive** — nothing in the existing execution path needs to be restructured. The JSONL writer hooks into `_log_skipped` and `_dry_run_log` which already exist. Circuit breaker state wraps the existing `_rate_limit()` module-level functions. Idempotency keys extend the `_build_legs` dispatcher output and the `execute()` method body.

**Primary recommendation:** Build each requirement as a focused, testable addition to existing modules — not a rewrite. Use Python's stdlib (`threading.Lock`, `hashlib`, `json`) for all new mechanisms to keep the dependency footprint zero.

---

## Standard Stack

### Core (already in requirements.txt — confirmed current versions)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| tenacity | 9.1.4 | Retry with exponential backoff | Already used on every API call |
| pytest | 9.0.2 | Test runner | Project standard (requirements-dev.txt) |
| json | stdlib | JSONL writer | Zero dependency; one JSON object per line |
| hashlib | stdlib | Idempotency key generation | SHA-256 hash of order fields |
| threading | stdlib | Circuit breaker state (thread-safe) | Already used in rate limit globals |

### No New Dependencies Needed
All Phase 2 work uses only stdlib (`json`, `hashlib`, `threading`, `time`, `os`) plus already-installed `tenacity`. No new `pip install` required.

**Verification:** Checked `requirements.txt` and `requirements-dev.txt` — tenacity 9.1.4, pytest 9.0.2.

---

## Architecture Patterns

### Recommended Project Structure (additions only)
```
DATA_DIR/
└── decisions.jsonl      # HARDEN-03: structured trade decision log (new)

tests/
└── integration/         # HARDEN-01: per-strategy dry-run scripts (new)
    ├── run_all.py        # orchestrator — runs each scanner mode, captures pass/fail
    ├── run_binary.py     # python scanner.py --mode binary
    ├── run_negrisk.py
    ├── run_cross.py      # covers cross-all
    ├── run_kalshi.py
    ├── run_spread.py
    ├── run_betfair.py
    ├── run_smarkets.py
    ├── run_sxbet.py
    ├── run_matchbook.py
    ├── run_gemini.py
    ├── run_ibkr.py
    ├── run_triangular.py
    ├── run_multi_cross.py
    ├── run_stale.py       # note: requires --continuous; one-shot is a no-op
    ├── run_resolution.py
    ├── run_convergence.py
    ├── run_mm.py
    └── RESULTS.md         # HARDEN-01: test results report
```

### Pattern 1: JSONL Decision Logger (HARDEN-03)

**What:** A lightweight append-only JSONL writer initialized once by `ArbitrageExecutor.__init__()`. Every call to `_log_skipped`, `_dry_run_log`, and the live execution success/failure path appends one JSON line to `DATA_DIR/decisions.jsonl`.

**When to use:** Hook into the 7 existing call sites of `_log_skipped` plus `_dry_run_log` and `_execute_legs` outcome.

**Key design:** Keep it a plain file handle + threading lock inside `ArbitrageExecutor`. No external library, no rotation in Phase 2 (rotation is Claude's discretion — defer to size-based at 10MB, which stdlib `os.path.getsize` handles trivially).

```python
# Source: stdlib json + existing executor.py structure
import json
import os
import threading
import time

class ArbitrageExecutor:
    def __init__(self, ...):
        ...  # existing init
        # HARDEN-03: structured decision log
        data_dir = os.getenv("DATA_DIR", ".")
        self._decision_log_path = os.path.join(data_dir, "decisions.jsonl")
        self._decision_log_lock = threading.Lock()

    def _write_decision(self, opp: dict, decision: str, reason: str, risk_check: str | None = None):
        """Append one JSON line to decisions.jsonl."""
        entry = {
            "ts": time.time(),
            "strategy": opp.get("type", ""),
            "market": opp.get("market", ""),
            "decision": decision,          # "execute" | "skip" | "reject"
            "reason": reason,
            "prices": opp.get("prices", ""),
            "expected_profit": opp.get("net_profit", 0),
            "expected_roi": opp.get("net_roi", ""),
            "risk_check": risk_check,
        }
        line = json.dumps(entry) + "\n"
        with self._decision_log_lock:
            with open(self._decision_log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
```

Call sites — replace `_log_skipped(opp, reason)` body:
```python
def _log_skipped(self, opportunity: dict, reason: str):
    """Log a skipped opportunity to DB and JSONL."""
    # existing DB log_opportunity call (unchanged)
    ...
    # NEW: structured decision log
    self._write_decision(opportunity, "skip", reason)
```

For executed opportunities, call `_write_decision(opp, "execute", "filled", risk_check=None)` after successful `_execute_legs` or in `_dry_run_log`.

### Pattern 2: Circuit Breaker (HARDEN-04)

**What:** A per-platform circuit breaker wrapping the existing `_rate_limit()` module-level function. Three states: CLOSED (normal), HALF-OPEN (testing), OPEN (rejecting for 30s).

**When to use:** Add a `PlatformCircuitBreaker` class to a new `rate_limiter.py` module. Each `*_api.py` instantiates one at import time. The existing `@retry(stop_after_attempt(3))` tenacity decorator already handles 3 retries — the circuit breaker fires after tenacity exhausts retries (i.e., wraps the tenacity-decorated method).

**Implementation — stdlib only (no pybreaker needed):**
```python
# Source: stdlib threading + time
import threading
import time

class PlatformCircuitBreaker:
    """Open after consecutive_fail_limit failures, reset after reset_timeout_secs."""

    def __init__(self, name: str, fail_limit: int = 3, reset_timeout: float = 30.0):
        self.name = name
        self.fail_limit = fail_limit
        self.reset_timeout = reset_timeout
        self._failures = 0
        self._open_since: float | None = None
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            if self._open_since is None:
                return False
            if time.time() - self._open_since >= self.reset_timeout:
                # Auto-reset to HALF-OPEN — let next call through
                self._open_since = None
                self._failures = 0
                return False
            return True

    def record_success(self):
        with self._lock:
            self._failures = 0
            self._open_since = None

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.fail_limit:
                self._open_since = time.time()
```

**Integration in each `*_api.py`:**
```python
# Module-level circuit breaker instance
_circuit = PlatformCircuitBreaker("betfair", fail_limit=3, reset_timeout=30.0)

# Wrap tenacity-decorated method:
def _api_call_with_breaker(self, ...):
    if _circuit.is_open():
        raise _RateLimitError("Circuit open — Betfair in backoff")
    try:
        result = self._api_call_inner(...)  # tenacity @retry is on this method
        _circuit.record_success()
        return result
    except Exception as exc:
        _circuit.record_failure()
        raise
```

**Note on existing rate limit values in config.py:**
- `PM_RATE_LIMIT = 0.01` → 100 req/s (more permissive than the 10/s decision — leave as-is, throttle is advisory)
- `KALSHI_RATE_LIMIT = 0.05` → 20 req/s
- `GEMINI_RATE_LIMIT = 0.1` → 10 req/s
- `MIN_REQUEST_INTERVAL = 0.2` → 5 req/s in `betfair_api.py` (matches decision)
- Other platforms (smarkets, sxbet, matchbook) use inline throttle globals but lack a config constant

Platforms currently lacking a config constant for rate limit: `smarkets_api.py`, `sxbet_api.py`, `matchbook_api.py`. These need new `SMARKETS_RATE_LIMIT`, `SXBET_RATE_LIMIT`, `MATCHBOOK_RATE_LIMIT` constants added to `config.py` at 0.2s (5/s per decision).

### Pattern 3: Idempotency Key (HARDEN-05)

**What:** A deterministic hash per order attempt, passed as `client_order_id` where the platform supports it. Computed from `(market_id, side, price_rounded, timestamp_minute)` using SHA-256 truncated to 16 hex chars.

```python
# Source: stdlib hashlib
import hashlib
import time

def _make_idempotency_key(market_id: str, side: str, price: float, extra: str = "") -> str:
    """16-char hex key stable within a 60-second window."""
    minute_bucket = int(time.time()) // 60
    raw = f"{market_id}:{side}:{price:.4f}:{minute_bucket}:{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

**DB dedup check** (in `execute()`, before `_build_legs`):
```python
# Check for a recent identical trade (same market + side + price, within 60s)
if self.db.has_recent_trade(market_id=market, side="cross", price=opportunity.get("net_roi", ""), window_secs=60):
    self._log_skipped(opportunity, "duplicate_trade")
    return False
```

`TradeDB` needs a new `has_recent_trade()` method:
```python
def has_recent_trade(self, market_id: str, side: str, price: str, window_secs: float = 60.0) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_secs)).isoformat()
    with self._lock:
        row = self.conn.execute(
            "SELECT 1 FROM trades WHERE platform LIKE ? AND timestamp > ? LIMIT 1",
            (f"%{market_id[:20]}%", cutoff),
        ).fetchone()
    return row is not None
```

**Platform-specific client_order_id support:**
- **Polymarket:** `py-clob-client` order placement accepts `client_order_id` — HIGH confidence (documented in py-clob-client)
- **Kalshi:** `/trade-api/v2/portfolio/orders` — accepts `client_order_id` string field — MEDIUM confidence (check API docs)
- **Betfair:** `customerRef` field on `placeOrders` — HIGH confidence (documented in Betfair API docs)
- **Smarkets:** No documented client order ID field — skip, rely on DB dedup only
- **SX Bet:** No documented client order ID field — skip, rely on DB dedup only
- **Matchbook:** No documented client order ID field — skip, rely on DB dedup only
- **Gemini:** Order placement accepts `client_order_id` — MEDIUM confidence
- **IBKR:** `orderId` assigned by TWS locally, no client-side UUID — skip, rely on DB dedup + `ib_insync` order tracking

### Pattern 4: Integration Test Script Structure (HARDEN-01)

**What:** Standalone Python scripts in `tests/integration/` that call `scanner.py --mode <X>` as a subprocess and assert on exit code + log output.

**Pass criteria (from CONTEXT.md):** Zero crashes, zero unhandled exceptions, valid API response (any HTTP 200 or equivalent). Finding zero opportunities is acceptable.

```python
# tests/integration/run_binary.py
import subprocess
import sys

def test_binary_dry_run():
    result = subprocess.run(
        [sys.executable, "scanner.py", "--mode", "binary", "--dry-run"],
        capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, f"Crashed: {result.stderr[-500:]}"
    assert "Traceback" not in result.stderr, f"Unhandled exception: {result.stderr}"
    # Pass: found opps or "no opportunities" log
    print(f"PASS: binary — {result.stdout[-200:]}")

if __name__ == "__main__":
    test_binary_dry_run()
```

**RESULTS.md format** (from CONTEXT.md):
```markdown
# Phase 2: Harden & Test — Integration Test Results

| Strategy | Mode | Pass/Fail | Evidence | Timestamp |
|----------|------|-----------|----------|-----------|
| binary | dry-run | PASS | 3 candidates found | 2026-03-20 |
| kalshi | dry-run | PASS | 0 opportunities, no errors | 2026-03-20 |
| stale | continuous (30s) | SKIP — requires --continuous | N/A | — |
...
```

### Pattern 5: Fee Comparison (HARDEN-02)

**What:** A dedicated comparison script that reads `decisions.jsonl` (or `trades.db`) for dry-run opportunities, computes expected fees using `fees.py`, and compares against platform fee schedules documented in their APIs/docs. Output: per-platform table of calculated vs documented fees.

**Approach (from CONTEXT.md):** Manual spot-check 3-5 trades per platform against fee pages. Script generates the calculation side; human verifies against platform UI/docs.

```python
# tests/integration/verify_fees.py
from fees import net_profit_binary_internal, net_profit_cross_generic
# Read a sample from trades.db or decisions.jsonl
# Print: platform, buy_price, sell_price, calculated_fee, documented_fee_url
```

**Platform fee documentation locations (verified from codebase and public sources):**
- Polymarket: 2% taker fee — documented at https://docs.polymarket.com/ (public)
- Kalshi: 7% of profit, capped at $1.75 — `KALSHI_FEE_CAP_CENTS` in config.py
- Betfair: 2-5% commission — `BETFAIR_COMMISSION_RATE` in config.py (default 3%)
- Smarkets: 2% fixed — `SMARKETS_COMMISSION_RATE` in config.py
- Gemini: 1% maker / 5% taker — `GEMINI_FEE_RATE` in config.py (MEMORY.md confirmed)
- IBKR: $0.00 commission — MEMORY.md confirmed
- SX Bet / Matchbook: 0% on prediction markets (Matchbook 0% confirmed in CLAUDE.md)

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry with backoff | Custom sleep loop | tenacity (already present) | Already on every API call; version 9.1.4 installed |
| JSONL writing | Custom buffering/batching | stdlib `json` + append-mode file | Zero overhead, zero dependency; line-by-line append is sufficient |
| Circuit breaker | Third-party `pybreaker` | Stdlib threading + time (as above) | No new dependency; ~30 lines; state is per-process only |
| Test subprocess invocation | Custom process manager | `subprocess.run()` stdlib | Direct, readable, no framework overhead |
| Idempotency key | UUID v4 (random, not deterministic) | `hashlib.sha256` of deterministic fields | Deterministic key = same opportunity in same minute gets same key; UUIDs don't deduplicate across crashes |

**Key insight:** Prediction market platforms have varying levels of idempotency support. For platforms without `client_order_id`, DB dedup is the primary guard — which is why the `has_recent_trade()` check in `executor.py` must fire *before* order placement, not after.

---

## Common Pitfalls

### Pitfall 1: JSONL Writer Opens File on Every Write
**What goes wrong:** `open()` on every `_write_decision` call — fine for low-volume but creates file handle churn at scale (WS-triggered mode can produce hundreds of decisions/minute).
**Why it happens:** Simplest implementation opens/closes each time.
**How to avoid:** Hold an open file handle in `ArbitrageExecutor`. Open in `__init__`, close in a `close()` method. Use `threading.Lock` to serialize writes (already the pattern in `TradeDB`).
**Warning signs:** `IOError: too many open files` in logs; slow writes under load.

### Pitfall 2: Circuit Breaker Not Thread-Safe
**What goes wrong:** `_failures` counter incremented from multiple threads simultaneously → race condition → circuit opens prematurely or never opens.
**Why it happens:** The existing `_rate_limit()` functions use a `threading.Lock` correctly — but a naive circuit breaker class without locking will race.
**How to avoid:** All state mutations (`_failures`, `_open_since`) inside `with self._lock:` blocks (shown in pattern above).
**Warning signs:** Intermittent circuit trips at low failure counts; `_open_since` set but `_failures` not matching.

### Pitfall 3: Idempotency Key Too Narrow (Same-Minute Collision)
**What goes wrong:** Two different opportunities in the same market within the same 60-second window get the same key → second one is rejected as duplicate even though it's a fresh price.
**Why it happens:** `timestamp_minute` bucket makes keys stable for dedup but also groups distinct opportunities.
**How to avoid:** Include `net_roi` or a truncated price in the key fields, so different price points in the same minute get different keys. The key should be: `(market_id, side, round(price, 3), minute_bucket)`.
**Warning signs:** Legitimate opportunities silently skipped in fast-moving markets.

### Pitfall 4: Integration Tests Require Real Credentials
**What goes wrong:** `tests/integration/` scripts fail in CI because API credentials aren't present in CI environment.
**Why it happens:** These are live API tests by design (per CONTEXT.md).
**How to avoid:** Guard each script with a credential check at the top — if env var missing, print "SKIP — no credentials" and exit 0. CI runs them in an env with real credentials; local dev without credentials gets clean skips.
**Warning signs:** CI fails on missing env var before any API call.

### Pitfall 5: Stale Scan Integration Test Hangs
**What goes wrong:** Running `scanner.py --mode stale --continuous` in an integration test subprocess never exits.
**Why it happens:** Stale detection requires `--continuous` mode (an infinite loop). One-shot mode is a no-op and exits — but the test author might not know which mode to use.
**How to avoid:** For `run_stale.py`, test the one-shot mode and assert on the "stale scan has no historical data" informational warning (correct behavior per CLAUDE.md). Document as "continuous-only" in RESULTS.md.
**Warning signs:** Integration test hangs indefinitely with no output.

### Pitfall 6: `has_recent_trade` Query Too Broad
**What goes wrong:** A substring match on `market_id` in the trades table matches unrelated markets, blocking legitimate opportunities.
**Why it happens:** Market identifiers are long strings; a 20-char prefix match can hit false positives.
**How to avoid:** Use the full market identifier, not a prefix. The `opportunities.market` field stores the full market string — join through `opportunity_id` for accurate dedup.
**Warning signs:** Opportunities rejected as "duplicate_trade" when they are clearly different markets.

---

## Code Examples

### JSONL File Handle Pattern (thread-safe, persistent)
```python
# Source: stdlib json + threading — mirrors TradeDB pattern in db.py
class ArbitrageExecutor:
    def __init__(self, ...):
        ...
        data_dir = os.getenv("DATA_DIR", ".")
        self._decision_log_path = os.path.join(data_dir, "decisions.jsonl")
        self._decision_log_lock = threading.Lock()
        # Open for append on init; kept open for session lifetime
        self._decision_fh = open(self._decision_log_path, "a", encoding="utf-8", buffering=1)

    def close(self):
        """Release resources."""
        if hasattr(self, "_decision_fh") and self._decision_fh:
            self._decision_fh.close()

    def _write_decision(self, opp: dict, decision: str, reason: str, risk_check: str | None = None):
        entry = {
            "ts": time.time(),
            "strategy": opp.get("type", ""),
            "market": opp.get("market", ""),
            "decision": decision,
            "reason": reason,
            "prices": opp.get("prices", ""),
            "expected_profit": opp.get("net_profit", 0),
            "expected_roi": opp.get("net_roi", ""),
            "risk_check": risk_check,
        }
        line = json.dumps(entry) + "\n"
        with self._decision_log_lock:
            self._decision_fh.write(line)
```

### Rate Limit Config Constants for Missing Platforms
```python
# Source: config.py additions
SMARKETS_RATE_LIMIT = _env_float("SMARKETS_RATE_LIMIT", "0.2")   # 5/s
SXBET_RATE_LIMIT = _env_float("SXBET_RATE_LIMIT", "0.2")          # 5/s
MATCHBOOK_RATE_LIMIT = _env_float("MATCHBOOK_RATE_LIMIT", "0.2")  # 5/s
```

### DB Dedup Query (accurate full-key match)
```python
# Source: mirrors existing TradeDB.get_pending_trades() pattern in db.py
def has_recent_trade(self, market: str, window_secs: float = 60.0) -> bool:
    """Return True if an identical market trade exists within the last window_secs."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_secs)).isoformat()
    with self._lock:
        row = self.conn.execute(
            """SELECT 1 FROM opportunities
               WHERE market = ? AND timestamp > ? AND action NOT LIKE 'skipped:%'
               LIMIT 1""",
            (market, cutoff),
        ).fetchone()
    return row is not None
```

### Tenacity Retry Pattern (existing, confirmed)
```python
# Source: existing betfair_api.py, kalshi_api.py patterns
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_RateLimitError),
    reraise=True,
)
def _api_call_inner(self, ...):
    _rate_limit()
    response = self.session.get(...)
    if response.status_code == 429:
        raise _RateLimitError(f"429 from {self.name}")
    response.raise_for_status()
    return response.json()
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Static MIN_NET_ROI threshold | Dynamic gas-aware threshold via GasMonitor | Already built (Phase 1 dependency) | Rejects trades when Polygon gas spikes |
| Retry-on-429 only | Pre-request throttle + retry (current) | Already in kalshi_api.py, betfair_api.py | Reduces 429s before they happen |
| No decision audit trail | `_log_skipped` writes to `opportunities` table (current) | Already built | DB queryable; Phase 2 adds JSONL for streaming analysis |

**Deprecated/outdated:**
- The `MIN_REQUEST_INTERVAL = 0.2` hardcode in `betfair_api.py`: Replace with `BETFAIR_RATE_LIMIT` config constant for consistency with Kalshi/Gemini/PM patterns.

---

## Open Questions

1. **Kalshi `client_order_id` field**
   - What we know: Kalshi's REST API docs show order placement at `/trade-api/v2/portfolio/orders`
   - What's unclear: Whether the request body accepts a `client_order_id` field; Kalshi API docs require auth to access
   - Recommendation: In `executor.py`, attempt to pass idempotency key as `client_order_id` in Kalshi order dict; catch field-rejection response and fall back to DB dedup silently

2. **JSONL rotation policy**
   - What we know: Claude's discretion per CONTEXT.md
   - What's unclear: Volume in production — continuous mode with all 20 strategies could generate hundreds of JSONL entries/minute
   - Recommendation: Size-based rotation at 10MB using `os.path.getsize()` check before each write; rename to `decisions.jsonl.1`, open fresh `decisions.jsonl`. No third-party library needed.

3. **`has_recent_trade` window for cross-platform opportunities**
   - What we know: A cross-platform arb touches two platforms; both trades share the same `opportunity_id` in the DB
   - What's unclear: Whether to dedup at the opportunity level (same `market` string) or at the individual leg level
   - Recommendation: Dedup at opportunity level using `opportunities.market` — this is the natural key and avoids leg-level false positives

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 |
| Config file | none — direct pytest invocation |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HARDEN-01 | Each scanner mode exits cleanly with dry-run | integration (manual) | `python tests/integration/run_all.py` | ❌ Wave 0 |
| HARDEN-02 | Fee calculations match documented rates | manual spot-check | `python tests/integration/verify_fees.py` | ❌ Wave 0 |
| HARDEN-03 | `_write_decision` appends valid JSON to decisions.jsonl | unit | `pytest tests/test_executor.py -k decision_log -x` | ❌ Wave 0 (new tests) |
| HARDEN-03 | Every `_log_skipped` call triggers `_write_decision` | unit | `pytest tests/test_executor.py -k log_skipped -x` | partial (test_executor.py exists, new cases needed) |
| HARDEN-04 | Circuit breaker opens after 3 failures | unit | `pytest tests/test_rate_limiter.py -x` | ❌ Wave 0 |
| HARDEN-04 | Circuit breaker auto-resets after 30s | unit | `pytest tests/test_rate_limiter.py -k reset -x` | ❌ Wave 0 |
| HARDEN-04 | Pre-request throttle fires before API call | unit | existing `test_kalshi_api.py`, `test_betfair_api.py` (extend) | partial |
| HARDEN-05 | `_make_idempotency_key` is deterministic | unit | `pytest tests/test_executor.py -k idempotency -x` | ❌ Wave 0 |
| HARDEN-05 | `has_recent_trade` returns True within window | unit | `pytest tests/test_db.py -k recent_trade -x` | partial (test_db.py exists, new case needed) |
| HARDEN-05 | Crash recovery dedup skips already-placed orders | unit | `pytest tests/test_recovery.py -k dedup -x` | partial (test_recovery.py exists, new case needed) |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -q` (1,488 tests, ~10s)
- **Per wave merge:** `pytest tests/ -v` (full suite)
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_rate_limiter.py` — covers HARDEN-04 circuit breaker behavior
- [ ] `tests/integration/` directory + `run_all.py` skeleton — covers HARDEN-01
- [ ] `tests/integration/RESULTS.md` — report template for HARDEN-01 pass/fail evidence
- [ ] New test cases in `tests/test_executor.py` — decision log (`_write_decision`, `_log_skipped` hook, `_dry_run_log` hook)
- [ ] New test cases in `tests/test_db.py` — `has_recent_trade()` method
- [ ] New test cases in `tests/test_recovery.py` — order dedup check in `reconcile_orphaned_positions`

*(Framework itself is already installed: pytest 9.0.2 in requirements-dev.txt)*

---

## Sources

### Primary (HIGH confidence)
- Codebase direct read — `executor.py`, `kalshi_api.py`, `betfair_api.py`, `config.py`, `db.py`, `recovery.py`, `requirements.txt` — all patterns verified from source
- `.planning/phases/02-harden-test/02-CONTEXT.md` — user decisions locked

### Secondary (MEDIUM confidence)
- MEMORY.md project notes — Gemini fee rates, IBKR commission confirmed
- `requirements.txt` version pins — tenacity 9.1.4, verified current for project

### Tertiary (LOW confidence — needs validation at implementation time)
- Kalshi `client_order_id` field support — not verified from live API docs (requires auth)
- Gemini Predictions `client_order_id` field — stated in MEMORY.md but not independently verified against API reference

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries are already installed; no new deps needed
- Architecture: HIGH — patterns derived directly from existing codebase conventions
- Pitfalls: HIGH — derived from codebase analysis (thread safety, file handles, query correctness)
- Platform idempotency field support: MEDIUM — Polymarket/Betfair well-documented; Kalshi/Gemini need validation at implementation

**Research date:** 2026-03-20
**Valid until:** 2026-04-20 (30 days — stable platform APIs)
