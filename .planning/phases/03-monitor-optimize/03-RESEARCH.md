# Phase 3: Monitor & Optimize - Research

**Researched:** 2026-03-21
**Domain:** Python monitoring/observability wiring, dashboard enhancement, async task scheduling
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Dashboard & Monitoring**
- Per-strategy P&L breakdown + total on dashboard — existing `_DashboardState` tracks `daily_pnl`, add per-strategy from `decisions.jsonl`
- Wire `metrics.py` counters into executor + scans, expose Prometheus text format at `/metrics`
- Keep existing 15s polling via `__REFRESH_SECONDS__` in dashboard_ui.py
- Add `/api/balances` endpoint querying cached platform balances from continuous.py bankroll refresh (wired in Phase 1)

**Alerting & Anomaly Detection**
- Anomaly triggers: loss spike (>3x avg loss), consecutive failures (5+), daily loss at 80%/100% — extend existing AlertManager
- Detection within 60s — check after every trade execution, rate-limit 1 alert per type per 5 min (existing behavior)
- Weekly rebalancing digest via webhook — per-platform balance distribution vs opportunity flow, suggest moves
- Alert delivery via existing webhook (notifier.py) — auto-detects Slack/Discord/generic format

**Optimization & Automation**
- Backtest-to-config feedback loop: nightly backtest writes recommended thresholds to JSON file, continuous.py reads on next cycle. No auto-apply — human reviews via dashboard
- Priority execution lane: priority queue in continuous.py — resolution sniping and stale price get priority over market making and convergence (sorted by time-sensitivity, not profit)
- Dynamic fee schedule: hourly fee check via platform APIs, update fees.py runtime constants if changed, log changes
- Scope: build all infrastructure here, enable progressively in Go Live (Phase 4)

### Claude's Discretion
- Dashboard chart library choice (Chart.js already embedded in dashboard_ui.py)
- Exact priority queue implementation (heapq vs sorted list)
- Backtest recommendation JSON schema
- Rebalancing recommendation format and thresholds

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MONITOR-01 | Dashboard UI with live P&L and positions | Enhance `_DashboardState`, add per-strategy data from `decisions.jsonl`, wire into `dashboard_ui.py` Chart.js charts |
| MONITOR-02 | Per-strategy metrics | `metrics.py` MetricsCollector supports labeled counters/histograms; wire into `executor.py:execute()` with `strategy` label |
| MONITOR-03 | Anomaly alerting | `AlertManager` already has rate-limiting and severity levels; add `LOSS_SPIKE`, `ZERO_OPP_PERIOD` alert types |
| MONITOR-04 | Platform fund rebalancing alerts | `_check_platform_balance()` in continuous.py already exists but only logs; wire to `AlertManager` for weekly webhook digest |
| OPTIMIZE-01 | Dynamic fee schedule updates | `config.py` fee constants are module-level; reload from env vars at runtime without restart using `importlib.reload` pattern |
| OPTIMIZE-02 | Automated backtest-to-config feedback loop | `backtest.py` produces `BacktestResult`; add nightly scheduler task in `continuous.py`, write JSON to `DATA_DIR/backtest_recommendations.json` |
| OPTIMIZE-03 | Priority execution lane for time-sensitive strategies | `_execution_priority()` and `_PRIORITY_WEIGHTS` already exist in `continuous.py`; add `asyncio.PriorityQueue` for WS-triggered execution path |
| OPTIMIZE-04 | Live bankroll tracking across all platforms | `_fetch_balances("Cross")` and `update_bankroll()` already wired (Phase 1); expose via new `/api/balances` dashboard endpoint |
| OPTIMIZE-05 | Automated fund rebalancing recommendations | Extend `_check_platform_balance()` logic to compute specific transfer amounts; surface in dashboard via `/api/rebalance` endpoint |
</phase_requirements>

## Summary

Phase 3 is a wiring and enhancement phase, not a build phase. The infrastructure for every requirement already exists — `metrics.py`, `alerting.py`, `dashboard.py`, `backtest.py`, `position_sizer.py`, and `continuous.py` are all production-ready. The work is connecting them together and filling the remaining gaps.

