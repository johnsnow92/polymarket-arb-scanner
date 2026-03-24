---
phase: 03-monitor-optimize
verified: 2026-03-21T10:15:00Z
status: gaps_found
score: 8/9 truths verified
re_verification: false
gaps:
  - truth: "Fee rate env vars can be changed on Railway and take effect within one scan cycle without restart"
    status: failed
    reason: "reload_fee_rates() updates config module globals, but scan modules (scans/betfair.py, scans/gemini.py, scans/smarkets.py) use 'from config import BETFAIR_COMMISSION_RATE' which binds names at import time. After reload, scan call sites still hold pre-reload float values — confirmed via runtime test."
    artifacts:
      - path: "config.py"
        issue: "reload_fee_rates() correctly updates config.BETFAIR_COMMISSION_RATE etc. but has no mechanism to propagate to already-imported module-level names"
      - path: "scans/betfair.py"
        issue: "Line 8: 'from config import BETFAIR_COMMISSION_RATE' — name bound at import, not re-read from config module on each scan call"
      - path: "scans/gemini.py"
        issue: "Line 7: 'from config import GEMINI_FEE_RATE' — same binding issue"
      - path: "scans/smarkets.py"
        issue: "Line 7: 'from config import SMARKETS_COMMISSION_RATE' — same binding issue"
    missing:
      - "Scan modules must reference 'config.BETFAIR_COMMISSION_RATE' (attribute access) instead of 'from config import BETFAIR_COMMISSION_RATE' (name binding), OR reload_fee_rates() must also update the module-level globals in scans/betfair.py, scans/gemini.py, and scans/smarkets.py"
      - "Alternative: pass fee rate as a parameter read from config at call time inside the scan loop body"
human_verification:
  - test: "Dashboard auto-refresh visual check"
    expected: "GET / shows live P&L chart, strategy P&L section, and platform balances panel. Data updates every 15s."
    why_human: "Requires running dashboard server with active continuous mode — chart rendering and data population cannot be verified programmatically against the static HTML template alone."
  - test: "Priority queue 500ms latency guarantee"
    expected: "StalePriceOpp and ResolutionSnipeOpp execute within 500ms of WS trigger under real load"
    why_human: "End-to-end timing under real WebSocket feed and executor load cannot be measured with unit tests — requires live continuous mode run with instrumented logging."
  - test: "Weekly rebalance digest delivery"
    expected: "After REBALANCE_DIGEST_INTERVAL seconds, a webhook fires with platform balance vs opportunity-flow summary"
    why_human: "Timer-based behavior requires 7-day or shortened test window; webhook delivery to external endpoint cannot be confirmed programmatically."
---

# Phase 3: Monitor & Optimize Verification Report

**Phase Goal:** Full visibility into bot performance. Automated capital optimization. Dashboard showing live P&L.
**Verified:** 2026-03-21T10:15:00Z
**Status:** gaps_found — 1 gap blocking OPTIMIZE-01 goal
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | Every executor.execute() call increments a per-strategy Prometheus counter | VERIFIED | executor.py lines 275, 287 use `{"strategy": opp_type}` label; grep shows 6 occurrences of `"strategy":` in metrics calls |
| 2 | Execution latency is recorded per-strategy as a histogram observation | VERIFIED | executor.py line 285: `_metrics.observe("execution_latency_seconds", {"strategy": opp_type}, latency)` |
| 3 | A loss exceeding 3x rolling average fires a LOSS_SPIKE alert | VERIFIED | alerting.py: `check_loss_spike()` method implemented with 10-trade guard and 3x avg threshold; 8 tests confirm behavior |
| 4 | A sustained zero-opportunity period fires a ZERO_OPP_PERIOD alert | VERIFIED | alerting.py: `check_zero_opp_period()` fires WARNING at 5+ consecutive empty scans; wired in continuous.py line 1431 |
| 5 | GET /api/strategy-pnl returns per-strategy win rate, trade count, and realized P&L | VERIFIED | db.py `get_strategy_pnl()` joins trades+opportunities; dashboard.py route registered and calls it; 5 DB tests pass |
| 6 | Dashboard HTML renders per-strategy P&L chart and platform balances panel | VERIFIED | dashboard_ui.py contains `strategyPnlChart`, `balancesChart`, `strategy-pnl-tbody`; both sections integrated in 15s refresh loop |
| 7 | Time-sensitive opportunities execute before lower-priority ones via priority queue | VERIFIED | continuous.py: `asyncio.PriorityQueue` with negated `_execution_priority`; `_priority_consumer` task started; 5 priority queue tests pass |
| 8 | Nightly backtest produces a recommendations JSON file with suggested threshold adjustments | VERIFIED | backtest.py `build_recommendations()` and `write_recommendations()` implemented; continuous.py wires nightly task via `run_in_executor`; 6 tests confirm schema and output |
| 9 | Fee rate env vars can be changed on Railway and take effect within one scan cycle without restart | FAILED | `reload_fee_rates()` updates config module globals but scan modules used `from config import BETFAIR_COMMISSION_RATE` — runtime test confirms stale binding: `scan_rate` stays 0.03 after reload changes config to 0.01 |

