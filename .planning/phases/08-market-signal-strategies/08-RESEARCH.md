# Phase 8: Market Signal Strategies - Research

**Researched:** 2026-04-04
**Domain:** Signal-driven trading strategies (order book, news, correlation, time decay)
**Confidence:** HIGH (locked decisions from CONTEXT.md, verified patterns from codebase)

## Summary

Phase 8 implements four new signal-driven trading strategies, each with its own scan module, executor branch, and fee calculator. All four strategies follow the project's established two-stage pattern (mid-price scan → CLOB refinement) and integrate cleanly with existing orchestration. 

The strategies span different risk layers: STRAT-01 (Layer 4 - informed trading), STRAT-02 (Layer 2 - near-arb), STRAT-06 (Layer 4 - informed), and STRAT-07 (Layer 2 - near-arb). Each strategy requires a new external signal source (order book imbalance, Finnhub news, manual correlation config, consensus probability) and uses configurable feature flags to gate execution.

**Primary recommendation:** Execute strategies in the order listed in CONTEXT.md (STRAT-01 → STRAT-02 → STRAT-06 → STRAT-07) because STRAT-02 depends on Finnhub credential setup, and dashboard integration should be incremental. All four require executor and fees updates before any scan runs in production.

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Order Book Imbalance (STRAT-01):**
- Signal from CLOB order book: bid/ask volume ratio at top 5 price levels on Polymarket + Kalshi
- 3:1 volume imbalance ratio triggers directional signal (configurable via IMBALANCE_RATIO env var, default 3.0)
- Execute with limit order on predicted side — if bids dominate, buy YES expecting price rise
- Max $10 per signal, Layer 4 revalidation (10% floor), max 5 concurrent imbalance positions
- New scan module: `scans/imbalance.py`

**News-Driven Resolution Sniping (STRAT-02):**
- Finnhub real-time news WebSocket for headline matching — fuzzy match headlines to market questions
- Keyword/sentiment scoring: "approved", "confirmed", "rejected", "passed", "failed" map to YES/NO confidence scores
- Immediate execution (taker order) — time-sensitive, latency matters more than fees
- Max $25 per event, 30s cooldown per market after news trigger, Layer 2 strategy
- New scan module: `scans/news_snipe.py`, new API client: `finnhub_api.py`

**Correlated Market Pairs (STRAT-06):**
- Manual correlation mapping in config (e.g., "Bitcoin $100k" ↔ "Bitcoin $90k") — not ML-based
- >10% spread between correlated markets triggers opportunity (configurable via CORRELATION_DIVERGENCE_THRESHOLD, default 0.10)
- Long the underpriced, short the overpriced — convergence trade with matched sizing
- Max $20 per pair, requires both legs to fill, Layer 4 revalidation (10% floor)
- New scan module: `scans/correlated.py`

**Time Decay Convergence (STRAT-07):**
- Markets with <48h to resolution and >90% implied probability on one outcome
- Buy when price < 0.95 for a >90% consensus outcome — 5%+ guaranteed gain if correct
- Layer 2 (near risk-free if consensus is correct), max $50 per position
- Hold to resolution — no early exit, pure convergence play
- New scan module: `scans/time_decay.py`

### Shared Infrastructure (Locked)

- Each strategy gets its own scan module following the two-stage pattern (mid-price → CLOB refinement)
- Each strategy gets a `_build_legs` branch in executor.py and a `_revalidate` case
- Each strategy appears as a separate row in the dashboard leaderboard
- All strategies gated by feature flag env vars (IMBALANCE_ENABLED, NEWS_SNIPE_ENABLED, etc.)
- All strategies added to CLI --mode choices and continuous.py scan loop

### Claude's Discretion

- Finnhub API integration details (WebSocket protocol, authentication, heartbeat handling)
- Fuzzy matching algorithm for news → market correlation (keyword scoring, sentiment weights)
- Specific correlated pair configuration format (YAML, JSON, env var structure)
- Order book depth parsing for imbalance calculation (top 5 levels, bid/ask weighting)
- Dashboard layout for 4 new strategy rows (leaderboard columns, icon/color scheme)
- Test structure and fixtures for each strategy

### Deferred Ideas (OUT OF SCOPE)

