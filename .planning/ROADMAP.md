# ROADMAP.md — Polymarket Arb Scanner

## Milestone 1: Production-Ready Automated Trading

**Goal**: All 20 strategies running profitably in full-auto mode across 8 platforms for 7+ days.

**Success Criteria**:
1. Net positive P&L in `trades.db` over 7-day period
2. <5% false positive rate on detected opportunities
3. At least one profitable autonomous round-trip trade

---

## Phase 1: Wire & Enable

**Goal**: Connect all existing code into the live pipeline. All 20 strategies reachable from continuous mode with proper configuration.

**Plans:** 3/3 plans complete

Plans:
- [x] 01-01-PLAN.md — Wire find_lowest_fee_path into cross-platform scans and executor
- [x] 01-02-PLAN.md — Fix MM dry_run, add Kalshi resolution, wire bankroll refresh
- [ ] 01-03-PLAN.md — Update config defaults and document Railway env vars

### Requirements

| ID | Requirement | Key Files |
|----|-------------|-----------|
| INTEG-01 | Wire `find_lowest_fee_path()` into cross-platform scans | `scans/cross.py`, `executor.py`, `fees.py` |
| INTEG-02 | Fix MM one-shot `dry_run` hardcode | `cli.py` |
| INTEG-03 | Add Kalshi to continuous mode resolution scan | `continuous.py` |
| INTEG-04 | Wire `update_bankroll()` into continuous mode | `continuous.py`, `position_sizer.py` |
| INTEG-05 | Document stale scan one-shot behavior (no code change) | CLAUDE.md |
| ENABLE-01 | Enable market making with safe defaults | `config.py` |
| ENABLE-02 | Enable snapshot recording for backtesting | `config.py` |
| ENABLE-03 | Enable dynamic fee monitoring | `config.py`, `gas_monitor.py` |
| ENABLE-04 | Enable event monitor for signal aggregation | `config.py` |
| ENABLE-05 | Configure all 8 platform credentials in Railway | Railway env vars |

### Verification

- All 20 strategies callable via `--mode` flag or continuous mode
- `find_lowest_fee_path()` invoked on at least one cross-platform opportunity
- Market making runs with `--exec-mode full-auto` (not hardcoded dry_run)
- Resolution scan finds both Polymarket and Kalshi near-settled markets
- `update_bankroll()` called in continuous mode loop
- Existing tests still pass (1471+)

---

## Phase 2: Harden & Test

**Goal**: Validate every strategy produces correct results with real API data. Confidence that live trading won't lose money due to bugs.

**Plans:** 3 plans

Plans:
- [ ] 02-01-PLAN.md — Structured JSONL decision logging and circuit breaker module
- [ ] 02-02-PLAN.md — Idempotent order placement and fee verification
- [ ] 02-03-PLAN.md — Per-strategy integration test scripts

### Requirements

| ID | Requirement | Key Files |
|----|-------------|-----------|
| HARDEN-01 | Live dry-run test per strategy type | All scan modules |
| HARDEN-02 | Validate fee calculations against actual charges | `fees.py`, platform APIs |
| HARDEN-03 | Structured logging for trade decisions | `executor.py`, `risk_manager.py` |
| HARDEN-04 | Rate-limit awareness per platform | All `*_api.py` |
| HARDEN-05 | Idempotent order placement | `executor.py` |

### Verification

- Each strategy type tested with real API data in dry-run mode
- Fee calculations match actual platform charges within 0.1%
- Every execution decision produces structured JSON log
- Zero 429 errors in 1-hour continuous run
- Crash-recovery test produces no duplicate orders

### Dependencies

- Phase 1 complete (all strategies wired)

---

## Phase 3: Monitor & Optimize

**Goal**: Full visibility into bot performance. Automated capital optimization. Dashboard showing live P&L.

### Requirements