The five major threads are: (1) dashboard enhancement (per-strategy P&L chart + balances panel in `dashboard_ui.py`), (2) metrics wiring into executor/scans with per-strategy labels, (3) anomaly alert type extensions to `AlertManager`, (4) nightly backtest task + JSON recommendation file in `continuous.py`, and (5) dynamic fee reloading without restart. Priority execution already has the scaffold (`_execution_priority`, `_PRIORITY_WEIGHTS`) — the gap is an `asyncio.PriorityQueue` in the WS-triggered path to guarantee sub-500ms execution for time-sensitive strategies.

**Primary recommendation:** All 9 requirements can be satisfied by targeted additions to existing files. No new modules are needed except possibly a `fee_refresher.py` for the hourly fee schedule check. Structure work in two plans: (1) metrics + alerting wiring (MONITOR-02, MONITOR-03, MONITOR-04) and (2) dashboard + optimization (MONITOR-01, OPTIMIZE-01 through OPTIMIZE-05).

## Standard Stack

### Core (all already installed)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python stdlib | 3.10+ | asyncio, heapq, threading, importlib | No new deps for priority queue or fee reload |
| Chart.js | 4.4.7 (CDN) | Dashboard charts | Already embedded in `dashboard_ui.py` |
| pytest | project requirement | Tests | Already in `requirements-dev.txt` |

### No New Dependencies Required
All Phase 3 work uses existing stdlib + already-installed packages. The priority queue uses `heapq` (stdlib). Fee reloading uses `importlib.reload` (stdlib). Nightly scheduler uses `asyncio` sleep loop (already in `continuous.py`).

**Installation:** None — no new packages required.

## Architecture Patterns

### Recommended Project Structure (no changes)
```
polymarket-arb-scanner/
├── executor.py          # Add per-strategy metrics.inc() calls
├── continuous.py        # Add nightly backtest task, priority queue, fee refresh timer
├── alerting.py          # Add LOSS_SPIKE, ZERO_OPP_PERIOD alert types
├── dashboard.py         # Add /api/balances, /api/rebalance endpoints
├── dashboard_ui.py      # Add per-strategy P&L chart, balances panel
├── config.py            # Add FEE_REFRESH_INTERVAL, BACKTEST_RUN_INTERVAL
└── DATA_DIR/
    └── backtest_recommendations.json   # Written nightly by backtest task
```

### Pattern 1: Per-Strategy Metrics via Labels
**What:** Use the existing `metrics.inc(name, labels={"strategy": opp_type})` pattern already supported by `MetricsCollector`. Labels are a dict; `metrics.py` converts them to a sorted tuple for storage and Prometheus `{key="value"}` syntax for output.
**When to use:** Every `executor.execute()` call — increment counters and histograms with the strategy type from `opp["type"]`.
**Example:**
```python
# Source: metrics.py lines 163-175 (existing API)
# In executor.py execute() success path:
if _metrics:
    _metrics.inc("trades_executed", labels={"strategy": opp_type})
    _metrics.observe("execution_latency_seconds", labels={"strategy": opp_type}, value=elapsed)

# In executor.py rejection path:
if _metrics:
    _metrics.inc("risk_rejections", labels={"strategy": opp_type})
```

### Pattern 2: AlertManager Extension (New Alert Types)
**What:** Add new `AlertType` enum values; call `alert_manager.alert()` from executor after each trade decision. Rate-limiting and webhook dispatch are already implemented — just add new types.
**When to use:** After every `execute()` call (loss spike detection), and in `continuous.py` scan loop (zero-opportunity period detection).
**Example:**
```python
# Source: alerting.py lines 16-22 (existing AlertType enum)
class AlertType(str, Enum):
    # ... existing ...
    LOSS_SPIKE = "LOSS_SPIKE"          # New: >3x average loss
    ZERO_OPP_PERIOD = "ZERO_OPP_PERIOD"  # New: sustained zero opps

# New method on AlertManager:
def check_loss_spike(self, loss_amount: float) -> bool:
    """Fire if single loss exceeds 3x rolling average loss."""
    # Track rolling window of losses, fire if spike detected
```

