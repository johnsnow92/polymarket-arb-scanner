---
phase: 02-harden-test
plan: 03
subsystem: testing
tags: [integration-tests, pytest, subprocess, dry-run, orchestrator, results-report]

# Dependency graph
requires:
  - phase: 02-harden-test/02-02
    provides: "verify_fees.py script capturing fee schedule for all 8 platforms"
provides:
  - "Per-strategy dry-run integration tests (19 modes) callable via pytest"
  - "run_all.py orchestrator that generates RESULTS.md combining strategy + fee verification"
  - "RESULTS.md template at .planning/phases/02-harden-test/RESULTS.md"
affects: [verify, phase-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Integration tests use subprocess.run to call scanner.py --mode X --dry-run as a black-box CLI test"
    - "Credential gating via pytest.skip() — tests skip gracefully when env vars absent"
    - "run_all.py catches BaseException (not Exception) to handle pytest.skip Skipped signals"

key-files:
  created:
    - tests/integration/test_strategies.py
    - tests/integration/run_all.py
    - .planning/phases/02-harden-test/RESULTS.md
  modified: []

key-decisions:
  - "catch BaseException (not Exception) in run_all.py to handle pytest.skip — Skipped inherits from BaseException, not Exception"
  - "Integration tests run scanner.py as a subprocess to test the full CLI stack end-to-end"
  - "Event divergence test (Metaculus) needs no creds — only one test that can pass in CI without secrets"
  - "Stale mode test asserts exit 0 only, not opportunity count — one-shot stale is intentionally a no-op"

patterns-established:
  - "Credential gate: if not _has_X_creds(): pytest.skip('No X credentials')"
  - "run_all.py uses importlib.util to load test_strategies without package install"
  - "RESULTS.md has two sections: Strategy Dry-Run Tests (HARDEN-01) and Fee Verification (HARDEN-02)"

requirements-completed: [HARDEN-01]

# Metrics
duration: 8min
completed: 2026-03-21
---

# Phase 2 Plan 3: Per-Strategy Integration Tests Summary

**19 dry-run integration tests covering every scanner --mode value, with run_all.py orchestrator generating a structured RESULTS.md that includes fee verification evidence from Plan 02-02**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-03-21T08:00:00Z
- **Completed:** 2026-03-21T08:07:17Z
- **Tasks:** 1
- **Files modified:** 4 (created)

## Accomplishments

- Created `tests/integration/test_strategies.py` with 19 test methods (one per scanner `--mode`), all with credential-gated skip logic
- Created `tests/integration/run_all.py` orchestrator that runs all 19 strategy tests plus `verify_fees.py` and generates `RESULTS.md`
- Created `RESULTS.md` template with both "Strategy Dry-Run Tests (HARDEN-01)" and "Fee Verification (HARDEN-02)" sections
- Fixed bug where `pytest.skip()` signals (inheriting from `BaseException`) were not caught by `except Exception` in the orchestrator

## Task Commits

Each task was committed atomically:

1. **Task 1: Per-strategy integration tests and run_all orchestrator** - `ea1dfe1` (feat)

**Plan metadata:** (docs commit below)

## Files Created/Modified

- `tests/integration/test_strategies.py` - 19 dry-run integration tests, one per scanner --mode
- `tests/integration/run_all.py` - Orchestrator that runs all tests + fee verification, generates RESULTS.md
- `tests/integration/__init__.py` - Integration test package marker (from Plan 02, newly tracked)
- `.planning/phases/02-harden-test/RESULTS.md` - Template report with both HARDEN-01 and HARDEN-02 sections

## Decisions Made

- `catch BaseException` not `except Exception` in run_all.py — pytest's `Skipped` exception inherits from `BaseException`, not `Exception`, so the original catch was silently missing all skip signals and crashing
- Run scanner.py as a subprocess (not import) — tests the complete CLI stack including argparse, env loading, and all imports
- Event divergence test (`test_event_dry_run`) has no credential requirement since Metaculus is a public API — the only test that can run in CI without secrets

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed pytest.skip signal not caught in run_all.py**
- **Found during:** Task 1 (running orchestrator for verification)
- **Issue:** `pytest.skip()` raises `_pytest.outcomes.Skipped` which inherits `BaseException`, not `Exception`. The `except Exception` clause in `_run_one()` propagated the skip signal as an unhandled exception, crashing the orchestrator
- **Fix:** Changed `except Exception as exc:` to `except BaseException as exc:` in `run_all.py`
- **Files modified:** `tests/integration/run_all.py`
- **Verification:** `python tests/integration/run_all.py` exits 0 with 18 SKIPs and 1 PASS
- **Committed in:** `ea1dfe1` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Fix was essential for orchestrator correctness. The bug would cause run_all.py to crash on any test without credentials. No scope creep.

## Issues Encountered

None beyond the auto-fixed bug above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All 19 scanner modes have integration test coverage
- `python tests/integration/run_all.py` generates `RESULTS.md` when run with credentials
- RESULTS.md captures HARDEN-01 (strategy tests) and HARDEN-02 (fee verification) evidence in one report
- Ready for phase verification (`/gsd:verify-work 2`)

---
*Phase: 02-harden-test*
*Completed: 2026-03-21*
