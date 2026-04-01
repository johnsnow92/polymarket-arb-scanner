---
phase: "05-deploy-execute"
plan: "01"
subsystem: "fees, config"
tags: ["fee-model", "polymarket-2026", "gemini-2026", "strategy-layers", "config"]
dependency_graph:
  requires: []
  provides: ["fees.polymarket_taker_fee", "fees.kalshi_maker_fee", "config.STRATEGY_LAYERS", "config.get_layer", "config.REVAL_FLOORS"]
  affects: ["executor.py", "scans/cross.py", "backtest.py", "plan-02-revalidation"]
tech_stack:
  added: []
  patterns: ["env-var-overrides", "tdd-red-green", "single-source-of-truth"]
key_files:
  created: []
  modified:
    - "config.py"
    - "fees.py"
    - "tests/test_fees.py"
    - "backtest.py"
    - "tests/test_integration.py"
decisions:
  - "polymarket_taker_fee uses rate*P*(1-P) with 0.04 default — fee charged at entry not settlement"
  - "gemini_fee uses P*(1-P)*rate rounded up to cent with 0.07 taker / 0.0175 maker defaults"
  - "STRATEGY_LAYERS lives in config.py as single source of truth — backtest.py imports from there"
  - "polymarket_fee() kept as deprecated alias to avoid breaking any caller outside core files"
  - "net_profit_* cross-platform functions simplified: PM fee is always an entry fee, no case logic needed"
metrics:
  duration: "16 minutes"
  completed_date: "2026-04-01"
  tasks_completed: 3
  tasks_total: 3
  files_modified: 5
---

# Phase 05 Plan 01: Fee Model Overhaul & Config Infrastructure Summary

**One-liner:** 2026 Polymarket/Gemini fee formulas (rate*P*(1-P) entry-time), kalshi_maker_fee, STRATEGY_LAYERS/REVAL_FLOORS in config.py as single source of truth.

## What Was Built

Three tasks executed to establish accurate fee calculations and shared configuration infrastructure.

### Task 1: config.py — Fee Override Env Vars + Strategy Layer Config

Added to config.py following the existing `_env_float` pattern:

- `POLYMARKET_DEFAULT_TAKER_RATE = _env_float("POLYMARKET_TAKER_FEE_RATE", "0.04")`
- `POLYMARKET_MAKER_FEE_RATE = _env_float("POLYMARKET_MAKER_FEE_RATE", "0.0")`
- `GEMINI_TAKER_RATE = _env_float("GEMINI_TAKER_FEE_RATE", "0.07")`
- `GEMINI_MAKER_RATE = _env_float("GEMINI_MAKER_FEE_RATE", "0.0175")`
- `KALSHI_MAKER_MULTIPLIER = _env_float("KALSHI_MAKER_FEE_MULTIPLIER", "1.75")`
- `MATCHBOOK_PREDICTION_COMMISSION = _env_float("MATCHBOOK_PREDICTION_COMMISSION", "0.0")`
- `STRATEGY_LAYERS: dict[str, int]` (23 entries: all 20 strategy types)
- `REVAL_FLOORS: dict[int, float]` (layers 1-4: 2%, 5%, 3%, 10%)
- `get_layer(opp_type: str) -> int` (exact + prefix match)
- Updated `reload_fee_rates()` to cover all new fee globals

### Task 2 (TDD): fees.py + tests/test_fees.py — 2026 Fee Formula Rewrite

RED commit: Added failing tests for `polymarket_taker_fee`, `kalshi_maker_fee`, `PLATFORM_FEE_SCHEDULE` (all 8 platforms).

GREEN commit: Implemented:

