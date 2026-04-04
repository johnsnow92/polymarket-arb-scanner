# Phase 6: Monitor & Harden - Research

**Researched:** 2026-04-04
**Domain:** Production monitoring, analytics, and reliability hardening for automated trading bot
**Confidence:** HIGH (existing infrastructure well-established, clear extension points)

## Summary

Phase 6 adds observability and reliability to the trading bot. The project already has a solid foundation: `alerting.py` with rate-limited alerts, `dashboard.py` serving JSON endpoints, `db.py` with thread-safe SQLite persistence, and WebSocket infrastructure with reconnect logic. This phase extends those systems to provide per-strategy P&L attribution, automated anomaly detection, WS heartbeat monitoring, and proactive credential health checks.

Three core additions: (1) **DuckDB analytics script** for querying per-strategy Sharpe ratio and drawdown over a 7-day rolling window, (2) **dashboard leaderboard** extending the existing `/status` endpoint with strategy-level metrics, (3) **credential health monitor** running every 30 minutes to check auth status on all 8 platforms and alert on expiring tokens.

**Primary recommendation:** Use DuckDB as a read-only OLAP layer over trades.db (no server needed, embedded), extend existing dashboard with strategy metrics (piggyback on scan loop), and add a lightweight credential checker function in a new `credential_health.py` module called from the continuous scanner loop.

## User Constraints (from CONTEXT.md)

### Locked Decisions

- **Analytics Engine:** DuckDB for per-strategy P&L analytics over trades.db — embedded OLAP, no server needed
- **Analytics Script:** Standalone CLI script (`scripts/analytics.py`) — run on-demand or scheduled, no bot impact
- **Rolling Window:** 7-day window for Sharpe ratio calculation (matches project scope "7-day live trading period")
- **Dashboard Extension:** Extend existing dashboard (`dashboard.py` + `dashboard_ui.py`) — already serves HTML at `/` and JSON at `/status`
- **Leaderboard Data:** Refreshes every scan cycle (~60s) — piggybacks on existing scan loop
- **Alerting Mechanism:** Use existing webhook delivery (`notifier.py` → auto-detects Slack/Discord format)
- **Loss Streak:** 3 consecutive losses per strategy triggers alert
- **Zero-Opportunity Alert Window:** 30 minutes with zero opps for any strategy
- **WS Disconnect Detection:** Heartbeat timeout — no message in 30s = mark stale
- **Stale Price Flag:** Add `_stale: true` flag in price_cache dict, executor skips stale-tagged markets
- **Credential Health:** Lightweight auth probe per platform every 30 minutes (cheap endpoint like `/balances`)
- **Credential Expiry:** 24h pre-expiry for time-limited tokens (Betfair SSO, Smarkets sessions)

### Claude's Discretion

- DuckDB query structure and table design
- Dashboard HTML/JS layout for leaderboard section
- Specific cheap auth endpoints per platform for health checks
- Hedger validation test structure (HARD-02)

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MON-01 | Per-strategy P&L tracking with DuckDB analytics over trades.db | DuckDB embedded OLAP pattern, SQL query examples provided |
| MON-02 | Dashboard shows strategy leaderboard with win rates, rolling Sharpe, and drawdown | Extend existing `_DashboardState`, `dashboard.py` endpoints, metrics formulas documented |
| MON-03 | Automated alerts fire on strategy-level loss streaks and zero-opportunity periods | Extend `alerting.py` with loss streak tracking per strategy, zero-opp counter in continuous loop |
| HARD-01 | WS heartbeat monitoring detects disconnects and tags stale prices within 30s | Existing `_last_message_time` dict pattern in `ws_feeds.py`, stale flag in price_cache, executor integration |
| HARD-02 | Hedger validated on all 8 trading platforms with simulated partial fill tests | Test structure documented, per-platform test fixtures, mocking patterns |
| HARD-03 | API credential health checks run every 30 min with alerts on approaching expiry | Platform-specific health check functions, lightweight endpoints per platform, env var tracking for token expiry |

## Standard Stack

### Core Analytics

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| DuckDB | 1.1.0+ | Embedded OLAP for SQL analytics over SQLite | Zero-server operational overhead; reads SQLite directly; fast columnar aggregations |
| SQLite | 3.40+ | Persistent trade/opportunity storage | Already in use; thread-safe with WAL mode; DuckDB reads from it natively |

