---
phase: 02-harden-test
verified: 2026-03-21T12:00:00Z
status: human_needed
score: 9/10 must-haves verified
human_verification:
  - test: "Run python tests/integration/run_all.py with all 8 platform credentials set"
    expected: "19/19 tests PASS (not skip), RESULTS.md shows PASS for every strategy mode"
    why_human: "18 of 19 integration tests skipped due to missing credentials in current environment. HARDEN-01 requires each strategy type validated against real API data. Cannot verify without live credentials."
---

# Phase 2: Harden & Test — Verification Report

**Phase Goal:** Validate every strategy produces correct results with real API data. Confidence that live trading won't lose money due to bugs.
**Verified:** 2026-03-21T12:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Every opportunity that reaches executor.execute() produces a JSON line in DATA_DIR/decisions.jsonl | VERIFIED | `_write_decision` called from `_log_skipped` (line 2342), `_dry_run_log` (line 1520), and `execute()` live paths (lines 292, 294). 8 unit tests in test_decision_log.py pass. |
| 2 | Each platform API client has a circuit breaker that opens after 3 consecutive failures | VERIFIED | All 8 `*_api.py` files import `PlatformCircuitBreaker` and instantiate `_circuit = PlatformCircuitBreaker("<name>", fail_limit=3, reset_timeout=30.0)`. Confirmed: betfair, gemini, ibkr, kalshi, matchbook, polymarket, smarkets, sxbet. |
| 3 | Circuit breaker auto-resets after 30 seconds | VERIFIED | `rate_limiter.py` `is_open()` checks elapsed time against `reset_timeout`; resets to closed state on expiry. Thread-safety via `threading.Lock`. 16 unit tests pass. |
| 4 | Missing platform rate limit constants (Smarkets, SX Bet, Matchbook) exist in config.py and are used by API clients | VERIFIED | `BETFAIR_RATE_LIMIT`, `SMARKETS_RATE_LIMIT`, `SXBET_RATE_LIMIT`, `MATCHBOOK_RATE_LIMIT` all present in config.py (lines 220-223). Each respective `*_api.py` imports and uses the constant instead of hardcoded value. |
| 5 | Duplicate opportunities within 60 seconds are rejected before order placement | VERIFIED | `has_recent_trade(market, window_secs=60.0)` called in `execute()` at line 201 before `_build_legs`. Excludes `skipped:*` actions. 19 unit tests in test_idempotency.py pass. |
| 6 | Each order attempt carries a deterministic idempotency key derived from market+side+price+minute | VERIFIED | `_make_idempotency_key()` at executor.py line 58 (SHA-256, 16-char hex, minute-bucket stable). `_build_legs()` attaches key at line 1169. `import hashlib` confirmed. |
| 7 | Crash recovery reconciliation detects and skips already-placed orders | VERIFIED | `recovery.py` checks for filled siblings (lines 78-91) and marks trades as `dedup_skipped` instead of re-submitting. |
| 8 | Fee calculations for all 8 platforms can be verified against documented rates | VERIFIED | `tests/integration/verify_fees.py` runs 24 cases (3 per platform) and exits 0. All 8 platforms show YES/PASS within 0.1% tolerance. Confirmed by direct execution. |
| 9 | Every scanner --mode value has a corresponding integration test that calls the real CLI | VERIFIED | `tests/integration/test_strategies.py` has exactly 19 test methods covering all modes: binary, negrisk, cross, kalshi, cross-all, spread, betfair, smarkets, sxbet, matchbook, gemini, ibkr, event, triangular, multi-cross, stale, resolution, convergence, mm. |
| 10 | Each strategy validated against real API data in dry-run mode | NEEDS HUMAN | RESULTS.md shows 18/19 skipped (no credentials in environment). Only `event` (Metaculus public API) passed. Infrastructure is correct — tests need credentials to run. |

