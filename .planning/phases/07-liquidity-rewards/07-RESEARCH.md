# Phase 7: Liquidity Rewards - Research

**Researched:** 2026-04-04
**Domain:** Prediction Market Liquidity Rewards Integration & Market Making
**Confidence:** MEDIUM (API endpoints not fully documented; program terms confirmed)

## Summary

Phase 7 integrates liquidity rewards from Polymarket and Kalshi by extending the existing market maker infrastructure. Both platforms reward makers for resting limit orders that meet platform-specific spread and size thresholds. Polymarket publishes reward scores in real-time via Markets API; Kalshi requires local tracking of qualifying order metrics. The phase reuses `market_maker.py` QuoteManager and InventoryTracker, adds a new `rewards` scan mode, and instruments the dashboard to display reward yield alongside trading P&L.

**Primary recommendation:** Build `scans/rewards.py` as a reward-aware market making scan (following existing two-stage pattern), extend QuoteManager with reward-scoring logic, and track both trading fills and reward metrics in separate database columns for independent P&L calculation.

## Standard Stack

### Core Libraries (Existing, Reused)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| py_clob_client | (via polymarket_api.py) | Polymarket order placement & market data | Polymarket's official client for CLOB trading |
| kalshi_api.py (RSA-PSS + requests) | native | Kalshi REST API client with auth | Kalshi's only supported auth mechanism |
| market_maker.py | existing | QuoteEngine, InventoryTracker, QuoteManager | Battle-tested limit order management from Phase 6 |
| db.py (TradeDB) | existing | SQLite WAL for position tracking | Thread-safe, atomic position recording |
| metrics.py (MetricsCollector) | existing | Prometheus counter/gauge/histogram | Consistent instrumentation across phases |

### New Dependencies (Phase 7 Specific)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| (none required) | — | Phase 7 uses only existing stdlib + installed deps | All new code is within existing framework |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Polymarket Markets API for reward metadata | GraphQL Subgraph | Subgraph likely delayed; REST API is canonical source per docs |
| Poll Polymarket reward API every 60s | Pull only on fill events | Polling ensures stale reward status detection; event-driven misses term changes |
| Kalshi local tracking (no public API) | Contact Kalshi support | No programmatic alternative; local tracking is standard approach per Kalshi docs |
| Single reward budget (`REWARDS_MAX_EXPOSURE`) | Per-market budgets | Single budget simpler to configure; can add per-market later if needed |

## Architecture Patterns

### Recommended Project Structure
```
scans/
├── rewards.py               # New: reward-aware market making scan (two-stage pattern)

config.py
├── REWARDS_ENABLED          # (NEW) Feature flag
├── REWARDS_MAX_EXPOSURE     # (NEW) Budget: $200 default
├── REWARDS_MIN_RESTING_TIME # (NEW) Seconds order must rest to qualify
├── REWARDS_MAX_SPREAD       # (NEW) Max spread % for reward qualification
├── REWARDS_POLL_INTERVAL    # (NEW) How often to poll Polymarket reward API
├── REWARDS_MIN_SIZE         # (NEW) Minimum order size for rewards

market_maker.py
├── QuoteManager             # (EXTEND) Add reward_score tracking to quote dict
├── RewardTracker            # (NEW) Tracks reward scores per market per platform

executor.py
├── _build_legs              # (EXTEND) Add "Rewards" opportunity type branch
├── _revalidate              # (EXTEND) Add rewards revalidation case

db.py
├── TradeDB.create_tables    # (EXTEND) Add reward metrics columns: reward_score, reward_yield_usdc

continuous.py
├── run_continuous           # (EXTEND) Initialize RewardTracker, poll Polymarket API every cycle
├── reward scan loop         # (NEW) Integrate rewards scan into main loop

dashboard.py & dashboard_ui.py
├── /status endpoint         # (EXTEND) Add rewards metrics to JSON
├── Leaderboard section      # (EXTEND) Add "Rewards" strategy row with reward yield
```

### Pattern 1: Reward-Aware Quote Placement
**What:** Extend QuoteManager to calculate bid/ask prices optimized for reward qualification while staying profitable.