**Score:** 8/9 truths verified

---

## Required Artifacts

### Plan 01 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `executor.py` | Per-strategy metrics labels on trades_executed and execution_latency_seconds | VERIFIED | Contains `"strategy":` label in 5 metrics calls (dry_run, filled, failed, latency, risk_rejections) |
| `alerting.py` | LOSS_SPIKE and ZERO_OPP_PERIOD alert types with detection methods | VERIFIED | Enum members added at lines 24-25; `check_loss_spike()` and `check_zero_opp_period()` methods fully implemented |
| `tests/test_metrics_wiring.py` | Unit tests for per-strategy metrics wiring | VERIFIED | 210 lines, 5 tests in `TestPerStrategyMetrics`, all pass |
| `tests/test_anomaly_alerting.py` | Unit tests for loss spike and zero-opp alerting | VERIFIED | 216 lines, 17 tests across 3 classes, all pass |

### Plan 02 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `dashboard.py` | New API endpoints /api/strategy-pnl, /api/balances, /api/rebalance | VERIFIED | All 3 routes registered; handlers call db.get_strategy_pnl() and state.platform_balances |
| `dashboard_ui.py` | Per-strategy P&L chart section and platform balances panel in HTML | VERIFIED | `strategyPnlChart`, `balancesChart` canvases present; both fetch from new API endpoints in refresh loop |
| `db.py` | get_strategy_pnl() method joining trades and opportunities tables | VERIFIED | Method at line 450, joins on opportunity_id, returns list[dict] with strategy/trade_count/win_count/total_pnl/avg_profit |
| `tests/test_dashboard_endpoints.py` | Unit tests for new dashboard API endpoints | VERIFIED | 324 lines, 19 tests across 5 classes, all pass |

### Plan 03 Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `continuous.py` | asyncio.PriorityQueue consumer, nightly backtest task, hourly fee reload timer, weekly rebalance digest timer | VERIFIED | All 4 timers present; PriorityQueue at line 749; `_priority_consumer` task created at line 827 |
| `config.py` | reload_fee_rates() function and BACKTEST_RUN_INTERVAL, FEE_REFRESH_INTERVAL constants | VERIFIED | All 3 constants present at lines 361/364/367; `reload_fee_rates()` at line 370 |
| `fees.py` | Updated fee functions that reference config module globals (so reload takes effect) | FAILED | fees.py was intentionally not modified; scan modules (betfair.py, gemini.py, smarkets.py) use `from config import` binding that does not update after reload — reload has no effect on actual scan fee calculations |
| `backtest.py` | build_recommendations() function that writes JSON to DATA_DIR | VERIFIED | `build_recommendations()` at line 441, `write_recommendations()` at line 475, outputs to `backtest_recommendations.json` |
| `tests/test_priority_queue.py` | Unit tests for priority queue ordering and thread-safe push | VERIFIED | 118 lines, 5 tests, all pass |
| `tests/test_fee_backtest.py` | Unit tests for fee reload safety and backtest recommendation output | VERIFIED | 206 lines, 11 tests across `TestFeeReload` and `TestBacktestRecommendations`, all pass |

---

## Key Link Verification

### Plan 01 Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| executor.py | metrics.py | `_metrics.inc("trades_executed", {"strategy": ...})` | WIRED | Confirmed at lines 275, 287, 289 |
| alerting.py | notifier.py | `AlertManager._send_webhook` / `alert()` | WIRED | LOSS_SPIKE and ZERO_OPP_PERIOD both call `self.alert()` which routes to notifier |