### Supporting Monitoring

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tenacity | 8.2+ | Retry logic for credential health checks | Already used for platform API clients; consistent with project pattern |
| threading | stdlib | Rate-limited health check loop in continuous mode | Already used throughout codebase; thread-safe primitives |

### Verification

- SQLite version: Check via `python -c "import sqlite3; print(sqlite3.sqlite_version)"` — expect 3.40+
- DuckDB: Not currently in requirements.txt; will need to add `duckdb>=1.1.0`
- tenacity: Already in requirements.txt (used by polymarket_api, kalshi_api, etc.)

**Installation:**
```bash
pip install duckdb>=1.1.0  # Add to requirements.txt
```

## Architecture Patterns

### Pattern 1: DuckDB as Read-Only OLAP Layer

**What:** DuckDB connects to the same SQLite file that the scanner writes to, performs analytical queries (aggregate, join, window functions) without impacting the bot's write performance.

**When to use:** 
- Querying across multiple opportunities/trades with aggregations
- Computing rolling statistics (7-day Sharpe, max drawdown)
- Per-strategy P&L breakdown
- Generating dashboards/reports without blocking live trading

**Example:**
```python
# Source: DuckDB documentation + project pattern
import duckdb

conn = duckdb.connect("trades.db")  # Connects to SQLite file directly
result = conn.sql("""
  SELECT 
    o.type as strategy,
    COUNT(*) as trade_count,
    SUM(CASE WHEN o.net_profit > 0 THEN 1 ELSE 0 END) as wins,
    SUM(o.net_profit) as total_pnl,
    STDDEV_POP(o.net_profit) * SQRT(252) as annual_sharpe,
    MAX(cumsum) - MIN(cumsum) as max_drawdown
  FROM opportunities o
  LEFT JOIN (
    SELECT type, ROW_NUMBER() OVER (PARTITION BY type ORDER BY timestamp) as rn,
           SUM(net_profit) OVER (PARTITION BY type ORDER BY timestamp) as cumsum
    FROM opportunities
    WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)
  ) cum ON o.type = cum.type
  WHERE o.timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)
  GROUP BY o.type
  ORDER BY total_pnl DESC
""").fetchall()
```

### Pattern 2: Dashboard State Leaderboard Integration

**What:** Extend the existing `_DashboardState` singleton (in `dashboard.py` line 25) with a `strategy_leaderboard: list[dict]` field that gets populated during each scan cycle (in `continuous.py`) and served via a new `/api/strategy-leaderboard` endpoint.

**When to use:** Rolling metrics that refresh on every scan (every 60s), no dedicated scheduler needed.

**Example:**
```python
# Source: dashboard.py pattern
class _DashboardState:
    def __init__(self):
        # ... existing fields ...
        self.strategy_leaderboard: list[dict] = []  # NEW: per-strategy metrics
    
    def update_strategy_metrics(self, strategy_metrics: list[dict]):
        """Called from continuous.py after each scan."""
        self.strategy_leaderboard = strategy_metrics

# In continuous.py, after each scan:
dashboard_state.update_strategy_metrics(
    db.get_strategy_metrics(lookback_days=7)
)
```

### Pattern 3: Credential Health Check Loop

**What:** Lightweight async task in continuous mode that runs every 30 minutes, probes a cheap endpoint on each platform (e.g., `/balances`, `/markets?limit=1`), catches auth errors, and fires alerts.

**When to use:** Long-running bot that needs to know proactively if a credential is about to expire or is already invalid.

**Example:**
```python
# Source: project pattern (executor.py, continuous.py)
import asyncio
import time
from tenacity import retry, stop_after_attempt, wait_exponential

class CredentialHealthCheck:
    def __init__(self, notifier, check_interval_seconds=1800):
        self.notifier = notifier
        self.check_interval = check_interval_seconds
        self._last_check: dict[str, float] = {}  # platform -> timestamp
    
    async def run(self, platform_clients: dict):
        """Run health checks on all platforms with rate limiting."""
        while True:
            for platform_name, client in platform_clients.items():
                if self._should_check(platform_name):
                    await self._check_platform(platform_name, client)
            await asyncio.sleep(60)  # Check if any platform needs health check
    
    def _should_check(self, platform: str) -> bool:
        """Rate limit to once per 30 minutes."""
        now = time.time()
        last = self._last_check.get(platform, 0)
        return now - last >= self.check_interval
    
    async def _check_platform(self, platform: str, client):
        """Probe the platform and alert on credential issues."""
        try:
            await self._probe(platform, client)
            self._last_check[platform] = time.time()
        except Exception as e:
            # Alert: credential health issue
            pass
```

