# Strategy Expansion & Scope Update Plan

## Context

The Polymarket Arb Scanner has outgrown its original scope ("find one real arb opportunity"). It now has 8 platform integrations, 7 arb strategies, execution, risk management, and continuous mode. The new scope targets a **profitable 24/7 automated trading bot** — full-stack across all platforms.

Currently, only **Layer 1 (pure arbitrage)** is built. Pure arbs are mathematically risk-free but rare and small. To maximize earnings, we need to layer additional strategy types that generate income even when no pure arbs exist — especially **market making** (the biggest untapped revenue stream).

This plan defines the complete 20-strategy framework across 5 risk layers and the phased implementation roadmap.

---

## Strategy Framework: 5 Layers, 20 Strategies

### Layer 1: Pure Arbitrage (Risk-Free) — ALREADY BUILT

Mathematically guaranteed profit. Buy a complete outcome set for less than payout.

| # | Strategy | Status | Platforms |
|---|----------|--------|-----------|
| 1 | Same-platform binary overround | BUILT | PM, Kalshi, Gemini, IBKR |
| 2 | Same-platform multi-outcome overround | BUILT | PM (NegRisk), Kalshi, Gemini |
| 3 | Cross-platform 2-way | BUILT | All 28 pairs |
| 4 | Multi-outcome cross-platform | BUILT | PM + Kalshi |
| 5 | Triangular (3-way) | BUILT | All platforms |
| 6 | Back-all / Back-lay | BUILT | Betfair, Smarkets, SX Bet, Matchbook |

**Action needed:** Harden — fix audit critical issues (MultiCross execution gap, IBKR requirements, Betfair/Smarkets retries).

### Layer 2: Near-Arbitrage (Near Risk-Free) — TO BUILD

Not mathematically guaranteed, but probability of loss is extremely low (<5%).

| # | Strategy | Description |
|---|----------|-------------|
| 7 | **Resolution sniping** | Buy near-certain outcomes (95%+ probability) at a discount before market fully prices to $1.00. Monitor resolution signals (news APIs, official results), detect when outcome is ~certain but market still shows $0.90-$0.97, buy and hold to settlement. |
| 8 | **Stale price exploitation** | When a price moves on a liquid platform (Polymarket/Kalshi), trade against stale prices on less liquid platforms (Matchbook, SX Bet, Gemini) before they update. Requires real-time price monitoring across all platforms and fast execution. |
| 9 | **Fee promotional arbitrage** | Systematically track which platforms currently have reduced/zero fees (Gemini promo, Matchbook 0% predictions). Route identical cross-platform arbs through lowest-fee paths. An arb unprofitable at 2% becomes profitable at 0%. |

### Layer 3: Market Making (Low Risk) — TO BUILD

**Biggest untapped revenue stream.** Provide liquidity and earn bid-ask spreads continuously, even when no mispricings exist.

| # | Strategy | Description |
|---|----------|-------------|
| 10 | **Passive market making** | Post resting limit orders on both sides of a market (bid + ask). Capture the spread when both fill. E.g., bid $0.48 / ask $0.52 = $0.04 per round-trip. Start with most liquid markets on Polymarket. |
| 11 | **Cross-platform market making** | Post opposing limit orders on different platforms for the same event. Buy at $0.47 on Kalshi, sell at $0.53 on Polymarket. Like cross-platform arb but using patient limit orders instead of market orders — better fills, more opportunities. |
| 12 | **Inventory-hedged MM** | When market making creates a directional position (e.g., long YES), hedge by: (a) buying NO on another platform, (b) adjusting quotes to attract offsetting flow, or (c) reducing position size. Cross-platform hedging turns risky MM into near risk-free. |

**Key components needed:**
- `market_maker.py` — Quote engine: spread calculation, quote placement, fill monitoring
- Inventory tracker — Tracks net position per market across platforms
- Quote adjustment logic — Widens/narrows spread based on inventory, volatility, time-to-resolution
- Integration with existing `executor.py` for order placement and `risk_manager.py` for limits

### Layer 4: Informed / Statistical Edge (Moderate Risk) — TO BUILD

Directional positions based on information advantages. Positive expected value, not risk-free.

