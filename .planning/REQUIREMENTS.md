# Requirements: Polymarket Arb Scanner

**Defined:** 2026-04-01
**Core Value:** Automated profit extraction from prediction market inefficiencies across platforms

## v2.0 Requirements

Requirements for Profitable Trading & Strategy Expansion milestone. Each maps to roadmap phases.

### Execution & Profitability

- [ ] **EXEC-01**: Bot deploys revalidation fix and validates with 24h dry-run showing 5-30% pass rate
- [ ] **EXEC-02**: Executor routes orders as maker (limit) instead of taker (market) on Polymarket and Kalshi
- [x] **EXEC-03**: Revalidation thresholds are strategy-layer-aware (2% Layer 1, 5% Layer 2, 3% Layer 3, 10% Layer 4)
- [x] **EXEC-04**: Fee calculations verified against current 2026 platform fee structures for all 8 platforms
- [ ] **EXEC-05**: Bot captures Polymarket liquidity rewards via resting limit orders with reward score tracking
- [ ] **EXEC-06**: Bot captures Kalshi liquidity incentive program via qualifying limit orders
- [ ] **EXEC-07**: Bot executes at least one profitable autonomous round-trip trade

### Monitoring & Analytics

- [ ] **MON-01**: Per-strategy P&L tracking with DuckDB analytics over trades.db
- [ ] **MON-02**: Dashboard shows strategy leaderboard with win rates, rolling Sharpe, and drawdown
- [ ] **MON-03**: Automated alerts fire on strategy-level loss streaks and zero-opportunity periods

### Production Hardening

- [ ] **HARD-01**: WS heartbeat monitoring detects disconnects and tags stale prices within 30s
- [ ] **HARD-02**: Hedger validated on all 8 trading platforms with simulated partial fill tests
- [ ] **HARD-03**: API credential health checks run every 30 min with alerts on approaching expiry

### New Strategies

- [ ] **STRAT-01**: Order book imbalance scan detects directional signals from bid/ask volume ratio
- [ ] **STRAT-02**: News-driven resolution sniping uses Finnhub real-time news feed for event detection
- [ ] **STRAT-03**: Liquidity rewards farming strategy optimizes quote placement for maximum USDC rewards
- [ ] **STRAT-04**: Combinatorial/logical arb detects semantic inconsistencies across related markets
- [ ] **STRAT-05**: Whale copy trading follows profitable Polymarket wallets via on-chain monitoring
- [ ] **STRAT-06**: Correlated market pairs trading captures spread divergences between related events
- [ ] **STRAT-07**: Time decay convergence buys near-certain outcomes as expiry approaches

## v3.0 Requirements

Deferred to future milestone. Tracked but not in current roadmap.

### Advanced Strategies

- **STRAT-08**: Betfair/Smarkets in-play scalping during live events
- **STRAT-09**: Contrarian sentiment trading on extreme consensus markets
- **STRAT-10**: AI ensemble probability model for superior price estimates

### Infrastructure

- **INFRA-01**: Async HTTP (aiohttp) for revalidation hot path if latency remains bottleneck
- **INFRA-02**: scipy-based threshold optimization with statistical sweep over backtest data
- **INFRA-03**: Kalshi liquidity incentive program optimization (after maker routing working)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| HFT/sub-millisecond latency | Prediction markets don't have HFT dynamics; edge is detection accuracy |
| ML price prediction models | Overfitting risk massive on thin prediction market data |
| Social media sentiment scraping | Noisy, expensive, marginal improvement over existing signal sources |
| Cross-chain DeFi integration | Complexity explosion for marginal benefit |
| Public-facing product / SaaS | Personal trading tool — no user accounts or access selling |
| Full Kelly on non-structural trades | Use fractional Kelly; full Kelly on uncertain estimates causes ruin |
| PMXT unified SDK replacement | Immature (Jan 2026 launch); existing 10 clients are battle-tested |
| Redis/Celery task infrastructure | Overkill for single-process bot; asyncio + ThreadPoolExecutor sufficient |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| EXEC-01 | Phase 5 | Pending |
| EXEC-02 | Phase 5 | Pending |
| EXEC-03 | Phase 5 | Complete |
| EXEC-04 | Phase 5 | Complete |
| EXEC-07 | Phase 5 | Pending |
| MON-01 | Phase 6 | Pending |
| MON-02 | Phase 6 | Pending |
| MON-03 | Phase 6 | Pending |
| HARD-01 | Phase 6 | Pending |
| HARD-02 | Phase 6 | Pending |
| HARD-03 | Phase 6 | Pending |
| EXEC-05 | Phase 7 | Pending |
| EXEC-06 | Phase 7 | Pending |
| STRAT-03 | Phase 7 | Pending |
| STRAT-01 | Phase 8 | Pending |
| STRAT-02 | Phase 8 | Pending |
| STRAT-06 | Phase 8 | Pending |
| STRAT-07 | Phase 8 | Pending |
| STRAT-04 | Phase 9 | Pending |
| STRAT-05 | Phase 9 | Pending |

**Coverage:**
- v2.0 requirements: 20 total
- Mapped to phases: 20
- Unmapped: 0 ✓

---
*Requirements defined: 2026-04-01*
*Last updated: 2026-04-01 — traceability filled after roadmap creation*
