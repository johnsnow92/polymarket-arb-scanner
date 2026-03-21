# Phase 2: Harden & Test - Context

**Gathered:** 2026-03-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Validate every strategy produces correct results with real API data. Build confidence that live trading won't lose money due to bugs. Covers: live dry-run testing per strategy, fee calculation verification, structured trade decision logging, per-platform rate limiting, and idempotent order placement with crash recovery dedup.

Requirements: HARDEN-01 through HARDEN-05.

</domain>

<decisions>
## Implementation Decisions

### Testing Methodology
- Per-strategy integration test scripts that call real APIs in dry-run mode — run manually or via CI with credentials
- A strategy "passes" if it finds candidates OR logs "no opportunities" without errors — zero crashes, zero unhandled exceptions, valid API responses
- Fee verification via calculated-vs-actual comparison on paper trades — dry-run logs expected fees, manual spot-check against platform fee pages/docs for 3-5 trades per platform
- Test results stored as markdown report in `.planning/phases/02-harden-test/` — structured per-strategy pass/fail with evidence

### Structured Logging & Observability
- JSON lines format (one JSON object per decision) to a dedicated log file — machine-parseable, separate from human-readable console logs
- Fields: timestamp, strategy, market, decision (execute/skip/reject), reason, prices, expected_profit, risk_check
- Log destination: `DATA_DIR/decisions.jsonl` alongside `trades.db` — single source of truth for all trade decisions
- Log every opportunity that reaches the executor — whether executed, skipped (risk), or rejected (revalidation). Include reason for skip/reject.
- Keep existing console logging, ADD structured JSON alongside — console for human monitoring, JSONL for analysis

### Rate Limiting & Idempotency
- Per-platform rate limiters with platform-specific limits: Polymarket 10/s, Kalshi 10/s, Betfair 5/s, others conservative 5/s. Pre-request throttle, not just retry-on-429
- Exponential backoff + circuit breaker per platform — 3 retries via tenacity, then circuit-open for 30s for that platform
- Client-side idempotency key per opportunity — hash of (market_id, side, price, timestamp_minute) passed as client_order_id where platforms support it. Check DB for recent identical trades before placing.
- Crash recovery dedup: reconcile on startup by querying open orders from each platform API — compare against `trades.db` pending records. Extend existing `recovery.py` with order dedup check.

### Claude's Discretion
- Exact rate limit values per platform (can tune based on API docs during planning)
- Circuit breaker implementation details (stdlib vs third-party)
- JSONL rotation policy (size-based vs time-based)
- Idempotency key hash algorithm and exact fields

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `executor.py` — Already has per-market locks, failed-trade cooldown, revalidation loop
- `recovery.py` — Existing crash recovery reconciliation for all 8 platforms
- `db.py` — Thread-safe SQLite with WAL mode, tracks trades and positions
- `config.py` — Centralized env-var backed configuration with `validate_config()`
- All `*_api.py` clients already use `tenacity` retries with exponential backoff

### Established Patterns
- Tenacity retry decorator on API calls: `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))`
- `_RateLimitError` custom exception in `kalshi_api.py` and `polymarket_api.py`
- Logging uses `%`-style format strings (except `executor.py` which uses f-strings)
- `DATA_DIR` env var for persistent data storage

### Integration Points
- `executor.py:execute()` — Main execution path where structured logging hooks in
- `risk_manager.py` — Risk check results need to feed into decision log
- `*_api.py` modules — Rate limiters wrap existing API call methods
- `recovery.py:reconcile_orphaned_positions()` — Extend with order dedup

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. Phase 1 decisions (dual-layer fee routing, all-platform MM, bankroll refresh) are upstream dependencies that Phase 2 validates.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