| # | Strategy | Description |
|---|----------|-------------|
| 13 | **Event divergence (Metaculus)** | ALREADY BUILT. Trade toward Metaculus consensus when platform prices diverge >15%. |
| 14 | **Cross-platform convergence** | When one platform is significantly mispriced vs the median of all others (but fees prevent pure arb), take a directional position expecting convergence. Less risky than pure directional because multiple platforms confirm the "true" price. |
| 15 | **Multi-source signal aggregation** | Aggregate probability estimates from Metaculus, Manifold Markets, INFER, Good Judgment Open, and prediction polls. Build a weighted consensus probability. Trade against platforms that deviate significantly from consensus. |

### Layer 5: Capital & Execution Optimization (Multiplier) — TO BUILD

Force multipliers that increase returns from all other layers.

| # | Strategy | Description |
|---|----------|-------------|
| 16 | **Dynamic fee routing** | For any opportunity tradeable on multiple platform paths, pick the lowest-fee path. Extend existing gas_monitor to include platform fee comparison. |
| 17 | **Kelly criterion sizing** | Size positions using Kelly criterion (or fractional Kelly for safety). Requires edge estimate per strategy and bankroll tracking. Prevents over-betting on any single opportunity. |
| 18 | **Platform fund rebalancing** | When capital gets concentrated on one platform (all positions there), detect imbalance and suggest/execute fund transfers to platforms with more opportunity flow. |
| 19 | **Latency optimization** | Prioritize execution for time-sensitive opportunities (stale prices, resolution sniping). Add priority queuing in continuous mode. Already have WebSocket feeds — extend with execution priority. |
| 20 | **Backtesting-driven tuning** | Complete the backtesting engine. Use historical data to optimize: MIN_NET_ROI thresholds, position sizes, spread widths for MM, divergence thresholds, and platform allocation. |

---

## Implementation Phases

### Phase A: Harden Layer 1 (Pure Arbitrage Foundation)
**Priority:** Immediate — prerequisite for everything else
**Risk:** None — fixing existing code

1. Fix MultiCross `_build_legs()` + revalidation in `executor.py`
2. Wire `scan_multi_cross` into `continuous.py`
3. Add `ib_insync` to `requirements.txt`
4. Add retry logic to Betfair & Smarkets API clients
5. Fix Gemini 429 handling (`_RateLimitError` pattern)
6. Remove hardcoded password from `run_dashboard.py`
7. Complete test coverage for critical paths

**Files:** `executor.py`, `continuous.py`, `requirements.txt`, `betfair_api.py`, `smarkets_api.py`, `gemini_api.py`, `run_dashboard.py`

### Phase B: Layer 2 — Near-Arbitrage Strategies
**Priority:** High — near risk-free income with existing infrastructure
**Risk:** Very low

**B1: Fee promotional routing** (lowest effort, immediate value)
- Add `fee_schedule` dict to each platform client tracking current fee rates
- Modify `scans/cross.py` to compare fee paths and pick cheapest
- Config: `GEMINI_FEE_RATE`, `MATCHBOOK_FEE_RATE`, etc. (some already exist)
- Files: `fees.py`, `scans/cross.py`, platform `*_api.py` files

**B2: Stale price exploitation**
- Add `PriceTracker` module — maintains rolling price per market per platform with timestamps
- Detect when Platform A price moves >X% while Platform B hasn't updated in >Y seconds
- Generate `StalePriceOpp` with the stale platform as the target
- Files: NEW `price_tracker.py`, NEW `scans/stale.py`, wire into `cli.py` and `continuous.py`

**B3: Resolution sniping**
- Add resolution signal detection — monitor for events approaching resolution (time-based, API status fields)
- When market status = "resolving" or resolution date within window, check if any outcome price < $0.97 despite >95% consensus
- Generate `ResolutionSnipeOpp` with near-certain outcome
- Signal sources: platform API status fields, Metaculus resolution, optional news API integration
- Files: NEW `scans/resolution.py`, extend `event_monitor.py`

### Phase C: Layer 3 — Market Making Engine
**Priority:** High — biggest new revenue stream
**Risk:** Low (with proper hedging)

**C1: Core market making engine**
- NEW `market_maker.py` — Core engine:
  - `QuoteEngine` class: calculates bid/ask quotes given mid price, desired spread, inventory position
  - `InventoryTracker`: tracks net position per market per platform
  - `QuoteManager`: places/cancels/updates resting limit orders