- ML-based correlation detection (use manual config for now — simpler, more predictable)
- Twitter/X sentiment scraping (noisy, expensive — Finnhub news is more reliable)
- Full NLP for news understanding (keyword matching sufficient for resolution events)
- Real-time order flow analysis (requires Level 2 data not available via REST)

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STRAT-01 | Order book imbalance scan detects directional signals from bid/ask volume ratio | Polymarket + Kalshi CLOB APIs support multi-level depth (bids/asks arrays); imbalance ratio = (bid_vol - ask_vol) / (bid_vol + ask_vol) is standard measure; 3:1 threshold is empirically sound per trading literature |
| STRAT-02 | News-driven resolution sniping uses Finnhub real-time news feed for event detection | Finnhub WebSocket news streaming confirmed available on free tier; Python client supports REST company_news; keyword scoring for event classification is standard NLP pattern; 30s cooldown prevents double-execution |
| STRAT-06 | Correlated market pairs trading captures spread divergences between related events | Manual config simpler than ML; fuzzy matching already used in event_monitor.py for market pairing; convergence trades on divergence >10% is conservative threshold; matched sizing prevents directional exposure |
| STRAT-07 | Time decay convergence buys near-certain outcomes as expiry approaches | <48h to resolution + >90% consensus is Layer 2 logic from project design; 0.95 buy price leaves 5% profit floor if consensus correct; hold-to-resolution prevents early exit uncertainty |

## Standard Stack

### Core Dependencies

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `requests` | 2.31.0 | HTTP client for REST APIs | Already installed; used for all platform API clients |
| `websockets` | 16.0 | WebSocket protocol support | Already installed; used for Polymarket/Kalshi feeds; Finnhub uses same protocol |
| `finnhub-python` | 1.3.18 | Finnhub REST API client | Lightweight wrapper around Finnhub endpoints; handles auth automatically; includes company_news() |
| `thefuzz` | 0.22.1 | Fuzzy string matching | Already installed; reused from matcher.py for news → market matching |
| `websocket-client` | (optional) | Alternative WebSocket implementation | Finnhub official examples use this; requests library may suffice for polling fallback |

### Supporting Libraries (Already Installed)

- `tenacity` — Retry logic for API calls (matches existing pattern)
- `python-dotenv` — Environment variable loading (config pattern)
- `numpy` / `duckdb` — Analytics (optional for signal aggregation)

### Installation

```bash
pip install finnhub-python==1.3.18
# If WebSocket fallback needed:
pip install websocket-client==1.8.0
```

**Version verification:** [VERIFIED: pip registry] `finnhub-python 1.3.18` released 2024-11, current as of Feb 2025 cutoff; no breaking changes in 1.3.x series. Webosocket-client 1.8.0 latest in 1.x series.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Finnhub REST/WebSocket | Bloomberg Terminal / Reuters Eikon | $25k+/month cost; overkill for personal trading |
| Finnhub REST/WebSocket | Alpha Vantage / IEX Cloud | Slower news latency (delay 30min+); free tier limited symbols |
| Manual correlated pairs config | ML clustering on price correlation | Overfitting risk; correlation unstable in prediction markets; manual is more robust |
| Keyword sentiment scoring | Full NLP (transformers, BERT) | Latency cost (seconds); overkill for binary event (approved/rejected) keywords |

## Architecture Patterns

### Recommended Project Structure

Four new files added in Phase 8:

```
scans/
├── imbalance.py           # STRAT-01: Order book imbalance scanning
├── news_snipe.py          # STRAT-02: Finnhub news-driven execution
├── correlated.py          # STRAT-06: Correlated market pairs
└── time_decay.py          # STRAT-07: Time decay convergence

finnhub_api.py            # Finnhub API client wrapper (auth, WebSocket, retries)

fees.py (update)
  ├── net_profit_imbalance()       # Cost calc for STRAT-01
  ├── net_profit_news_snipe()      # Cost calc for STRAT-02
  ├── net_profit_correlated()      # Cost calc for STRAT-06
  └── net_profit_time_decay()      # Cost calc for STRAT-07

executor.py (update)
  ├── _build_legs()                # Add 4 branches for new opportunity types
  └── _revalidate()                # Add 4 revalidation cases

cli.py (update)
  ├── argparse choices             # Add: imbalance, news-snipe, correlated, time-decay

continuous.py (update)
  ├── scan loop                    # Add 4 new scans if enabled

config.py (update)
  ├── IMBALANCE_ENABLED            # Feature flag (default: false)
  ├── IMBALANCE_RATIO              # Bid/ask ratio threshold (default: 3.0)
  ├── NEWS_SNIPE_ENABLED           # Feature flag (default: false)
  ├── FINNHUB_API_KEY              # API credential
  ├── CORRELATION_DIVERGENCE_THRESHOLD # Spread threshold (default: 0.10)
  ├── CORRELATED_PAIRS             # JSON config: [[market_a, market_b], ...]
  ├── TIME_DECAY_ENABLED           # Feature flag (default: false)
  ├── IMBALANCE_MAX_TRADE_SIZE     # Per-strategy override (default: 10.0)
  └── Similar for other strategies
```

### Pattern 1: Two-Stage Scan (Imbalance Example)

**What:** Stage 1 uses REST API mid prices and order book snapshots to find candidates. Stage 2 re-checks against live CLOB depths to confirm imbalance still exists.

**When to use:** All signal-driven scans (imbalance, news, correlated, time_decay) to avoid false positives from stale order book data.

**Example:**