### Pattern 3: asyncio.PriorityQueue for WS Execution
**What:** Replace direct `executor.execute(opp)` in the WS callback with a push to `asyncio.PriorityQueue`. A dedicated consumer coroutine drains the queue, executing highest-priority items first. Priority = `-_execution_priority(opp)` (negate for min-heap semantics).
**When to use:** Only for the WebSocket-triggered execution path in `continuous.py`. Scan-cycle batch execution already sorts by `_execution_priority` at line 1137.
**Example:**
```python
# Source: continuous.py lines 459-476 (_execution_priority already defined)
import heapq
import asyncio

# In run_continuous():
_priority_queue = asyncio.PriorityQueue()

async def _priority_consumer():
    while not shutdown_event.is_set():
        priority, opp = await _priority_queue.get()
        # Execute with 500ms deadline tracking
        start = time.monotonic()
        executor.execute(opp)
        elapsed = time.monotonic() - start
        if elapsed > 0.5:
            logger.warning("Priority execution exceeded 500ms: %.3fs for %s",
                           elapsed, opp.get("type"))
        _priority_queue.task_done()

# In on_price_update() WS callback:
priority = -_execution_priority(opp)  # negate: lower value = higher priority
asyncio.run_coroutine_threadsafe(
    _priority_queue.put((priority, opp)), loop)
```

### Pattern 4: Dynamic Fee Reload Without Restart
**What:** Fee constants in `config.py` are module-level floats set at import time. To support runtime updates, add a `reload_fee_rates()` function in `fees.py` (or `config.py`) that reads env vars fresh and updates module globals. Call hourly from a `continuous.py` timer.
**When to use:** Hourly check — only update if value changed, log changes.
**Example:**
```python
# In config.py — new function:
def reload_fee_rates() -> dict[str, tuple[float, float]]:
    """Re-read fee-related env vars and update module globals.
    Returns dict of {var_name: (old_value, new_value)} for changed vars.
    """
    global BETFAIR_COMMISSION_RATE, SMARKETS_COMMISSION_RATE, GEMINI_FEE_RATE
    changes = {}
    for name, attr in [
        ("BETFAIR_COMMISSION_RATE", "BETFAIR_COMMISSION_RATE"),
        ("SMARKETS_COMMISSION_RATE", "SMARKETS_COMMISSION_RATE"),
        ("GEMINI_FEE_RATE", "GEMINI_FEE_RATE"),
    ]:
        new_val = _env_float(name, str(globals()[attr]))
        if abs(new_val - globals()[attr]) > 1e-9:
            changes[attr] = (globals()[attr], new_val)
            globals()[attr] = new_val
    return changes
```

### Pattern 5: Nightly Backtest Task
**What:** `asyncio.sleep` loop in `continuous.py` that runs `backtest.py`'s `BacktestEngine` nightly (or at configurable interval). Writes recommendations to `DATA_DIR/backtest_recommendations.json`. Dashboard reads this file on load.
**When to use:** Once per 24h (configurable via `BACKTEST_RUN_INTERVAL` env var). Runs in background coroutine so it doesn't block scan loop.
**Example:**
```python
# Recommendation JSON schema (Claude's discretion — this is the design):
{
    "generated_at": "2026-03-21T02:00:00Z",
    "period_days": 7,
    "total_trades": 142,
    "win_rate": 0.73,
    "recommended": {
        "MIN_NET_ROI": 0.008,
        "FUZZY_MATCH_THRESHOLD": 75,
        "MIN_PROFIT_THRESHOLD": 0.006
    },
    "current": {
        "MIN_NET_ROI": 0.0,
        "FUZZY_MATCH_THRESHOLD": 72,
        "MIN_PROFIT_THRESHOLD": 0.005
    },
    "by_strategy": {
        "Binary": {"win_rate": 0.91, "avg_profit": 0.012},
        "Cross": {"win_rate": 0.68, "avg_profit": 0.009}
    }
}
```

### Pattern 6: Per-Strategy P&L from decisions.jsonl
**What:** `decisions.jsonl` already records every executor decision with `opp_type` and `net_profit` fields. A new `db.get_strategy_pnl()` method can aggregate this — or the dashboard's `/api/strategies` endpoint (already exists via `db.get_opportunity_stats_by_type()`) can be extended.
**When to use:** Dashboard `/api/strategy-pnl` endpoint (new) reads `decisions.jsonl` or extends existing `db.get_opportunity_stats_by_type()` query.

