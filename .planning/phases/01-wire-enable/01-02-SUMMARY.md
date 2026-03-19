---
phase: 01-wire-enable
plan: 02
subsystem: scanning
tags: [continuous-mode, market-making, resolution-sniping, bankroll, position-sizing, kalshi]

# Dependency graph
requires:
  - phase: 01-wire-enable/01-01
    provides: cross-platform scan infrastructure and fee routing

provides:
  - MM dry_run flag threaded through from executor in one-shot mode
  - Kalshi markets included in continuous mode resolution sniping
  - Timer-based bankroll refresh every 5 minutes in continuous mode
  - Post-trade bankroll refresh immediately after each execution

affects:
  - executor.position_sizer (receives updated total balance)
  - scans/resolution.py (now called for both polymarket and kalshi)
  - market_maker.py (now receives correct dry_run flag in one-shot)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Post-trade immediate refresh: executor._fetch_balances('Cross') called after executor.execute() returns True"
    - "Timer-based state: nonlocal _last_bankroll_refresh tracks last refresh time in continuous loop"
    - "Kalshi data flattening: kalshi_data[1] dict iterated via .items() to produce flat list for resolution scan"

key-files:
  created:
    - .planning/phases/01-wire-enable/01-02-SUMMARY.md
  modified:
    - cli.py
    - continuous.py
    - tests/test_cli.py
    - tests/test_continuous.py

key-decisions:
  - "executor.dry_run (not hardcoded True) controls MarketMaker dry_run in one-shot mode"
  - "Kalshi resolution uses kalshi_data[1] (markets_by_event dict) flattened to flat list"
  - "Bankroll uses _fetch_balances('Cross') to get all 8 platform balances for total capital picture"
  - "Timer refresh sets _last_bankroll_refresh even on failure to avoid immediate retry loops"

patterns-established:
  - "Bankroll refresh pattern: _fetch_balances -> sum values -> position_sizer.update_bankroll with guards for None sizer and zero total"

requirements-completed: [INTEG-02, INTEG-03, INTEG-04]

# Metrics
duration: 18min
completed: 2026-03-19
---

# Phase 01 Plan 02: Fix MM Dry Run, Kalshi Resolution, and Bankroll Refresh Summary

**Three integration gaps closed: MM one-shot respects exec-mode, resolution sniping covers Kalshi via data[1] flattening, and bankroll refreshes every 5 minutes plus immediately after each trade**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-03-19T17:55:00Z
- **Completed:** 2026-03-19T18:13:00Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Fixed `dry_run=True` hardcode in `cli.py` MarketMaker instantiation — now uses `executor.dry_run` so live trading mode is actually live for MM
- Added Kalshi resolution sniping block in continuous.py that flattens `kalshi_data[1]` (markets_by_event dict) into a flat list before calling `scan_resolution_snipes(platform="kalshi")`
- Wired bankroll refresh into continuous loop: timer-based (300s interval) with `nonlocal _last_bankroll_refresh` + post-trade (immediately after `executor.execute()` returns True)
- Added 10 new tests across TestMarketMakerDryRun, TestKalshiResolution, TestBankrollRefresh — all guards and edge cases covered (None position_sizer, empty kalshi data, fetch failures)

## Task Commits

1. **Task 1a: Fix MM dry_run hardcode (INTEG-02)** - `b02ba21` (fix)
2. **Task 1b+2: Kalshi resolution scan + bankroll refresh (INTEG-03 + INTEG-04)** - `b7c4f5a` (feat)

## Files Created/Modified

- `cli.py` - Changed `dry_run=True` → `dry_run=executor.dry_run` at line 488
- `continuous.py` - Added Kalshi resolution block (after Polymarket block), added `_last_bankroll_refresh` state, added timer-based and post-trade bankroll refresh
- `tests/test_cli.py` - Added `TestMarketMakerDryRun` class (2 tests)
- `tests/test_continuous.py` - Added `TestKalshiResolution` (3 tests) and `TestBankrollRefresh` (5 tests)

## Decisions Made

- Used `nonlocal _last_bankroll_refresh` to track refresh time inside the async `_continuous_loop` closure (matches existing `_last_snapshot_time` pattern)
- Timer sets `_last_bankroll_refresh = _now` even on exception to prevent retry storms
- Post-trade refresh is best-effort (exception caught + logged) — trade success is never rolled back due to refresh failure
- `_fetch_balances("Cross")` is the right call — fetches all 8 platforms for true total capital picture

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - all three fixes applied cleanly. Test suite grew from 87 to 97 tests (10 new) with 1484 total passing.

## Next Phase Readiness

- All three INTEG gaps from plan 01-02 are closed
- Market making in one-shot mode is now live-trading capable
- Resolution sniping coverage doubled (Polymarket + Kalshi)
- Kelly criterion position sizer receives current capital from all 8 platforms

---
*Phase: 01-wire-enable*
*Completed: 2026-03-19*