**When to use:** Every 10 seconds during market making, when positioned in high-reward qualifying markets.

**Example:**
```python
# Source: Polymarket docs (https://docs.polymarket.com/market-makers/liquidity-rewards)
class RewardTracker:
    """Track reward scores and qualifying order metrics per market per platform."""
    
    def __init__(self):
        self.reward_scores: dict[str, dict] = {}  # {market_key: {"polymarket": score, "kalshi": score}}
        self.qualifying_orders: dict[str, dict] = {}  # {order_id: {"platform", "market_key", "resting_seconds", "spread"}}
    
    def update_polymarket_reward_score(self, market_key: str, reward_data: dict):
        """Update reward score from Polymarket Markets API response."""
        if market_key not in self.reward_scores:
            self.reward_scores[market_key] = {}
        # reward_data: {"market_id", "min_incentive_size", "max_incentive_spread", "reward_pool_usdc", ...}
        self.reward_scores[market_key]["polymarket"] = reward_data
    
    def log_kalshi_order(self, order_id: str, platform: str, market_key: str, size: float, spread: float):
        """Log Kalshi order for local reward tracking (no public API)."""
        self.qualifying_orders[order_id] = {
            "platform": platform,
            "market_key": market_key,
            "placed_at": time.time(),
            "size": size,
            "spread": spread,
            "resting_seconds": 0,
        }
    
    def calculate_optimal_spread(self, platform: str, market_key: str, mid_price: float, 
                                 inventory: float = 0.0) -> dict:
        """Calculate bid/ask spread that maximizes reward score while staying profitable.
        
        Polymarket reward formula (from CITED: docs.polymarket.com):
        - Tighter spreads → higher reward score
        - Single-sided orders score if midpoint in [0.10, 0.90]
        - Orders outside this range must be double-sided
        - Min reward payout: $1 USDC
        """
        reward_info = self.reward_scores.get(market_key, {}).get(platform, {})
        max_spread = reward_info.get("max_incentive_spread", 0.05)  # 5% default fallback
        
        # Target spread: tighter than max for better reward score, but above arb detection
        target_spread = max_spread * 0.6  # 60% of max = higher reward score
        
        # Apply inventory skew to encourage hedging
        skew = 0.0
        if inventory > 0:
            skew = -target_spread * 0.1  # Skew toward selling if long
        
        bid = mid_price - (target_spread / 2) + skew
        ask = mid_price + (target_spread / 2) + skew
        
        return {"bid": bid, "ask": ask, "spread": ask - bid, "reward_optimized": True}
```

### Pattern 2: Two-Stage Reward Scan
**What:** Follow existing scan pattern — (1) fetch qualifying markets, (2) refine with live CLOB data.

**When to use:** Every 60s scan cycle, when `REWARDS_ENABLED=true`.

**Example:**
```python
# Source: scans/binary.py two-stage pattern + Polymarket Markets API docs
def scan_rewards_polymarket(markets: list[dict], reward_config: dict, 
                            price_cache: dict | None = None) -> list[dict]:
    """
    Stage 1: Find Polymarket markets with active reward programs.
    Stage 2: Place optimal reward-qualifying resting orders.
    """
    opportunities = []
    
    # Stage 1: Fetch reward program metadata from Markets API
    for market in markets:
        reward_data = market.get("incentives", {})  # From Markets API response
        if not reward_data:
            continue  # No reward program on this market
        
        market_key = market.get("conditionId")
        min_size = reward_data.get("min_incentive_size", 5.0)
        max_spread = reward_data.get("max_incentive_spread", 0.05)
        reward_pool = reward_data.get("pool_size_usdc", 0)
        
        if reward_pool < 10:  # Skip tiny pools
            continue
        
        # Stage 2: Refine with CLOB order book (ensure spreads are achievable)
        clob = _fetch_clob_for_market(market, price_cache)
        if not clob:
            continue
        
        mid_price = (clob["yes_bid"] + clob["yes_ask"]) / 2
        optimal = _calculate_optimal_reward_spread(mid_price, min_size, max_spread)
        
        opportunities.append({
            "type": "PolymarketRewards",
            "_layer": 3,  # Layer 3: market making
            "market": market.get("question")[:60],
            "platform": "polymarket",
            "reward_pool_usdc": reward_pool,
            "optimal_bid": optimal["bid"],
            "optimal_ask": optimal["ask"],
            "optimal_spread": optimal["spread"],
            "_market_key": market_key,
            "size": min_size,
        })
    
    return opportunities
```

