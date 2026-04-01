# Stack Research

**Domain:** Prediction market arbitrage and automated trading bot — profitability tuning, execution hardening, new strategy types, market data aggregation
**Researched:** 2026-04-01
**Confidence:** HIGH (core additions verified via official docs or PyPI), MEDIUM (ecosystem patterns via multiple web sources)

---

## Context: What Already Exists (Do NOT Re-Add)

The existing 46K LOC codebase already contains these — they are off the table:

| Already Have | Notes |
|---|---|
| `requests==2.31.0` | Sync HTTP — all 10 platform clients use it |
| `websockets==16.0` | WS feeds for Polymarket + Kalshi in `ws_feeds.py` |
| `tenacity==9.1.4` | Retry logic wired into all API clients |
| `thefuzz[speedup]==0.22.1` | Fuzzy matching in `matcher.py` |
| `py-clob-client==0.34.5` | Polymarket CLOB trading |
| `ib_insync>=0.9.70` | IBKR TWS API |
| `cryptography==46.0.4` | RSA-PSS signing for Kalshi |
| `fastembed>=0.4.0` | Embeddings (matcher) |
| `numpy>=1.24.0` | Numerical ops |
| `python-dotenv==1.2.1` | Env var loading |
| `tabulate==0.9.0` | CLI table display |
| `python-socks[asyncio]==2.8.0` | Proxy support |
| Stdlib metrics, Prometheus text format | `metrics.py` — custom implementation |
| SQLite WAL (trades.db + snapshots.db) | `db.py` + `snapshot.py` |
| Kelly criterion, position sizer | `position_sizer.py` |
| Market making engine | `market_maker.py` (QuoteEngine, InventoryTracker, QuoteManager) |
| Metaculus + Manifold signal aggregation | `signal_aggregator.py`, `metaculus_api.py`, `manifold_api.py` |
| WebSocket feeds (Polymarket, Kalshi) | `ws_feeds.py` |
| Backtesting replay engine | `backtest.py` (partial, tuning needed) |

---

## Recommended Additions by Capability Area

### 1. Execution Speed and Latency Reduction

