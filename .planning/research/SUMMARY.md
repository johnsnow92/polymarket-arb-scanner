# Research Summary — Polymarket Arb Scanner v2.0

**Synthesized:** 2026-04-01
**Sources:** STACK.md, FEATURES.md, ARCHITECTURE.md, PITFALLS.md

---

## Key Findings

### 1. The Revalidation Fix is Gate Zero
Every research dimension converges on the same point: **nothing else matters until the revalidation fix is deployed and validated**. The bot has 20 strategies built across 8 platforms, 1592+ tests passing, and full infrastructure — but 100% of opportunities are rejected at revalidation. The fix is committed. Deploy it.

### 2. Maker-Order Routing is the Biggest Profitability Lever
Polymarket introduced taker fees up to 1.8% at 50% probability (March 2026). Kalshi has similar taker fees. Switching to limit (maker) orders = 0% fee on both platforms. This single change could be the difference between profitable and unprofitable for every strategy. Additionally, Polymarket pays daily USDC liquidity rewards ($150-300/day per market reported) and Kalshi offers up to $7K/week in liquidity incentives — both require resting limit orders.

### 3. Stack Additions Are Minimal
- **uvloop** (one-line install) for ~2x asyncio throughput on Railway
- **DuckDB + pandas** for analytical P&L queries over existing SQLite
- **finnhub-python** for real-time news feeds (resolution sniping enhancement)
- Everything else: NOT needed. The existing stack is sufficient.

### 4. New Strategies with Highest Expected ROI
| Strategy | Expected Edge | Complexity | Research Confidence |
|----------|--------------|------------|---------------------|
| Combinatorial/logical arb | $11M documented on Polymarket | HIGH | HIGH (academic paper) |
| News-driven repricing | $2.2M case study | HIGH | MEDIUM |
| Order book imbalance | 1-3% per trade | LOW | MEDIUM |
| Liquidity rewards farming | $150-300/day per market | MEDIUM | HIGH (official docs) |
| Whale copy trading | 2-5% per trade | MEDIUM | MEDIUM |
| Resolution timing arb | Sell into platform lag | MEDIUM | MEDIUM |

### 5. Architecture Supports Extension Cleanly
The existing patterns (opportunity dicts, two-stage scans, `_build_legs` dispatcher, feature flags) make adding new strategies low-risk. New scan modules drop into `scans/`, new execution branches add to `_build_legs()`, feature flags gate everything. Build order: foundation → new scans → execution wiring → orchestration.

### 6. Top Pitfalls to Prevent
1. **Deploying without staged dry-run** — validate revalidation pass rates before live trading
2. **Starting with too much capital** — 2 platforms, minimum capital, 7 days
3. **Single revalidation threshold for all strategies** — Layer 1 needs tight (2%), Layer 4 needs loose (10%)
4. **Oracle divergence on cross-platform positions** — UMA vs CFTC resolution mismatch
5. **Adding complexity before proving profitability** — prove 3 strategies profitable before adding new ones

---

## Recommended Priority Order

### Phase 1: Deploy & Profit (fix what's broken)
- Deploy revalidation fix (committed, just push)
- Implement maker-order routing (biggest profitability lever)
- Per-strategy revalidation thresholds
- Fee audit against current platform rates
- Add uvloop + DuckDB + pandas for monitoring
- P&L tracking and strategy performance dashboard
- 7-day dry-run validation → go live with minimal capital

### Phase 2: Harden & Validate (prove existing strategies work)
- Validate all 20 strategies in production (which ones actually trigger?)
- Hedger testing with real partial fills
- WS heartbeat monitoring
- API credential health checks
- Strategy-level P&L attribution
- Tune thresholds based on live data

### Phase 3: Expand Strategies (add new profit sources)
- Liquidity rewards farming (dedicated strategy)
- Order book imbalance trading (low complexity)
- News-driven resolution sniping enhancement (finnhub integration)
- Correlated market pairs detection
- Time decay / expiry convergence
- Weekend/off-hours MM

### Phase 4: Advanced Strategies (highest complexity, highest potential)
- Combinatorial/logical arbitrage (LLM-assisted)
- Whale copy trading (on-chain monitoring)
- Resolution timing arbitrage
- Betfair in-play scalping
- Backtest-driven threshold optimization (scipy)

---

## Anti-Recommendations (Do NOT Build)
- HFT/sub-millisecond latency — prediction markets don't have HFT dynamics
- ML price prediction — overfitting risk on thin data
- Social media sentiment scraping — noisy, marginal improvement
- Cross-chain DeFi integration — complexity explosion
- Full Kelly on non-structural strategies — use fractional Kelly
- Unified PMXT SDK — immature, existing clients are battle-tested

---

*Research synthesis for: Polymarket Arb Scanner v2.0*
*Synthesized: 2026-04-01*
