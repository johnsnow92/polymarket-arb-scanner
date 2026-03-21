# Phase 3: Monitor & Optimize - Context

**Gathered:** 2026-03-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Full visibility into bot performance with live P&L dashboard, per-strategy metrics, anomaly alerting, and automated capital optimization. All monitoring/optimization infrastructure ships in this phase; progressive enablement happens in Phase 4 (Go Live).

Requirements: MONITOR-01 through MONITOR-04, OPTIMIZE-01 through OPTIMIZE-05.

</domain>

<decisions>
## Implementation Decisions

### Dashboard & Monitoring
- Per-strategy P&L breakdown + total on dashboard — existing `_DashboardState` tracks `daily_pnl`, add per-strategy from `decisions.jsonl`
- Wire `metrics.py` counters into executor + scans, expose Prometheus text format at `/metrics`
- Keep existing 15s polling via `__REFRESH_SECONDS__` in dashboard_ui.py
- Add `/api/balances` endpoint querying cached platform balances from continuous.py bankroll refresh (wired in Phase 1)

### Alerting & Anomaly Detection
- Anomaly triggers: loss spike (>3x avg loss), consecutive failures (5+), daily loss at 80%/100% — extend existing AlertManager
- Detection within 60s — check after every trade execution, rate-limit 1 alert per type per 5 min (existing behavior)
- Weekly rebalancing digest via webhook — per-platform balance distribution vs opportunity flow, suggest moves
- Alert delivery via existing webhook (notifier.py) — auto-detects Slack/Discord/generic format

### Optimization & Automation
- Backtest-to-config feedback loop: nightly backtest writes recommended thresholds to JSON file, continuous.py reads on next cycle. No auto-apply — human reviews via dashboard
- Priority execution lane: priority queue in continuous.py — resolution sniping and stale price get priority over market making and convergence (sorted by time-sensitivity, not profit)
- Dynamic fee schedule: hourly fee check via platform APIs, update fees.py runtime constants if changed, log changes
- Scope: build all infrastructure here, enable progressively in Go Live (Phase 4)

### Claude's Discretion
- Dashboard chart library choice (Chart.js already embedded in dashboard_ui.py)
- Exact priority queue implementation (heapq vs sorted list)
- Backtest recommendation JSON schema
- Rebalancing recommendation format and thresholds

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `dashboard.py` — Full HTTP server with `_DashboardState`, JSON API endpoints (`/status`, `/api/positions`, `/api/trades`, `/api/opportunities`, `/api/pnl`), optional HTTP Basic Auth
- `dashboard_ui.py` — Complete single-page HTML dashboard with Chart.js, 15s polling, dark theme, responsive layout
- `metrics.py` — Prometheus-compatible counters, gauges, histograms (stdlib-only, thread-safe), text exposition at `/metrics`
- `alerting.py` — `AlertManager` with rate limiting, severity levels (INFO/WARNING/CRITICAL), loss streak detection, daily loss limit alerts
- `notifier.py` — Async webhook sender with Slack/Discord/generic format auto-detection
- `backtest.py` — Full replay engine with simulated execution over recorded snapshots, standalone CLI
- `position_sizer.py` — Kelly criterion sizing with `update_bankroll()` already wired in continuous.py
- `continuous.py` — Async event loop, WebSocket feeds, FeedManager, OpportunityIndex, bankroll refresh timer

### Established Patterns
- Dashboard state updated by scanner loop (cli.py/continuous.py) via `dashboard_state.*` assignments
- AlertManager called from executor on failures, risk_manager on limit hits
- Metrics counters incremented inline but not yet wired into scan/execution paths
- Thread-safe patterns: `threading.Lock` on shared state, WAL mode on SQLite

### Integration Points
- `executor.py:execute()` — wire metrics counters for per-strategy tracking
- `continuous.py` — add priority queue for opportunity processing, add periodic backtest/fee-check tasks
- `dashboard.py` — add `/api/balances`, `/api/strategy-pnl` endpoints
- `dashboard_ui.py` — add per-strategy P&L charts and balance display sections
- `alerting.py` — add loss spike detection, rebalancing recommendation logic

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches. All existing code provides a strong foundation; Phase 3 wires it together and fills gaps.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>
