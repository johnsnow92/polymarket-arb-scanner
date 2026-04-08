---
phase: 06
plan: 02
subsystem: monitoring/dashboard
tags: [dashboard, metrics, leaderboard, api, strategy-analytics]
dependency_graph:
  requires: [06-01]
  provides: [leaderboard-endpoint, strategy-metrics-ui]
  affects: [continuous-mode, dashboard-ui]
tech_stack:
  added: [DuckDB analytics integration, per-strategy metrics API]
  patterns: [singleton state, HTTP endpoints, JSON serialization, JavaScript auto-refresh]
key_files:
  created: []
  modified:
    - dashboard.py (added _DashboardState.strategy_leaderboard field, /api/strategy-leaderboard endpoint)
    - dashboard_ui.py (added HTML leaderboard section, JavaScript fetch/render, formatting helpers)
    - continuous.py (integrated metrics update into scan loop)
    - tests/test_dashboard_endpoints.py (6 new test methods, all passing)
decisions:
  - Used existing analytics.get_strategy_metrics() from 06-01 rather than duplicating logic
  - Placed metrics update in continuous.py after all opportunities logged, once per scan cycle
  - Implemented XSS-safe DOM manipulation using textContent, createElement, removeChild (no innerHTML)
  - Leaderboard syncs with analytics 7-day rolling window for consistency
  - Endpoint returns state data as-is (no re-sorting); sorting happens upstream in analytics.py
metrics:
  duration: "~45 minutes (estimated from 4 commits)"
  completed_date: 2026-04-04T15:30:00Z
  tasks_completed: 4/4
  tests_added: 6
  tests_passing: 25/25
---

# Phase 6 Plan 02: Strategy Leaderboard Dashboard Summary

Extended the monitoring dashboard with a real-time per-strategy P&L metrics leaderboard, displaying win rate, rolling Sharpe ratio, max drawdown, and total P&L sorted by profit. Updates every scan cycle (~60s) with auto-refreshing 30s UI poll.

## Objective

Add `/api/strategy-leaderboard` endpoint to the dashboard that surfaces per-strategy performance metrics from the `trades.db` analytics layer, with corresponding HTML table rendering and continuous integration into the live scanner loop.

## Implementation

### 1. Dashboard API Extension (Task 1)
**File:** `dashboard.py`
**Commit:** 9a45d0b

Extended `_DashboardState.__init__()` with:
- `strategy_leaderboard: list[dict]` — leaderboard data
- `leaderboard_updated_at: float` — timestamp of last update

Added `update_strategy_metrics(strategy_metrics: list[dict])` method to sync analytics results into state.

Added `/api/strategy-leaderboard` endpoint that:
- Checks auth via `_check_auth()`
- Returns JSON: `{ strategies: list[dict], timestamp: float, lookback_days: int }`
- Uses `_send_json()` helper for serialization
- Mirrors response structure of existing balance/rebalance endpoints

### 2. Dashboard UI Integration (Task 2)
**File:** `dashboard_ui.py`
**Commit:** 11fd92c

Added HTML section (lines 494-530):
```html
<section id="leaderboard-section" class="section">
  <h2>Strategy Leaderboard (7-day rolling)</h2>
  <table id="leaderboard-table" class="tbl">
    <thead>
      <tr>
        <th>Strategy</th>
        <th class="right">Trades</th>
        <th class="right">Wins</th>
        <th class="right">Win Rate</th>
        <th class="right">Total P&L</th>
        <th class="right">Avg P&L</th>
        <th class="right">Annual Sharpe</th>
        <th class="right">Max Drawdown</th>
      </tr>
    </thead>
    <tbody id="leaderboard-body"></tbody>
  </table>
</section>
```

Added formatting helpers:
- `formatPercent(val)` — displays win rate as "XX.X%"
- `formatCurrency(val)` — displays P&L as "$X.XXXX"
- `formatSharpe(val)` — displays Sharpe as "X.XXX"

Added `renderLeaderboard(data)` function:
- XSS-safe clearing: `while (tbody.firstChild) { tbody.removeChild(tbody.firstChild) }`
- Creates rows with `document.createElement()` and safe `textContent` assignment
- Handles null/empty states with loading indicators
- Displays last-update timestamp from response

Wired into Promise.all fetch chain:
- Added `/api/strategy-leaderboard` request
- Added `renderLeaderboard()` to render pipeline

### 3. Continuous Mode Integration (Task 3)
**File:** `continuous.py`
**Commit:** b2937e8

Located existing `get_strategy_metrics()` call (line 1259-1260) and extended with:
```python
dashboard_state.update_strategy_metrics(metrics)
```

Placed after all opportunities logged to database, once per scan cycle. Metrics fetched with 7-day lookback window for consistency with UI display.

Error handling wraps both operations with single try/catch block.

### 4. Test Coverage (Task 4)
**File:** `tests/test_dashboard_endpoints.py`
**Commits:** 9a45d0b, 11fd92c, b2937e8

Created `TestStrategyLeaderboardEndpoint` class with 6 test methods:
1. `test_returns_200_with_required_keys` — verifies HTTP 200 and JSON structure (strategies, timestamp, lookback_days keys)
2. `test_leaderboard_reflects_state` — sets state with test data and verifies endpoint returns it unchanged
3. `test_empty_leaderboard_returns_valid_response` — validates handling of empty strategy list
4. `test_strategies_have_required_fields` — confirms all 8 required fields present (strategy, trade_count, wins, win_rate, total_pnl, avg_pnl, annual_sharpe, max_drawdown)
5. `test_strategies_sorted_by_pnl_descending` — verifies endpoint returns state order (no re-sorting at endpoint level)
6. `test_update_strategy_metrics_method` — unit test of `_DashboardState.update_strategy_metrics()` method

All tests use `_start_test_server(port)` and `_get(url, path)` test utilities.
Test ports: 19030-19034 (no conflicts).

**Result:** 25/25 tests passing (6 new + 19 existing).

## Success Criteria

- [x] `/api/strategy-leaderboard` endpoint returns 200 with correct JSON structure
- [x] Endpoint returns leaderboard data from state with required fields (8 total)
- [x] HTML table renders with auto-fetching every 30s via JavaScript
- [x] XSS-safe DOM manipulation (no innerHTML, using textContent/createElement)
- [x] Metrics updated in continuous.py scan loop after opportunities logged
- [x] 6 comprehensive endpoint + integration tests, all passing
- [x] Formatting helpers (percent, currency, Sharpe) display metrics correctly
- [x] Last-update timestamp displayed in UI

## Deviations from Plan

None. Plan executed exactly as designed. Reused existing `analytics.get_strategy_metrics()` from plan 06-01 rather than duplicating logic.

## Known Stubs

None. Leaderboard integrates fully with analytics backend and continuous scanner loop.

## Notes

- Leaderboard syncs with 7-day rolling window configured in analytics.py for consistency
- Endpoint returns state data as-is; sorting handled upstream in `get_strategy_metrics()`
- Metrics recalculate once per scan cycle (~60s), UI polls every 30s (2x polling frequency ensures fresh data)
- Authentication enforced via `_check_auth()` matching dashboard security model
