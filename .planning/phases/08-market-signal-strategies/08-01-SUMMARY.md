---
phase: 08-market-signal-strategies
plan: 01
subsystem: strategy
tags: [imbalance, order-book, clob, detection, layer-4]

requires:
  - phase: 07-liquidity-rewards
    provides: "Two-stage scan pattern (mid-price → CLOB refinement) from rewards module"

provides:
  - "scan_imbalance() function detecting order book imbalances from bid/ask volume ratios"
  - "Two-stage detection: mid-price scan (Stage 1) + CLOB depth validation (Stage 2)"
  - "Stage 2 refinement dropping collapsed imbalances (>30% drop in ratio magnitude)"
  - "net_profit_imbalance() fee calculator accounting for platform-specific taker fees"
  - "Comprehensive test suite (19 tests) covering ratio calculation, refinement, scanning"

affects: 
  - "08-02, 08-03, 08-04, 08-05: Executor integration and tuning"
  - "Phase 9: Position sizing and risk management using imbalance signals"

tech-stack:
  added: []
  patterns:
    - "Two-stage scan: mid-price (fast) → CLOB refinement (accurate)"
    - "Collapse detection using 70% magnitude threshold for ratio stability"
    - "Directional signal mapping: bid dominance → YES, ask dominance → NO"

key-files:
  created:
    - "scans/imbalance.py: Order book imbalance detection module (201 lines)"
    - "tests/test_imbalance.py: Comprehensive test suite (488 lines, 19 tests)"
  modified:
    - "fees.py: Added net_profit_imbalance() calculator (15 lines)"

key-decisions:
  - "Use top 5 price levels for imbalance calculation (balances accuracy vs noise)"
  - "Implement min_ratio as n:1 ratio converted to imbalance formula threshold (n-1)/(n+1)"
  - "Stage 2 refinement drops if abs(current_ratio) < 0.7 * abs(original_ratio)"
  - "Imbalance trades use taker fees (Layer 4: informed trading, time-sensitive)"
  - "Tests mock fetch_order_book at source (polymarket_api module) to avoid dynamic import issues"

requirements-completed:
  - STRAT-01

duration: 18min
completed: 2026-04-04
---

# Phase 08: Order Book Imbalance Strategy (STRAT-01) Summary

**Two-stage order book imbalance detection (Layer 4) with bid/ask ratio analysis, CLOB refinement validation, and comprehensive test coverage**

## Performance

- **Duration:** 18 min
- **Tasks:** 3
- **Files created:** 2
- **Files modified:** 1
- **Test count:** 19 (all passing)

## Accomplishments

- **scan_imbalance() implementation** — Stage 1 detects order book imbalances using formula (bid_vol - ask_vol) / (bid_vol + ask_vol), directional signals (YES/NO), supports top 5 price levels
- **_refine_imbalance_with_clob() validation** — Stage 2 re-fetches live order books and drops opportunities where imbalance collapsed >30% (prevents stale/spoofed detection)
- **net_profit_imbalance() fee calculator** — Platform-aware profit calculation using taker fees for Layer 4 time-sensitive execution (Polymarket, Kalshi, Gemini)
- **Comprehensive test suite** — 19 tests covering ratio calculation edge cases, refinement thresholds, scanning logic, API mocking patterns

## Task Commits

1. **Task 1: Create scan/imbalance.py module** — `21a0a90` (feat)
   - _calculate_imbalance_ratio() formula implementation
   - scan_imbalance() two-stage detection with min_ratio threshold conversion
   - _refine_imbalance_with_clob() collapse detection and graceful degradation

2. **Task 2: Add net_profit_imbalance() to fees.py** — `b6fcf85` (feat)
   - Platform-specific taker fee calculations
   - Docstring and parameter validation matching existing patterns

3. **Task 3: Create tests/test_imbalance.py** — `816efe1` (test)
   - TestImbalanceRatio: 8 tests for ratio calculation and edge cases
   - TestRefinement: 5 tests for Stage 2 collapse detection
   - TestScanStage1: 6 tests for market scanning and threshold filtering

## Files Created/Modified

- `scans/imbalance.py` — Order book imbalance detection with two-stage pattern (201 lines)
- `fees.py` — Added net_profit_imbalance() calculator (15 lines added)
- `tests/test_imbalance.py` — Comprehensive test suite (488 lines, 19 tests)

## Decisions Made

1. **Min ratio threshold conversion** — Convert user-facing n:1 ratio (e.g., 3.0) to imbalance formula threshold (n-1)/(n+1) so 3:1 bid/ask ratio = 0.5 imbalance threshold. Ensures consistent threshold behavior across all ratio scales.

2. **Stage 2 collapse detection** — Use 70% magnitude preservation as rejection criterion: abs(current_ratio) < 0.7 * abs(original_ratio) indicates >30% drop. This prevents trading on stale or spoofed order books detected during Stage 1.

3. **Taker fees for imbalance** — Layer 4 (informed trading) requires rapid execution on detected signals. Use taker fees (not maker) because execution is time-sensitive and market-responsive.

4. **Test mocking approach** — Mock fetch_order_book at source (polymarket_api module) rather than at scans.imbalance import. Avoids issues with dynamic imports inside _refine_imbalance_with_clob() function.

## Deviations from Plan

None — plan executed exactly as written. All three functions implemented to specification with exact formula implementations, test coverage requirements met (19 tests, all passing), and threat model mitigations in place (Stage 2 validation for T-08-01/T-08-02, config validation for T-08-05).

## Verification

- **pytest tests/test_imbalance.py -v** → 19/19 tests PASSED (0.51s)
- **Module imports** → `from scans.imbalance import scan_imbalance` succeeds, docstring verifiable
- **Fee function** → `grep "def net_profit_imbalance" fees.py` confirms existence with correct signature
- **Stage 2 refinement** — Code verified: collapse_threshold = 0.7 * abs(original_ratio), check: abs(current_ratio) < collapse_threshold

## Known Stubs

None — all implementations complete with no placeholder values or stubbed logic.

## Threat Surface Scan

No new threats introduced. All threat register items (T-08-01 through T-08-05) addressed:
- **T-08-01 (Tampering):** Stage 2 validation detects stale order books ✓
- **T-08-02 (Spoofing):** CLOB response validation and error handling ✓
- **T-08-03 (Information Disclosure):** Public market data, non-sensitive ✓
- **T-08-04 (DoS):** Rate limiting via platform API clients ✓
- **T-08-05 (Privilege):** Feature flag defaults false, validated in Phase 5 ✓

## Next Phase Readiness

Ready for Phase 08-02 (executor integration). Scan module is complete and tested. Pending:
1. Integration into executor.py _build_legs() dispatcher (Plan 05)
2. Config variables (IMBALANCE_ENABLED, IMBALANCE_RATIO, IMBALANCE_MAX_TRADE_SIZE, etc.)
3. Wiring into cli.py and continuous.py for --mode imbalance support

---

*Phase: 08-market-signal-strategies, Plan: 01*
*Completed: 2026-04-04*
