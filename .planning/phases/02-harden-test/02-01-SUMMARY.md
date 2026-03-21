---
phase: 02-harden-test
plan: "01"
subsystem: execution-hardening
tags: [circuit-breaker, decision-logging, rate-limits, resilience, testing]
dependency_graph:
  requires: []
  provides: [PlatformCircuitBreaker, decisions.jsonl, platform-rate-limit-constants]
  affects: [executor.py, rate_limiter.py, config.py, all-8-api-clients]
tech_stack:
  added: [rate_limiter.py]
  patterns: [circuit-breaker, jsonl-append-log, threading.Lock, module-level-state-isolation]
key_files:
  created:
    - rate_limiter.py
    - tests/test_rate_limiter.py
    - tests/test_decision_log.py
  modified:
    - config.py
    - executor.py
    - polymarket_api.py
    - kalshi_api.py
    - betfair_api.py
    - smarkets_api.py
    - sxbet_api.py
    - matchbook_api.py
    - gemini_api.py
    - ibkr_api.py
    - tests/test_kalshi_api.py
    - tests/test_betfair_api.py
    - tests/test_gemini_api.py
    - tests/test_smarkets_api.py
    - tests/test_sxbet_api.py
    - tests/test_matchbook_api.py
    - tests/test_ibkr_api.py
    - tests/test_polymarket_api.py
decisions:
  - "Circuit breaker wired at outermost call level (wrapping tenacity retries) so circuit opens only after tenacity exhausts all attempts"
  - "Module-level circuit breaker instances (not per-instance) to ensure shared state across all callers of a given platform client"
  - "SXBet and Matchbook did not have _RateLimitError — added one for circuit-open rejections to maintain consistent error semantics"
  - "autouse reset_circuit_breaker fixture added to all 8 API test files to prevent module-level state bleed between tests (Rule 1 auto-fix)"
  - "buffering=1 (line buffering) for decisions.jsonl file handle — ensures each JSON line is flushed to disk without explicit flush() calls"
metrics:
  duration_minutes: 15
  tasks_completed: 3
  tasks_total: 3
  files_created: 3
  files_modified: 18
  tests_added: 24
  tests_total: 1512
  completed_date: "2026-03-21"
---

# Phase 02 Plan 01: Decision Logging + Circuit Breakers Summary

Structured JSONL decision logging added to executor, thread-safe PlatformCircuitBreaker module created, and circuit breakers wired into all 8 trading platform API clients with config-backed rate limit constants.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | PlatformCircuitBreaker + rate limit config constants | 85110cd | rate_limiter.py, config.py, tests/test_rate_limiter.py |
| 2 | JSONL decision logging in ArbitrageExecutor | 40b121a | executor.py, tests/test_decision_log.py |
| 3 | Wire circuit breakers into all 8 API clients | bd7a511 | *_api.py (8 files), test fixtures (8 files) |

## What Was Built

### rate_limiter.py (new)

`PlatformCircuitBreaker` class with stdlib-only implementation (threading + time):
- Thread-safe via `threading.Lock` wrapping all state mutations
- `is_open()` auto-resets to closed after `reset_timeout` elapses (half-open → closed transition)
- `record_success()` resets failure counter, closes circuit
- `record_failure()` increments counter, opens circuit at `fail_limit`
- Default: 3 failures to open, 30s to auto-reset

### config.py additions

Four new rate limit constants using `_env_float` (env var backed with defaults):
- `BETFAIR_RATE_LIMIT = 0.2` (5/s)
- `SMARKETS_RATE_LIMIT = 0.2` (5/s)
- `SXBET_RATE_LIMIT = 0.2` (5/s)
- `MATCHBOOK_RATE_LIMIT = 0.2` (5/s)

Previously Betfair, Smarkets, SX Bet, and Matchbook each had hardcoded `MIN_REQUEST_INTERVAL = 0.2`.

### executor.py additions

`_write_decision(opp, decision, reason, risk_check=None)` — appends one JSON line to `DATA_DIR/decisions.jsonl`:
- Fields: `ts`, `strategy`, `market`, `decision`, `reason`, `prices`, `expected_profit`, `expected_roi`, `risk_check`
- Thread-safe via `threading.Lock` + buffering=1 (line-buffered file handle)
- Hooked into 4 call sites:
  - `_log_skipped` → `decision="skip"`
  - `_dry_run_log` → `decision="execute"`, `reason="dry_run"`
  - `execute()` live path (success) → `decision="execute"`, `reason="filled"`
  - `execute()` live path (failure) → `decision="reject"`, `reason="execution_failed"`
- `close()` method releases file handle

### API client wiring (all 8 trading platforms)

Each client now has:
- `from rate_limiter import PlatformCircuitBreaker` at top
- `_circuit = PlatformCircuitBreaker("<name>", fail_limit=3, reset_timeout=30.0)` module-level
- Circuit breaker check in primary API call method: `if _circuit.is_open(): raise _RateLimitError("Circuit open -- <platform> in backoff")`
- `_circuit.record_success()` on successful API responses
- `_circuit.record_failure()` on connection errors and retried exceptions

The circuit breaker wraps OUTSIDE tenacity's retry loop — tenacity exhausts its retries first, and only if it gives up does the circuit open. This prevents a single transient error from opening the circuit.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Circuit breaker state bleeds between tests via module-level singleton**

- **Found during:** Task 3 — first full test suite run after wiring circuit breakers
- **Issue:** Tests that trigger connection errors (e.g., `test_retry_on_connection_error`) cause `_circuit._failures` to increment. Subsequent tests in the same session inherit open circuit state, causing `_RateLimitError: Circuit open -- kalshi in backoff` in otherwise-passing tests
- **Fix:** Added `autouse reset_circuit_breaker` fixture to all 8 API test files, calling `_circuit.record_success()` before and after each test to guarantee clean state
- **Files modified:** tests/test_kalshi_api.py, tests/test_betfair_api.py, tests/test_gemini_api.py, tests/test_smarkets_api.py, tests/test_sxbet_api.py, tests/test_matchbook_api.py, tests/test_ibkr_api.py, tests/test_polymarket_api.py
- **Commit:** bd7a511

**2. [Rule 2 - Missing functionality] SXBet and Matchbook lacked _RateLimitError class**

- **Found during:** Task 3 — wiring circuit breakers into sxbet_api.py and matchbook_api.py
- **Issue:** The plan specifies raising `_RateLimitError` when circuit is open, but sxbet_api.py and matchbook_api.py had no such exception class (unlike Betfair, Smarkets, Kalshi, Gemini, Polymarket which all had one)
- **Fix:** Added `class _RateLimitError(Exception)` to both modules with consistent docstring pattern
- **Files modified:** sxbet_api.py, matchbook_api.py
- **Commit:** bd7a511

## Known Stubs

None. All wiring is functional — circuit breakers are instantiated and wired into live code paths. Decision logging writes to real files. No placeholder data flows to any external interface.

## Self-Check: PASSED

- rate_limiter.py: FOUND
- tests/test_rate_limiter.py: FOUND
- tests/test_decision_log.py: FOUND
- commit 85110cd: FOUND
- commit 40b121a: FOUND
- commit bd7a511: FOUND
- Full test suite: 1512 passed, 0 failed
