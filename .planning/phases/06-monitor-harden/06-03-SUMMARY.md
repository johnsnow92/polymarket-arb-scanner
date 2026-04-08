---
phase: 06-monitor-harden
plan: 03
subsystem: alerting, monitoring
tags: [MON-03, per-strategy-alerts, loss-streak, zero-opportunity]
requirements: [MON-03]
tech_stack:
  added:
    - Per-strategy loss streak tracking (dict[str, deque])
    - 30-minute zero-opportunity period detection
    - Rate-limited alerts via AlertManager
patterns:
  - Nested dicts for per-strategy state tracking
  - Deque-based rolling window for consecutive losses
  - Time-based window tracking for idle periods
key_files:
  created: []
  modified:
    - alerting.py
    - executor.py
    - continuous.py
    - tests/test_alerting.py
decisions:
  - Use AlertType.ZERO_OPP (not ZERO_OPP_PERIOD) for per-strategy zero-opp alerts
  - Fire strategy loss streak alert on exactly 3 losses (once per 5-min rate limit window)
  - Track last opportunity time per strategy; alert after 1800s (30 min) idle
  - Integrate loss streak check into executor post-trade; zero-opp check into continuous scan loop
dependency_graph:
  requires:
    - alerting.py: existing AlertManager, notifier.py webhook system
    - executor.py: trade result access (net_profit, opp type)
    - continuous.py: scan loop structure, opportunity aggregation
  provides:
    - Per-strategy loss streak detection with webhook alerts
    - Per-strategy zero-opportunity period detection with webhook alerts
    - Integration points in executor and continuous for real-time monitoring
  affects:
    - executor.py: adds alert_manager.check_strategy_loss_streak call post-trade
    - continuous.py: adds alert_manager.check_zero_opp_period_per_strategy call post-scan
metrics:
  tasks_completed: 4
  tests_added: 14
  test_coverage: 47 tests passing (all alerting suite)
  duration_minutes: 35
  completion_date: "2026-04-04T12:00:00Z"
---

# Phase 06 Plan 03: Per-Strategy Alerts (MON-03) Summary

Extended AlertManager with per-strategy loss streak tracking and zero-opportunity period detection. Integrated checks into executor trade logging and continuous scan loop. All 47 alerting tests passing.

## Objective

Extend the existing AlertManager to track loss streaks and zero-opportunity periods per strategy, firing alerts via webhook when (1) any single strategy hits 3 consecutive losses or (2) any strategy has no new opportunities for 30+ minutes. Integrate loss streak checking into the executor (post-trade) and zero-opportunity checking into the continuous scan loop.

## Key Deliverables

1. **AlertManager extended** — Per-strategy loss streak and zero-opportunity tracking
   - `_strategy_losses: dict[str, deque]` — rolling window of trade results per strategy
   - `_strategy_last_opp_time: dict[str, float]` — last opportunity timestamp per strategy
   - `check_strategy_loss_streak(strategy_type, trade_won) -> bool` — fires on 3 consecutive losses
   - `check_zero_opp_period_per_strategy(strategy_opportunities) -> None` — fires after 30min idle
   - `record_strategy_opportunity(strategy_type) -> None` — helper to initialize tracking

2. **Executor integration** — Loss streak check after each trade
   - Extracts strategy type from `opp["type"]`
   - Calculates trade profitability from `net_profit > 0`
   - Calls `alert_manager.check_strategy_loss_streak()` with error handling
   - Logs trade result for monitoring

3. **Continuous scan integration** — Zero-opportunity check per scan cycle
   - Counts opportunities per strategy from all detected opportunities
   - Calls `alert_manager.check_zero_opp_period_per_strategy()` with strategy counts
   - Calls `alert_manager.record_strategy_opportunity()` for each strategy
   - Maintains backward compatibility with existing checks

4. **Unit tests** — 14 new tests for MON-03 functionality
   - Loss streak tests: single loss, two losses, exactly 3 losses (fires alert), 4+ losses (rate limited), win resets, multiple strategies independent, metadata validation
   - Zero-opportunity tests: under 30min (no alert), 30+ min (fires alert), rate limiting, window reset on new opp, empty dict handling, multiple strategies, opp count resets window
   - All tests use time mocking with `side_effect` pattern for accurate timestamp control
   - 47 total alerting tests passing (no regressions)

## Files Modified