- Spread calculation: `base_spread + inventory_skew + volatility_adjustment`
- Quote update frequency: driven by WebSocket price updates or periodic timer
- Files: NEW `market_maker.py`, extend `continuous.py` to run MM alongside arb scanning

**C2: Single-platform passive MM**
- Start with Polymarket (most liquid, best order book)
- Select markets: liquid, mid-range prices (avoid $0.01 or $0.99), sufficient volume
- Post resting bids + asks with configurable spread (e.g., $0.03 minimum)
- Monitor fills, update inventory, adjust quotes
- Risk limits: max inventory per market, max total MM exposure

**C3: Cross-platform market making**
- Extend MM to post opposing orders on different platforms
- Buy side on cheaper platform, sell side on more expensive platform
- Captures cross-platform spread without waiting for pure arb
- Files: extend `market_maker.py`, wire into `matcher.py` for cross-platform market pairing

**C4: Inventory hedging integration**
- When inventory exceeds threshold, auto-hedge:
  - Primary: buy opposing outcome on another platform
  - Secondary: adjust quotes to attract offsetting flow
  - Tertiary: reduce position size
- Integrates with existing `hedger.py` for cross-platform hedge execution

### Phase D: Layer 4 — Informed Trading Enhancements
**Priority:** Medium — highest upside but also highest risk
**Risk:** Moderate

**D1: Multi-source signal aggregation**
- NEW `signal_aggregator.py`:
  - Metaculus API (already built)
  - Manifold Markets API (public, free)
  - Add configurable source weights
  - Output: weighted consensus probability per matched event
- Extend `event_monitor.py` to use aggregated signals instead of Metaculus-only
- Files: NEW `signal_aggregator.py`, NEW `manifold_api.py`, modify `event_monitor.py`

**D2: Cross-platform convergence**
- When median price across N platforms differs from one platform by >X% (but not enough for pure arb after fees):
  - Take a directional position expecting the outlier to converge
  - Position size based on confidence (how many platforms agree) and divergence magnitude
- Files: NEW `scans/convergence.py`, wire into `cli.py`

### Phase E: Layer 5 — Capital Optimization
**Priority:** Medium — multiplier effect on all other layers
**Risk:** None (optimization, not new trading)

**E1: Kelly criterion position sizing**
- Add `position_sizer.py`:
  - For pure arbs: Kelly fraction = 1.0 (guaranteed edge)
  - For near-arbs: fractional Kelly based on confidence level
  - For MM: fixed fraction based on historical fill rates
  - For informed trades: fractional Kelly based on signal strength
- Integrate into `executor.py` to replace static `MAX_TRADE_SIZE`

**E2: Platform fund rebalancing**
- Track available balance per platform in real-time
- When one platform has >X% of total capital but <Y% of opportunity flow, flag for rebalancing
- Manual rebalancing alerts first (via notifier), auto-transfer later if platforms support it

**E3: Complete backtesting engine**
- Finish `backtest.py`: replay historical snapshots through all strategies
- Output: per-strategy P&L, win rate, average holding period, max drawdown
- Use results to tune: MIN_NET_ROI, MM spreads, divergence thresholds, Kelly fractions

**E4: Dynamic fee routing**
- Extend `fees.py` with real-time fee schedule per platform
- When multiple paths exist for the same opportunity, pick lowest total fee
- Track promotional periods and adjust routing accordingly

---

## Updated Project Scope (to write into CLAUDE.md)

```
## Project Scope

- **What does "done" look like?** Profitable 24/7 automated trading bot on Railway — 20 strategies across 5 risk layers (pure arbitrage, near-arbitrage, market making, informed trading, capital optimization) operating across all 8 platforms. Full-stack: detection, execution, risk management, market making, monitoring, and backtesting — all production-grade and battle-tested.
- **How will you know it's working?** All three: (1) Net positive P&L in trades.db over a 7-day live trading period, (2) <5% false positive rate on detected opportunities (manually verified against platforms), (3) At least one profitable round-trip trade executed without human intervention.
- **What is explicitly out of scope?** Public-facing product — no user accounts, SaaS interface, or selling access. This is a personal trading tool.
- **Scope status:** Active
```

## Updated Strategies List (to write into CLAUDE.md)