- `polymarket_taker_fee(price, contracts, fee_rate)`: `rate * C * P * (1-P)`, default 0.04
- `polymarket_fee()`: kept as deprecated backward-compat alias (old 2% on net winnings)
- `kalshi_maker_fee(price, contracts)`: `ceil(1.75 * P * (1-P))` cents, min 1 cent
- `gemini_fee()` rewritten: `ceil(rate * C * P * (1-P) * 100) / 100`, default rate 0.07
- Updated all `net_profit_*` functions: PM uses entry fee not settlement fee
- `_platform_win_fee("polymarket", ...)` now returns 0.0
- `_platform_entry_fee("polymarket", ...)` now returns `polymarket_taker_fee(price)`
- `PLATFORM_FEE_SCHEDULE`: PM taker=0.04, Kalshi maker=0.0175, Gemini=0.07/0.0175
- `estimate_total_fee()`: uses new functions, supports maker/taker for Kalshi and Gemini

Updated test classes to reflect new 2026 formulas. Added:
- `TestPolymarketTakerFee` (8 tests)
- `TestKalshiMakerFee` (6 tests)
- `TestPlatformFeeSchedule` (9 tests — CI enforcement for all 8 platforms)

### Task 3: backtest.py + tests/test_integration.py — STRATEGY_LAYERS Consolidation

- Removed `STRATEGY_LAYERS` dict from backtest.py (was duplicated)
- Removed `_get_layer()` function from backtest.py
- Added `from config import STRATEGY_LAYERS, get_layer` to backtest.py
- Updated `tests/test_integration.py::TestBacktestNewTypes` to import from `config`

## Test Results

- `pytest tests/test_fees.py`: **106 passed**
- `pytest tests/` (excluding integration): **1615 passed, 0 failed**
- `pytest tests/test_integration.py -k "get_layer"`: **6 passed**

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| `polymarket_fee()` kept as deprecated alias | Some callers in cross-platform helpers still use it; avoids breaking chain |
| `net_profit_cross_platform` simplified (no case distinction) | PM fee now always an entry fee, case1/case2 collapsed into simple sum |
| `gemini_fee` signature: `fee_rate: float \| None = None` | Consistent with `polymarket_taker_fee`; None defaults to config GEMINI_TAKER_RATE |
| `net_profit_gemini_binary/multi` signature updated to `fee_rate: float \| None` | Ensures default propagates through to gemini_fee correctly |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] net_profit_gemini_* signature mismatch with new gemini_fee default**
- **Found during:** Task 2 GREEN verification
- **Issue:** `net_profit_gemini_binary/multi/cross_gemini` had `fee_rate: float = 0.05` as default. After `gemini_fee` changed to `fee_rate: float | None = None`, calling `gemini_fee(p, 0.05)` used old rate, causing test assertion mismatches.
- **Fix:** Updated signatures to `fee_rate: float | None = None` for all three gemini net_profit functions
- **Files modified:** fees.py
- **Commit:** 71f3b63

**2. [Rule 1 - Bug] reload_fee_rates() global variable name mismatch**
- **Found during:** Task 1 review
- **Issue:** Original `reload_fee_rates` loop used `globals()[env_var_name]` but new fee globals have different names (e.g., env var `POLYMARKET_TAKER_FEE_RATE` maps to global `POLYMARKET_DEFAULT_TAKER_RATE`)
- **Fix:** Changed `_fee_vars` to 3-tuple `(env_name, global_name, default)`, used `global_name` for lookup
- **Files modified:** config.py
- **Commit:** d110de8

**3. [Rule 1 - Bug] Existing fee tests asserted old formulas**
- **Found during:** Task 2 GREEN — 18 pre-existing tests failed because they were written for the old 2% settlement fee model
- **Fix:** Updated `TestNetProfitBinaryInternal`, `TestNetProfitNegriskInternal`, `TestNetProfitCrossPlatform`, `TestNetProfitCrossBetfair`, `TestGasFeeDeduction`, `TestGeminiFee`, `TestNetProfitGeminiBinary`, `TestNetProfitGeminiMulti`, `TestNetProfitCrossGemini` to assert new 2026 formulas
- **Files modified:** tests/test_fees.py
- **Commit:** 71f3b63

## Known Stubs

None — all fee functions are fully implemented with correct 2026 rates. No placeholder values or hardcoded empty returns.

## Self-Check: PASSED