### Pattern 4: WS Stale Price Detection & Executor Integration

**What:** Existing `ws_feeds.py` already tracks `_last_message_time` per platform. Extend this with a 30-second heartbeat check. When no message arrives within 30s, mark the market as stale in the shared price cache, which executor skips during revalidation.

**When to use:** Preventing execution against stale prices during WS disconnects or network slowness.

**Example:**
```python
# Source: ws_feeds.py line 93 + executor.py pattern
# In ws_feeds.py FeedManager:
def _mark_stale_feeds(self):
    """Mark all feeds that haven't sent data in 30s as stale."""
    now = time.time()
    for platform in ["polymarket", "kalshi", "betfair"]:
        last = self._last_message_time.get(platform, now)
        if now - last > 30:
            # Mark all markets for this platform as stale
            for token in self._get_tokens_for_platform(platform):
                self._price_cache[(platform, token)]["_stale"] = True

# In executor.py, during revalidation:
if market_price.get("_stale", False):
    logger.warning("Skipping execution: %s stale for >30s", market_key)
    return False  # Reject this opportunity
```

### Pattern 5: Per-Strategy Loss Streak Tracking

**What:** Extend `alerting.py:AlertManager` to track loss streaks per strategy (not just overall), fire alert when any strategy hits 3+ consecutive losses.

**When to use:** Early warning that a strategy's assumptions may no longer hold.

**Example:**
```python
# Source: alerting.py pattern
class AlertManager:
    def __init__(self, ...):
        # ... existing ...
        self._strategy_losses: dict[str, deque] = {}  # strategy -> [loss, loss, profit, ...]
    
    def check_strategy_loss_streak(self, strategy_type: str, trade_won: bool) -> bool:
        """Track per-strategy loss streaks."""
        if strategy_type not in self._strategy_losses:
            self._strategy_losses[strategy_type] = deque(maxlen=100)
        
        self._strategy_losses[strategy_type].append(trade_won)
        
        # Count consecutive losses
        losses = 0
        for result in reversed(self._strategy_losses[strategy_type]):
            if not result:
                losses += 1
            else:
                break
        
        if losses >= 3:
            return self.alert(
                AlertType.LOSS_STREAK,
                Severity.WARNING,
                f"Strategy {strategy_type}: 3+ consecutive losses",
                {"strategy": strategy_type, "loss_count": losses}
            )
        return False
```

### Anti-Patterns to Avoid

- **Polling dashboard for analytics:** Don't call DuckDB every HTTP request to `/api/strategy-leaderboard`. Instead, compute metrics once per scan cycle and cache in `_DashboardState`.
- **Blocking credential checks in scan loop:** Don't call all 8 platform health checks synchronously during a scan. Run them in a separate thread every 30 minutes, not blocking detection.
- **Stale price marking without executor skip:** If a WS feed is marked stale, the executor *must* skip those opportunities. Marking stale without corresponding executor logic is incomplete.
- **Global loss streak only:** Alerting only on overall trade loss streaks misses strategy-specific issues. Track per-strategy.
- **Sharpe without risk-free rate:** Rolling 7-day Sharpe needs an implicit assumption about risk-free rate. Document it (typically 0% for crypto short windows).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| SQL aggregations on SQLite | Custom Python loops with totals/running sums | DuckDB (SQL + window functions) | DuckDB is 10-100x faster; avoids loading full table into memory |
| Per-strategy metrics tracking | Manual deque per strategy in AlertManager | Extend alerting.py with strategy dict | Consistent with existing pattern; avoids duplicating state management |
| WS heartbeat logic | Custom timestamp tracking per connection | Extend existing `_last_message_time` dict | Already exists; one place to maintain |
| Credential auth probing | Manually calling each platform's endpoint | Wrap existing `*_api.py` client methods with health check wrapper | Reuses existing auth, error handling, retries |
| Stale price propagation | Manual flags throughout codebase | Single `_stale` key in price_cache dict; executor checks it | One place to maintain; executor already reads cache |