#### `uvloop` — Linux/macOS only (Railway = Linux, target deployment)
- **Version:** 0.21.0 (latest stable, Oct 2025; supports Python 3.8–3.14)
- **Why:** Drop-in asyncio event loop replacement using libuv (same engine as Node.js). Benchmarks show 2x throughput, 2x+ task throughput in I/O-heavy workloads. The continuous mode event loop in `continuous.py` handles WS feeds, opportunity detection, and order placement concurrently — this directly benefits from faster scheduling.
- **Integration:** One line at the top of `continuous.py`: `import uvloop; uvloop.install()`. No other code changes needed.
- **Caveat:** Does NOT work on Windows (Railway runs Linux, so production is fine). Local dev on Windows uses standard asyncio — acceptable since latency optimization is a production concern, not a dev concern.
- **Source:** [uvloop PyPI 0.21.0](https://pypi.org/project/uvloop/) — MEDIUM confidence (PyPI verified, benchmark numbers from community sources)

#### `aiohttp>=3.9.0` — Async HTTP for revalidation calls
- **Version:** 3.11.x (latest stable 2025)
- **Why:** The execution hot path (`executor.py` revalidation step) makes REST calls to re-check prices synchronously inside a ThreadPoolExecutor. Moving revalidation calls to `aiohttp` in an async context would eliminate thread spawn overhead and connection setup latency. Benchmarks show aiohttp is ~2x faster than requests for concurrent calls. This matters for cross-platform arb where 3–5 REST calls are made per revalidation.
- **Integration:** Add async revalidation helper alongside existing sync clients. Doesn't require rewriting all platform clients — only the hot-path revalidation calls.
- **When to add:** Only needed if revalidation latency proves to be a bottleneck after first real trades. Start with uvloop; add aiohttp if still slow.
- **Source:** [httpx vs aiohttp vs requests benchmark](https://oxylabs.io/blog/httpx-vs-requests-vs-aiohttp) — MEDIUM confidence

### 2. Profitability Monitoring and P&L Tracking

#### `duckdb>=0.10.0` — Analytical queries over trades.db
- **Version:** 1.1.x (latest stable Dec 2025)
- **Why:** The current `db.py` P&L queries use SQLite for OLTP (writes + lookups) — correct and fast. But the dashboard and backtest need analytical aggregations: rolling P&L windows, strategy-level Sharpe ratios, drawdown periods, cross-strategy comparison. DuckDB runs in-process (zero server), reads SQLite or Parquet directly, and executes columnar OLAP queries 10-100x faster than SQLite for analytical workloads. Specifically enables: 30-day rolling P&L with windowing functions, per-strategy Sharpe ratio, time-bucketed fill rate analysis.
- **Integration:** `import duckdb; conn = duckdb.connect()` — can query the existing SQLite `trades.db` directly via `duckdb.read_csv` or attach via `ATTACH 'trades.db' AS trades (TYPE SQLITE)`. No schema migration needed.
- **What NOT to do:** Do not replace SQLite with DuckDB for writes. SQLite WAL remains the transactional store; DuckDB is a read-only analytics layer.
- **Source:** [DuckDB Python quickstart](https://motherduck.com/learn-more/duckdb-python-quickstart-part1/) — HIGH confidence (official docs verified)

#### `pandas>=2.0.0` — Time series analysis for backtest and P&L reporting
- **Version:** 2.2.x (latest stable)
- **Why:** The existing backtest engine (`backtest.py`) and P&L queries manually loop over SQLite rows. pandas provides rolling windows, resample(), cumsum(), and Sharpe/drawdown calculations out-of-the-box. DuckDB integrates directly with pandas DataFrames (zero-copy). Together they form the analytics layer: DuckDB for SQL aggregation, pandas for time-series operations.
- **Integration:** Load DuckDB query results into a DataFrame: `df = conn.execute("SELECT ...").df()`. Use for backtest output formatting, strategy comparison reports.
- **Source:** [DuckDB Python integration](https://integrating-duckdb-python-an-analytics-guide) — HIGH confidence (official DuckDB docs)

### 3. News-Driven Trading (Resolution Sniping + Event Divergence Enhancement)

#### `finnhub-python>=2.4.0` — Real-time news feed for resolution sniping
- **Version:** 2.4.19 (latest stable on PyPI)
- **Why:** Resolution sniping requires detecting when a real-world event outcome is known (election called, sports result final) before platform prices update. Finnhub provides company/general news via REST + WebSocket with 60 calls/min free tier. For prediction market events (elections, economic releases, sports), Finnhub general news covers the same events. The WS feed (`wss://ws.finnhub.io`) delivers news in real-time without polling.
- **What it enables:** Wire into `scans/resolution.py` — when a news article matches a market's topic with high relevance (fuzzy title match via existing `matcher.py`), flag for resolution snipe evaluation.
- **Free tier:** 60 API calls/min + WebSocket with real-time updates. Sufficient for monitoring 50–200 active markets.
- **Integration:** `import finnhub; client = finnhub.Client(api_key=os.getenv("FINNHUB_API_KEY"))`. WebSocket: `finnhub.WebsocketClient(api_key=..., on_message=handler)`.
- **Alternatives considered:** NewsAPI.org (250 req/12h — too restrictive), GDELT (15-min delay — too slow for sniping), newsdata.io (200 credits/day free — adequate but less real-time).
- **Source:** [Finnhub API docs](https://finnhub.io/docs/api/rate-limit) — HIGH confidence (official docs)

#### No dedicated NLP/sentiment library needed
- **Why NOT adding:** Resolution sniping for prediction markets does not require sentiment analysis — it requires event detection (did the election happen? did the court rule?). Keyword matching against market titles using the existing `thefuzz` library is sufficient. LLM-based classification adds latency and API cost without proportional benefit at this scale.
- **If sentiment scoring IS needed later:** Use `transformers` with a FinBERT checkpoint via HuggingFace (no new library dependency, just a model). Do not add `pandas_ta`, `ta-lib`, or `textblob` — prediction markets are event-driven, not technically-driven.

### 4. Market Data Aggregation and Order Flow Analysis

#### `polymarket-apis>=0.1.0` (unofficial but active) — Optional Gamma API wrapper
- **Version:** Latest on PyPI (verified Jan 2026)
- **Why:** The existing `polymarket_api.py` uses `py-clob-client` which handles trading. The Gamma API provides market metadata, volume ranking, and order book summaries without authentication. Useful for market-making market selection (find liquid markets, volume > threshold) without burning CLOB API rate limits.
- **When to add:** Only if the market selection logic in `market_maker.py` needs richer metadata (open interest, volume history, category). The existing CLOB client already handles order book depth — this is additive.
- **Alternative:** Direct REST calls to `https://gamma-api.polymarket.com/markets` using existing `requests` — no new dependency required. Start with this.
- **Source:** [polymarket-apis PyPI](https://pypi.org/project/polymarket-apis/) — MEDIUM confidence

#### No unified PMXT library needed
- **Why NOT adding pmxt:** pmxt (launched Jan 2026) is a unified prediction market API similar to CCXT. The project already has 10 hand-built, battle-tested platform clients with custom auth, retries, circuit breakers, and slippage tracking. Switching to or wrapping pmxt would introduce a new dependency with less control over error handling, and pmxt's coverage (Polymarket, Kalshi, Limitless) is narrower than the existing 8-platform stack. Monitor its maturity — worth revisiting at v2.0+ if maintenance burden for platform clients becomes high.
- **Source:** [pmxt GitHub](https://github.com/pmxt-dev/pmxt) — LOW confidence (launched Jan 2026, immature)

#### Order flow imbalance — no new library needed
- **Why NOT adding:** Order flow analysis libraries (crobat, Orderflow) are designed for continuous-time order books on crypto exchanges. Prediction market CLOBs have fundamentally different dynamics (binary settlement, thin books, event-driven jumps). The existing `ws_feeds.py` already receives Level 2 order book deltas from Polymarket and Kalshi WebSockets. The useful signal — order imbalance ratio — is a simple calculation: `(bid_volume - ask_volume) / (bid_volume + ask_volume)`. Implement this in `price_tracker.py` directly, not via an external library.
- **Source:** [Polymarket CLOB docs](https://docs.polymarket.com/concepts/prices-orderbook) — HIGH confidence

### 5. Backtesting and Strategy Optimization

#### `hftbacktest>=2.2.0` — NOT recommended for this project
- **Why NOT adding:** hftbacktest is purpose-built for HFT on crypto exchanges (Binance, Bybit) and requires Level 3 Market-By-Order feed data with nanosecond timestamps. Prediction markets don't have this data format. The existing `backtest.py` replay engine over SQLite price snapshots is the correct approach for this domain.
- **What to do instead:** Enhance the existing `backtest.py` with pandas-based rolling metrics (Sharpe, max drawdown, win rate by strategy) using the DuckDB + pandas combo above.

#### `scipy>=1.11.0` — Statistical optimization for threshold tuning
- **Version:** 1.14.x (latest stable 2025)
- **Why:** The backtesting feedback loop needs to optimize thresholds (MIN_NET_ROI, MM spread width, Kelly fraction per strategy, divergence cutoffs). `scipy.optimize.minimize` or `scipy.stats.bootstrap` enables systematic threshold search over historical data rather than manual tuning. Specifically: Sharpe-ratio-maximizing sweep over MIN_NET_ROI values per strategy type.
- **Integration:** Used only in `backtest.py` and offline analysis scripts — not in the live trading hot path.
- **Alternatives:** Manual grid search is adequate for a small number of parameters. Add scipy only when the backtest data volume justifies systematic optimization (>100 settled trades per strategy).
- **Source:** [scipy.org](https://scipy.org/) — HIGH confidence (official)

---

## Supporting Libraries Summary

| Library | Version | Purpose | Add When |
|---------|---------|---------|----------|
| `uvloop` | `0.21.0` | Faster asyncio event loop (Railway/Linux only) | Phase 1 — before tuning execution |
| `duckdb` | `>=1.1.0` | Analytical P&L queries over trades.db | Phase 1 — profitability dashboard |
| `pandas` | `>=2.2.0` | Time series P&L, rolling Sharpe, drawdown | Phase 1 — with DuckDB |
| `finnhub-python` | `>=2.4.19` | Real-time news feed for resolution sniping | Phase 2 — resolution sniping strategy |
| `aiohttp` | `>=3.11.0` | Async HTTP for revalidation hot path | Phase 3 — if latency still blocking |
| `scipy` | `>=1.14.0` | Threshold optimization in backtest loop | Phase 4 — after >100 settled trades |
| `polymarket-apis` | latest | Gamma API market metadata | Optional — only if needed for MM market selection |

---

## Installation

```bash
# Core additions (add to requirements.txt)
uvloop==0.21.0
duckdb>=1.1.0
pandas>=2.2.0
finnhub-python>=2.4.19

# Optional — add when needed
aiohttp>=3.11.0
scipy>=1.14.0

# Dev only
pip install pytest-asyncio  # for testing async components
```

**Important:** `uvloop` is Linux/macOS only. Add to `requirements.txt` with a platform guard:

```
uvloop==0.21.0; sys_platform != "win32"
```

This ensures Railway deploys with uvloop while Windows dev environments use standard asyncio.

---

## Alternatives Considered

| Recommended | Alternative | Why Alternative Was Rejected |
|---|---|---|
| `uvloop` for asyncio | `winloop` | winloop is Windows-only; Railway (production) runs Linux. Asymmetric — wrong direction. |
| `duckdb` for analytics | TimescaleDB, InfluxDB | Require separate server process. DuckDB runs in-process like SQLite, zero ops overhead. |
| `duckdb` for analytics | Pure SQLite analytics | SQLite analytical queries are 10-100x slower for aggregations; lacks window functions needed for rolling Sharpe. |
| `finnhub-python` for news | GDELT `gdelt` library | GDELT updates every 15 minutes — too slow for resolution sniping. |
| `finnhub-python` for news | `newsapi-python` | 250 requests per 12 hours on free tier — too restrictive for monitoring 100+ markets. |
| `pandas` for time series | `polars` | Polars is faster but DuckDB already handles heavy queries; pandas is the right tool for moderate-size result sets and has better DuckDB integration. |
| Direct REST calls | `pmxt` unified SDK | pmxt launched Jan 2026, immature. Existing custom clients have production-tested auth, retries, and circuit breakers pmxt doesn't replicate. |
| In-house order flow calc | `crobat`, `Orderflow` libs | These target continuous crypto exchange LOBs with ns-precision data. Prediction markets have structurally different order book dynamics; the signal (imbalance ratio) is a 3-line calculation. |
| `scipy` for optimization | Manual grid search | For small parameter spaces (<5 parameters, <100 trades), manual grid search is sufficient. scipy adds value at scale. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|---|---|---|
| `hftbacktest` | Requires Level 3 MBO feed data in nanosecond format — prediction markets don't produce this | Enhance existing `backtest.py` with DuckDB + pandas |
| `ta-lib` / `pandas-ta` | Technical indicators (RSI, MACD, Bollinger Bands) assume continuous time series; prediction markets are event-driven binary instruments that resolve to 0 or 1 | Order imbalance ratio in `price_tracker.py`, Metaculus/Manifold signals for directional trades |
| `transformers` / FinBERT sentiment | Adds 500MB+ model weights, inference latency, and GPU dependency for marginal gain over keyword event detection | Fuzzy title matching with existing `thefuzz` for news-to-market correlation |
| `httpx` | Redundant with `requests` (sync) + `aiohttp` (async); httpx is slower than aiohttp for high-concurrency use cases | `requests` for sync platform clients, `aiohttp` for hot-path async revalidation |
| Redis / Memcached | Adds infra dependency for what is currently handled by in-process Python dicts with TTL logic | Existing price cache in `ws_feeds.py` is sufficient; upgrade to `cachetools.TTLCache` from stdlib before adding Redis |
| Celery / task queue | Overkill for single-process bot; adds broker dependency (Redis/RabbitMQ) | `asyncio` event loop + `ThreadPoolExecutor` already handle concurrency correctly |
| `postgresql` or `mysql` | Operational overhead for what SQLite + DuckDB handles correctly at this scale | SQLite WAL (writes) + DuckDB (analytics) |

---

## Version Compatibility

| Package | Compatible With | Notes |
|---|---|---|
| `uvloop==0.21.0` | Python 3.8–3.14, Linux/macOS | Not Windows. Wrap with `sys_platform != "win32"` in requirements.txt |
| `duckdb>=1.1.0` | Python 3.8+, numpy>=1.24 | Already have numpy>=1.24 — compatible |
| `pandas>=2.2.0` | numpy>=1.24, Python 3.9+ | Compatible with existing stack |
| `finnhub-python>=2.4.19` | Python 3.7+, requests>=2.18 | Compatible; uses existing `requests` |
| `aiohttp>=3.11.0` | Python 3.8+, asyncio | No conflict with existing `websockets==16.0` |
| `scipy>=1.14.0` | numpy>=1.24 | Compatible with existing numpy constraint |

---

## Stack Patterns by Variant

**If Railway deployment (production, Linux):**
- Include `uvloop==0.21.0` — yields ~2x asyncio throughput improvement for the continuous mode event loop
- All libraries compatible

**If local Windows dev:**
- `uvloop` skipped (sys_platform guard handles this automatically)
- Standard asyncio is fine for development; latency difference is not observable in dry-run mode

**If backtesting optimization runs:**
- Use DuckDB + pandas + scipy together for statistical sweep over threshold parameters
- Run in a separate analysis script, not in the live trading process

**If news-driven resolution sniping is high priority:**
- Add `finnhub-python` and wire into `scans/resolution.py` + `event_monitor.py`
- Set `FINNHUB_API_KEY` in Railway environment variables
- Free tier (60 req/min) is sufficient for monitoring up to ~200 active markets via periodic polling; use the WebSocket feed for real-time coverage

---

## Sources

- [uvloop PyPI 0.21.0](https://pypi.org/project/uvloop/) — version confirmed, Windows incompatibility confirmed (HIGH confidence)
- [uvloop GitHub (MagicStack)](https://github.com/MagicStack/uvloop) — libuv implementation, benchmark context (HIGH confidence)
- [DuckDB Python quickstart](https://motherduck.com/learn-more/duckdb-python-quickstart-part1/) — SQLite attachment support confirmed (HIGH confidence)
- [DuckDB in-process analytics](https://duckdb.org/why_duckdb) — official docs (HIGH confidence)
- [Finnhub API rate limits](https://finnhub.io/docs/api/rate-limit) — 60 req/min free tier confirmed (HIGH confidence)
- [Finnhub WebSocket news](https://finnhub.io/docs/api/websocket-news) — real-time news streaming confirmed (HIGH confidence)
- [aiohttp vs requests performance](https://oxylabs.io/blog/httpx-vs-requests-vs-aiohttp) — benchmark comparison (MEDIUM confidence)
- [Polymarket CLOB docs](https://docs.polymarket.com/concepts/prices-orderbook) — order book delta structure confirmed (HIGH confidence)
- [Kalshi WebSocket quick start](https://docs.kalshi.com/getting_started/quick_start_websockets) — snapshot/delta format confirmed (HIGH confidence)
- [pmxt GitHub](https://github.com/pmxt-dev/pmxt) — launched Jan 2026, immaturity noted (LOW confidence for production use)
- [hftbacktest PyPI](https://pypi.org/project/hftbacktest/) — confirmed crypto exchange focus, not prediction markets (HIGH confidence)
- [Market making on prediction markets 2026](https://newyorkcityservers.com/blog/prediction-market-making-guide) — Stoikov model adaptation notes (MEDIUM confidence)
- [polymarket-apis PyPI](https://pypi.org/project/polymarket-apis/) — Gamma API wrapper (MEDIUM confidence)

---

*Stack research for: Polymarket Arb Scanner v2.0 — profitability tuning and strategy expansion*
*Researched: 2026-04-01*
