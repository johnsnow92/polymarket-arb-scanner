# Architecture Research

**Domain:** Prediction market arbitrage bot — strategy expansion & profitability features
**Researched:** 2026-04-01
**Confidence:** HIGH (based on direct codebase inspection + verified community patterns)

---

## Standard Architecture

### Current System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATION SHELL                           │
│   scanner.py (re-export facade)                                   │
│   ┌──────────────┐   ┌──────────────────┐   ┌────────────────┐   │
│   │   cli.py     │   │  continuous.py   │   │  display.py    │   │
│   │ (entry+init) │   │ (async loop+WS)  │   │ (output fmt)   │   │
│   └──────┬───────┘   └────────┬─────────┘   └────────────────┘   │
└──────────┼────────────────────┼─────────────────────────────────-─┘
           │                    │
┌──────────▼────────────────────▼──────────────────────────────────┐
│                        SCAN LAYER                                  │
│   scans/ package — two-stage: mid-price scan → CLOB refinement    │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐    │
│   │ binary   │ │ kalshi   │ │ cross    │ │  14 other modules │    │
│   │ negrisk  │ │ stale    │ │ triangul.│ │  convergence, mm  │    │
│   └──────────┘ └──────────┘ └──────────┘ └──────────────────┘    │
│   Shared: helpers.py, matcher.py, fees.py                          │
└───────────────────────────────┬──────────────────────────────────┘
                                │  opportunity dicts (plain Python)
┌───────────────────────────────▼──────────────────────────────────┐
│                      EXECUTION LAYER                               │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  ArbitrageExecutor                                           │  │
│  │  kill switch → cooldown → idempotency → revalidate          │  │
│  │  → risk gate → gas gate → size → _build_legs → fill         │  │
│  └──────────┬─────────────┬────────────────┬────────────────┐   │  │
│             │             │                │                │   │  │
│         RiskManager   TradeDB (SQLite   hedger.py      metrics   │  │
│                         WAL mode)                                  │
└──────────────────────────────────────────────────────────────────┘

PLATFORM APIs (10 modules):  polymarket | kalshi | betfair | smarkets
                              sxbet | matchbook | gemini | ibkr
                              metaculus | manifold  (read-only signals)

SUPPORTING MODULES:
  market_maker.py  position_sizer.py  signal_aggregator.py
  price_tracker.py  event_monitor.py  gas_monitor.py
  snapshot.py  backtest.py  recovery.py
  dashboard.py  alerting.py  metrics.py  notifier.py