**Score:** 9/10 truths verified (1 needs human)

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `rate_limiter.py` | PlatformCircuitBreaker class | VERIFIED | 81 lines, contains class + is_open + record_success + record_failure + threading.Lock |
| `executor.py` | `_write_decision` method + file handle | VERIFIED | `_decision_fh = open(...)` at line 135, `_write_decision` at line 2344, 4 call sites confirmed |
| `config.py` | SMARKETS_RATE_LIMIT + SXBET_RATE_LIMIT + MATCHBOOK_RATE_LIMIT | VERIFIED | All 4 new constants at lines 220-223 via `_env_float` |
| `tests/test_decision_log.py` | Unit tests for JSONL decision logging | VERIFIED | 250 lines, TestDecisionLog class, 8 tests, all pass |
| `tests/test_rate_limiter.py` | Unit tests for PlatformCircuitBreaker | VERIFIED | 176 lines, TestPlatformCircuitBreaker class, 16 tests, all pass |
| `executor.py` | `_make_idempotency_key` + DB dedup in execute() | VERIFIED | Function at line 58, dedup at line 201, hashlib imported at line 3 |
| `db.py` | `has_recent_trade` method | VERIFIED | Defined at line 289, SQL excludes `skipped:%` actions |
| `recovery.py` | Order dedup check in reconcile_orphaned_positions | VERIFIED | Lines 78-91 check filled siblings, status `dedup_skipped` |
| `tests/test_idempotency.py` | Tests for idempotency key + DB dedup | VERIFIED | 350 lines, 19 tests across 4 classes, all pass |
| `tests/integration/verify_fees.py` | Fee verification script | VERIFIED | 442 lines, 24 cases, all 8 platforms pass, exits 0 |
| `tests/integration/test_strategies.py` | Per-strategy dry-run integration tests | VERIFIED | 253 lines, 19 test methods, TestStrategyDryRun class |
| `tests/integration/run_all.py` | Orchestrator generating RESULTS.md | VERIFIED | 264 lines, invokes verify_fees.py, generates RESULTS.md |
| `.planning/phases/02-harden-test/RESULTS.md` | Structured results report | VERIFIED | Contains "Strategy Dry-Run Tests (HARDEN-01)" and "Fee Verification (HARDEN-02)" sections |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| executor.py | DATA_DIR/decisions.jsonl | `_write_decision` called from `_log_skipped`, `_dry_run_log`, `execute()` | WIRED | 5 occurrences of `_write_decision(` in executor.py confirmed |
| rate_limiter.py | *_api.py modules | `PlatformCircuitBreaker` imported and instantiated per platform | WIRED | All 8 trading API clients import and instantiate `_circuit` |
| smarkets_api.py | config.py | `SMARKETS_RATE_LIMIT` imported and used in throttle logic | WIRED | Line 11: `from config import SMARKETS_RATE_LIMIT`, used at lines 37-38 |
| sxbet_api.py | config.py | `SXBET_RATE_LIMIT` imported and used | WIRED | Line 10: `from config import SXBET_RATE_LIMIT`, used at lines 34-35 |
| matchbook_api.py | config.py | `MATCHBOOK_RATE_LIMIT` imported and used | WIRED | Line 10: `from config import MATCHBOOK_RATE_LIMIT`, used at lines 34-35 |
| executor.py execute() | db.py has_recent_trade() | Dedup check before `_build_legs` | WIRED | Line 201: `self.db.has_recent_trade(market, window_secs=60.0)` |
| executor.py _build_legs() | `_make_idempotency_key()` | Key attached to each leg dict | WIRED | Line 1169: `leg["_idempotency_key"] = _make_idempotency_key(...)` |
| tests/integration/test_strategies.py | scanner.py | subprocess.run calling `python scanner.py --mode X --dry-run` | WIRED | Pattern `_run_scanner` calls `scanner.py --mode <mode>` for all 19 modes |
| tests/integration/run_all.py | tests/integration/verify_fees.py | subprocess.run invokes fee verification | WIRED | Line 125 runs `verify_fees.py`, captures exit code for RESULTS.md |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| HARDEN-01 | 02-03 | Live dry-run test per strategy type | PARTIAL | Infrastructure complete: 19 test methods, run_all.py, RESULTS.md. Actual live validation blocked by missing credentials. 1/19 tested (event mode). Needs human to run with credentials. |
| HARDEN-02 | 02-02 | Validate fee calculations against actual platform charges | SATISFIED | `verify_fees.py` runs 24 cases across all 8 platforms, all within 0.1% tolerance, exits 0. RESULTS.md contains "Fee Verification (HARDEN-02)" section. |
| HARDEN-03 | 02-01 | Structured logging for trade decisions | SATISFIED | `_write_decision` method writes JSON lines to `decisions.jsonl`. All 4 decision paths covered (skip, dry_run, filled, execution_failed). Thread-safe. |
| HARDEN-04 | 02-01 | Rate-limit awareness per platform | PARTIAL | Circuit breakers prevent cascade failures after repeated errors. 429 raises `_RateLimitError` triggering tenacity retry. However, `Retry-After` header parsing not implemented — no adaptive backoff proportional to server-provided wait time. REQUIREMENTS.md acceptance says "Backoff triggers before hitting hard limits" which is not fully met. |
| HARDEN-05 | 02-02 | Idempotent order placement | SATISFIED | `_make_idempotency_key` (16-char SHA-256 hex), DB-level dedup via `has_recent_trade`, recovery dedup marks `dedup_skipped`. 19 tests pass. |