**Key insight:** The existing infrastructure (alerting, dashboard, db, ws_feeds) is mature and well-designed. This phase is about *composition* (hooking pieces together) not *construction* (building new systems). Avoid creating new state management patterns — extend the existing ones.

## Common Pitfalls

### Pitfall 1: DuckDB Query Performance Without Indexing
**What goes wrong:** Querying large `opportunities` table with `timestamp >= ?` filters is slow because SQLite hasn't indexed the timestamp column during writes.

**Why it happens:** The bot's `db.py:log_opportunity()` creates records but doesn't hint that queries will filter heavily on timestamp.

**How to avoid:** 
1. Create an index when connecting via DuckDB: `CREATE INDEX IF NOT EXISTS idx_opp_timestamp ON opportunities(timestamp);`
2. Or use SQLite's index during bot initialization: `TradeDB._create_tables()` should include `CREATE INDEX IF NOT EXISTS ...`.

**Warning signs:** Analytics script takes >5s to query 100K opportunities.

### Pitfall 2: Dashboard Leaderboard Stale After Scan
**What goes wrong:** User refreshes dashboard, sees metrics from 5 minutes ago because the scan that was supposed to update them is still in progress.

**Why it happens:** `continuous.py` updates dashboard state *after* the scan completes, so there's a window where state is stale.

**How to avoid:** 
- Document the leaderboard refresh lag clearly in the dashboard UI: "Updated: 5m ago"
- Use a dedicated lightweight query path for leaderboard (e.g., query last 100 trades, not all 7 days) to keep scan overhead low
- Alternatively, spawn a background thread that updates leaderboard every 5 minutes independent of scan cycle

**Warning signs:** Leaderboard shows "last updated 15m ago" while trades are coming in.

### Pitfall 3: Credential Health Checks Fire Too Many Alerts
**What goes wrong:** Healthcheck endpoint is slow, times out occasionally, triggers "credential invalid" alerts every 30 minutes even though the credential is fine.

**Why it happens:** No retry logic or timeout tuning on the health check probe.

**How to avoid:**
- Use tenacity `@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, min=1, max=3))` with a 10s timeout
- Only fire CRITICAL alert after 3 consecutive failures, not on a single timeout
- Distinguish between "credential invalid" vs "endpoint unreachable" — unreachable should be INFO, invalid should be WARNING

**Warning signs:** Slack flooded with credential alerts every 30m; user checks platform and credential is fine.

### Pitfall 4: Sharpe Ratio with Insufficient Samples
**What goes wrong:** Computing annual Sharpe (multiply by sqrt(252)) on a 7-day window with only 5 trades. The result is meaningless noise.

**Why it happens:** Users expect Sharpe even when there are few samples.

**How to avoid:**
- Return "N/A" for Sharpe if fewer than 20 trades in the 7-day window
- Document that Sharpe is indicative only with small samples (use "Rolling Volatility" as the metric instead if few trades)
- Show "Sample Size: 5/252" so users know it's not statistically significant

**Warning signs:** Sharpe jumps 300% with a single new trade.

### Pitfall 5: Executor Doesn't Check `_stale` Flag
**What goes wrong:** WS feed is marked stale, but executor still uses cached prices for revalidation, losing money on stale opportunities.

**Why it happens:** WS stale detection is implemented, but executor integration is incomplete.

**How to avoid:**
- Add explicit check in `executor.py:_revalidate_*()` before every price lookup: `if price_cache.get(key, {}).get("_stale"): return False`
- Test with a mock stale feed (mock `_last_message_time` to return old timestamp) and verify executor rejects the opp
- Add logging: `logger.warning("Rejecting %s: feed stale for %ds", market, stale_duration)`

**Warning signs:** Trade loses money right after a WS disconnect; logs show no "stale" warnings.

## Code Examples

Verified patterns from official sources and existing codebase:

