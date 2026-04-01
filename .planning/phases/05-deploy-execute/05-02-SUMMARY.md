---
phase: "05-deploy-execute"
plan: "02"
subsystem: "executor, scans"
tags: ["layer-revalidation", "calibration-logging", "maker-routing", "layer-tagging"]
dependency_graph:
  requires: ["config.REVAL_FLOORS", "config.get_layer", "config.STRATEGY_LAYERS"]
  provides: ["executor.layer_aware_revalidation", "executor.calibration_logging", "executor.maker_routing", "scans._layer_tag"]
  affects: ["continuous.py", "plan-03-deployment"]
tech_stack:
  added: []
  patterns: ["structured-logging", "tuple-returns", "layer-dispatch", "gtc-timeout-cancel"]
key_files:
  created: []
  modified:
    - "executor.py"
    - "tests/test_executor.py"
    - "scans/binary.py"
    - "scans/negrisk.py"
    - "scans/kalshi.py"
    - "scans/cross.py"
    - "scans/spread.py"
    - "scans/betfair.py"
    - "scans/smarkets.py"
    - "scans/sxbet.py"
    - "scans/matchbook.py"
    - "scans/gemini.py"
    - "scans/ibkr.py"
    - "scans/multi_cross.py"
    - "scans/triangular.py"
    - "scans/stale.py"
    - "scans/resolution.py"
    - "scans/convergence.py"
    - "event_monitor.py"
    - "market_maker.py"
decisions:
  - "_layer tag is hardcoded integer per scan module (not dynamically looked up) for auditability"
  - "_revalidate_* methods return tuple[bool, float, str] for structured calibration logging"
  - "REVAL| pipe-delimited log format for Railway log parsing and 80th-percentile drift analysis"
  - "Maker routing uses GTC orders with timeout cancel and NO taker fallback (per D-05)"
  - "Layer 1 aggressive maker (best ask price), Layer 3-4 passive maker (target price)"
metrics:
  duration: "20 minutes"
  completed_date: "2026-04-01"
  tasks_completed: 2
  tasks_total: 2
deviations: []
self_check: "PASSED"
---

## Summary

Implemented layer-aware revalidation with calibration logging and maker order routing across the entire scan and execution pipeline.

## Task 1: Add _layer tag to all scan modules

Added `"_layer": N` to every opportunity dict construction across all 18 files:
- **Layer 1** (pure arbitrage): binary, negrisk, kalshi, cross, spread, betfair, smarkets, sxbet, matchbook, gemini, ibkr, multi_cross, triangular
- **Layer 2** (near-arbitrage): stale, resolution
- **Layer 3** (market making): market_maker
- **Layer 4** (informed trading): convergence, event_monitor

## Task 2: Layer-aware revalidation, calibration logging, maker routing

**Revalidation floors:** `_get_revalidation_threshold()` now uses `REVAL_FLOORS[layer]` (2% L1, 5% L2, 3% L3, 10% L4) instead of flat minimum. Missing `_layer` key falls back to `get_layer(opp["type"])` with warning.

**Calibration logging:** Every revalidation decision emits structured `REVAL|layer=N|type=...|scan_roi=...|reval_roi=...|delta=...|passed=...|reason=...|elapsed_ms=N|floor=...` log line. All `_revalidate_*` methods return `tuple[bool, float, str]` for reason tracking.

**Maker routing:** Polymarket and Kalshi legs use GTC (limit) orders by default. Unfilled orders cancelled after `GTC_ORDER_TIMEOUT` (5s). No taker fallback per D-05 — cancelled orders return None.

## Test Results

50 revalidation/layer/maker tests passing:
- TestLayerAwareRevalidation: 6 tests (L1-L4 floors, missing layer fallback, high ROI bypass)
- TestCalibrationLogging: 2 tests (structured format, required fields)
- TestMakerRouting: 3 tests (GTC placement, timeout cancel, no taker fallback)
- All existing revalidation tests updated and passing