| ID | Requirement | Key Files |
|----|-------------|-----------|
| MONITOR-01 | Dashboard UI with live P&L and positions | `dashboard_ui.py`, `dashboard.py` |
| MONITOR-02 | Per-strategy metrics | `metrics.py`, `executor.py` |
| MONITOR-03 | Anomaly alerting | `alerting.py`, `notifier.py` |
| MONITOR-04 | Platform fund rebalancing alerts | `alerting.py`, `position_sizer.py` |
| OPTIMIZE-01 | Dynamic fee schedule updates | `fees.py`, `config.py` |
| OPTIMIZE-02 | Automated backtest-to-config feedback loop | `backtest.py`, `config.py` |
| OPTIMIZE-03 | Priority execution lane for time-sensitive strategies | `continuous.py`, `executor.py` |
| OPTIMIZE-04 | Live bankroll tracking across all platforms | `continuous.py`, `position_sizer.py` |
| OPTIMIZE-05 | Automated fund rebalancing recommendations | `alerting.py`, `dashboard_ui.py` |

### Verification

- Dashboard shows live P&L, positions, and per-strategy breakdown
- Prometheus metrics exposed at `/metrics`
- Anomaly alert fires within 60s of simulated loss spike
- Rebalancing recommendation produced weekly
- Priority queue executes time-sensitive trades within 500ms

### Dependencies

- Phase 2 complete (hardened, validated)

---

## Phase 4: Go Live

**Goal**: Progressive deployment from safest (Layer 1) to riskiest (Layer 4). 7-day validation against success criteria.

### Requirements

| ID | Requirement | Timeline |
|----|-------------|----------|
| LIVE-01 | Deploy Layer 1 in full-auto mode | Day 0 |
| LIVE-02 | Monitor and tune Layer 1 for 48 hours | Day 0-2 |
| LIVE-03 | Enable Layer 2 (near-arbitrage) | Day 2 |
| LIVE-04 | Enable Layer 3 (market making) | Day 4 |
| LIVE-05 | Enable Layer 4 (informed trading) | Day 5 |
| LIVE-06 | 7-day validation against success criteria | Day 5-12 |

### Verification

- Layer 1: Net positive P&L after 48 hours
- Layer 2: Resolution and stale strategies execute without errors
- Layer 3: Market making quotes placed, filled, inventory within limits
- Layer 4: Signal quality validated against actual outcomes
- Final: All 3 success criteria met over 7-day window

### Dependencies

- Phase 3 complete (monitoring in place before going live)

### Risk Mitigation

- Start with `MAX_TRADE_SIZE` at 10% of target
- Keep `DAILY_LOSS_LIMIT` conservative until Layer 1 proven
- Manual kill switch via Railway dashboard (stop deployment)
- `DRY_RUN=true` fallback — one env var change to pause all execution

---

## Phase Dependency Graph

```
Phase 1 (Wire & Enable)
    └── Phase 2 (Harden & Test)
            └── Phase 3 (Monitor & Optimize)
                    └── Phase 4 (Go Live)
                            ├── LIVE-01: Layer 1
                            ├── LIVE-02: Monitor 48h
                            ├── LIVE-03: Layer 2
                            ├── LIVE-04: Layer 3
                            ├── LIVE-05: Layer 4
                            └── LIVE-06: 7-day validation
```

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| API credential expiry mid-trade | Partial fill, no hedge | `recovery.py` reconciliation, hedger sells filled leg |
| Platform rate limits during high-activity | Missed opportunities | HARDEN-04 adaptive backoff |
| Gas spike on Polygon during Polymarket trade | Negative P&L trade | ENABLE-03 dynamic fee monitoring |
| Market making inventory buildup | Directional exposure | Inventory limits in `market_maker.py`, hedger |
| Stale price detection false positive | Bad trade on "stale" but actually moved price | Conservative threshold, HARDEN-01 validation |
| Cross-platform settlement timing mismatch | Capital locked longer than expected | Position tracking in `db.py`, rebalancing alerts |