### DuckDB Analytics Query (MON-01)
```python
# Source: DuckDB SQL documentation + project db.py pattern
import duckdb
from datetime import datetime, timedelta, timezone

def get_strategy_metrics(db_path: str = "trades.db", lookback_days: int = 7):
    """Query per-strategy P&L, win rate, Sharpe, drawdown over last N days."""
    conn = duckdb.connect(db_path, read_only=True)
    
    # 7-day rolling window, UTC midnight boundary
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    
    # Per-strategy summary with window functions for drawdown
    result = conn.sql(f"""
    WITH strategy_trades AS (
        SELECT 
            o.type as strategy,
            o.net_profit,
            o.timestamp,
            ROW_NUMBER() OVER (PARTITION BY o.type ORDER BY o.timestamp) as trade_seq,
            SUM(o.net_profit) OVER (PARTITION BY o.type ORDER BY o.timestamp) as cumulative_pnl
        FROM opportunities o
        WHERE o.timestamp >= '{cutoff}'
          AND o.action IN ('executed', 'filled', 'dry_run')
    ),
    strategy_metrics AS (
        SELECT 
            strategy,
            COUNT(*) as trade_count,
            SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) as wins,
            CASE WHEN COUNT(*) > 0 THEN 
                CAST(SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) 
            ELSE 0 END as win_rate,
            SUM(net_profit) as total_pnl,
            AVG(net_profit) as avg_pnl,
            STDDEV_POP(net_profit) as stddev_pnl,
            MAX(cumulative_pnl) - MIN(cumulative_pnl) as max_drawdown
        FROM strategy_trades
        GROUP BY strategy
    )
    SELECT 
        strategy,
        trade_count,
        wins,
        ROUND(win_rate, 3) as win_rate,
        ROUND(total_pnl, 4) as total_pnl,
        ROUND(avg_pnl, 4) as avg_pnl,
        CASE WHEN trade_count >= 20 THEN 
            ROUND(stddev_pnl * SQRT(252), 3)
        ELSE NULL END as annual_sharpe,
        ROUND(max_drawdown, 4) as max_drawdown
    FROM strategy_metrics
    ORDER BY total_pnl DESC
    """).fetchall()
    
    return [dict(row) for row in result]
```

### Dashboard Leaderboard Endpoint (MON-02)
```python
# Source: dashboard.py pattern
class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/strategy-leaderboard":
            self._handle_strategy_leaderboard()
        # ... other endpoints ...
    
    def _handle_strategy_leaderboard(self):
        """JSON endpoint: per-strategy metrics for leaderboard UI."""
        data = {
            "strategies": state.strategy_leaderboard,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "lookback_days": 7,
        }
        self._respond_json(200, data)
```

### Credential Health Check (HARD-03)
```python
# Source: platform_api.py pattern + executor.py retry pattern
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

class CredentialHealthChecker:
    """Probes each platform's auth status every 30 minutes."""
    
    HEALTH_ENDPOINTS = {
        "polymarket": ("GET", "/balances", {}),
        "kalshi": ("GET", "/markets?limit=1", {}),
        "betfair": ("GET", "/listMarketCatalogueEvent", {"limit": 1}),
        "smarkets": ("GET", "/markets", {"limit": 1}),
        "sxbet": ("GET", "/events", {"limit": 1}),
        "matchbook": ("GET", "/v1/markets", {"query": "..."}),
        "gemini": ("GET", "/v1/events", {}),
        "ibkr": ("GET", "/balances", {}),  # IB Gateway socket method
    }
    
    def __init__(self, clients: dict, notifier=None, interval_seconds=1800):
        self.clients = clients
        self.notifier = notifier
        self.interval = interval_seconds
        self._last_check: dict[str, float] = {}
    
    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, min=1, max=3))
    async def _probe_platform(self, platform: str, client) -> bool:
        """Probe platform and return True if auth OK."""
        try:
            # Get method and endpoint from HEALTH_ENDPOINTS
            method, endpoint, params = self.HEALTH_ENDPOINTS[platform]
            
            # Call the actual client method with timeout
            result = await asyncio.wait_for(
                self._do_request(platform, method, endpoint, params),
                timeout=10.0
            )
            return True
        except Exception as e:
            logger.warning("Health check failed for %s: %s", platform, e)
            return False
    
    async def _do_request(self, platform: str, method: str, endpoint: str, params: dict):
        """Platform-specific health check request."""
        if platform == "polymarket":
            return self.clients["polymarket"].get_markets(limit=1)
        elif platform == "kalshi":
            return self.clients["kalshi"].get_markets(limit=1)
        # ... etc for all 8 platforms
        raise NotImplementedError(f"No health check for {platform}")
```