### Plan 02 Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| dashboard.py | db.py | `db.get_strategy_pnl()` in /api/strategy-pnl handler | WIRED | Line 540: `strategies = db.get_strategy_pnl()` |
| dashboard.py | dashboard state | `state.platform_balances` in /api/balances handler | WIRED | Line 553: `balances = getattr(state, "platform_balances", {})` |
| dashboard_ui.py | dashboard.py | `fetch('/api/strategy-pnl')` in JavaScript | WIRED | Line 1077: `api('/api/strategy-pnl')` in refresh Promise.all() |

### Plan 03 Links

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| continuous.py | config.py | hourly call to `reload_fee_rates()` | WIRED | Lines 1366-1374: timer checks `FEE_REFRESH_INTERVAL`, calls `config.reload_fee_rates()` |
| continuous.py | backtest.py | nightly call to `build_recommendations()` via `run_in_executor` | WIRED | Lines 1378-1399: `loop.run_in_executor(None, _sync_run)` inside `asyncio.ensure_future` |
| continuous.py | asyncio.PriorityQueue | WS callback pushes via `run_coroutine_threadsafe`, consumer coroutine drains | WIRED | Lines 656-669 (push), lines 765-816 (consumer), line 827 (task creation) |
| config.reload_fee_rates() | scans/betfair.py fee calls | Updated `BETFAIR_COMMISSION_RATE` module global flows to scan calculations | NOT WIRED | `scans/betfair.py` uses `from config import BETFAIR_COMMISSION_RATE` — stale binding. Runtime-confirmed: scan module retains pre-reload value after `reload_fee_rates()` executes |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| MONITOR-01 | 03-02 | Dashboard UI with live P&L and positions | SATISFIED | `/api/strategy-pnl` endpoint + `strategyPnlChart` in dashboard; auto-refreshes via `DASHBOARD_REFRESH_SECONDS` (default 15s) |
| MONITOR-02 | 03-01 | Per-strategy metrics via metrics.py | SATISFIED | executor.py emits labeled counters and histogram observations; `/metrics` endpoint returns Prometheus text |
| MONITOR-03 | 03-01, 03-03 | Anomaly alerting for loss spikes and zero-opp periods | SATISFIED | `check_loss_spike()` and `check_zero_opp_period()` implemented; `check_zero_opp_period` wired to scan loop in continuous.py line 1431 |
| MONITOR-04 | 03-02, 03-03 | Platform fund rebalancing alerts (weekly digest) | SATISFIED | Weekly timer in continuous.py lines 1401-1426 sends digest via `notifier.notify_text()`; `/api/rebalance` endpoint returns recommendations |
| OPTIMIZE-01 | 03-03 | Dynamic fee schedule updates without restart | BLOCKED | `reload_fee_rates()` updates config globals but scan modules hold stale `from config import` bindings — fee changes do NOT propagate to scan calculations |
| OPTIMIZE-02 | 03-03 | Automated backtest-to-config feedback loop | SATISFIED | `build_recommendations()` + `write_recommendations()` in backtest.py; nightly task wired in continuous.py |
| OPTIMIZE-03 | 03-03 | Priority execution lane for time-sensitive strategies | SATISFIED | `asyncio.PriorityQueue` in continuous.py with negated weights; StalePriceOpp (3.0) and ResolutionSnipeOpp (2.5) dequeue before Binary (2.0) and MarketMake (1.0) |
| OPTIMIZE-04 | 03-02 | Live bankroll tracking across all platforms | SATISFIED | `_DashboardState.platform_balances` populated at runtime by bankroll refresh in continuous.py; `/api/balances` exposes it |
| OPTIMIZE-05 | 03-02, 03-03 | Automated fund rebalancing recommendations | SATISFIED | `/api/rebalance` returns transfer recommendations with `current_pct`, `recommended_pct`, `transfer_amount` fields; `balancesChart` renders in dashboard |

---

## Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `scans/betfair.py:8` | `from config import BETFAIR_COMMISSION_RATE` — stale binding after reload | Blocker | Fee reload (OPTIMIZE-01) has no effect on Betfair scan calculations |
| `scans/gemini.py:7` | `from config import GEMINI_FEE_RATE` — stale binding after reload | Blocker | Fee reload has no effect on Gemini scan calculations |
| `scans/smarkets.py:7` | `from config import SMARKETS_COMMISSION_RATE` — stale binding after reload | Blocker | Fee reload has no effect on Smarkets scan calculations |
| `backtest.py:451` | `datetime.utcnow()` deprecated in Python 3.12+ | Info | Generates deprecation warning in test output; no functional impact yet |

