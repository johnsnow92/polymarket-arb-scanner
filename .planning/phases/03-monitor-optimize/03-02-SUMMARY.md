---
phase: 03-monitor-optimize
plan: 02
subsystem: dashboard
tags: [dashboard, monitoring, capital-allocation, api-endpoints, charts]
dependency_graph:
  requires: []
  provides: [strategy-pnl-api, balances-api, rebalance-api, dashboard-charts]
  affects: [dashboard.py, dashboard_ui.py, db.py]
tech_stack:
  added: []
  patterns: [TradeDB query method, HTTP handler pattern, Chart.js horizontal bar, Chart.js doughnut]
key_files:
  created:
    - tests/test_dashboard_endpoints.py
  modified:
    - db.py
    - dashboard.py
    - dashboard_ui.py
decisions:
  - get_strategy_pnl uses opportunities.net_profit as proxy for trade P&L since trades table has no dedicated pnl column
  - rebalance threshold is 5% drift before recommendation is generated
  - platform_balances/opp_flow attributes added to _DashboardState as empty dicts (populated by continuous.py at runtime)
  - Win count uses parent opportunity net_profit > 0 as signal since individual trade legs have no separate pnl
metrics:
  duration_seconds: 403
  completed_date: "2026-03-21"
  tasks_completed: 2
  files_modified: 4
---

# Phase 03 Plan 02: Dashboard API Endpoints and UI Charts Summary

Dashboard extended with three new API endpoints for per-strategy P&L, platform balances, and rebalancing recommendations, plus two new Chart.js visualizations wired into the 15s refresh loop.

## What Was Built

**GET /api/strategy-pnl** — Joins trades with opportunities to return per-strategy trade count, win count, total P&L, and avg profit. Uses `TradeDB.get_strategy_pnl()` (new DB method).

**GET /api/balances** — Returns `state.platform_balances` dict with total and `last_updated` timestamp. Populated by continuous.py bankroll refresh at runtime.

**GET /api/rebalance** — Compares each platform's capital share to its opportunity flow share. Returns transfer recommendations when drift exceeds 5%.

**Dashboard UI** — Two new `grid-2` sections:
1. Per-Strategy P&L: horizontal bar chart (green/red by sign) + table with win rate, total P&L, avg profit
2. Platform Capital Balances: doughnut chart + table with current %, target %, and directional transfer recommendations

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 562ec6d | feat(03-02): add strategy-pnl, balances, rebalance API endpoints |
| 2 | fce792b | feat(03-02): enhance dashboard UI with strategy P&L chart and balances panel |

## Tests

19 new tests in `tests/test_dashboard_endpoints.py`:
- `TestGetStrategyPnl` (5 tests) — DB method unit tests
- `TestDashboardStateNewAttributes` (4 tests) — new state attribute defaults
- `TestStrategyPnlEndpoint` (3 tests) — HTTP endpoint integration
- `TestBalancesEndpoint` (3 tests) — HTTP endpoint integration
- `TestRebalanceEndpoint` (4 tests) — HTTP endpoint integration with imbalance scenarios

Full suite: 1572 tests passing (was 1553 before this plan).

## Deviations from Plan

None — plan executed exactly as written. The `DASHBOARD_HTML` attribute referenced in the plan's verify command does not exist in the module (actual attribute is `_TEMPLATE`), but the verification was adapted to use the correct attribute.

## Known Stubs

- `state.platform_balances` and `state.platform_opp_flow` default to `{}` — they are populated at runtime by `continuous.py:_check_platform_balance()`. The `/api/balances` and `/api/rebalance` endpoints return empty/zero results until the continuous mode bot runs and updates these values. This is intentional — the attributes exist and the endpoints work correctly, they just have no data until the bot has run.

## Self-Check: PASSED

All created/modified files exist. Both task commits (562ec6d, fce792b) confirmed in git log. 1572 tests pass.