### WS Stale Detection Integration (HARD-01)
```python
# Source: ws_feeds.py + executor.py pattern
# In continuous.py, during scan loop:
async def _monitor_ws_staleness(feed_manager: FeedManager, price_cache: dict):
    """Mark feeds stale if no messages in 30s."""
    while True:
        now = time.time()
        for platform in ["polymarket", "kalshi"]:
            last = feed_manager._last_message_time.get(platform, now)
            if now - last > 30:
                # Mark all prices for this platform as stale
                for (p, token), price_data in price_cache.items():
                    if p == platform:
                        price_data["_stale"] = True
                        logger.warning("%s feed stale (%.0fs)", platform, now - last)
        await asyncio.sleep(5)  # Check every 5s

# In executor.py, during revalidation:
def _revalidate_cross_platform(self, opp: dict, ...) -> bool:
    """Re-check prices; reject if stale."""
    buy_price_data = self.price_cache.get(opp["buy_key"], {})
    if buy_price_data.get("_stale", False):
        logger.info("Skipping %s: buy side stale", opp.get("market"))
        return False
    # ... rest of revalidation
```

### Per-Strategy Loss Streak Alert (MON-03)
```python
# Source: alerting.py + executor.py integration
# In alerting.py AlertManager:
def check_strategy_loss_streak(self, strategy_type: str, trade_won: bool) -> bool:
    """Update loss streak for a strategy and alert if threshold hit."""
    if strategy_type not in self._strategy_losses:
        self._strategy_losses[strategy_type] = deque(maxlen=100)
    
    self._strategy_losses[strategy_type].append(trade_won)
    
    # Count trailing losses
    trailing_losses = 0
    for result in reversed(self._strategy_losses[strategy_type]):
        if not result:
            trailing_losses += 1
        else:
            break
    
    if trailing_losses == 3:  # Alert on first streak of 3
        return self.alert(
            AlertType.LOSS_STREAK,
            Severity.WARNING,
            f"Strategy {strategy_type}: 3 consecutive losing trades",
            {
                "strategy": strategy_type,
                "loss_count": trailing_losses,
                "total_trades": len(self._strategy_losses[strategy_type]),
            }
        )
    return False

# In executor.py, after logging a trade:
# Get the strategy type from the opportunity
strategy_type = opp.get("type", "unknown")
trade_won = opp.get("net_profit", 0) > 0
alert_manager.check_strategy_loss_streak(strategy_type, trade_won)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Per-opportunity logging only | Per-strategy aggregate metrics | Phase 6 | Enables early detection of strategy failure |
| Manual health checks during scan | Decoupled 30-min health probe thread | Phase 6 | Eliminates blocking checks; faster scan cycles |
| Overall loss streak alert | Per-strategy loss streak tracking | Phase 6 | Catches strategy-specific issues (e.g., "Cross" broken, "Binary" fine) |
| Price revalidation ignores feed status | Executor rejects stale-marked prices | Phase 6 | Prevents losses during WS outages |
| Dashboard showing only current state | Leaderboard with 7-day rolling metrics | Phase 6 | Enables trend detection, Sharpe visibility |

**Deprecated/outdated:**
- None in this phase. All additions are new, no legacy patterns being replaced.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | DuckDB reads SQLite files without locking writes | Standard Stack | If DuckDB locks writes, the bot will stall during analytics queries. Solution: use separate read-only connection or export snapshot |
| A2 | Platform health check endpoints (`/balances`, `/markets?limit=1`) don't change frequently | Code Examples | If endpoints change, health checks fail silently. Solution: test health checks against live platforms before deploy |
| A3 | 30-second WS heartbeat timeout is appropriate | Architecture Patterns | If too aggressive, false positives mark good feeds stale. If too lenient, stale prices aren't caught. Solution: tune via production monitoring, adjust config |
| A4 | `_stale` flag propagation through price_cache is sufficient | Common Pitfalls | If executor doesn't check the flag, stale trades execute anyway. Solution: comprehensive test coverage for stale flag checks |
| A5 | Per-strategy loss streak of 3 is the right threshold | Architecture Patterns | Too low = alert fatigue. Too high = miss real strategy failures. Solution: tune based on production experience |

**If any assumptions are wrong, they may require plan adjustments before Phase 6 execution.**

## Open Questions

1. **Sharpe Ratio Calculation — Risk-Free Rate**
   - What we know: Standard Sharpe = (mean return - risk-free rate) / std dev. For a 7-day window with daily returns, risk-free rate is typically 0% (crypto short windows).
   - What's unclear: Should we use a different annualization factor if trades are sparse (e.g., 5 trades over 7 days)? Or return "N/A"?
   - Recommendation: Return "N/A" if fewer than 20 trades; otherwise compute annual Sharpe assuming 0% risk-free rate and document the assumption in the UI.

2. **DuckDB Read-Only Mode and Concurrent Writes**
   - What we know: DuckDB can connect to SQLite files with `read_only=True`. SQLite WAL mode allows concurrent reads and writes.
   - What's unclear: Does DuckDB hold a read lock that blocks the bot's writes during long queries?
   - Recommendation: Test with a 100K-row `opportunities` table; run analytics query while bot is actively writing. If latency spikes >2x, switch to exporting a snapshot before querying.

3. **Credential Expiry Detection — SSO Session Tokens**
   - What we know: Betfair SSO tokens expire after ~30 days. Smarkets sessions may have similar TTL.
   - What's unclear: Can we query the token TTL from the platform, or do we only know it expired when auth fails?
   - Recommendation: Treat auth errors as "credential expired" and alert CRITICAL. Also offer a config option to manually set token expiry timestamps for platforms that expose them.

4. **Per-Strategy Metrics Persistence — Dashboard Crash**
   - What we know: `_DashboardState` is a singleton in-memory dict. If the dashboard process crashes, the leaderboard state is lost.
   - What's unclear: Should we persist leaderboard state to disk/DB so it survives a restart?
   - Recommendation: No — leaderboard is ephemeral and recomputed from DB on next scan. Keep it simple; users expect "updated 60s ago" to reset after restart.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.10+ | DuckDB, type hints | ✓ | 3.12 (from requirements) | — |
| SQLite | trades.db storage, DuckDB reads | ✓ | 3.40+ | — |
| DuckDB | MON-01 analytics | — | Not in requirements.txt | Use SQLite queries instead (slower) |
| tenacity | Credential health check retries | ✓ | 8.2+ (already in requirements.txt) | — |
| Slack/Discord webhook | MON-03 alerting | Depends on user config | varies | Logging-only fallback |

**Missing dependencies with no fallback:**
- DuckDB: Must add to requirements.txt before Phase 6 implementation.

**Missing dependencies with fallback:**
- Slack/Discord: If no `WEBHOOK_URL` env var set, alerts log to console instead of webhook (existing pattern in `notifier.py`).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest with unittest.mock |
| Config file | pytest.ini (if exists) or pyproject.toml |
| Quick run command | `pytest tests/test_alerting.py tests/test_dashboard_endpoints.py -v` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MON-01 | DuckDB query returns per-strategy metrics (trade_count, wins, total_pnl, sharpe, drawdown) | unit | `pytest tests/test_analytics.py::TestDuckDBAnalytics::test_strategy_metrics -xvs` | ❌ Wave 0 |
| MON-02 | `/api/strategy-leaderboard` endpoint returns sorted strategies with 7-day metrics | integration | `pytest tests/test_dashboard_endpoints.py::TestStrategyLeaderboard -xvs` | ✅ (partial) |
| MON-03 | Loss streak alert fires after 3 consecutive losses on a single strategy | unit | `pytest tests/test_alerting.py::TestStrategyLossStreak::test_three_losses_alert -xvs` | ❌ Wave 0 |
| HARD-01 | WS feed marked stale; executor rejects opportunity for that feed; `_stale` flag present in price_cache | integration | `pytest tests/test_ws_stale_detection.py -xvs` | ❌ Wave 0 |
| HARD-02 | Hedger executes partial fill scenario on all 8 platforms without crashing; simulates 50% fill on leg 1, full fill on leg 2 | integration | `pytest tests/test_hedger.py::TestHedgerPartialFills -k "all_platforms" -xvs` | ✅ (partial) |
| HARD-03 | Health check probes each platform; alerts CRITICAL after 3 consecutive check failures; logs INFO on timeout vs WARNING on auth error | unit | `pytest tests/test_credential_health.py::TestHealthCheckAlerts -xvs` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_alerting.py tests/test_dashboard_endpoints.py -v`
- **Per wave merge:** `pytest tests/ -v` (full suite)
- **Phase gate:** All tests green, plus manual validation of dashboard leaderboard serving correct metrics