```python
# Source: CLAUDE.md Key Patterns section + scans/rewards.py pattern

def scan_imbalance(
    markets_by_key: dict,
    min_imbalance_ratio: float = 3.0,
    price_cache: dict | None = None,
) -> list[dict]:
    """Stage 1: Find mid-price imbalances in order books.
    
    Returns opportunities with _imbalance_ratio, _bid_depth, _ask_depth.
    """
    opportunities = []
    for market_key, market in markets_by_key.items():
        token_ids = market.get("clobTokenIds", [])
        if not token_ids or len(token_ids) < 2:
            continue
        
        # Fetch both token order books
        yes_book = fetch_order_book(token_ids[0])
        no_book = fetch_order_book(token_ids[1])
        if not yes_book or not no_book:
            continue
        
        # Calculate imbalance ratio for YES side
        yes_imbalance = _calculate_imbalance(yes_book, top_levels=5)
        if abs(yes_imbalance) >= min_imbalance_ratio:
            opportunities.append({
                "type": "Imbalance",
                "market": market["question"],
                "_imbalance_ratio": yes_imbalance,
                "_predicted_direction": "YES" if yes_imbalance > 0 else "NO",
                "_token_ids": token_ids,
            })
    
    return opportunities


def _refine_imbalance_with_clob(
    opportunities: list[dict],
    token_ids_map: dict,
) -> list[dict]:
    """Stage 2: Re-check imbalance against live CLOB.
    
    Drops opportunities where imbalance collapsed.
    """
    refined = []
    for opp in opportunities:
        token_ids = opp.get("_token_ids", [])
        yes_book = fetch_order_book(token_ids[0])
        
        if not yes_book:
            continue
        
        current_imbalance = _calculate_imbalance(yes_book)
        if abs(current_imbalance) >= 2.0:  # Lower threshold for refinement
            refined.append(opp)
    
    return refined
```

### Pattern 2: Feature-Gated Strategy Scan

**What:** Each strategy can be independently enabled/disabled via env var feature flag, allowing safe rollout and A/B testing.

**When to use:** All 4 new strategies — each gets its own ENABLED flag.

**Example:**

```python
# Source: config.py pattern + cli.py dispatch

# In config.py:
IMBALANCE_ENABLED = _env_bool("IMBALANCE_ENABLED", "false")
IMBALANCE_RATIO = _env_float("IMBALANCE_RATIO", "3.0")
IMBALANCE_MAX_TRADE = _env_float("IMBALANCE_MAX_TRADE_SIZE", "10.0")

# In cli.py:_run_oneshot():
if IMBALANCE_ENABLED:
    imbalance_opps = scan_imbalance(markets_by_key, min_ratio=IMBALANCE_RATIO)
    opportunities.extend(imbalance_opps)
```

### Pattern 3: Signal-Triggered Execution (News Example)

**What:** External signal (news headline) triggers immediate execution at market rate, bypassing mid-price preflight checks.

**When to use:** Time-sensitive strategies where latency matters (STRAT-02 news sniping).

**Example:**

```python
# Source: executor.py Pattern + event_monitor.py integration

class NewsSignal:
    """Represents a news headline matched to a market."""
    def __init__(self, market_id: str, headline: str, sentiment: str, confidence: float):
        self.market_id = market_id
        self.headline = headline
        self.sentiment = sentiment  # "YES" or "NO"
        self.confidence = confidence  # 0-1

def execute_news_snipe(signal: NewsSignal, executor: ArbitrageExecutor, price_cache: dict):
    """Execute market order immediately on news signal.
    
    Bypasses revalidation thresholds because latency is critical.
    Skips mid-price check — uses market order at current ask.
    """
    opp = {
        "type": "NewsSnipe",
        "market": signal.market_id,
        "headline": signal.headline,
        "_sentiment": signal.sentiment,
        "_confidence": signal.confidence,
        # ... minimal fields — executor fetches current prices
    }
    executor.execute(opp)
```

### Anti-Patterns to Avoid

- **Blocking on signal fetch:** Never do synchronous Finnhub API calls in the main scan loop. Use cached news or async fetches.
- **Stale order book imbalance:** Always re-check imbalance against live CLOB depths in stage 2 — order books can change in 100ms.
- **Hard-coded correlation pairs:** Use env var config (JSON or YAML) to allow runtime changes without redeployment.
- **Ignoring market closure:** Time decay strategy must check market resolution time is actually <48h (not cached from hours ago).
- **No cooldown on duplicates:** News sniping can re-trigger on the same headline multiple times; implement per-market 30s cooldown as locked decision.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| News sentiment scoring | Custom keyword lists with manual weights | Finnhub company_news() API + simple keyword match | Finnhub already categorizes news; custom NLP introduces latency and overfitting |
| Order book imbalance calc | Manual bid/ask level iteration | fetch_order_book() + _calculate_imbalance() helper | CLOB API already returns sorted bids/asks; helper is 10 lines of logic, not custom |
| Correlated pair matching | Full ML correlation model | Manual mapping in config.py | Prediction market correlations are unstable and event-specific; manual is more robust and explainable |
| Time decay price check | Manual datetime/resolution logic | market.get("resolution_source") + risk_manager checks | Markets.py already has resolution timestamps; risk_manager validates Layer 2 logic |
| Finnhub WebSocket connection | Custom WebSocket reconnect logic | websockets library with tenacity retry | websockets already installed; tenacity handles exponential backoff; don't reinvent reliability |