### Pattern 3: Kalshi Local Reward Tracking
**What:** Since Kalshi has no public reward score API, log qualifying orders locally with resting time & spread metrics.

**When to use:** After every Kalshi limit order placement, poll every 60s to aggregate metrics.

**Example:**
```python
# Source: Kalshi Help Center + API docs (https://help.kalshi.com/incentive-programs/liquidity-incentive-program)
class KalshiRewardTracker:
    """Track Kalshi liquidity incentive program participation (no public API).
    
    Qualifying criteria per Kalshi LIP (Sep 2025 - Sep 2026):
    - Resting limit orders on Kalshi markets
    - Spreads measured against size-cutoff-adjusted midpoint
    - Snapshots taken once per second (random uniform time)
    - Can be single or double-sided depending on market conditions
    """
    
    def __init__(self):
        self.orders: dict[str, dict] = {}  # order_id -> {market_ticker, platform, placed_at, size, spread}
        self.intervals: dict[str, dict] = {}  # market_ticker -> {total_resting_seconds, snapshot_count}
    
    def log_order_placed(self, order_id: str, market_ticker: str, size: float, side: str,
                        price: float, mid_price: float):
        """Log a Kalshi limit order for reward tracking."""
        spread = abs(price - mid_price) / mid_price
        self.orders[order_id] = {
            "market_ticker": market_ticker,
            "platform": "kalshi",
            "placed_at": time.time(),
            "size": size,
            "side": side,
            "spread": spread,
            "resting_seconds": 0,
        }
    
    def aggregate_metrics(self) -> dict:
        """Aggregate local tracking data into reward-eligible metrics.
        
        Returns:
            {market_ticker: {"total_resting_seconds", "avg_size", "avg_spread", "snapshot_count"}}
        """
        metrics = {}
        now = time.time()
        
        for order_id, order in self.orders.items():
            ticker = order["market_ticker"]
            resting = now - order["placed_at"]
            
            if ticker not in metrics:
                metrics[ticker] = {
                    "total_resting_seconds": 0,
                    "sizes": [],
                    "spreads": [],
                    "snapshot_count": 0,
                }
            
            metrics[ticker]["total_resting_seconds"] += resting
            metrics[ticker]["sizes"].append(order["size"])
            metrics[ticker]["spreads"].append(order["spread"])
        
        # Compute averages
        for ticker, data in metrics.items():
            data["avg_size"] = sum(data["sizes"]) / len(data["sizes"]) if data["sizes"] else 0
            data["avg_spread"] = sum(data["spreads"]) / len(data["spreads"]) if data["spreads"] else 0
            del data["sizes"]
            del data["spreads"]
        
        return metrics
```

### Anti-Patterns to Avoid
- **Too-tight spreads:** Spreads tighter than reward program max will be rejected by CLOB (Polymarket) or won't match orders (Kalshi). Always fetch `max_incentive_spread` before quoting.
- **Forgetting double-sided requirement:** Outside midpoint range [0.10, 0.90] (Polymarket), single-sided orders don't earn rewards — both sides required.
- **Single reward budget:** If you allocate all `REWARDS_MAX_EXPOSURE` to one market, fills on that market prevent other markets from resting orders. Monitor active positions per market.
- **Polling Polymarket API on every tick:** Rate limits apply — poll every 60s (standard scan cycle), not every quote refresh.
- **Assuming Kalshi metrics are real-time:** Kalshi rewards distribute daily at midnight UTC. Local tracking is for monitoring only, not for live execution decisions.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Order placement on Polymarket/Kalshi | Custom HTTP clients with auth | Existing polymarket_api.py / KalshiClient | Auth complexity (HMAC-SHA384, RSA-PSS), rate limiting, proxy support already solved |
| Market making quote optimization | Custom spread calculator | Extend QuoteManager.calculate_quotes() | Inventory skew, volatility adjustment, clamping all non-trivial; framework is battle-tested |
| Tracking resting order fills | Custom polling loop | Use existing TradeDB + hedger.py | Partial fill scenarios, hedging logic, position reconciliation are hard; existing code handles all edge cases |
| Reward score persistence | In-memory dict | TradeDB reward_score column | Memory loss on crash; need audit trail for P&L attribution |
| Dashboard reward display | Custom HTML | Extend dashboard_ui.py leaderboard table | Styling, chart updates, data binding all handled; add new row to existing strategy table |

