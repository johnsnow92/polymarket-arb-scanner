---
status: partial
phase: 04-go-live
source: [04-01-PLAN.md, 04-02-PLAN.md, 04-03-PLAN.md]
started: 2026-03-24
updated: 2026-03-24
---

## Current Test

[awaiting deployment — Day 0 not started]

## Tests

### 1. Configure Railway env vars for Layer 1 full-auto (Day 0)
expected: Railway deployed green with DRY_RUN=false, EXECUTION_MODE=full-auto, MAX_TRADE_SIZE=3.0, DAILY_LOSS_LIMIT=5.0. Pre-flight check all PASS.
result: [pending]

### 2. Monitor Layer 1 for 48 hours (Day 0-2)
expected: Net positive P&L (or break-even), >90% trade success rate, no crash loops, no duplicate trades.
result: [pending]

### 3. Confirm Layer 2 active (Day 2)
expected: Resolution/stale scans visible in Railway logs, combined L1+L2 P&L net positive.
result: [pending]

### 4. Enable Layer 3 market making (Day 4)
expected: MM_ENABLED=true set, quotes placed, inventory within $500/market, P&L still positive.
result: [pending]

### 5. Enable Layer 4 informed trading (Day 5)
expected: EVENT_MONITOR_ENABLED=true set, divergence signals fetched, all 4 layers stable.
result: [pending]

### 6. 7-day validation (Day 12)
expected: `python scripts/validation_report.py --db trades.db --days 7` shows all 3 criteria PASS.
result: [pending]

## Summary

total: 6
passed: 0
issues: 0
pending: 6
skipped: 0
blocked: 0

## Gaps