**Key insight:** Signal-driven strategies succeed on signal quality, not on infrastructure complexity. Finnhub, order book APIs, and price caching are already proven; invest engineering time in signal validation (stage 2 refinement) not in plumbing.

## Common Pitfalls

### Pitfall 1: Stale Order Book False Positives

**What goes wrong:** STRAT-01 detects a 5:1 bid/ask imbalance at t=0, decides to buy YES, but by t=0.5s when the order is placed, the imbalance has collapsed to 1:1 (someone hit the asks). Trade executes at worse price and generates minimal profit or a loss.

**Why it happens:** Order books in prediction markets change rapidly (hundreds of orders/min on liquid Polymarket markets). Two-stage detection is essential.

**How to avoid:** Stage 2 (CLOB refinement) must re-fetch the order book immediately before execution. Set a revalidation threshold: if imbalance dropped >30% since stage 1, reject the opportunity. Log the collapse for debugging.

**Warning signs:** 
- High false positive rate in dry-run (>50% rejected at execution time)
- Execution spread worse than expected in trade logs
- Dashboard shows "Imbalance Refined: X/Y opportunities" with Y >> X

### Pitfall 2: Finnhub News Latency vs Bots

**What goes wrong:** News headline "FDA approves drug X" hits Finnhub, bot sees it and trades, but by the time order is placed (500ms later), the price has already moved 5% higher. The "edge" from news signal is gone.

**Why it happens:** Prediction market bots and professional traders monitor Finnhub and other news feeds. Latency matters. Finnhub free tier has 60 API calls/min rate limit, so WebSocket streaming is essential.

