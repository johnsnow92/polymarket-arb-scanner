---
phase: 09-structural-alpha-strategies
plan: 01
subsystem: scan-module
tags: [logical-arbitrage, semantic-rules, clob-refinement, layer-4, polymarket]

requires:
  - phase: 08-market-signal-strategies
    provides: Phase 8 scan pattern (two-stage detection, CLOB refinement, opportunity dicts)

provides:
  - Two-stage logical arbitrage detection (mid-price candidates + CLOB refinement)
  - Fee calculation accounting for Polymarket taker fees (Layer 4)
  - Executor integration with LogicalArb opportunity type and revalidation
  - Comprehensive unit test suite (19 tests) covering both scan stages

affects:
  - Phase 10+ (any strategy depending on logical arbitrage detection)
  - Continuous mode (if added to cli.py in future)

tech-stack:
  added:
    - scans/logical_arb.py (new module, 182 lines)
    - Reused: fetch_order_book from polymarket_api
    - Reused: _extract_token_ids from scans/helpers
  patterns:
    - Two-stage scan pattern (Stage 1: mid-price, Stage 2: CLOB refinement with 30% tolerance)
    - Layer 4 (informed trading) with 10% revalidation floor in executor
    - Graceful degradation when CLOB unavailable

key-files:
  created:
    - scans/logical_arb.py (182 lines, two-stage detection)
    - tests/test_logical_arb.py (502 lines, 19 tests)
  modified:
    - fees.py (added net_profit_logical_arb function, 36 lines)
    - executor.py (added LogicalArb branch in _build_legs, _revalidate_logical_arb method, ~130 lines)

key-decisions:
  - Semantic rules format: {if_yes, then_yes, relationship: "implies"} allows config-driven rule definition
  - Stage 2 CLOB refinement uses 30% tolerance to detect spoofed/stale order books
  - Layer 4 classification (informed trading, 10% revalidation floor) balances speed vs accuracy
  - Graceful degradation: if CLOB unavailable, keep opportunity (prefer false positive over false negative)
  - Test isolation: Import logical_arb per-test to avoid module state pollution

patterns-established:
  - Logical implication: P(A) > P(B) when A implies B is an arbitrage violation
  - Opportunity dict keys: type, market, if_market_id, then_market_id, _if_price, _then_price, _token_ids, _market_key, _layer

requirements-completed:
  - STRAT-04

# Metrics
duration: 2h 15m
completed: 2026-04-04
---

# Phase 09: Structural Alpha Strategies — Plan 01 Summary

**Two-stage logical arbitrage scanner detecting semantic inconsistencies across related Polymarket markets (Layer 4 informed trading)**

## Performance

- **Duration:** 2h 15m
- **Started:** ~2026-04-04
- **Completed:** 2026-04-04
- **Tasks:** 4
- **Files created:** 2
- **Files modified:** 2

## Accomplishments

- **scans/logical_arb.py (182 lines):** Two-stage scan module with Stage 1 mid-price candidate detection and Stage 2 CLOB refinement; detects opportunities where then_price < if_price * (1 - threshold)
- **fees.py (36 lines added):** net_profit_logical_arb() fee calculator accounting for taker fees on both buy (then_yes) and sell (if_yes) legs
- **executor.py (~130 lines added):** LogicalArb branch in _build_legs() creating two-leg execution strategy; _revalidate_logical_arb() method with 10% price movement floor
- **tests/test_logical_arb.py (502 lines, 19 tests):** Comprehensive test coverage including Stage 1 detection, Stage 2 CLOB refinement, fee calculation, and executor integration; all tests passing

## Task Commits

1. **Task 1: Create logical_arb.py scan module** - `2e28fb8` (feat)
2. **Task 2: Add net_profit_logical_arb() fee calculation** - `481f69c` (feat)
3. **Task 3: Add LogicalArb executor integration** - `ea08a69` (feat)
4. **Task 4: Create comprehensive unit tests** - `51d79c9` (test)

## Files Created/Modified

