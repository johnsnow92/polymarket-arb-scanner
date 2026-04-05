# ROADMAP.md — Polymarket Arb Scanner

## Milestones

- ✅ **v1.0 Production-Ready Automated Trading** — Phases 1-4 (shipped 2026-04-01) | [Archive](milestones/v1.0-ROADMAP.md)
- 🚧 **v2.0 Profitable Trading & Strategy Expansion** — Phases 5-9 (in progress)

<details>
<summary>✅ v1.0 Production-Ready Automated Trading (Phases 1-4) — SHIPPED 2026-04-01</summary>

- [x] Phase 1: Wire & Enable (3/3 plans) — fee routing, MM integration, bankroll refresh, config defaults
- [x] Phase 2: Harden & Test (3/3 plans) — circuit breakers, idempotency, dedup, integration tests
- [x] Phase 3: Monitor & Optimize (3/3 plans) — strategy metrics, alerting, dashboard, priority queue, backtest
- [x] Phase 4: Go Live (3/3 plans) — pre-flight scripts, validation endpoint, 7-day report

</details>

---

### 🚧 v2.0 Profitable Trading & Strategy Expansion (In Progress)

**Milestone Goal:** Bot makes money. Revalidation unblocked, maker routing deployed, monitoring in place, and new strategies running — net positive P&L over a sustained 7-day period.

## Phases

- [x] **Phase 5: Deploy & Execute** - Unblock production execution and get the first profitable trade
- [ ] **Phase 6: Monitor & Harden** - Full observability and reliability for all 20 strategies
- [ ] **Phase 7: Liquidity Rewards** - Capture Polymarket and Kalshi liquidity incentive programs
- [ ] **Phase 8: Market Signal Strategies** - Add four signal-driven strategies to expand edge sources
- [ ] **Phase 9: Structural Alpha Strategies** - Implement high-complexity structural alpha strategies

## Phase Details

### Phase 5: Deploy & Execute
**Goal**: Bot executes profitable trades in production with correct fees and strategy-aware revalidation
**Depends on**: Phase 4 (v1.0 complete)
**Requirements**: EXEC-01, EXEC-02, EXEC-03, EXEC-04, EXEC-07
**Success Criteria** (what must be TRUE):
  1. 24-hour dry-run shows 5-30% of detected opportunities pass revalidation (not 0%)
  2. Executor routes qualifying orders as limit (maker) orders on Polymarket and Kalshi
  3. Revalidation applies layer-specific floors: 2% Layer 1, 5% Layer 2, 3% Layer 3, 10% Layer 4
  4. All 8 platform fee rates verified and corrected against current 2026 schedules
  5. At least one autonomous round-trip trade completes with net positive P&L recorded in trades.db
**Plans:** 3 plans (completed)
Plans:
- [x] 05-01-PLAN.md — Fee model overhaul + layer/revalidation config infrastructure
- [x] 05-02-PLAN.md — Layer tagging, layer-aware revalidation, calibration logging, maker routing
- [x] 05-03-PLAN.md — Deploy to Railway, 72h calibration dry-run, enable first live trade

### Phase 6: Monitor & Harden
**Goal**: Every strategy has observable P&L attribution, disconnects are detected, and the system is reliable enough to leave running unattended
**Depends on**: Phase 5
**Requirements**: MON-01, MON-02, MON-03, HARD-01, HARD-02, HARD-03
**Success Criteria** (what must be TRUE):
  1. DuckDB analytics query against trades.db returns per-strategy P&L, win rate, and Sharpe ratio
  2. Dashboard strategy leaderboard shows rolling win rates and drawdown for each of the 20 strategies
  3. Webhook alert fires within 5 minutes when any strategy hits a loss streak or zero-opportunity period
  4. WS feed disconnects are detected within 30 seconds and stale price markers appear on affected markets
  5. API credential health check runs every 30 minutes and alerts before any credential expires
**Plans:** 6 plans
Plans:
- [x] 06-01-PLAN.md — DuckDB analytics infrastructure, per-strategy P&L queries, CLI script
- [x] 06-02-PLAN.md — Dashboard leaderboard endpoint and HTML UI with strategy metrics table
- [x] 06-03-PLAN.md — Per-strategy loss streak alerts and zero-opportunity period detection
- [x] 06-04-PLAN.md — WS heartbeat monitoring, stale feed detection, executor integration
- [x] 06-05-PLAN.md — Hedger validation tests across all 8 trading platforms
- [x] 06-06-PLAN.md — Credential health checker, per-platform probes, pre-expiry alerts