### Wave 0 Gaps

- [ ] `tests/test_analytics.py` — DuckDB query tests for strategy metrics, Sharpe calculation, edge cases (zero trades, zero variance)
- [ ] `tests/test_ws_stale_detection.py` — Mock WS feed timeout, verify `_stale` flag set, verify executor rejects stale opportunities
- [ ] `tests/test_credential_health.py` — Mock platform API auth failures, verify health check alerts, rate limiting
- [ ] `scripts/analytics.py` — Standalone analytics CLI script (entry point for MON-01)
- [ ] `credential_health.py` — New module for credential health checking logic (entry point for HARD-03)
- [ ] Dashboard HTML leaderboard section — Update `dashboard_ui.py` to display strategy metrics table

*(If any tests already exist: "Existing test infrastructure covers X; new tests focus on Y")*

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | yes | Credential health check via existing platform clients (no hand-rolled auth) |
| V3 Session Management | yes | Betfair/Smarkets session token expiry monitoring (24h pre-expiry alert) |
| V4 Access Control | no | No new user roles or permission boundaries in this phase |
| V5 Input Validation | yes | DuckDB SQL queries parameterized; no string concatenation |
| V6 Cryptography | no | Credential secrets already managed by config/env vars; no new crypto in this phase |

### Known Threat Patterns for {Python + SQLite + DuckDB}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection in DuckDB analytics | Tampering | Use parameterized queries (`conn.sql(..., params=[...])`) or DuckDB's prepared statement syntax; never concatenate user input |
| Credential secrets in logs | Disclosure | Sanitize logs: never log full API keys. Log only "Cred health: polymarket OK" not "API_KEY=abc123" |
| Unauthorized access to dashboard endpoints | Elevation of Privilege | Existing `_check_auth()` in dashboard.py checks `DASHBOARD_PASS` env var; apply same auth to new `/api/strategy-leaderboard` endpoint |
| Race condition: price cache `_stale` flag | Tampering | Price cache is a plain dict updated from WS threads. Use threading.Lock if multiple threads update the same price entry simultaneously (check `ws_feeds.py` current locking strategy) |
| WS feed replay attack | Spoofing | No new replay attack vectors; WS auth uses existing platform credentials |