```

### Component Responsibilities

| Component | Responsibility | Integration Point for New Work |
|-----------|---------------|-------------------------------|
| `scans/` package | Detect opportunities, return dicts | Add new scan modules here |
| `scans/__init__.py` | Export registry for all scan functions | Add new scan exports here |
| `executor.py:_build_legs()` | Convert opp dict to platform legs | Add new `elif opp_type ==` branch |
| `executor.py:_revalidate()` | Re-price opp before execution | Add new revalidation case |
| `continuous.py:OpportunityIndex` | WS → opp mapping for fast lookup | Add new `_extract_keys()` cases |
| `continuous.py:run_continuous()` | Main scan dispatch loop | Wire new scans into scan cycle |
| `cli.py:_run_oneshot()` | One-shot scan dispatch | Wire new scans here too |
| `risk_manager.py:RiskManager` | Gate execution by risk rules | Add skip sets for new types |
| `db.py:TradeDB` | SQLite WAL persistence | Schema migrations only |
| `position_sizer.py` | Kelly + strategy-aware sizing | Add new type prefix sets |
| `metrics.py` | Prometheus counters/gauges | Add new strategy metrics |
| `dashboard.py:_DashboardState` | Live state for HTTP dashboard | Add new state fields |
| `fees.py` | Net profit calculators | Add new fee functions |

---

## Recommended Project Structure (v2.0 additions)

```
polymarket-arb-scanner/
├── scans/                      # EXISTING — add new modules here
│   ├── order_flow.py           # NEW: order flow imbalance analysis
│   ├── news_driven.py          # NEW: news-triggered directional trades
│   └── fee_routing.py          # NEW: cross-platform fee path optimizer
├── pnl_tracker.py              # NEW: per-strategy P&L attribution engine
├── strategy_registry.py        # NEW: strategy metadata + performance catalog
├── news_monitor.py             # NEW: news API polling + event detection
├── rebalancer.py               # NEW: platform fund rebalancing logic
├── db.py                       # MODIFY: add strategy_pnl + equity_snapshots tables
├── executor.py                 # MODIFY: _build_legs() + _revalidate() new cases
├── continuous.py               # MODIFY: wire new scans, add MM order loop
├── metrics.py                  # MODIFY: add per-strategy P&L gauges
├── dashboard.py                # MODIFY: add per-strategy P&L state
├── risk_manager.py             # MODIFY: add skip/limit sets for new types
└── position_sizer.py           # MODIFY: add prefix sets for new types
```

### Structure Rationale

- **New scan modules in `scans/`:** Follows the two-stage pattern. No changes to `__init__.py` or `cli.py` dispatch until the module is wire-ready, preventing half-baked integrations.
- **`pnl_tracker.py` as separate module:** P&L attribution is query logic over `trades` + `positions` tables. Keeps `db.py` as pure persistence (schema + CRUD). PnL queries belong separately to avoid inflating the 350+ line `db.py`.
- **`strategy_registry.py`:** Centralizes strategy metadata (name, layer, risk class, sizing mode) so `position_sizer.py`, `metrics.py`, `dashboard.py`, and the new `pnl_tracker.py` share one source of truth instead of independent prefix-matching tuples.
- **`news_monitor.py` alongside `event_monitor.py`:** `event_monitor.py` handles Metaculus divergence signals. `news_monitor.py` handles raw news APIs (GDELT, NewsAPI.ai, Google News RSS) for faster breaking-news detection. Separation keeps responsibilities clear.

---

## Architectural Patterns

### Pattern 1: The Opportunity Dict Contract

**What:** All opportunities flow through the system as plain Python dicts with a stable key schema. New strategies must conform to this schema.

**When to use:** Every new scan module. No exceptions.

**Required keys:**
```python
{
    "type": str,           # Unique type string, used as dispatcher key
    "market": str,         # Human-readable market name
    "prices": str,         # Human-readable price summary
    "total_cost": str,     # "$X.XX" — total capital required
    "net_profit": float,   # Expected net profit after fees
    "net_roi": float,      # net_profit / total_cost
    # Internal (prefixed with _):
    "_clob_depth": float,  # Available depth at ask
    "_market_key": str,    # Dedup key (used by idempotency + risk)
    # Type-specific _* keys follow
}
```

**Why:** `executor.py:_build_legs()`, `risk_manager.py:check()`, `position_sizer.py`, `OpportunityIndex._extract_keys()`, and `snapshot.py` all read from this dict. Deviating breaks five downstream consumers simultaneously.

**Trade-offs:** Adding new type-specific fields is cheap (just prefix with `_`). The constraint is on the standard fields — changing their semantics requires auditing all consumers.

### Pattern 2: Two-Stage Scan (Mid-Price → CLOB Refinement)

**What:** Every scan module runs a fast mid-price pass to find candidates, then a slower CLOB-accurate pass to confirm. Only confirmed candidates are returned.

**When to use:** Any scan that accesses exchange order books.

**Example structure:**
```python
def scan_order_flow(markets: list, *clients) -> list[dict]:
    """Stage 1: Fast filter using mid prices."""
    candidates = []
    for market in markets:
        if _quick_filter(market):
            candidates.append(market)

    """Stage 2: CLOB refinement on candidates only."""
    return _refine_order_flow_with_clob(candidates, *clients)

def _refine_order_flow_with_clob(candidates, *clients) -> list[dict]:
    """Confirm against actual ask/bid prices. Drop false positives."""
    ...
```

**Trade-offs:** Adds latency for the refinement step. Without it, false positives flood the executor and cause wasted API calls during revalidation.

### Pattern 3: _build_legs() Dispatcher Extension

**What:** Adding a new opportunity type requires exactly one new `elif` branch in `executor.py:_build_legs()` and one new case in `_revalidate()`.

**When to use:** Every new execution-capable opportunity type.

**Minimal addition:**
```python
elif opp_type == "OrderFlowImbalance":
    # Build legs for order flow imbalance trade
    legs = self._build_directional_legs(opportunity, size)
    # OR if it has unique leg structure:
    legs = [
        {"platform": opportunity.get("_platform", ""),
         "side": opportunity.get("_direction", "BUY"),
         "price": opportunity.get("_entry_price", 0),
         "_token_id": opportunity.get("_token_id", "")},
    ]