The existing `db.get_opportunity_stats_by_type()` already returns per-strategy counts and profits from the `opportunities` table. The gap is wiring actual P&L (from `trades` table) rather than just expected P&L (from opportunity detection).

### Anti-Patterns to Avoid
- **Module-level metrics calls outside of conditional check:** Every `metrics.inc()` call MUST be inside `if _metrics:` guard (pattern already established in `continuous.py` and `executor.py`)
- **Blocking asyncio loop with synchronous backtest:** Run backtest in `ThreadPoolExecutor` via `loop.run_in_executor()`, not directly in coroutine
- **importlib.reload for fee update:** Do NOT use `importlib.reload(config)` — it reinitializes ALL config including credentials. Use targeted `globals()` update or a dedicated reload function
- **Replacing sort with PriorityQueue for scan-cycle path:** The scan-cycle path already sorts correctly at line 1137. Only the WS-triggered path needs the queue
- **Storing per-strategy state in DashboardState:** Keep per-strategy data in the DB (decisions.jsonl / trades table) and serve via API endpoint — don't cache in memory in `_DashboardState`

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Prometheus metrics | Custom serializer | `metrics.get_prometheus_text()` (already exists) | Already produces valid exposition format |
| Priority queue | Custom sorted list | `asyncio.PriorityQueue` (stdlib) | Thread-safe, asyncio-native, no external dep |
| Rate-limited alerting | Custom throttle | `AlertManager.alert()` (existing) | Already has per-type rate limiting |
| Webhook delivery | Custom HTTP | `notifier._send_raw()` (existing) | Already handles Slack/Discord/generic |
| Per-strategy aggregation | In-memory accumulators | `db.get_opportunity_stats_by_type()` (existing) | SQLite aggregation is more reliable across restarts |
| Scheduling | External scheduler (APScheduler, etc.) | `asyncio.sleep` loop in `_continuous_loop()` | No new dep; continuous.py already uses this pattern for bankroll refresh and snapshot timers |

**Key insight:** Every scheduling, alerting, and persistence primitive needed by Phase 3 already exists in the codebase. The only real new code is glue logic and dashboard HTML sections.

## Common Pitfalls

### Pitfall 1: asyncio/threading boundary in priority queue
**What goes wrong:** `asyncio.PriorityQueue` is not thread-safe for direct `.put()` from threading threads (WS callbacks run on daemon threads).
**Why it happens:** The WS feed callbacks (`on_price_update`) run in threading threads, not the asyncio event loop.
**How to avoid:** Use `asyncio.run_coroutine_threadsafe(_priority_queue.put(item), loop)` from threading callbacks. The event loop reference must be captured before starting the WS threads.
**Warning signs:** `RuntimeError: Non-thread-safe operation invoked on an event loop` or items silently dropped.

### Pitfall 2: decisions.jsonl per-strategy aggregation is slow at scale
**What goes wrong:** Reading all of `decisions.jsonl` on every dashboard refresh becomes slow after days of data.
**Why it happens:** JSONL is append-only; no index.
**How to avoid:** Prefer `db.get_opportunity_stats_by_type()` (SQLite, indexed) for the dashboard `/api/strategy-pnl` endpoint. Use `decisions.jsonl` only for last-N lookups or for the anomaly detector's rolling window (keep in-memory via `deque`).
**Warning signs:** Dashboard API response time > 200ms.

### Pitfall 3: Fee reload clobbering non-fee config
**What goes wrong:** Calling `importlib.reload(config)` to refresh fee rates also reinitializes `DRY_RUN`, `EXECUTION_MODE`, and all credentials.
**Why it happens:** Full module reload re-runs all `_env_*` calls.
**How to avoid:** Implement `reload_fee_rates()` as a targeted function that only updates specific float globals. Never call `importlib.reload(config)`.
**Warning signs:** `DRY_RUN` flips to `true` after a fee reload, or API key references break.