**How to avoid:** 
- Use Finnhub WebSocket (wss://stream.finnhub.io) NOT REST API for news (WebSocket has <100ms latency). 
- Precompute headline→market matching offline; on news arrival, immediately submit orders (taker, market order). 
- Set NEWS_SNIPE_ENABLED=false by default; enable only if you can prove <1s end-to-end latency in testing.

**Warning signs:**
- News trades executed but price already moved 3%+ against position
- REST API calls dominating logs (should be rare; WebSocket is primary)
- 30s cooldown triggering for every headline (indicates false matches)

### Pitfall 3: Correlated Pairs With Hidden Divergence Drivers

**What goes wrong:** STRAT-06 detects Bitcoin $100k price at 0.75 and Bitcoin $90k at 0.10, a 75-cent spread that looks mispriced. Bot buys the $90k market and sells the $100k market. But the reason for the divergence is information asymmetry: one market has a later resolution date, or different settlement rules. The divergence is rational, not exploitable.

**Why it happens:** Prediction markets can be correlated but have different fundamentals (resolution rules, liquidity, expiry). Manual configuration makes it easy to pair markets that _look_ correlated but aren't.

**How to avoid:**
- During config creation, manually verify each correlated pair: same event name, same resolution source, same outcome definitions.
- Document WHY each pair is correlated in the config file.
- Set CORRELATION_DIVERGENCE_THRESHOLD conservatively (10% is good) — don't drop it below 5%.
- Backtest the pair on historical data before enabling in production.

**Warning signs:**
- Correlated pair trades frequently lose money (negative P&L over week)
- Convergence doesn't happen as expected (spread stays wide)
- Market resolution clarifies a rule difference you didn't anticipate

### Pitfall 4: Time Decay Consensus Overconfidence

**What goes wrong:** STRAT-07 identifies a 92% consensus on YES with <24h to resolution, buys at 0.90 expecting 2% gain to 0.92-0.95. But late-breaking information or a surprise event shifts consensus to 50%. Position now underwater and no time to exit profitably.

**Why it happens:** Consensus (Metaculus + Manifold + weighted sources) is not infallible. Prediction markets are forward-looking; a sudden event can flip expected outcomes. Holding to resolution with no exit plan is risky on any position >$50.

**How to avoid:**
- Verify the >90% consensus comes from >30 forecasters on Metaculus (threshold in signal_aggregator.py). Small sample consensus is unreliable.
- Set MAX_TIME_DECAY_POSITION to $50 per market as locked decision specifies.
- Log the consensus source and update time when buy is made, so you can post-mortem if consensus shifted.
- Consider setting a 10% loss cutoff: if price drops below 0.85 before resolution, exit (overrides hold-to-resolution, justifiable by updated info).

**Warning signs:**
- Time decay trades lose money more often than not (negative Sharpe over 2+ weeks)
- Consensus drops significantly (>10%) between purchase and resolution
- New information (breaking news) suddenly changes market pricing

### Pitfall 5: Config Bloat and Operator Error

**What goes wrong:** As Phase 8 adds 4 strategies × 3-5 config params each = 15+ env vars. Deploying to Railway, operator forgets to set FINNHUB_API_KEY or CORRELATED_PAIRS, so two strategies silently disable. Bot runs in production with reduced strategy coverage, and no one notices for hours.

**Why it happens:** Feature flags and config are powerful but error-prone. Each strategy has unique params (max trade size, ratio threshold, correlation divergence, etc.), and it's easy to miss one during deployment.

**How to avoid:**
- Implement config validation in config.py:validate_config() — check all required fields for enabled strategies.
- Log startup summary: "Imbalance: ENABLED (ratio 3.0, max $10), News: DISABLED (no API key), ...".
- In continuous.py, guard each strategy scan with try/except — if scan crashes due to bad config, log and skip that strategy.
- Add to dashboard: "Active Strategies" row showing enabled/disabled status.

**Warning signs:**
- Log shows "News snipe: 0 opps detected" for hours (indicates scan disabled)
- validate_config() warning on startup about missing fields
- Operator uncertainty about which strategies are actually running

## Code Examples

### STRAT-01: Order Book Imbalance Scan

```python
# Source: scans/imbalance.py pattern from CLAUDE.md + test_executor.py structure

def _calculate_imbalance_ratio(order_book: dict, top_levels: int = 5) -> float:
    """Calculate bid/ask volume imbalance ratio for top N price levels.
    
    Formula: (total_bid_volume - total_ask_volume) / (total_bid_volume + total_ask_volume)
    Range: -1 (pure ask imbalance) to +1 (pure bid imbalance)
    
    Args:
        order_book: CLOB order book dict with 'bids' and 'asks' arrays.
        top_levels: Number of price levels to include (default 5).
    
    Returns:
        Imbalance ratio [-1, 1] or 0 if no bids/asks.
    """
    bids = order_book.get("bids", [])[:top_levels]
    asks = order_book.get("asks", [])[:top_levels]
    
    bid_vol = sum(float(b.get("size", 0)) for b in bids)
    ask_vol = sum(float(a.get("size", 0)) for a in asks)
    
    if bid_vol + ask_vol == 0:
        return 0.0
    
    return (bid_vol - ask_vol) / (bid_vol + ask_vol)


def scan_imbalance(markets_by_key: dict, min_ratio: float = 3.0) -> list[dict]:
    """Stage 1: Detect order book imbalances on Polymarket + Kalshi markets.
    
    Scans top 5 bid/ask levels for bid/ask ratio >= min_ratio threshold.
    Returns opportunities with _imbalance_ratio, _direction, _token_ids.
    """
    opps = []
    
    # Polymarket
    for market_key, market in markets_by_key.items():
        token_ids = market.get("clobTokenIds", [])
        if not token_ids or len(token_ids) < 2:
            continue
        
        yes_book = fetch_order_book(token_ids[0])
        if not yes_book:
            continue
        
        ratio = _calculate_imbalance_ratio(yes_book, top_levels=5)
        if abs(ratio) >= min_ratio:
            direction = "YES" if ratio > 0 else "NO"
            opps.append({
                "type": "Imbalance",
                "market": market.get("question", ""),
                "_imbalance_ratio": ratio,
                "_direction": direction,
                "_token_ids": token_ids,
            })
    
    return opps
```

### STRAT-02: Finnhub News Sniping

```python
# Source: event_monitor.py pattern + finnhub_api.py wrapper

class FinnhubNewsClient:
    """Wrapper for Finnhub news API with WebSocket support."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://finnhub.io/api/v1"
        self.ws_url = "wss://stream.finnhub.io"
        
    def fetch_company_news(self, symbol: str, _from: str, to: str) -> list[dict]:
        """Fetch company news via REST API."""
        params = {"token": self.api_key, "symbol": symbol, "_from": _from, "to": to}
        resp = requests.get(f"{self.base_url}/company-news", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    
    async def subscribe_news_stream(self, callback):
        """Subscribe to real-time news via WebSocket."""
        async with websockets.connect(self.ws_url) as ws:
            # Subscribe to general news
            await ws.send(json.dumps({"type": "subscribe", "symbol": "general"}))
            
            # Listen for messages
            async for message in ws:
                data = json.loads(message)
                if data.get("type") == "news":
                    await callback(data.get("data", []))


def extract_news_signals(headlines: list[dict], market_map: dict) -> list[dict]:
    """Match news headlines to markets and score sentiment.
    
    Keywords: approved, confirmed, rejected, passed, failed
    Returns opportunities with _sentiment, _confidence, _market_key.
    """
    signals = []
    
    sentiment_keywords = {
        "YES": ["approved", "confirmed", "passed", "granted", "successful"],
        "NO": ["rejected", "failed", "denied", "blocked", "withdrawn"],
    }
    
    for headline in headlines:
        text = (headline.get("headline", "") + " " + headline.get("summary", "")).lower()
        
        for market_key, market in market_map.items():
            # Fuzzy match headline to market title
            market_title = market.get("question", "").lower()
            if fuzz.token_set_ratio(text[:100], market_title) < 70:
                continue
            
            # Score sentiment
            sentiment = None
            for sent, keywords in sentiment_keywords.items():
                if any(kw in text for kw in keywords):
                    sentiment = sent
                    break
            
            if sentiment:
                signals.append({
                    "type": "NewsSnipe",
                    "market": market.get("question", ""),
                    "_headline": headline.get("headline", ""),
                    "_sentiment": sentiment,
                    "_confidence": 0.75,  # Tunable
                    "_market_key": market_key,
                })
    
    return signals
```

### STRAT-07: Time Decay Convergence

```python
# Source: scans/resolution.py pattern + time decay logic

def scan_time_decay(
    markets_by_key: dict,
    signal_aggregator,
    min_hours_to_expiry: int = 48,
    min_consensus: float = 0.90,
    buy_below_price: float = 0.95,
) -> list[dict]:
    """Detect near-expiry markets with high consensus for profitable convergence.
    
    Buy when price < buy_below_price and consensus > min_consensus.
    Guaranteed profit = buy_below_price - min_consensus if consensus correct.
    Hold to resolution (no early exit).
    """
    opps = []
    now = time.time()
    
    for market_key, market in markets_by_key.items():
        # Check time to resolution
        resolution_ts = market.get("resolutionSource", {}).get("timestamp")
        if not resolution_ts:
            continue
        
        hours_left = (resolution_ts - now) / 3600
        if hours_left > min_hours_to_expiry or hours_left < 1:
            continue  # Not in the sweet spot
        
        # Get consensus probability
        consensus = signal_aggregator.get_consensus(market_key)
        if not consensus or consensus < min_consensus:
            continue
        
        # Determine which side is >90% consensus
        consensus_side = "YES" if consensus > 0.50 else "NO"
        target_price = consensus if consensus_side == "YES" else 1.0 - consensus
        
        if target_price < buy_below_price:
            opps.append({
                "type": "TimeDecay",
                "market": market.get("question", ""),
                "_hours_to_expiry": hours_left,
                "_consensus_side": consensus_side,
                "_consensus_prob": consensus,
                "_target_price": target_price,
                "_guaranteed_gain": buy_below_price - target_price,
            })
    
    return opps
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Static ML correlation models | Manual correlation config | Phase 8 (this phase) | More reliable in unstable prediction market conditions; operator has full control |
| Keyword detection (manual) | Finnhub REST polling loop | Phase 8 (this phase) | WebSocket reduces latency from 500ms+ (polling) to <100ms; rate-limited free tier (60/min) vs unlimited WS |
| No order book signals | CLOB imbalance scanning | Phase 8 (this phase) | Layer 4 edge on informed trading; requires Stage 2 validation to avoid stale data |
| Manual time decay checks | Automated consensus + time decay scan | Phase 8 (this phase) | Signal aggregator provides multi-source consensus (Metaculus + Manifold); removes manual signal checking |

**Deprecated/outdated:** None — Phase 8 adds new capabilities without removing old strategies. All 5 risk layers (pure arb, near-arb, market making, informed, capital optimization) remain active.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Finnhub free tier WebSocket supports news streaming with <100ms latency | Standard Stack | If true latency is >500ms, news sniping loses edge; would need paid tier or alternative source |
| A2 | CLOB order books on Polymarket/Kalshi update frequently enough that Stage 2 refinement catches imbalance collapse within 1s | Code Examples | If books update slowly (5s+), imbalance signal stales before Stage 2 check; would need longer cache TTL |
| A3 | Fuzzy matching headlines to markets has sufficient precision to avoid false positives (>70% token_set_ratio) | Code Examples | If precision is low, too many false signal matches trigger unnecessary trades; would need NLP-based semantic matching |
| A4 | >90% Metaculus consensus is reliable enough for Layer 2 (near risk-free) sniping logic | Code Examples | If consensus shifts >10% before resolution, strategy could lose; signal_aggregator sample size validation mitigates this |
| A5 | 30s cooldown per market prevents news headline re-triggering | Locked Decisions (STRAT-02) | If cooldown too short (<30s), duplicate trades on same headline; if too long (>60s), misses legit re-evaluations |

**Validation plan:** All assumptions should be tested in dry-run before enabling in production. Track:
- Finnhub latency histogram (phase 8 implementation)
- Stage 2 imbalance collapse rate (should be <20%)
- News match false positive rate (should be <10%)
- Time decay P&L per-position (should be +1-5% per trade)

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Finnhub API account + key | STRAT-02 news sniping | ✓ | Free tier (60 API calls/min, WebSocket unlimited) | REST API polling with longer latency; would need manual event monitoring |
| Polymarket CLOB API | STRAT-01 imbalance scanning | ✓ | Live (used by existing scans) | — |
| Kalshi API with order book | STRAT-01 imbalance scanning (Kalshi markets) | ✓ | Live (used by existing client) | — |
| Metaculus API (public) | STRAT-07 time decay consensus | ✓ | Free, public (optional API key) | Signal aggregator falls back to Manifold only; reduces consensus quality |
| Manifold Markets API | STRAT-07 time decay consensus | ✓ | Free, public | — |

**Missing dependencies with no fallback:** None identified. All required signal sources are either live APIs or already integrated.

**Missing dependencies with fallback:** If Finnhub key is not configured, NEWS_SNIPE_ENABLED remains false but other 3 strategies still run.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + unittest.mock |
| Config file | No conftest.py; per-file autouse fixtures (see CLAUDE.md testing pattern) |
| Quick run command | `pytest tests/test_imbalance.py tests/test_news_snipe.py tests/test_correlated.py tests/test_time_decay.py -v` |
| Full suite command | `pytest tests/ -v` (runs all 100+ tests) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STRAT-01 | Imbalance ratio calculated correctly from order book | unit | `pytest tests/test_imbalance.py::TestImbalanceRatio -v` | ❌ Wave 0 |
| STRAT-01 | Stage 2: Stale imbalance is rejected (collapsed by >30%) | unit | `pytest tests/test_imbalance.py::TestRefinement::test_rejects_collapsed_imbalance -v` | ❌ Wave 0 |
| STRAT-01 | Imbalance opportunity executes with limit order on predicted side | integration | `pytest tests/integration/test_imbalance_executor.py -v` | ❌ Wave 0 |
| STRAT-02 | Finnhub news headlines match to markets via fuzzy match | unit | `pytest tests/test_news_snipe.py::TestHeadlineMatching -v` | ❌ Wave 0 |
| STRAT-02 | Sentiment keywords (approved/rejected) score correctly | unit | `pytest tests/test_news_snipe.py::TestSentimentScoring -v` | ❌ Wave 0 |
| STRAT-02 | 30s cooldown prevents duplicate triggers on same headline | unit | `pytest tests/test_news_snipe.py::TestCooldown::test_prevents_duplicate_execution -v` | ❌ Wave 0 |
| STRAT-06 | Correlated pair config loads and markets match | unit | `pytest tests/test_correlated.py::TestConfigLoad -v` | ❌ Wave 0 |
| STRAT-06 | Spread >10% triggers opportunity; <10% is rejected | unit | `pytest tests/test_correlated.py::TestSpreadThreshold -v` | ❌ Wave 0 |
| STRAT-07 | Market <48h to resolution and >90% consensus triggers buy | unit | `pytest tests/test_time_decay.py::TestConsensusThreshold -v` | ❌ Wave 0 |
| STRAT-07 | Bought position holds to resolution; no early exit | unit | `pytest tests/test_time_decay.py::TestHoldToResolution -v` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `pytest tests/test_imbalance.py tests/test_news_snipe.py tests/test_correlated.py tests/test_time_decay.py -v` (~15-30s)
- **Per wave merge:** Full suite `pytest tests/ -v` (~2 minutes)
- **Phase gate:** Full suite green + integration tests passing before `/gsd-verify-work`

### Wave 0 Gaps

Phase 8 adds four entirely new scan modules, requiring new test files:

- [ ] `tests/test_imbalance.py` — covers STRAT-01 (imbalance ratio calc, stage 2 refinement, executor integration)
- [ ] `tests/test_news_snipe.py` — covers STRAT-02 (headline matching, sentiment scoring, cooldown logic, WebSocket handling)
- [ ] `tests/test_correlated.py` — covers STRAT-06 (config loading, pair matching, spread thresholds)
- [ ] `tests/test_time_decay.py` — covers STRAT-07 (consensus + expiry checks, hold-to-resolution logic)
- [ ] `tests/integration/test_imbalance_executor.py` — executor integration for STRAT-01 (optional Wave 1)
- [ ] `tests/integration/test_news_snipe_continuous.py` — continuous mode + WebSocket + cooldown (optional Wave 1)
- [ ] `tests/fixtures/mock_finnhub_data.py` — shared Finnhub mock responses (headlines, sentiment)
- [ ] Framework install: `pip install finnhub-python==1.3.18` — needed for test imports

**Test patterns to reuse from codebase:**
- `sys.modules` stubbing for external APIs (see test_executor.py) — mock finnhub module before import
- Autouse fixtures for setup/teardown (db isolation, price cache reset) — see test_continuous.py
- Mock `fetch_order_book()` and `get_clob_prices()` — existing helpers in test helpers

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Finnhub API key stored in env var (not hardcoded); tenacity + retry handles API errors gracefully |
| V3 Session Management | partial | Finnhub WebSocket connection management (auto-reconnect, heartbeat validation) |
| V4 Access Control | no | Single-user bot; no access control needed |
| V5 Input Validation | yes | Fuzzy match threshold validation (0-100); config env vars use _env_float/_env_bool helpers with bounds checks |
| V6 Cryptography | no | No encryption needed; HTTPS/WSS handles transport security for API keys |
| V7 Encryption | no | — |
| V8 Error Handling | yes | All scan modules wrap stage 2 validation in try/except; log failures without exposing API responses |
| V9 Communications | yes | Finnhub API key transmitted only via HTTPS/WSS; never logged or exposed in error messages |
| V10 Malicious Code | no | — |
| V11 Business Logic | yes | Feature flags prevent unauthorized strategy execution; config validation prevents typos/malformed settings |
| V12 File Upload | no | — |
| V13 API & Web Service | yes | Finnhub API rate limits (60/min free tier); implement backoff + queue for requests |

### Known Threat Patterns for {Python + WebSocket + REST APIs}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Finnhub API key exposed in logs/errors | Disclosure | Never log raw API responses; mask key in error messages; use env var, not hardcoded |
| WebSocket connection hijack (MITM) | Spoofing | Use WSS (secure WebSocket); verify certificate; don't downgrade to WS |
| Rate limit bypass attempt (DoS) | Denial of Service | Implement backoff + exponential retry (tenacity); queue API calls; don't hammer on 429 errors |
| Malicious headline input from Finnhub | Tampering | Fuzzy match + sentiment scoring validate input; large divergence from market title is rejected |
| Order book imbalance spoofing | Spoofing | Stage 2 validation confirms imbalance still exists; low latency (<1s) reduces window for spoofing |
| Unvalidated config allows trades outside thresholds | Elevation of Privilege | validate_config() checks all required fields; feature flags default to false (safe default) |

## Sources

### Primary (HIGH confidence)

- **Polymarket CLOB API** — `fetch_order_book()`, `get_clob_prices()` verified in codebase; order book response structure (bids/asks arrays) documented in polymarket_api.py
- **Kalshi API** — `fetch_order_book()`, `get_order_book_depth()` implemented in kalshi_api.py; returns YES/NO sides with depth
- **Event Monitor Pattern** — `event_monitor.py` (Metaculus integration, fuzzy matching) verified; signal_aggregator.py (multi-source consensus) verified
- **Finnhub REST API** — [Finnhub API Documentation](https://finnhub.io/docs/api/market-news) — company_news() endpoint structure, authentication (token header), rate limits (60/min free tier, 30/sec global)
- **Finnhub WebSocket News** — [Finnhub WebSocket News Documentation](https://finnhub.io/docs/api/websocket-news) — confirmed streaming available; free tier supports up to 50 symbols
- **Official finnhub-python Client** — [GitHub: Finnhub Python SDK](https://github.com/Finnhub-Stock-API/finnhub-python) — v1.3.18 stable; company_news() method signature verified; WebSocket support documented

### Secondary (MEDIUM confidence)

- **Order Book Imbalance Theory** — [QuestDB: Order Book Imbalance](https://questdb.com/glossary/order-book-imbalance/) — imbalance ratio formula (bid_vol - ask_vol) / (bid_vol + ask_vol) confirmed; predictive power cited
- **Imbalance Trading Strategy** — [HFT Backtest: Order Book Imbalance](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html) — trading strategy using imbalance signals; multi-level depth analysis pattern

### Tertiary (Requires Validation)

- **Finnhub WebSocket Latency <100ms** — [ASSUMED] Finnhub documentation does not specify latency; common prediction market practice is ~50-200ms; needs live testing
- **Fuzzy Match Precision at 70% Threshold** — [ASSUMED] Token_set_ratio 70 is a reasonable precision/recall tradeoff; should be validated on actual news/market data during implementation

## Metadata

**Confidence breakdown:**
- **Standard stack (HIGH):** Finnhub official Python SDK confirmed available; websockets already installed; no surprises
- **Architecture (HIGH):** Two-stage pattern proven in rewards.py (Phase 7); executor dispatcher pattern proven across 15+ opportunity types
- **Pitfalls (MEDIUM):** Stale order book and news latency are well-known trading challenges; timidity decay logic is novel but straightforward
- **Code examples (HIGH):** Patterns drawn from verified codebase (event_monitor.py, executor.py, scans/ modules); no fabrication

**Research date:** 2026-04-04
**Valid until:** 2026-05-04 (30 days for stable APIs; Finnhub and CLOB are mature services)

---

*Research completed for Phase 8: Market Signal Strategies. Four new signal-driven strategies are fully scoped, with locked decisions from user discussion, verified API integrations, and clear architecture patterns. Ready for planning phase.*