---

## Test Suite Results

All 57 phase-3 tests pass (confirmed via `pytest tests/test_metrics_wiring.py tests/test_anomaly_alerting.py tests/test_dashboard_endpoints.py tests/test_priority_queue.py tests/test_fee_backtest.py`):

- `test_metrics_wiring.py` — 5/5 passed
- `test_anomaly_alerting.py` — 17/17 passed
- `test_dashboard_endpoints.py` — 19/19 passed
- `test_priority_queue.py` — 5/5 passed
- `test_fee_backtest.py` — 11/11 passed (includes 5 `TestFeeReload` + 6 `TestBacktestRecommendations`)

Note: `test_fee_backtest.py::TestFeeReload` tests verify that `config.reload_fee_rates()` correctly updates `config.BETFAIR_COMMISSION_RATE`. The tests pass because they test the config module in isolation. The wiring gap — that scan modules hold stale `from config import` bindings — is not tested and was not caught by the test suite.

---

## Human Verification Required

### 1. Dashboard Live Data Rendering

**Test:** Start `python scanner.py --continuous` and navigate to `http://localhost:8080/` in a browser.
**Expected:** Dashboard loads with strategy P&L horizontal bar chart (empty initially), platform balances doughnut chart, and both tables. After the bot runs scans, data populates. Charts update every 15 seconds.
**Why human:** Requires live continuous mode with real API credentials and a browser to confirm Chart.js renders correctly.

### 2. Priority Queue Latency Under Load

**Test:** Run in continuous mode with WebSocket feeds active and observe log output for priority queue execution timing.
**Expected:** Log lines show execution latency < 500ms for StalePriceOpp and ResolutionSnipeOpp opportunities. Warning logged when latency exceeds 500ms.
**Why human:** End-to-end timing requires real WebSocket feed load and executor throughput — cannot be measured with mocked unit tests.

### 3. Weekly Rebalance Digest Delivery

**Test:** Temporarily set `REBALANCE_DIGEST_INTERVAL=60` and `WEBHOOK_URL` to a test endpoint (e.g., webhook.site). Run continuous mode for 2 minutes.
**Expected:** Webhook fires with platform balance vs opportunity-flow summary text within 2 minutes.
**Why human:** Timer-based behavior requiring webhook delivery to an external endpoint.

---

## Gaps Summary

**1 gap blocking OPTIMIZE-01 (Dynamic fee schedule updates)**

`reload_fee_rates()` in `config.py` is correctly implemented and wired into the continuous mode scan loop (hourly call). However, the Python `from config import NAME` pattern used in `scans/betfair.py`, `scans/gemini.py`, and `scans/smarkets.py` creates a module-level name binding at import time. When `reload_fee_rates()` updates `config.BETFAIR_COMMISSION_RATE`, the scan modules' local names (`BETFAIR_COMMISSION_RATE`, `GEMINI_FEE_RATE`, `SMARKETS_COMMISSION_RATE`) still hold the original values.

Runtime test confirms: after `os.environ['BETFAIR_COMMISSION_RATE'] = '0.01'` and `config.reload_fee_rates()`, `config.BETFAIR_COMMISSION_RATE` is 0.01 but a previously-imported `from config import BETFAIR_COMMISSION_RATE` binding remains 0.03.

**Fix options (in order of preference):**

1. Change scan modules to use `import config` and reference `config.BETFAIR_COMMISSION_RATE` directly in the scan loop body — this reads the current module global on every call.
2. Have `reload_fee_rates()` also import the scan modules and update their module-level globals explicitly.
3. Have fee functions call `config.BETFAIR_COMMISSION_RATE` (attribute access) as their default rather than using a Python default parameter.

**All other goals are fully met:** per-strategy metrics (MONITOR-02), anomaly alerting (MONITOR-03), dashboard with live P&L (MONITOR-01), rebalancing alerts and recommendations (MONITOR-04, OPTIMIZE-05), priority execution queue (OPTIMIZE-03), backtest feedback loop (OPTIMIZE-02), and bankroll tracking (OPTIMIZE-04) are all wired, substantive, and covered by passing tests.

---

_Verified: 2026-03-21T10:15:00Z_
_Verifier: Claude (gsd-verifier)_