### Pitfall 4: Backtest blocking the scan loop
**What goes wrong:** Running the full `BacktestEngine` replay (can take 10-30s on large snapshot DBs) blocks the asyncio loop, missing WS updates.
**Why it happens:** `backtest.py` runs synchronously; calling it directly in a coroutine stalls the loop.
**How to avoid:** `await loop.run_in_executor(None, run_backtest_and_write)` — runs in thread pool, yields control to event loop.
**Warning signs:** Scan cycle takes >30s during nightly backtest window; WS feed stale warnings.

### Pitfall 5: Loss spike detection false positives on startup
**What goes wrong:** When the rolling average loss window is empty (first few trades), any loss looks like a spike.
**Why it happens:** Division by zero or inflated ratio when sample count < minimum window.
**How to avoid:** Require at least N=10 trades in the rolling window before enabling spike detection.
**Warning signs:** `LOSS_SPIKE` alert fires on the first or second loss of the session.

### Pitfall 6: Weekly rebalancing digest firing every scan
**What goes wrong:** Rebalancing check in continuous.py (every 5 scans) is too frequent; weekly digest becomes per-minute.
**Why it happens:** The existing `_check_platform_balance()` check runs every 5 scans. Weekly digest needs a separate timer.
**How to avoid:** Track `_last_rebalance_digest_time` (separate from the per-scan balance check). Fire the digest only if 7 days have elapsed since the last one. The per-scan check can still fire ad-hoc urgent alerts via `AlertManager` rate limiting.
**Warning signs:** Webhook log shows rebalancing digest messages every 150 seconds.

## Code Examples

Verified patterns from existing code:

### MetricsCollector — labeled counter increment
```python
# Source: metrics.py lines 163-166
# The API supports labels dict; converted to tuple internally
metrics.inc("trades_executed", labels={"strategy": "Binary"}, value=1)
metrics.observe("execution_latency_seconds", labels={"strategy": "Binary"}, value=0.12)
```

### Prometheus output for labeled metrics
```
# HELP arb_trades_executed Counter metric
# TYPE arb_trades_executed counter
arb_trades_executed{strategy="Binary"} 14
arb_trades_executed{strategy="Cross"} 7
arb_trades_executed{strategy="KalshiBinary"} 3
```

### AlertManager — adding a new alert type
```python
# Source: alerting.py lines 16-22 (AlertType enum)
# Pattern for new type:
class AlertType(str, Enum):
    LOSS_SPIKE = "LOSS_SPIKE"  # fire when single loss > 3x rolling avg

# New AlertManager method:
def check_loss_spike(self, loss_amount: float) -> bool:
    if len(self._trade_losses) < 10:
        return False  # Pitfall 5 guard
    avg = sum(self._trade_losses) / len(self._trade_losses)
    if avg > 0 and loss_amount > 3 * avg:
        return self.alert(
            AlertType.LOSS_SPIKE, Severity.CRITICAL,
            f"Loss spike: ${loss_amount:.2f} (avg ${avg:.2f})",
            {"loss": loss_amount, "avg": avg, "ratio": loss_amount / avg},
        )
    return False
```

### asyncio.PriorityQueue — thread-safe push from WS callback
```python
# Source: continuous.py lines 459-476 for _execution_priority
# Canonical pattern for WS thread -> asyncio queue:
_priority_queue: asyncio.PriorityQueue | None = None
_event_loop: asyncio.AbstractEventLoop | None = None

def on_price_update(platform, ticker, new_price):
    opps = opp_index.lookup(platform, ticker)
    for opp in opps:
        priority = -_execution_priority(opp)  # negate for min-heap
        if _priority_queue and _event_loop:
            asyncio.run_coroutine_threadsafe(
                _priority_queue.put((priority, opp)), _event_loop)
```

### Dashboard endpoint — /api/balances pattern
```python
# Source: dashboard.py lines 408-419 (_handle_platforms pattern)
def _handle_balances(self):
    """GET /api/balances — cached platform balances from last bankroll refresh."""
    from dashboard import state as _state
    balances = getattr(_state, "platform_balances", {})
    _send_json(self, {
        "balances": balances,
        "total": sum(v for v in balances.values() if isinstance(v, (int, float))),
        "last_updated": getattr(_state, "last_bankroll_refresh", None),
    })
```

