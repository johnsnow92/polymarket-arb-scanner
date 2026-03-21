---
phase: 03-monitor-optimize
plan: "01"
subsystem: monitoring
tags: [metrics, alerting, observability, tdd]
dependency_graph:
  requires: []
  provides: [per-strategy-metrics, loss-spike-alerting, zero-opp-alerting]
  affects: [executor.py, alerting.py]
tech_stack:
  added: []
  patterns: [labeled-prometheus-metrics, rolling-window-anomaly-detection]
key_files:
  created:
    - tests/test_metrics_wiring.py
    - tests/test_anomaly_alerting.py
  modified:
    - executor.py
    - alerting.py
decisions:
  - "strategy label replaces platform label for all executor metrics — enables per-strategy P&L attribution"
  - "loss spike guard requires 10+ trades in rolling window to prevent false positives on early data"
  - "_trade_losses deque maxlen=20 gives rolling average over recent trades only (older trades expire)"
  - "check_loss_spike uses strictly-greater-than (>) comparison so exactly 3x avg does not fire"
metrics:
  duration_minutes: 4
  completed_date: "2026-03-21"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 4
---

# Phase 3 Plan 1: Per-Strategy Metrics and Anomaly Alerting Summary

**One-liner:** Per-strategy Prometheus labels on all executor metrics + LOSS_SPIKE/ZERO_OPP_PERIOD anomaly detection with rolling-window guards.

## Objective

Wire per-strategy metrics into the executor (MONITOR-02) and extend AlertManager with two anomaly detection methods — loss spike detection and zero-opportunity period detection (MONITOR-03). Both features are covered by 22 new tests passing in full TDD RED-GREEN cycle.

## What Was Built

### Part A — Per-Strategy Metrics in executor.py (MONITOR-02)

Changed all four `_metrics` calls in `execute()` from `"platform"` to `"strategy"` as the primary label key:

| Metric | Old label | New label |
|--------|-----------|-----------|
| `trades_executed` (dry_run path) | `{"platform": opp_type, "status": "dry_run"}` | `{"strategy": opp_type, "status": "dry_run"}` |
| `trades_executed` (filled path) | `{"platform": opp_type, "status": "filled"}` | `{"strategy": opp_type, "status": "filled"}` |
| `trades_failed` | `{"platform": opp_type, "reason": "execution"}` | `{"strategy": opp_type, "reason": "execution"}` |
| `execution_latency_seconds` | `{"type": opp_type}` | `{"strategy": opp_type}` |
| `risk_rejections` | `{"reason": reason[:50]}` | `{"strategy": opp_type, "reason": reason[:50]}` |

This enables Prometheus queries like `arb_trades_executed{strategy="Binary"}` to drill into per-strategy performance.

### Part B — Anomaly Alert Types in alerting.py (MONITOR-03)

**New AlertType enum members:**
- `LOSS_SPIKE = "LOSS_SPIKE"` — fires when a single loss exceeds 3x rolling average
- `ZERO_OPP_PERIOD = "ZERO_OPP_PERIOD"` — fires after 5+ consecutive scans with zero opportunities

**New AlertManager state:**
- `_trade_losses: deque[float]` (maxlen=20) — rolling window of absolute loss amounts
- `_zero_opp_count: int` — consecutive empty-scan counter

**New AlertManager methods:**
- `record_trade_result(profit: float)` — appends `abs(profit)` to loss window when profit < 0
- `check_loss_spike(loss_amount: float) -> bool` — guards against false positives (< 10 trades); fires CRITICAL when `loss_amount > 3 * avg`
- `check_zero_opp_period(opportunities_found: int) -> bool` — resets counter on non-zero scan; fires WARNING at `consecutive >= 5`

### Part C — Tests

**`tests/test_metrics_wiring.py`** (5 tests, class `TestPerStrategyMetrics`):
- `test_trades_executed_uses_strategy_label_on_dry_run` — runtime call verification
- `test_trades_executed_strategy_value_matches_opp_type` — value equals opp type string
- `test_execution_latency_uses_strategy_label` — source inspection
- `test_risk_rejections_uses_strategy_label` — source inspection
- `test_trades_failed_uses_strategy_label` — source inspection

**`tests/test_anomaly_alerting.py`** (17 tests, classes `TestAlertTypeEnumMembers`, `TestLossSpike`, `TestZeroOppPeriod`):
- 3 enum membership tests
- 8 loss spike tests (guard clause, fire, no-fire, boundary, context, wins ignored, maxlen)
- 6 zero-opp tests (below threshold, at threshold, beyond, reset, reset-then-fire, context)

## Verification

```
grep -c '"strategy":' executor.py  → 6
grep "LOSS_SPIKE\|ZERO_OPP_PERIOD" alerting.py → 6 matches (enum + method refs)
pytest tests/test_metrics_wiring.py tests/test_anomaly_alerting.py → 22 passed
pytest tests/ -x -q --ignore=tests/integration → 1553 passed in 70s
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test fixture returned MagicMock as balance, causing TypeError in risk manager**
- **Found during:** Task 1 (GREEN phase, first run of wiring tests)
- **Issue:** `_get_cached_balances` calls `_fetch_balances` which calls `pm_trader.get_balances()` — the MagicMock returns another MagicMock, and `risk_manager.py:87` tries to compare it with `< float`
- **Fix:** Override `_get_cached_balances` with a lambda returning `None` in the two affected test methods
- **Files modified:** `tests/test_metrics_wiring.py`
- **Commit:** cca8892

## Known Stubs

None. All methods are fully implemented and wired.

## Self-Check: PASSED

- `executor.py` — FOUND (modified, 6 strategy labels)
- `alerting.py` — FOUND (modified, LOSS_SPIKE + ZERO_OPP_PERIOD + 3 methods)
- `tests/test_metrics_wiring.py` — FOUND (created, 5 tests)
- `tests/test_anomaly_alerting.py` — FOUND (created, 17 tests)
- Commit cca8892 — FOUND