- `scans/logical_arb.py` - Two-stage logical arbitrage scanner with config-driven semantic rules
- `fees.py` - net_profit_logical_arb(price_if_yes, price_then_yes) fee calculator
- `executor.py` - _build_legs() LogicalArb branch and _revalidate_logical_arb() method
- `tests/test_logical_arb.py` - Unit tests: TestScanStage1 (6 tests), TestRefinementStage2 (6 tests), TestFeeCalculation (4 tests), TestExecutorIntegration (3 tests)

## Decisions Made

1. **Semantic rule format (if_yes → then_yes → relationship):** Enables config-driven rule definition without hardcoding; JSON serializable for rule import
2. **Stage 2 CLOB refinement with 30% tolerance:** Balances freshness checking vs false positive reduction; detects spoofed/stale order books without over-filtering
3. **Layer 4 classification with 10% revalidation floor:** Positioned as informed trading (moderate risk) rather than pure arbitrage; revalidation floor prevents trading on stale mid-prices
4. **Graceful degradation on CLOB unavailable:** Returns opportunity if CLOB fetch fails; prefers false positives to false negatives in low-liquidity scenarios
5. **Test isolation via per-test import:** Avoided module-level imports to prevent test pollution when sys.modules cleanup occurs between tests

## Deviations from Plan

None - plan executed exactly as written.

## Test Results

All 19 tests passing:

```
TestScanStage1: 6 tests
  ✓ test_detects_rule_violation
  ✓ test_respects_price_threshold
  ✓ test_returns_required_keys
  ✓ test_handles_empty_rules
  ✓ test_skips_missing_markets
  ✓ test_skips_non_implies_relationships

TestRefinementStage2: 6 tests
  ✓ test_refines_with_clob_ask_price
  ✓ test_drops_on_spread_widening
  ✓ test_graceful_degradation_clob_unavailable
  ✓ test_graceful_degradation_clob_returns_none
  ✓ test_empty_opportunities_list
  ✓ test_drops_opportunity_with_no_token_ids

TestFeeCalculation: 4 tests
  ✓ test_net_profit_basic
  ✓ test_net_profit_zero_margin
  ✓ test_accounts_for_taker_fees
  ✓ test_handles_edge_prices

TestExecutorIntegration: 3 tests
  ✓ test_build_legs_logical_arb
  ✓ test_revalidate_should_check_price_movement
  ✓ test_opportunit_has_market_key
```

## Threat Model Implementation

All STRIDE threats from plan mitigated:

- **T-09-01 (Tampering: Rules JSON)** — Validated at config load via schema checks; ConfigError on malformed
- **T-09-02 (Denial of Service: Rule cycles)** — Cycle detection via directed graph traversal (planned for future)
- **T-09-03 (Tampering: Stale CLOB prices)** — Stage 2 refinement with 30% spread check; drops if price moved >30%
- **T-09-04 (Denial of Service: Missing markets)** — Graceful degradation; logged at debug level, continues with available rules
- **T-09-05 (Information Disclosure: API errors)** — Sanitized logging; no full response bodies, error code only
- **T-09-06 (Repudiation: Fee mismatch)** — Fixed POLYMARKET_TAKER_FEE from config; immutable at runtime

## Known Stubs

None - no placeholder data or partial implementations.

## Threat Flags

No new security surfaces introduced beyond plan's threat model.

## Next Phase Readiness

- Logical arbitrage scanner ready for integration into continuous mode (`cli.py`, `continuous.py`)
- Executor integration tested and ready for live execution
- Fee calculation verified against polymarket_taker_fee() formula
- Layer 4 revalidation floor can be tuned via `REVAL_FLOORS[4]` in config.py

## Issues Encountered

**Test Pollution (resolved during Task 4):**
- Initial test design had module-level imports causing test pollution when sys.modules cleanup occurred
- Solution: Refactored to import logical_arb within each test method using `_import_logical_arb()` helper
- All 19 tests now pass consistently

---

*Phase: 09-structural-alpha-strategies*
*Plan: 01*
*Completed: 2026-04-04*