### Backtest task — asyncio non-blocking pattern
```python
# Source: continuous.py line 724 (_last_bankroll_refresh timer pattern)
_last_backtest_run = 0.0
_backtest_run_interval = 86400.0  # 24h, configurable

async def _run_backtest_task(loop):
    """Non-blocking nightly backtest. Writes DATA_DIR/backtest_recommendations.json."""
    def _sync_backtest():
        from backtest import BacktestEngine
        engine = BacktestEngine()
        result = engine.run()
        recommendations = _build_recommendations(result)
        path = os.path.join(DATA_DIR, "backtest_recommendations.json")
        with open(path, "w") as f:
            json.dump(recommendations, f, indent=2)
        return recommendations

    await loop.run_in_executor(None, _sync_backtest)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Static fee constants | Runtime-reloadable via env var without restart | Phase 3 | Fee promotions take effect within one scan cycle |
| Sort-then-execute scan batch | PriorityQueue for WS path | Phase 3 | Time-sensitive ops execute within 500ms guaranteed |
| Manual rebalancing checks | Weekly automated digest via webhook | Phase 3 | Capital allocation drift is caught and actioned |
| Ad-hoc loss detection (streak only) | Loss spike + streak + daily limit | Phase 3 | Covers more anomaly patterns |

**Already implemented (not to rebuild):**
- `_execution_priority()` + `_PRIORITY_WEIGHTS` — scan-cycle sorting exists; only WS path needs queue
- `_check_platform_balance()` — per-scan balance check exists; weekly digest timer is the addition
- `/metrics` endpoint — already wired in `dashboard.py` to `metrics.get_prometheus_text()`; the gap is per-strategy labels in executor

## Open Questions

1. **Where to store per-strategy P&L for dashboard**
   - What we know: `db.get_opportunity_stats_by_type()` returns per-type count and expected profit from opportunities table. `decisions.jsonl` has actual realized P&L per trade.
   - What's unclear: Whether to extend the existing DB query or read `decisions.jsonl` directly.
   - Recommendation: Extend `db.get_strategy_pnl()` as a new method that JOINs `trades` and `opportunities` tables — gives accurate realized P&L per strategy, works after restart, and doesn't require reading JSONL.

2. **Backtest recommendation thresholds — which params to recommend**
   - What we know: `BacktestResult` has `total_pnl`, `win_rate`, `max_drawdown`, `sharpe_ratio`, `trades_by_type`.
   - What's unclear: Which config params to recommend (MIN_NET_ROI, FUZZY_MATCH_THRESHOLD, MIN_PROFIT_THRESHOLD are confirmed; others TBD).
   - Recommendation: Start with these 3 + per-strategy enable/disable recommendations. The JSON schema in Pattern 5 is the plan.

3. **Loss spike rolling window definition**
   - What we know: The existing loss streak uses a `deque(maxlen=100)` of bool results.
   - What's unclear: Should the spike window be time-based (last 1h) or count-based (last 20 trades)?
   - Recommendation: Count-based (last 20 trades) — simpler, consistent with existing streak detection pattern.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (version in requirements-dev.txt) |
| Config file | none — path insertion in each test file |
| Quick run command | `pytest tests/test_alerting.py tests/test_metrics.py -x -v` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MONITOR-01 | `/api/strategy-pnl` returns per-strategy breakdown | unit | `pytest tests/test_dashboard.py -k strategy_pnl -x` | ❌ Wave 0 |
| MONITOR-01 | `/api/balances` returns platform balances dict | unit | `pytest tests/test_dashboard.py -k balances -x` | ❌ Wave 0 |
| MONITOR-02 | `trades_executed` counter includes strategy label | unit | `pytest tests/test_metrics.py -k strategy_label -x` | ❌ Wave 0 |
| MONITOR-02 | `execution_latency_seconds` histogram labeled by strategy | unit | `pytest tests/test_metrics.py -k execution_latency -x` | ❌ Wave 0 |
| MONITOR-03 | `check_loss_spike()` fires when loss > 3x avg | unit | `pytest tests/test_alerting.py -k loss_spike -x` | ❌ Wave 0 |
| MONITOR-03 | Loss spike does not fire with < 10 trade window | unit | `pytest tests/test_alerting.py -k loss_spike_min_window -x` | ❌ Wave 0 |
| MONITOR-03 | Zero-opp period alert fires after N consecutive empty scans | unit | `pytest tests/test_alerting.py -k zero_opp -x` | ❌ Wave 0 |
| MONITOR-04 | Weekly digest sends rebalancing recommendations via notifier | unit | `pytest tests/test_continuous.py -k weekly_rebalance -x` | ❌ Wave 0 |
| OPTIMIZE-01 | `reload_fee_rates()` updates module globals from env | unit | `pytest tests/test_config.py -k reload_fee -x` | ❌ Wave 0 |
| OPTIMIZE-01 | Fee reload does not clobber DRY_RUN or credentials | unit | `pytest tests/test_config.py -k reload_fee_safe -x` | ❌ Wave 0 |
| OPTIMIZE-02 | Nightly backtest writes valid JSON to recommendations file | unit | `pytest tests/test_backtest.py -k recommendations -x` | ❌ Wave 0 |
| OPTIMIZE-03 | WS-triggered opps execute within 500ms for priority types | integration | `pytest tests/test_continuous.py -k priority_queue_500ms -x` | ❌ Wave 0 |
| OPTIMIZE-03 | StalePriceOpp executes before MarketMake in queue | unit | `pytest tests/test_continuous.py -k priority_ordering -x` | ❌ Wave 0 |
| OPTIMIZE-04 | `/api/balances` reflects most recent `_fetch_balances` result | unit | `pytest tests/test_dashboard.py -k balances_refresh -x` | ❌ Wave 0 |
| OPTIMIZE-05 | Rebalancing recommendation includes transfer amount and ROI impact | unit | `pytest tests/test_continuous.py -k rebalance_recommendation -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_alerting.py tests/test_metrics.py tests/test_dashboard.py -x`
- **Per wave merge:** `pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_alerting.py` — new test class for `LOSS_SPIKE`, `ZERO_OPP_PERIOD` types (add to existing file)
- [ ] `tests/test_metrics.py` — new test class for labeled counters/histograms per strategy (add to existing file)
- [ ] `tests/test_dashboard.py` — new test class for `/api/balances`, `/api/strategy-pnl`, `/api/rebalance` endpoints
- [ ] `tests/test_config.py` — new test class for `reload_fee_rates()` function
- [ ] `tests/test_backtest.py` — new test for recommendation JSON output (may add to existing `test_backtest.py`)
- [ ] `tests/test_continuous.py` — new test classes for priority queue ordering and weekly digest timer

## Sources

### Primary (HIGH confidence)
- Direct code inspection of `dashboard.py`, `metrics.py`, `alerting.py`, `continuous.py`, `executor.py`, `backtest.py`, `config.py`, `position_sizer.py` — all existing APIs verified by reading source
- `dashboard.py` lines 244-253 — existing route table (confirmed no `/api/balances` or `/api/strategy-pnl` yet)
- `continuous.py` lines 442-476 — `_PRIORITY_WEIGHTS` and `_execution_priority()` confirmed present
- `continuous.py` lines 1137 — sort-only approach confirmed; WS path does not yet use queue
- `alerting.py` lines 16-22 — `AlertType` enum; `LOSS_SPIKE` and `ZERO_OPP_PERIOD` not yet present
- `metrics.py` lines 129-157 — pre-registered metric names; per-strategy labels supported but not used
- `executor.py` lines 19-27 — conditional `_metrics` import pattern confirmed; labels not yet passed

### Secondary (MEDIUM confidence)
- `continuous.py` lines 479-538 — `_check_platform_balance()` confirmed present but only logs/notifies without AlertManager; weekly digest timer not present

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all code read directly, no external sources needed
- Architecture: HIGH — all integration points confirmed by code inspection
- Pitfalls: HIGH — identified from code structure and established patterns in existing phase summaries

**Research date:** 2026-03-21
**Valid until:** 2026-04-21 (stable codebase; only changes if Phase 3 work modifies these files)