```

**Key constraint:** The dispatcher is already 300+ lines. New branches should delegate to shared helpers (`_build_directional_legs`, `_build_mm_legs`) wherever possible rather than inlining platform-specific logic.

### Pattern 4: Per-Strategy P&L Attribution via strategy_layer Field

**What:** Add a `strategy_layer` integer (1-5) and `strategy_type` string to every opportunity logged to `db.opportunities`. This enables grouping by layer/type without schema joins.

**When to use:** Apply to `db.log_opportunity()` immediately. Retrofit is a single schema migration.

**Proposed schema migration for `db.py`:**
```sql
-- Migration: add strategy attribution columns
ALTER TABLE opportunities ADD COLUMN strategy_layer INTEGER;
ALTER TABLE opportunities ADD COLUMN strategy_type TEXT;

-- New table: per-strategy equity snapshots (hourly/daily)
CREATE TABLE IF NOT EXISTS strategy_equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    cumulative_pnl REAL NOT NULL,
    trade_count INTEGER NOT NULL,
    win_count INTEGER NOT NULL,
    total_roi REAL
);
CREATE INDEX IF NOT EXISTS idx_strategy_equity_type
    ON strategy_equity(strategy_type, timestamp);
```

**Trade-offs:** Adds two nullable columns to `opportunities` (backward compatible via `ALTER TABLE`). The `strategy_equity` table replaces manual P&L queries for the dashboard's strategy leaderboard.

### Pattern 5: Market Making as a Parallel Loop (Not a Scan)

**What:** Market making is fundamentally different from scan-based strategies. It runs a continuous quote-refresh loop alongside the scan cycle, not as a one-off scan result.

**When to use:** Market making (Layer 3) integration.

**Integration point in `continuous.py`:**
```python
# In run_continuous():
if config.MM_ENABLED:
    mm_task = asyncio.create_task(
        _run_mm_loop(mm_engine, executor, price_cache)
    )

async def _run_mm_loop(mm_engine, executor, price_cache):
    """MM quote refresh loop — runs independently of scan cycle."""
    while True:
        mm_engine.refresh_quotes(price_cache)
        await asyncio.sleep(config.MM_REFRESH_INTERVAL)
```

**Trade-offs:** The MM loop needs its own asyncio task because it refreshes quotes on a tighter interval (10-30s) than the full rescan interval (60s+). Running MM as a scan-and-execute would cause quote staleness. The existing `market_maker.py:MarketMaker.run()` method already implements this pattern — it just needs to be wired into the asyncio event loop.

### Pattern 6: News Monitor as an Async Task

**What:** News API polling runs as a background asyncio task, updating a shared signal cache. Scans read from the cache during their execution rather than calling APIs themselves.

**When to use:** Any news-driven scan (`news_driven.py`).

**Integration approach:**
```python
# news_monitor.py
class NewsMonitor:
    def __init__(self, api_key: str, cache_ttl: float = 300.0):
        self._signals: dict[str, dict] = {}  # {topic: {score, ts, articles}}
        self._lock = threading.Lock()

    def get_signal(self, market_title: str) -> dict | None:
        """Return cached news signal for a market, None if no match."""
        ...

# In continuous.py:
news_monitor = NewsMonitor(api_key=NEWS_API_KEY)
news_task = asyncio.create_task(news_monitor.run_polling_loop())
# Scans consume: news_monitor.get_signal(market_title)
```

**Trade-offs:** Cache-based approach means maximum 5-minute news lag (acceptable for day-scale resolution sniping). Direct API calls per scan would be too slow and hit rate limits. The pattern mirrors how `signal_aggregator.py` handles Metaculus/Manifold signals.

---

## Data Flow

### New Strategy Integration Flow

```
New scan module (scans/order_flow.py)
    ↓ returns list[dict] with standard + _* keys
cli.py:_run_oneshot()  /  continuous.py:run_continuous()
    ↓ merged into opportunities list
capital_efficiency_score() — ranks by ROI * depth
    ↓ sorted opportunity list