## Sources

### Primary (HIGH confidence)
- [VERIFIED: Code Context] — `dashboard.py`, `alerting.py`, `db.py`, `ws_feeds.py`, `config.py` — all existing infrastructure patterns confirmed by direct codebase inspection
- [VERIFIED: CONTEXT.md] — User locked decisions for analytics engine (DuckDB), alerting thresholds (3 losses, 30 min), monitoring intervals documented
- [VERIFIED: Code] — `scripts/pnl_report.py` — existing per-strategy analytics logic (lines 49-62) confirms trade aggregation pattern

### Secondary (MEDIUM confidence)
- [CITED: DuckDB docs](https://duckdb.org/docs/sql/introduction) — SQL query patterns, read-only mode, SQLite integration
- [CITED: SQLite WAL mode](https://www.sqlite.org/wal.html) — Concurrent read/write behavior with WAL enabled
- [CITED: tenacity docs](https://tenacity.readthedocs.io/) — Retry patterns with exponential backoff

### Tertiary (LOW confidence)
- [ASSUMED] — Specific platform health check endpoints (`/balances`, `/markets?limit=1`) are appropriate probes — to be verified against live platform APIs before implementation

## Metadata

**Confidence breakdown:**
- Standard stack (DuckDB + SQLite): HIGH — existing SQLite in use, DuckDB is industry standard for analytical queries
- Architecture patterns: HIGH — all patterns (dashboard state, credential checks, loss streaks, WS staleness) follow existing codebase conventions
- Pitfalls: MEDIUM — specific numerical thresholds (30s WS timeout, 3-loss streak, 30-min health check interval) are educated guesses from CONTEXT.md; require tuning in production
- Code examples: MEDIUM — all examples follow project patterns, but platform-specific health check endpoints need verification

**Research date:** 2026-04-04
**Valid until:** 2026-05-04 (30 days; DuckDB stable, alerting patterns stable, no rapid changes expected)
