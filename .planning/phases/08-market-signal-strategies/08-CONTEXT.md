# Phase 8: Market Signal Strategies - Context

**Gathered:** 2026-04-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Four new signal-driven strategies live in production: order book imbalance, news-driven sniping, correlated pairs, and time decay convergence. Each gets its own scan module, executor branch, and dashboard attribution.

Requirements: STRAT-01, STRAT-02, STRAT-06, STRAT-07.

</domain>

<decisions>
## Implementation Decisions

### Order Book Imbalance (STRAT-01)
- Signal from CLOB order book: bid/ask volume ratio at top 5 price levels on Polymarket + Kalshi
- 3:1 volume imbalance ratio triggers directional signal (configurable via IMBALANCE_RATIO env var, default 3.0)
- Execute with limit order on predicted side — if bids dominate, buy YES expecting price rise
- Max $10 per signal, Layer 4 revalidation (10% floor), max 5 concurrent imbalance positions
- New scan module: `scans/imbalance.py`

### News-Driven Resolution Sniping (STRAT-02)
- Finnhub real-time news WebSocket for headline matching — fuzzy match headlines to market questions
- Keyword/sentiment scoring: "approved", "confirmed", "rejected", "passed", "failed" map to YES/NO confidence scores
- Immediate execution (taker order) — time-sensitive, latency matters more than fees
- Max $25 per event, 30s cooldown per market after news trigger, Layer 2 strategy
- New scan module: `scans/news_snipe.py`, new API client: `finnhub_api.py`

### Correlated Market Pairs (STRAT-06)
- Manual correlation mapping in config (e.g., "Bitcoin $100k" ↔ "Bitcoin $90k") — not ML-based
- >10% spread between correlated markets triggers opportunity (configurable via CORRELATION_DIVERGENCE_THRESHOLD, default 0.10)
- Long the underpriced, short the overpriced — convergence trade with matched sizing
- Max $20 per pair, requires both legs to fill, Layer 4 revalidation (10% floor)
- New scan module: `scans/correlated.py`

### Time Decay Convergence (STRAT-07)
- Markets with <48h to resolution and >90% implied probability on one outcome
- Buy when price < 0.95 for a >90% consensus outcome — 5%+ guaranteed gain if correct
- Layer 2 (near risk-free if consensus is correct), max $50 per position
- Hold to resolution — no early exit, pure convergence play
- New scan module: `scans/time_decay.py`

### Shared Infrastructure
- Each strategy gets its own scan module following the two-stage pattern (mid-price → CLOB refinement)
- Each strategy gets a `_build_legs` branch in executor.py and a `_revalidate` case
- Each strategy appears as a separate row in the dashboard leaderboard
- All strategies gated by feature flag env vars (IMBALANCE_ENABLED, NEWS_SNIPE_ENABLED, etc.)
- All strategies added to CLI --mode choices and continuous.py scan loop

### Claude's Discretion
- Finnhub API integration details (WebSocket protocol, authentication)
- Fuzzy matching algorithm for news → market correlation
- Specific correlated pair configuration format
- Order book depth parsing for imbalance calculation
- Dashboard layout for 4 new strategy rows
- Test structure and fixtures for each strategy

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scans/rewards.py` — recent scan module pattern to follow (Phase 7)
- `executor.py:_build_legs()` — dispatcher for opportunity types
- `fees.py` — net profit calculators for each strategy
- `matcher.py` — fuzzy title matching (reusable for news → market matching)
- `signal_aggregator.py` — multi-source probability aggregation
- `event_monitor.py` — Metaculus divergence signals (pattern for external signal integration)
- `config.py` — feature flags and strategy config
- `dashboard.py` + `dashboard_ui.py` — strategy leaderboard (Phase 6+7)

### Patterns to Follow
- Two-stage scan: mid-price → CLOB refinement
- Config: `_env_bool`/`_env_float` with sensible defaults
- Executor: `_build_legs()` branch + `_revalidate` case
- CLI: `--mode <name>` in argparse choices
- Tests: unittest.mock, sys.modules stubs, class-based tests

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches.

</specifics>

<deferred>
## Deferred Ideas

- ML-based correlation detection (use manual config for now — simpler, more predictable)
- Twitter/X sentiment scraping (noisy, expensive — Finnhub news is more reliable)
- Full NLP for news understanding (keyword matching sufficient for resolution events)
- Real-time order flow analysis (requires Level 2 data not available via REST)
</deferred>