executor.py:execute()
    ↓ kill switch → cooldown → idempotency → revalidate
    ↓ _revalidate_order_flow() — confirms signal still valid
    ↓ risk_manager.check() — position limits, balance
    ↓ position_sizer.size_for_opportunity() — Kelly or fixed
    ↓ _build_legs() — opp dict → execution legs list
    ↓ _execute_legs() — place orders on platforms
    ↓ db.log_opportunity(strategy_layer=4) — attribution
    ↓ db.log_trade() — individual legs
    ↓ metrics.inc("trades_executed", {type: "OrderFlow"})
    ↓ dashboard_state.update()
```

### Per-Strategy P&L Data Flow

```
db.trades (fill_price, platform, side)
    ↓ JOIN db.opportunities (strategy_type, strategy_layer)
pnl_tracker.py:compute_strategy_pnl()
    ↓ aggregated by strategy_type
strategy_equity table — hourly snapshots
    ↓ read by
dashboard.py — strategy leaderboard endpoint
backtest.py — performance attribution report
alerting.py — strategy-specific loss streak detection
```

### WebSocket → New Strategy Execution Flow

For strategies that benefit from real-time price triggers (stale price, order flow):

```
ws_feeds.py:FeedManager.on_price_update(platform, ticker, data)
    ↓ continuous.py:_on_ws_price_update()
    ↓ OpportunityIndex.lookup(platform, ticker)
    → finds affected opportunities from last scan
    ↓ executor.execute(opp) — via per-market lock