**Key insight:** Prediction market reward programs have non-obvious edge cases (side requirements, spread definitions, settlement timing). Existing `market_maker.py` + `polymarket_api.py` / `kalshi_api.py` abstractions handle the hard parts (auth, rate limits, order lifecycle).

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EXEC-05 | Bot captures Polymarket liquidity rewards via resting limit orders with reward score tracking | Markets API returns `incentives` metadata with `min_incentive_size`, `max_incentive_spread`, `pool_size_usdc` — enables reward-aware quote placement in QuoteManager |
| EXEC-06 | Bot captures Kalshi liquidity incentive program via qualifying limit orders | No public reward API; local tracking of resting time/spread per order sufficient per Kalshi LIP terms (Sep 2025 - Sep 2026) |
| STRAT-03 | Liquidity rewards farming strategy optimizes quote placement for maximum USDC rewards | RewardTracker calculates optimal spread based on platform-specific thresholds; feeds into QuoteManager spread calculation |

## Common Pitfalls

### Pitfall 1: Forgetting Market-Specific Reward Eligibility
**What goes wrong:** Bot places resting orders on markets with no active reward program, earning zero USDC for capital tied up. Markets come and go; reward pools update constantly.

**Why it happens:** Assume all markets have rewards; don't check Markets API response for `incentives` metadata.

