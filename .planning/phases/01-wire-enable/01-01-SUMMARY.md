---
phase: 01-wire-enable
plan: "01"
subsystem: fee-routing
tags: [fees, cross-platform, executor, scan, tdd]
dependency_graph:
  requires: []
  provides: [_fee_path metadata on cross-platform opps, fee-path routing in executor]
  affects: [scans/cross.py, executor.py]
tech_stack:
  added: []
  patterns: [dual-layer fee routing (scan-time hint + execution-time re-validation)]
key_files:
  created: []
  modified:
    - scans/cross.py
    - executor.py
    - tests/test_cross.py
    - tests/test_executor.py
decisions:
  - find_lowest_fee_path called post-CLOB-refinement in both scan_cross_platform and scan_cross_all
  - _fee_path key is absent (not set to None) when no profitable path exists
  - Executor re-validates fee path at execution time using only the stored platform pair and prices
  - Stale re-validation (returns None or net_profit <= 0) falls back to default prices_str routing
  - Default routing for missing _fee_path ensures full backward compatibility
metrics:
  duration: "8 minutes"
  completed: "2026-03-19"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 4
  tests_added: 7
  tests_total: 1488
---

# Phase 01 Plan 01: Fee Path Wiring Summary

Wire `find_lowest_fee_path()` from fees.py into the cross-platform scan pipeline and executor, implementing dual-layer fee routing: scan-time hint attachment to opportunity dicts and execution-time re-validation with actual platform routing.

## What Was Built

### Task 1: Wire find_lowest_fee_path into scan_cross_platform and scan_cross_all

Added `find_lowest_fee_path` to the fees import in `scans/cross.py` and inserted fee path attachment passes at two points in the scan pipeline:

**In `scan_cross_platform`** (after `_refine_cross_with_clob`): Parses PM YES/NO prices from the `prices` string, uses `_kalshi_yes`/`_kalshi_no` from the opp dict, and calls `find_lowest_fee_path(["polymarket", "kalshi"], yes_prices, no_prices)`. If the function returns a profitable path dict, it is attached as `opp["_fee_path"]`. If it returns None, the key is left absent.

**In `scan_cross_all`** (after `_refine_cross_all_with_clob`): Parses the prices string using both the full platform name and 2-char abbreviation (e.g., "polymarket_Y" and "PO_Y") to handle both formats. Builds separate `yes_prices` and `no_prices` dicts from the available price values, then calls `find_lowest_fee_path` with only the platforms that have data. Key insight: each cross-all opp stores only ONE YES price and ONE NO price (not both for both platforms), so the yes/no dicts are built per-available-value rather than requiring both platforms in each dict.

**Tests added:** `TestFeePath` class with 3 methods:
- `test_fee_path_attached_on_cross_platform`: verifies all 7 keys present on opp when function returns a dict
- `test_fee_path_absent_when_no_profitable_path`: verifies `_fee_path` key is absent (not None) when function returns None
- `test_fee_path_on_cross_all`: verifies scan_cross_all opps also carry `_fee_path`

### Task 2: Wire fee path re-validation and routing into executor _build_legs

Added `find_lowest_fee_path` to the fees import in `executor.py` and inserted a fee path re-validation block at the top of the `elif opp_type.startswith("Cross"):` branch in `_build_legs`, before the existing `prices_str` parsing.

**Re-validation logic**: When `_fee_path` is present in the opp dict, calls `find_lowest_fee_path` again with only the stored platform pair and prices. If the result is profitable (`net_profit > 0`), builds YES and NO legs using `fresh["best_yes_platform"]` and `fresh["best_no_platform"]` for platform routing, with appropriate platform-specific leg structure (Polymarket gets `_token_id`, Kalshi gets `side`/`action`/`_ticker`). Returns legs immediately.

**Fallback behavior**: When re-validation returns None (stale) or net_profit <= 0, logs a message and falls through to the existing `prices_str` parsing. When `_fee_path` is absent entirely, the default path is used directly.

**Tests added:** `TestFeePathExecution` class with 4 methods:
- `test_build_legs_routes_using_fee_path`: verifies leg platforms match fee_path platforms
- `test_build_legs_no_fee_path_uses_default`: verifies backward-compatible default behavior
- `test_revalidation_calls_find_lowest_fee_path`: verifies `find_lowest_fee_path` is called once when fee_path present
- `test_stale_fee_path_falls_back_to_default`: verifies fallback routing when re-validation returns None

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed cross-all price parsing for single-strategy opps**
- **Found during:** Task 1 (TestFeePath::test_fee_path_on_cross_all failing)
- **Issue:** Initial implementation used `yes_p = {pa: a_yes, pb: b_yes} if a_yes and b_yes else {}` which requires both platforms to have YES prices. But each cross-all opp stores only ONE YES and ONE NO price (the winning strategy), not 4 prices total.
- **Fix:** Replaced with separate dicts built per-available-value: `if a_yes: yes_prices[pa] = a_yes`, `if b_yes: yes_prices[pb] = b_yes`, etc. This correctly handles the "A_YES + B_NO" and "A_NO + B_YES" strategy formats.
- **Files modified:** scans/cross.py
- **Commit:** 210aee4

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | 210aee4 | feat(01-01): wire find_lowest_fee_path into scan_cross_platform and scan_cross_all |
| Task 2 | 7bc2117 | feat(01-01): wire fee path re-validation and routing into executor _build_legs |

## Self-Check: PASSED

- FOUND: scans/cross.py
- FOUND: executor.py
- FOUND: tests/test_cross.py
- FOUND: tests/test_executor.py
- FOUND: commit 210aee4 (Task 1)
- FOUND: commit 7bc2117 (Task 2)
- Full test suite: 1488 passed, 0 failed