```

New strategies that need WS-triggered execution must:
1. Store `_* market identifier` keys in opp dict (e.g., `_token_id` for Polymarket)
2. Add key extraction to `OpportunityIndex._extract_keys()`
3. No other changes needed — existing WS dispatch handles the rest

---

## Integration Points: New vs Modified Components

### New Components (create from scratch)

| Component | Purpose | Pattern |
|-----------|---------|---------|
| `scans/order_flow.py` | Order book imbalance signal scan | Two-stage scan, returns `OrderFlowImbalance` dicts |
| `scans/news_driven.py` | News-triggered resolution/convergence | Reads from `news_monitor.py` cache, two-stage |
| `pnl_tracker.py` | Per-strategy P&L aggregation queries | Query module over existing `db.py` tables |
| `strategy_registry.py` | Central strategy metadata store | Plain dict — name, layer, risk class, sizing_mode |
| `news_monitor.py` | Background news API polling + cache | Async task, mirrors `signal_aggregator.py` pattern |
| `rebalancer.py` | Platform fund rebalancing logic | Called from dashboard on manual trigger or auto |

### Modified Components (targeted changes only)

| Component | What Changes | Risk Level |
|-----------|-------------|-----------|
| `executor.py:_build_legs()` | Add `elif` branches for new types | LOW — additive |
| `executor.py:_revalidate()` | Add revalidation cases for new types | LOW — additive |
| `continuous.py:run_continuous()` | Wire new scans + MM task | MEDIUM — central loop |
| `continuous.py:OpportunityIndex._extract_keys()` | Add key extraction for new opp types | LOW — additive |
| `cli.py:_run_oneshot()` | Wire new scans + add argparse modes | LOW — additive |
| `scans/__init__.py` | Export new scan functions | LOW — additive |
| `db.py:_create_tables()` | Add `strategy_equity` table + migrations | LOW — backward-compatible ALTER TABLE |
| `db.py:log_opportunity()` | Accept `strategy_layer` + `strategy_type` params | LOW — add optional params with defaults |
| `risk_manager.py:_SKIP_DEPTH_TYPES` | Add new type strings to skip sets | LOW — additive |
| `position_sizer.py` | Add new type strings to prefix sets | LOW — additive |
| `metrics.py` | Add new counters/gauges for new strategies | LOW — additive |
| `dashboard.py:_DashboardState` | Add per-strategy P&L fields | LOW — additive |
| `backtest.py:STRATEGY_LAYERS` | Add new type → layer mappings | LOW — additive |

---

## Build Order: Minimizing Risk to Working System

The key constraint is that `continuous.py` is the production entry point. Any change to it that breaks the run loop breaks the live bot. Changes should be deployed in this order:

### Phase A: Foundation (no production risk)

1. **`strategy_registry.py`** — New file, no imports from it yet. Defines metadata.
2. **`db.py` migrations** — ADD COLUMN only (backward compatible). Add `strategy_equity` table.
3. **`pnl_tracker.py`** — New query module. No side effects until called.
4. **`fees.py`** — Add fee functions for new strategy types.

### Phase B: New Scan Modules (isolated, not yet wired)

5. **`scans/order_flow.py`** — New module. Not exported or imported yet.
6. **`scans/news_driven.py`** — New module. Depends on `news_monitor.py`.
7. **`news_monitor.py`** — New module. Can be tested standalone.
8. **`scans/__init__.py`** — Export new scan functions. No production impact until wire-up.

### Phase C: Execution Layer (targeted additions only)

9. **`position_sizer.py`** — Add new prefix sets. Existing behavior unchanged.
10. **`risk_manager.py`** — Add new type strings to skip sets.
11. **`executor.py:_build_legs()`** — Add new `elif` branches. Existing branches untouched.
12. **`executor.py:_revalidate()`** — Add new revalidation cases.

### Phase D: Orchestration Wiring (highest risk, do last)

13. **`cli.py:_run_oneshot()`** — Wire new scans. Test with `--dry-run` first.
14. **`continuous.py:OpportunityIndex._extract_keys()`** — Add new key extraction.
15. **`continuous.py:run_continuous()`** — Wire new scans + MM task behind feature flags.

### Phase E: Monitoring & Dashboard

16. **`metrics.py`** — Add new strategy metrics.
17. **`dashboard.py`** — Add per-strategy P&L endpoints.
18. **`alerting.py`** — Add strategy-level loss alerts.

**Critical rule:** Each phase-D change to `continuous.py` should be behind a config flag (`MM_ENABLED`, `ORDER_FLOW_ENABLED`, etc.) so it can be toggled without redeployment. This is already the established pattern in the codebase (`SNAPSHOT_ENABLED`, `WS_TRIGGER_ENABLED`, etc.).

---

## Scaling Considerations

| Scale | Architecture Impact |
|-------|---------------------|
| Current (personal bot, <$10K capital) | SQLite WAL is sufficient. ThreadPoolExecutor handles parallelism. No scaling changes needed. |
| Medium ($10K-$100K, more strategies active) | SQLite WAL starts showing write contention under high WS-triggered execution volume. Consider WAL checkpointing tuning. Per-market locks already prevent the worst case. |
| Large ($100K+, high-frequency MM) | SQLite bottlenecks. Migrate `trades` and `strategy_equity` to TimescaleDB or QuestDB for time-series queries. Market making at scale needs colocated VPS near Polymarket/Kalshi endpoints, not Railway. |

**First bottleneck:** SQLite write lock under concurrent WS-triggered executions + MM quote refreshes. Mitigation: increase WAL checkpoint interval, batch MM writes, keep execution locks fine-grained (per-market, not global).

**Second bottleneck:** REST API rate limits across all 8 platforms during scan cycles with 20+ strategies running. Mitigation: scan deduplication (skip markets with no recent price change), scan interval tuning, CLOB refinement only for high-ROI candidates.

---

## Anti-Patterns

### Anti-Pattern 1: Adding Logic to `scanner.py`

**What people do:** Add new scan logic directly to `scanner.py` since it's the entry point.

**Why it's wrong:** `scanner.py` is a re-export facade. Any logic added there is invisible to tests (which patch `scanner.<name>` assuming it's a re-export) and creates a second code path.

**Do this instead:** Add to the appropriate module (`cli.py`, `continuous.py`, or a new scan module), then re-export through `scanner.py` if backward compatibility is needed.

### Anti-Pattern 2: Inline Platform Logic in `_build_legs()`

**What people do:** Add platform-specific order formatting directly inline in a new `elif` branch in `_build_legs()`.

**Why it's wrong:** `_build_legs()` is already 300+ lines. Each new strategy adds 20-40 more lines of mixed platform dispatch and strategy logic, making it increasingly hard to maintain and test.

**Do this instead:** Create a `_build_<type>_legs()` helper method on `ArbitrageExecutor` (following the existing `_build_event_divergence_legs`, `_build_mm_legs` pattern). The `elif` branch calls the helper.

### Anti-Pattern 3: Per-Strategy P&L as a Pandas Query

**What people do:** Load all trades from SQLite, compute P&L using Pandas in a dashboard endpoint.

**Why it's wrong:** This blocks the dashboard HTTP server thread for seconds as trade history grows. Pandas is not in requirements.txt — adding it pulls in heavy dependencies.

**Do this instead:** Use the `strategy_equity` table (pre-aggregated hourly snapshots) for dashboard queries. Full P&L recalculation runs in `pnl_tracker.py` as a background job during low-activity periods and writes to `strategy_equity`. Keep the dashboard query to a simple `SELECT` against the summary table.

### Anti-Pattern 4: Polling News APIs in Scan Functions

**What people do:** Call news API endpoints directly inside `scan_news_driven()` to get fresh signals per scan.

**Why it's wrong:** News APIs have rate limits (NewsAPI.ai: 500 req/day free, commercial plans rate-limited). With a 60s rescan interval, direct calls exhaust quotas within hours.

**Do this instead:** `news_monitor.py` polls on a 5-10 minute interval and updates a module-level signal cache. Scan functions call `news_monitor.get_signal(market_title)` which returns the cached value. This matches how `signal_aggregator.py` handles Metaculus signals.

### Anti-Pattern 5: Bypassing the Kill Switch for Market Making

**What people do:** Run the MM quote loop outside the `ArbitrageExecutor.execute()` path, bypassing the kill switch check in `execute()`.

**Why it's wrong:** The dashboard kill switch (pause button) must be able to stop all trading including MM. If MM runs outside `execute()`, it ignores the pause.

**Do this instead:** MM quote placement should go through `executor.execute()` with `opp_type = "MarketMake"`. The existing kill switch check at step 0 of `execute()` will catch it. `_build_mm_legs()` already exists for this.

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Polymarket CLOB WS | `ws_feeds.py:FeedManager` — existing | Add new subscription keys via `OpportunityIndex` |
| Kalshi WS | `ws_feeds.py:FeedManager` — existing | Same as above |
| News APIs (NewsAPI.ai, GDELT) | `news_monitor.py` — new polling task | Cache TTL 5-10 min. Rate limit: 500 req/day free tier. |
| Metaculus / Manifold | `signal_aggregator.py` — existing | Already wired into `event_monitor.py` |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `scan` modules → `executor` | Opportunity dicts (plain Python) | Never pass objects — dicts only |
| `news_monitor` → `scans/news_driven.py` | Module-level cache via `get_signal()` | Thread-safe via lock (matches `signal_aggregator.py`) |
| `pnl_tracker` → `dashboard` | `strategy_equity` SQLite table | Dashboard reads snapshots, never runs aggregation queries live |
| `market_maker` → `executor` | Via `execute({"type": "MarketMake", ...})` | MM generates opp dicts, executor handles risk + kill switch |
| `rebalancer` → platform APIs | Direct API calls (read balance, suggest transfer) | Execution of transfers is manual or behind explicit confirmation |
| New scan → `continuous.py` | Added to `_run_scans()` with feature flag | Feature flag pattern: `if config.ORDER_FLOW_ENABLED: ...` |

---

## Sources

- Direct codebase inspection of `executor.py`, `continuous.py`, `scans/__init__.py`, `db.py`, `market_maker.py`, `signal_aggregator.py`, `position_sizer.py`, `risk_manager.py` (HIGH confidence)
- [Polymarket CLOB Documentation](https://docs.polymarket.com/polymarket-learn/trading/using-the-orderbook) — order book structure, WS latency under 50ms (MEDIUM confidence)
- [Market Making on Prediction Markets Guide](https://newyorkcityservers.com/blog/prediction-market-making-guide) — inventory management, quote engine architecture (MEDIUM confidence)
- [Polymarket Agents SQLite schema](https://github.com/artvandelay/polymarket-agents) — per-strategy attribution pattern (MEDIUM confidence)
- [NewsAPI.ai Event Types](https://newsapi.ai/blog/event-types-real-time-signals/) — structured news signals for trading (MEDIUM confidence)
- [GDELT Project](https://www.gdeltproject.org/) — 15-minute update interval, NLP event classification (MEDIUM confidence)
- [Python asyncio oracle lag bot pattern](https://hashnode.com/forums/thread/how-i-used-python-asyncio-to-trade-a-55-second-oracle-lag-on-polymarket) — asyncio task architecture for Polymarket trading (MEDIUM confidence)

---

*Architecture research for: Polymarket Arb Scanner v2.0 — Strategy Expansion & Profitability*
*Researched: 2026-04-01*