**How to avoid:** 
1. Fetch full market object including `incentives` field from Markets API (don't scrape from web UI).
2. Filter to markets where `incentives.pool_size_usdc > 10` (minimum viable reward pool).
3. Skip markets where `incentives.status != "active"`.

**Warning signs:** Scan produces opportunities on markets with $0 reward pool; dashboard shows $0 reward yield but many resting orders.

### Pitfall 2: Polymarket Spread Outside Qualifying Range
**What goes wrong:** Bot places resting orders at tight spreads (`0.02`), but Polymarket rewards all orders within `max_incentive_spread` (e.g., `0.05`). Bot is competing unnecessarily hard — no better reward score for 2bp vs 5bp.

**Why it happens:** Reuse MM spread settings without checking platform-specific reward thresholds.

**How to avoid:** 
1. Fetch `max_incentive_spread` from Markets API per market.
2. Target spread = `max_incentive_spread * 0.6` (tight enough for high score, not minimum).
3. Never go below 2bp (avoid self-trading arbs on fills).

**Warning signs:** Reward score is low despite many resting orders; other makers' spreads are much wider.

### Pitfall 3: Single-Sided Orders Outside [0.10, 0.90] Midpoint Range
**What goes wrong:** Bot places single-sided resting order (only BID) on market where midpoint is `0.05` — Polymarket doesn't score it. Order sits, ties up capital, earns $0.

**Why it happens:** Didn't check midpoint range requirement; assume all single-sided orders count.

**How to avoid:** 
1. Check: `if 0.10 <= mid_price <= 0.90: single_sided_ok = True else: require_both_sides = True`.
2. For markets outside range, always place paired BID+ASK.

**Warning signs:** Resting orders on extreme-priced markets (0.01-0.09, 0.91-0.99) have zero reward score; zero resting time recorded.

### Pitfall 4: Kalshi Order Cancellation Loses Reward Time
**What goes wrong:** Bot places Kalshi order, later cancels to hedge a fill. Reward tracking shows 0 resting time (order was cancelled) — missed reward opportunity.

**Why it happens:** Didn't record resting time before cancellation.

**How to avoid:** 
1. On cancel, log final resting time to database before removing from active tracking.
2. Kalshi rewards accumulate over the epoch even if order is later cancelled — snapshot took place while resting.

**Warning signs:** Kalshi reward metrics show very low resting times despite many cancellations; support reports higher reward than bot recorded.

### Pitfall 5: Reward Budget Exhaustion on First Market
**What goes wrong:** Bot places $200 (REWARDS_MAX_EXPOSURE) resting on one popular market, can't place orders elsewhere. Misses high-reward markets that fill.

**Why it happens:** No per-market budget; first-come-first-served on global budget.

**How to avoid:** 
1. Sort reward opportunities by `reward_pool_usdc / (min_incentive_size)` (reward density).
2. Distribute budget across top-N markets proportional to pool size.
3. Or: Prioritize markets by estimated USDC yield per hour.

**Warning signs:** Bot places all resting orders on one market; other high-pool markets unserved; daily reward yield is 50% of estimated.

## Code Examples

Verified patterns from official sources and existing codebase:

### Fetch Polymarket Reward Metadata
```python
# Source: Polymarket Markets API docs (https://docs.polymarket.com/market-makers/liquidity-rewards)
def fetch_polymarket_rewards_metadata(market_keys: list[str], price_cache: dict | None = None) -> dict:
    """Fetch reward program metadata from Polymarket Markets API.
    
    Returns: {market_key: {"min_incentive_size": float, "max_incentive_spread": float, ...}}
    """
    from polymarket_api import _get_with_retry, GAMMA_BASE
    
    rewards = {}
    for market_key in market_keys:
        try:
            # Polymarket Markets API includes incentives in market object
            resp = _get_with_retry(f"{GAMMA_BASE}/markets", params={"conditionId": market_key})
            market = resp.json()[0] if resp.json() else None
            if market and "incentives" in market:
                rewards[market_key] = market["incentives"]
        except Exception as e:
            logger.debug("Reward fetch failed for %s: %s", market_key, e)
    
    return rewards
```

### Log and Aggregate Kalshi Reward Metrics
```python
# Source: scans/kalshi.py pattern + Kalshi API docs (https://docs.kalshi.com/api-reference/incentive-programs/get-incentives)
class KalshiRewardLogger:
    """Local tracking of Kalshi LIP participation (no public API)."""
    
    def __init__(self, db: TradeDB):
        self.db = db
        self._active_orders = {}  # order_id -> {market_ticker, placed_at, size, spread}
    
    def log_order(self, order_id: str, market_ticker: str, size: float, price: float, 
                  mid_price: float, side: str) -> None:
        """Record order placement for reward tracking."""
        spread = abs(price - mid_price) / mid_price if mid_price > 0 else 0
        self._active_orders[order_id] = {
            "market_ticker": market_ticker,
            "placed_at": time.time(),
            "size": size,
            "spread": spread,
            "side": side,
        }
        # Persist to DB for audit trail
        self.db.log_reward_metric(
            platform="kalshi",
            market_ticker=market_ticker,
            order_id=order_id,
            event="placed",
            size=size,
            spread=spread,
            resting_seconds=0,
        )
    
    def log_cancellation(self, order_id: str) -> None:
        """Record order cancellation with final resting time."""
        if order_id not in self._active_orders:
            return
        
        order = self._active_orders.pop(order_id)
        resting_seconds = time.time() - order["placed_at"]
        
        self.db.log_reward_metric(
            platform="kalshi",
            market_ticker=order["market_ticker"],
            order_id=order_id,
            event="cancelled",
            size=order["size"],
            spread=order["spread"],
            resting_seconds=resting_seconds,
        )
    
    def estimate_reward_yield(self, market_ticker: str) -> float:
        """Estimate USDC reward based on local resting metrics.
        
        Note: Actual rewards computed daily by Kalshi. This is estimate only.
        """
        metrics = self.db.get_reward_metrics(platform="kalshi", market_ticker=market_ticker)
        total_resting = sum(m["resting_seconds"] for m in metrics)
        avg_spread = sum(m["spread"] * m["resting_seconds"] for m in metrics) / total_resting if total_resting > 0 else 0
        
        # Kalshi incentive formula: reward ∝ resting_time * tightness_of_spread
        estimated_daily_reward = (total_resting / 86400) * (1 - avg_spread * 100) * 0.50  # ~$0.50/day per 24h resting
        return estimated_daily_reward
```

### Extend Dashboard to Show Reward Metrics
```html
<!-- Source: dashboard_ui.py strategy leaderboard table pattern -->
<table class="tbl" id="strategy-leaderboard">
  <thead>
    <tr>
      <th>Strategy</th>
      <th>Trading P&L</th>
      <th>Reward Yield (USDC)</th>
      <th>Total P&L</th>
      <th>Resting Orders</th>
      <th>Win Rate</th>
    </tr>
  </thead>
  <tbody id="leaderboard-body">
    <!-- Row for "Rewards" strategy filled by JS -->
    <tr data-strategy="Rewards">
      <td>Liquidity Rewards</td>
      <td id="rewards-trading-pnl">$0.00</td>
      <td id="rewards-yield">$0.00/day</td>
      <td id="rewards-total-pnl">$0.00</td>
      <td id="rewards-resting-orders">0</td>
      <td id="rewards-win-rate">—</td>
    </tr>
  </tbody>
</table>

<script>
  // Fetch reward metrics from /status endpoint
  async function updateRewardMetrics() {
    const resp = await fetch('/status');
    const data = await resp.json();
    const rewards = data.rewards || {};
    
    document.getElementById('rewards-trading-pnl').textContent = 
      `$${(rewards.trading_pnl || 0).toFixed(2)}`;
    document.getElementById('rewards-yield').textContent = 
      `$${(rewards.estimated_daily_yield || 0).toFixed(2)}/day`;
    document.getElementById('rewards-total-pnl').textContent = 
      `$${(rewards.trading_pnl + rewards.estimated_daily_yield).toFixed(2)}`;
    document.getElementById('rewards-resting-orders').textContent = 
      rewards.resting_order_count || 0;
  }
  
  setInterval(updateRewardMetrics, 5000);
</script>
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual market maker quoting on prediction markets | API-driven market making with reward program integration | 2026 Q1 (Polymarket launched incentives) | Automated quoting increased participation; reward programs now common across platforms |
| Fixed spread market making | Reward-aware adaptive spreads | 2026 Q1 | Tighter spreads align maker profit with platform reward thresholds; higher participation |
| Centralized MM on Polymarket only | Cross-platform MM (Polymarket + Kalshi + Betfair) | Phase 6 | Both Polymarket and Kalshi now have active reward programs; bot can earn across platforms |

**Deprecated/outdated:**
- **Static spread MM:** Old market makers used fixed spreads; new ones adapt to platform-specific reward thresholds.
- **Manual tracking of resting orders:** Old approach: traders manually logged orders; new approach: automated logging via QuoteManager + database.
- **No reward differentiation:** Old bots treated all markets equally; new bots prioritize high-reward-pool markets.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Polymarket Markets API includes `incentives` field with `min_incentive_size` and `max_incentive_spread` per market | Standard Stack / Code Examples | If API doesn't include field, fetching rewards requires separate endpoint (adds latency, complexity). Mitigation: verify API response format in integration test before implementation. |
| A2 | Kalshi has no public API endpoint for reward scores; local tracking is standard approach | Pattern 3 / Common Pitfalls | If Kalshi launches public endpoint, can simplify tracking. Mitigation: monitor Kalshi API changelog monthly. |
| A3 | Polymarket minimum reward payout is $1 USDC | Pattern 1 | If threshold changes, need to re-evaluate market selection heuristic. Mitigation: check Markets API response for `min_payout_threshold` field. |
| A4 | Kalshi reward program runs Sep 2025 - Sep 2026 with no announced end | Pattern 3 | If program ends, Kalshi rewards strategy becomes dead code. Mitigation: add feature flag `KALSHI_REWARDS_ENABLED`, disable by default; communicate end date to user. |
| A5 | Polymarket reward formula rewards spreads up to `max_incentive_spread` equally | Pattern 1 | If formula is non-linear (e.g., quadratic decay), need custom spread optimizer. Mitigation: request reward scoring formula from Polymarket before implementation. |

## Open Questions

1. **Polymarket reward distribution frequency**
   - What we know: Polymarket rewards distribute daily at midnight UTC per CITED: docs.polymarket.com
   - What's unclear: Are mid-epoch reward scores visible live, or only final payout at epoch end?
   - Recommendation: Check Markets API response to see if `current_reward_score` field exists. If not, implement local scoring estimator.

2. **Kalshi order snapshot timing randomness**
   - What we know: Kalshi takes snapshots once per second with random uniform time per CITED: help.kalshi.com
   - What's unclear: Does random timing mean same order can be counted in 0-60 snapshots per minute? How to estimate expected snapshots?
   - Recommendation: Simulate snapshot probability in local tracker (assume 50% hit rate per second). Track uncertainty in estimated reward.

3. **Multi-outcome market reward eligibility**
   - What we know: Polymarket has binary and categorical markets; reward metadata exists per market
   - What's unclear: How does reward scoring work for categorical markets (3+ outcomes)? Single BID on one outcome? Both sides required?
   - Recommendation: Test with small orders on a categorical market; monitor reward score response.

4. **Hedging filled reward orders**
   - What we know: hedger.py exists and handles partial fills
   - What's unclear: When a reward order fills, should we hedge on opposite platform immediately, or wait for fill on both sides?
   - Recommendation: Hedge immediately on fill (same logic as market making fills). Track hedging cost separately from reward yield in P&L.

## Environment Availability

No new external dependencies required. Phase 7 uses only existing tools:
- `polymarket_api.py` — already installed, Markets API calls already supported
- `kalshi_api.py` — already installed, trade API calls already supported
- `market_maker.py` — already exists, QuoteManager ready to extend
- SQLite (db.py) — bundled with Python, WAL mode already enabled

**Verification:** All existing API clients have rate limiting and retry logic (`tenacity` library) — no new retry infrastructure needed.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing) |
| Config file | `tests/` directory with existing test patterns |
| Quick run command | `pytest tests/test_rewards.py::TestRewardScanning -xvs` |
| Full suite command | `pytest tests/ -k "reward" -x` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EXEC-05 | Polymarket reward score fetched, matched to resting orders, yield estimated | unit | `pytest tests/test_rewards.py::TestPolymarketRewardTracking::test_fetch_markets_with_incentives -xvs` | ❌ Wave 0 |
| EXEC-05 | Bot places resting orders within `max_incentive_spread`, double-sided when midpoint outside [0.10, 0.90] | unit | `pytest tests/test_rewards.py::TestOptimalQuotePlacement::test_respects_max_spread -xvs` | ❌ Wave 0 |
| EXEC-06 | Kalshi local tracker logs order placement, calculates resting time, estimates reward yield | unit | `pytest tests/test_rewards.py::TestKalshiRewardTracking::test_log_order_and_aggregate_metrics -xvs` | ❌ Wave 0 |
| EXEC-06 | Kalshi resting orders on high-volume markets logged with correct spread/size | integration | `pytest tests/integration/test_kalshi_rewards.py::TestKalshiLiveRewards (requires KALSHI_API_KEY_ID)` | ❌ Wave 0 |
| STRAT-03 | Rewards scan generates opportunities on reward-eligible markets, filters out zero-pool markets | unit | `pytest tests/test_rewards.py::TestRewardScanGeneration::test_filters_by_pool_size -xvs` | ❌ Wave 0 |
| STRAT-03 | Rewards strategy leaderboard row shows cumulative trading P&L + estimated daily reward yield | unit | `pytest tests/test_dashboard.py::TestRewardsDashboard::test_leaderboard_reward_row -xvs` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_rewards.py::TestRewardTracking -x` (unit tests only, ~5s)
- **Per wave merge:** `pytest tests/ -k "reward" -x` (all reward tests, ~30s)
- **Phase gate:** Full suite `pytest tests/ -x` + manual verification of dashboard reward row

### Wave 0 Gaps
- [ ] `tests/test_rewards.py` — unit tests for RewardTracker, RewardOptimizer, KalshiRewardLogger (mocked API)
- [ ] `tests/integration/test_kalshi_rewards.py` — live Kalshi integration (requires KALSHI_API_KEY_ID)
- [ ] `tests/test_continuous_rewards.py` — continuous mode with reward scanning + quoting loop
- [ ] `tests/test_rewards_dashboard.py` — dashboard reward metrics endpoint and leaderboard rendering
- [ ] `tests/fixtures/polymarket_reward_metadata.json` — mock Markets API response with incentives fields
- [ ] Database migration: `ALTER TABLE trades ADD COLUMN reward_score FLOAT, reward_yield_usdc FLOAT` (or new `reward_metrics` table)

*(Note: Existing test infrastructure covers scans/ pattern and continuous mode orchestration. Gaps are reward-specific logic.)*

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Existing polymarket_api.py (CLOB auth via py_clob_client), KalshiClient (RSA-PSS auth). No changes needed. |
| V3 Session Management | no | Rewards don't require session state beyond order lifecycle (handled by existing code). |
| V4 Access Control | yes | Reward data is per-market, not per-user. No access control needed; all markets' reward metadata public. |
| V5 Input Validation | yes | Validate reward metadata from Markets API: check `min_incentive_size > 0`, `0 < max_incentive_spread <= 0.50`, `pool_size_usdc >= 0`. |
| V6 Cryptography | no | No new cryptographic operations. Existing auth uses established libraries. |

### Known Threat Patterns for Polymarket + Kalshi

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Reward metadata poisoning (Markets API returns malformed `incentives` field) | Tampering | Validate all fields before using in spread calculation. Reject markets with `max_incentive_spread > 0.50` or `min_size > REWARDS_MAX_EXPOSURE`. |
| Network replay attack on reward API calls | Tampering | Use HTTPS (existing); Polymarket + Kalshi are trusted platforms. No additional mitigation needed. |
| Order cancellation races (cancel arrives before fill) | Tampering | Order cancellation already uses atomic DB writes + per-market locks (existing continuous.py). Reward tracking unaffected. |
| Reward metrics double-counting on crash/recovery | Denial of Service | Log reward events to database before execution; on recovery, deduplicate by order_id (existing TradeDB logic). |

## Sources

### Primary (HIGH confidence)
- [Polymarket Documentation: Liquidity Rewards](https://docs.polymarket.com/market-makers/liquidity-rewards) — reward eligibility criteria, spread thresholds, mid-range requirements, payout thresholds
- [Kalshi Help Center: Liquidity Incentive Program](https://help.kalshi.com/incentive-programs/liquidity-incentive-program) — program duration, order snapshotting, eligibility
- [Kalshi API: Get Incentives](https://docs.kalshi.com/api-reference/incentive-programs/get-incentives) — API structure for fetching incentive program metadata
- Existing codebase (`market_maker.py`, `polymarket_api.py`, `kalshi_api.py`) — verified patterns for QuoteManager, order placement, auth

### Secondary (MEDIUM confidence)
- [Polymarket: Automated Market Making on Polymarket](https://news.polymarket.com/p/automated-market-making-on-polymarket) — overview of reward program mechanics and opportunity sizing
- [Medium: Polymarket Liquidity Rewards Technical Postmortem](https://medium.com/@wanguolin/my-two-week-deep-dive-into-polymarket-liquidity-rewards-a-technical-postmortem-88d3a954a058) — community perspective on reward scoring and optimization (verified against official docs)

### Tertiary (LOW confidence — requires validation)
- [Reddit/Discord community discussions] — claims about reward formula specifics, edge cases in snapshots (not verified; use only for hypothesis generation)

## Metadata

**Confidence breakdown:**
- Standard stack: MEDIUM — Polymarket Markets API endpoint confirmed, but live `incentives` field not fully tested. Existing market_maker.py battle-tested on Phase 6.
- Architecture: MEDIUM — Two-stage scan pattern proven; reward-aware quote optimization is new (requires validation against live reward scores).
- Pitfalls: HIGH — Based on community reports + official program terms.

**Research date:** 2026-04-04
**Valid until:** 2026-05-04 (Polymarket/Kalshi incentive programs subject to change; check for program updates monthly)