| File | Changes |
|------|---------|
| alerting.py | Added `_strategy_losses`, `_strategy_opp_count`, `_strategy_last_opp_time` dicts; added `check_strategy_loss_streak()`, `check_zero_opp_period_per_strategy()`, `record_strategy_opportunity()` methods; added AlertType.ZERO_OPP |
| executor.py | Added loss streak check after successful trade logging; extracting strategy type and profitability; error handling |
| continuous.py | Added opportunity aggregation per strategy; integrated `check_zero_opp_period_per_strategy()` call; called `record_strategy_opportunity()` for each strategy |
| tests/test_alerting.py | Added TestStrategyLossStreak (7 tests) and TestZeroOpportunityPeriod (7 tests); all passing |

## Integration Details

**Executor (post-trade logging):**
```python
strategy_type = opp.get("type", "unknown")
trade_won = opp.get("net_profit", 0) > 0
alert_manager.check_strategy_loss_streak(strategy_type, trade_won)
```

**Continuous (post-scan cycle):**
```python
strategy_opp_counts = {}
for opp in opportunities:
    strategy_type = opp.get("type", "unknown")
    strategy_opp_counts[strategy_type] = strategy_opp_counts.get(strategy_type, 0) + 1

alert_manager.check_zero_opp_period_per_strategy(strategy_opp_counts)
for strategy_type in strategy_opp_counts:
    alert_manager.record_strategy_opportunity(strategy_type)
```

## Alert Types & Rates

| Alert Type | Severity | Threshold | Rate Limit |
|-----------|----------|-----------|-----------|
| LOSS_STREAK | WARNING | 3 consecutive losses per strategy | 5 minutes |
| ZERO_OPP | INFO | 30+ minutes with no opportunities for strategy | 5 minutes |

## Test Coverage

- **Loss Streak Tests** (7):
  - Single loss → no alert
  - Two losses → no alert
  - Exactly 3 losses → LOSS_STREAK alert with strategy name
  - 4 consecutive losses → no re-alert (rate limited)
  - Win resets counter → streak breaks
  - Multiple strategies independent → only affected strategy alerts
  - Alert metadata includes strategy name and loss count

- **Zero-Opportunity Tests** (7):
  - Under 30 minutes idle → no alert
  - 30+ minutes idle → ZERO_OPP alert
  - Second check rate-limited → only 1 alert fires
  - New opportunity resets window → 2nd alert can fire
  - Empty opportunity dict → no crash
  - Multiple strategies → independent tracking
  - Non-zero count resets window → next check at 30min can alert

## Implementation Notes

- **Rate Limiting**: AlertManager's existing 5-minute cooldown prevents alert spam. Loss streak alerts fire once per strategy per 5-min window. Zero-opp alerts fire once per strategy per 5-min window.
- **Time Mocking**: Tests use `patch("alerting.time")` with `side_effect` lambdas to intercept `time.time()`, `time.gmtime()`, and `time.strftime()` calls.
- **Alert Type**: Uses `AlertType.ZERO_OPP` (not `ZERO_OPP_PERIOD`) to match test expectations and differentiate from existing `check_zero_opp_period()` global alert.
- **Error Handling**: Both new methods wrap try/except to prevent alerting failures from crashing the bot.
- **State Initialization**: Deques and dicts are created on-demand when a strategy is first seen, avoiding pre-allocation.

## Verification

All success criteria met:
- ✓ AlertManager tracks loss streaks per strategy in `_strategy_losses` dict
- ✓ Loss streak alert fires after exactly 3 consecutive losses (once per 5-min window)
- ✓ Zero-opportunity alert fires after 30 minutes with no new opportunities for a strategy
- ✓ Zero-opportunity alert fires once per 5-min rate limit window
- ✓ executor.py calls check_strategy_loss_streak after logging each trade
- ✓ Strategy type extracted from opp["type"]; profitability from net_profit > 0
- ✓ continuous.py counts opportunities per strategy and calls check_zero_opp_period_per_strategy after each scan
- ✓ check_zero_opp_period_per_strategy receives dict mapping strategy → opp_count
- ✓ All methods handle errors gracefully (try/except) without crashing bot
- ✓ Alerts delivered via existing notifier.py webhook mechanism
- ✓ All tests pass: `pytest tests/test_alerting.py` → 47 passed

## Commits

| Commit | Message |
|--------|---------|
| c02a850 | feat(06-03): extend AlertManager with per-strategy loss streak tracking |
| 69480f2 | feat(06-03): integrate loss streak checking into executor trade logging |
| 2f2622d | feat(06-03): integrate zero-opportunity checking into continuous scan loop |
| 5b045f0 | test(06-03): add unit tests for per-strategy loss streak and zero-opportunity alerts |

## Deviations from Plan

**None** — plan executed exactly as written. All tasks completed, all tests passing, MON-03 requirement satisfied.

## Known Issues

**None** — All 47 alerting tests passing without errors or warnings.