**Orphaned requirements check:** REQUIREMENTS.md does not map any additional requirement IDs to Phase 2 beyond the 5 declared above. No orphaned requirements.

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `tests/integration/RESULTS.md` | — | 18/19 integration tests SKIP — not actual live test results | Info | HARDEN-01 requires real API validation; skips are by design (no creds). Not a code stub. |

No code stubs found in phase-modified files. No `TODO`/`FIXME` patterns in any of the new or modified files. No placeholder `return null` or empty implementations. Decision logging writes to real files. Circuit breakers are wired into live call paths.

---

## Human Verification Required

### 1. Run Integration Tests With Live Credentials

**Test:** Set all 8 platform credentials as environment variables and run:
```
python tests/integration/run_all.py
```
**Expected:** RESULTS.md shows 19/19 tests PASS (or SKIP with a clear "no opportunities at this moment" message, not a crash). No "Traceback" in any test stderr. Fee verification still PASS.
**Why human:** 18 of 19 integration tests were skipped in the automated run due to missing API credentials. HARDEN-01 requires each strategy type tested with real API data. The test infrastructure is correct and complete — the tests themselves cannot be run in this environment.

### 2. Confirm `Retry-After` Header Behavior Under Load

**Test:** Run `python scanner.py --continuous --interval 10` and generate load (many scans per minute) on Kalshi and Polymarket. Monitor logs for 429 responses.
**Expected:** When a 429 occurs, the client retries after a fixed interval (current behavior) or ideally after the server-specified `Retry-After` delay. No "steady-state" 429 errors in a 1-hour run.
**Why human:** HARDEN-04 acceptance criterion says "No 429 errors in steady-state operation. Backoff triggers before hitting hard limits." The current implementation handles 429 reactively (raises exception → tenacity retry) but does not parse `Retry-After` headers for adaptive wait time. This gap may be acceptable given the circuit breaker prevents cascade failures, but only a live run can confirm whether steady-state 429s occur.

---

## Gaps Summary

Two items fall short of full requirement acceptance, one requiring human verification and one representing a narrowed implementation scope:

**HARDEN-01 (Partial — Needs Human):** The integration test framework is complete and correct. All 19 scanner modes have tests with credential-gated skip logic. The `event` mode (Metaculus public API) passed in the automated environment. The remaining 18 modes require live platform credentials to execute. The infrastructure fully enables manual validation — but automated verification of real API responses is blocked until credentials are available in the test environment.

**HARDEN-04 (Partial — Scoping Gap):** The plan (02-01) scoped HARDEN-04 to circuit breakers only, which was implemented correctly. The original requirement in REQUIREMENTS.md additionally calls for parsing rate-limit headers ("Implement adaptive backoff when approaching limits"). The current implementation raises `_RateLimitError` on 429 which triggers tenacity exponential backoff, but does not read server-provided `Retry-After` values. Whether this constitutes a gap depends on whether 429 errors occur in steady-state operation (human verification item #2 above).

---

*Verified: 2026-03-21T12:00:00Z*
*Verifier: Claude (gsd-verifier)*