### Phase 7: Liquidity Rewards
**Goal**: Bot earns exchange liquidity rewards by resting limit orders on Polymarket and Kalshi
**Depends on**: Phase 5 (maker routing must be working)
**Requirements**: EXEC-05, EXEC-06, STRAT-03
**Success Criteria** (what must be TRUE):
  1. Bot places resting limit orders on Polymarket qualifying markets and reward score is tracked in the dashboard
  2. Bot participates in Kalshi liquidity incentive program and qualifying order metrics are logged
  3. Dedicated liquidity rewards farming strategy optimizes quote placement to maximize USDC reward yield
**Plans:** 4 plans
Plans:
- [x] 07-01-PLAN.md — Reward tracking infrastructure (config, database, RewardTracker, unit tests)
- [x] 07-02-PLAN.md — Rewards scan module (Polymarket + Kalshi detection, fee calculations)
- [x] 07-03-PLAN.md — Executor integration + CLI wiring (opportunity execution, one-shot scanning)
- [x] 07-04-PLAN.md — Continuous mode integration + dashboard metrics (60s polling, leaderboard row, integration tests)

### Phase 8: Market Signal Strategies
**Goal**: Four new signal-driven strategies are live in production — order book imbalance, news-driven sniping, correlated pairs, and time decay convergence
**Depends on**: Phase 6 (monitoring in place before adding strategies)
**Requirements**: STRAT-01, STRAT-02, STRAT-06, STRAT-07
**Success Criteria** (what must be TRUE):
  1. Order book imbalance scan detects directional signals from bid/ask volume ratios and logs qualifying trades
  2. Resolution sniping strategy ingests Finnhub real-time news feed and fires trades on event detection signals
  3. Correlated market pairs scan detects spread divergences between related markets and opens convergence positions
  4. Time decay convergence strategy buys near-certain outcomes approaching expiry and logs resolved P&L
**Plans**: 5 plans (completed)
Plans:
- [x] 08-01-PLAN.md — Imbalance detection + news snipe API integration + correlated pairs scan + time decay
- [x] 08-02-PLAN.md — Executor integration (legs, revalidation, position sizing)
- [x] 08-03-PLAN.md — CLI + continuous mode wiring
- [x] 08-04-PLAN.md — Configuration, feature flags, tuning parameters
- [x] 08-05-PLAN.md — Comprehensive test coverage and dashboard integration

### Phase 9: Structural Alpha Strategies
**Goal**: Two high-complexity structural alpha strategies — combinatorial logical arb and whale copy trading — are live and attributing P&L
**Depends on**: Phase 8 (signal strategies validated before higher complexity work)
**Requirements**: STRAT-04, STRAT-05
**Success Criteria** (what must be TRUE):
  1. Combinatorial/logical arb scan detects semantic inconsistencies across related markets and logs opportunity events
  2. Whale copy trading monitors profitable Polymarket wallets on-chain and triggers mirror positions within the execution latency budget
  3. Both strategies appear in the Phase 6 monitoring dashboard with their own P&L attribution rows
**Plans**: 3 plans
Plans:
- [ ] 09-01-PLAN.md — Logical arbitrage scan, fee calculator, executor integration, unit tests
- [ ] 09-02-PLAN.md — Whale copy API client (Polygonscan), scan module, executor integration, unit tests
- [ ] 09-03-PLAN.md — Config, CLI modes, continuous integration, dashboard metrics, integration tests

## Progress

**Execution Order:**
Phases execute in numeric order: 5 → 6 → 7 → 8 → 9

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 5. Deploy & Execute | v2.0 | 3/3 | Planning complete | ✓ |
| 6. Monitor & Harden | v2.0 | 6/6 | Planning complete | - |
| 7. Liquidity Rewards | v2.0 | 4/4 | Planning complete | - |
| 8. Market Signal Strategies | v2.0 | 5/5 | Planning complete | - |
| 9. Structural Alpha Strategies | v2.0 | 3/3 | Planning complete | - |
