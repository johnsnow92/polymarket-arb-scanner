---
phase: 03-monitor-optimize
plan: "03"
subsystem: optimization-feedback
tags: [priority-queue, fee-reload, backtest-feedback, asyncio, OPTIMIZE-01, OPTIMIZE-02, OPTIMIZE-03]
dependency_graph:
  requires: [03-01, 03-02]
  provides: [priority-execution-queue, dynamic-fee-reload, nightly-backtest-recommendations]
  affects: [continuous.py, config.py, backtest.py]
tech_stack:
  added: []
  patterns: [asyncio.PriorityQueue, run_in_executor, nonlocal-timer-pattern, reload-globals]
key_files:
  created:
    - tests/test_priority_queue.py
    - tests/test_fee_backtest.py
  modified:
    - continuous.py
    - config.py
    - backtest.py
decisions:
  - "Priority queue uses negated _execution_priority as min-heap value so StalePriceOpp (weight 3.0) dequeues before MarketMake (weight 1.0)"
  - "reload_fee_rates() only touches BETFAIR_COMMISSION_RATE, SMARKETS_COMMISSION_RATE, GEMINI_FEE_RATE — never DRY_RUN, EXECUTION_MODE, or API keys"
  - "Nightly backtest uses run_in_executor(None, _sync_run) to avoid blocking asyncio loop — wrapped in asyncio.ensure_future"
  - "_seq_counter provides tie-breaking for equal-priority opps — GIL makes int increment effectively atomic for our use case"
  - "WS callback falls back to direct execution if priority queue push fails (defensive)"
  - "Backtest recommendations clamp MIN_NET_ROI to [0.001, 0.05] and FUZZY_MATCH_THRESHOLD to [60, 90]"
metrics:
  duration_seconds: 495
  completed_date: "2026-03-21T09:09:23Z"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 16
  tests_total: 1588
---

# Phase 03 Plan 03: Priority Queue, Dynamic Fee Reload, and Nightly Backtest Feedback Loop Summary

One-liner: asyncio.PriorityQueue for time-sensitive WS execution, hourly fee env-var reload without restart, and nightly backtest writing threshold recommendations JSON.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Priority execution queue and dynamic fee reload | d18d1d7 | continuous.py, config.py, tests/test_priority_queue.py, tests/test_fee_backtest.py |
| 2 | Nightly backtest-to-config feedback loop | d18d1d7 | backtest.py, continuous.py, tests/test_fee_backtest.py |

## What Was Built

### Priority Execution Queue (OPTIMIZE-03)

`continuous.py` now maintains an `asyncio.PriorityQueue` (`_priority_queue`) scoped to the `run_continuous()` invocation. The WS callback (`on_price_update`) pushes qualifying opportunities to the queue using `asyncio.run_coroutine_threadsafe` for thread-safe cross-thread push. Priority is `-(weight * capital_efficiency_score)` — StalePriceOpp (weight 3.0) and ResolutionSnipeOpp (weight 2.5) dequeue before Binary (2.0) and MarketMake (1.0). A monotonic `_seq_counter` breaks ties. A `_priority_consumer` coroutine runs as a background asyncio task alongside the scan loop, draining the queue and logging a warning if execution latency exceeds 500ms.

### Dynamic Fee Reload (OPTIMIZE-01)

`config.py` gains `reload_fee_rates()` — a function that re-reads `BETFAIR_COMMISSION_RATE`, `SMARKETS_COMMISSION_RATE`, and `GEMINI_FEE_RATE` from environment variables and updates module globals in place. It returns a dict of changed variables with `(old_value, new_value)` tuples. It explicitly never touches `DRY_RUN`, `EXECUTION_MODE`, or API keys. Three new interval constants: `FEE_REFRESH_INTERVAL` (3600s), `BACKTEST_RUN_INTERVAL` (86400s), `REBALANCE_DIGEST_INTERVAL` (604800s). The scan loop in `continuous.py` calls `reload_fee_rates()` every `FEE_REFRESH_INTERVAL` seconds using the same timer pattern as bankroll refresh.

### Nightly Backtest Feedback (OPTIMIZE-02)

`backtest.py` gains `build_recommendations(result: BacktestResult) -> dict` and `write_recommendations(result, data_dir) -> str`. The recommendation dict contains `generated_at`, `period_days`, `total_trades`, `win_rate`, `recommended` (MIN_NET_ROI, FUZZY_MATCH_THRESHOLD, MIN_PROFIT_THRESHOLD), `current` (same keys), and `by_strategy` breakdown. Suggestion logic: win_rate > 0.7 lowers MIN_NET_ROI 10%, win_rate < 0.5 raises it 20%, clamped to [0.001, 0.05]. `continuous.py` fires a nightly backtest via `asyncio.ensure_future` + `loop.run_in_executor(None, _sync_run)` at `BACKTEST_RUN_INTERVAL` intervals — non-blocking.

### Additional Wiring

- Weekly rebalance digest timer sends platform balance vs opportunity-flow report via notifier.
- `alert_manager.check_zero_opp_period(len(all_opportunities))` called after every scan cycle (MONITOR-03 wiring from Plan 01).

## Deviations from Plan

None — plan executed exactly as written. The `fees.py` file was listed in the plan's `files_modified` frontmatter but the plan body did not require any changes to fees.py directly (fee reload works by updating config globals that fees.py already references through `config.BETFAIR_COMMISSION_RATE` imports). This was the intended design — fees.py is already wired correctly.

## Known Stubs

None. All connections are live:
- Priority queue consumer executes real `executor.execute(opp)` calls
- `reload_fee_rates()` updates real module globals
- `build_recommendations()` reads real `config.MIN_NET_ROI` etc.
- `write_recommendations()` writes real JSON to `DATA_DIR`

## Self-Check: PASSED