```
**Strategies (20 across 5 layers)**:

*Layer 1 — Pure Arbitrage (risk-free):*
- **Binary/NegRisk internal** — same-platform overround arbs on Polymarket, Kalshi, Gemini, IBKR
- **Back-all/Back-lay** — exchange-specific arbs on Betfair, Smarkets, SX Bet, Matchbook
- **Cross-platform 2-way** — mispricings between any pair of 8 platforms (28 pairs)
- **Multi-outcome cross-platform** — cheapest YES per outcome across platforms
- **Triangular** — 3-way mispricings across 3+ platforms

*Layer 2 — Near-Arbitrage (near risk-free):*
- **Resolution sniping** — buy near-certain outcomes at a discount before settlement
- **Stale price exploitation** — trade against slow-updating platforms after price moves
- **Fee promotional arbitrage** — route through lowest-fee platform paths

*Layer 3 — Market Making (low risk):*
- **Passive market making** — bid/ask spread capture on liquid markets
- **Cross-platform market making** — opposing limit orders across platforms
- **Inventory-hedged MM** — cross-platform hedging to neutralize directional exposure

*Layer 4 — Informed Trading (moderate risk):*
- **Event divergence** — multi-source consensus vs platform price signals
- **Cross-platform convergence** — directional bets on outlier platforms converging to median
- **Multi-source signal aggregation** — weighted consensus from Metaculus, Manifold, prediction polls

*Layer 5 — Capital Optimization (multiplier):*
- **Dynamic fee routing** — lowest-fee path selection for each opportunity
- **Kelly criterion sizing** — optimal position sizing by strategy risk class
- **Platform fund rebalancing** — capital allocation across platforms by opportunity flow
- **Latency optimization** — priority execution for time-sensitive opportunities
- **Backtesting-driven tuning** — historical data to optimize all thresholds
- **Spread detection** — Polymarket/Kalshi bid-ask spreads (existing, feeds into MM)
```

---

## New Files to Create

| File | Purpose | Phase |
|------|---------|-------|
| `market_maker.py` | Core MM engine (QuoteEngine, InventoryTracker, QuoteManager) | C1 |
| `price_tracker.py` | Rolling price tracker with staleness detection | B2 |
| `position_sizer.py` | Kelly criterion + strategy-aware sizing | E1 |
| `signal_aggregator.py` | Multi-source probability aggregation | D1 |
| `manifold_api.py` | Manifold Markets API client | D1 |
| `scans/stale.py` | Stale price opportunity detection | B2 |
| `scans/resolution.py` | Resolution sniping detection | B3 |
| `scans/convergence.py` | Cross-platform convergence detection | D2 |

## Existing Files to Modify

| File | Changes | Phase |
|------|---------|-------|
| `executor.py` | MultiCross legs, MM order dispatch, Kelly sizing integration | A, C, E |
| `continuous.py` | Wire MM engine, stale price monitor, resolution sniper | B, C |
| `cli.py` | New modes: `stale`, `resolution`, `convergence`, `mm` | B, C, D |
| `fees.py` | Real-time fee schedules, fee routing logic | B1, E4 |
| `scans/cross.py` | Fee-aware path selection | B1 |
| `event_monitor.py` | Multi-source signals, resolution detection | B3, D1 |
| `hedger.py` | MM inventory hedging integration | C4 |
| `risk_manager.py` | MM-specific risk limits, Kelly integration | C, E |
| `config.py` | New env vars for MM, stale detection, signal sources | B, C, D |
| `backtest.py` | Complete engine for all strategy types | E3 |
| `betfair_api.py`, `smarkets_api.py` | Add retry logic | A |
| `gemini_api.py` | Fix 429 handling | A |
| `requirements.txt` | Add `ib_insync`, `manifoldpy` (or requests-based) | A, D |
| `CLAUDE.md` | Updated scope and strategies list | Immediate |

## Verification

After each phase:
```bash
pytest tests/ -v                              # All tests pass
python scanner.py --mode all --dry-run        # Full scan works
python scanner.py --continuous --dry-run      # Continuous mode works
```

Phase-specific checks:
- **Phase A:** `python scanner.py --mode multi-cross --dry-run` (MultiCross executes)
- **Phase B:** New scan modes work: `--mode stale`, `--mode resolution`
- **Phase C:** `python scanner.py --mode mm --dry-run` (MM quotes generated, no fills in dry-run)
- **Phase D:** `python scanner.py --mode convergence --dry-run` (convergence signals detected)
- **Phase E:** Backtest: `python backtest.py --strategy all --days 7` (produces P&L report)
