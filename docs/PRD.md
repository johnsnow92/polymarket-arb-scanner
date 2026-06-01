# Product Requirements — arbgrid

> **Owner:** Jonathon Tamm · **Review cadence:** quarterly, or when scope/definition-of-done changes.
> Lifts and formalizes the scope from CLAUDE.md. This is the "what / why / done" doc; `ROADMAP.md` sequences it.

## Problem
Prediction markets are fragmented across many venues with independent order books, divergent prices, and different fee/auth models. That fragmentation creates risk-free and low-risk edges (overrounds, cross-platform mispricings, stale prices, market-making spreads) that are only capturable with fast, automated, multi-venue detection + execution.

## What arbgrid is
A personal, 24/7 automated trading bot that scans **8 trading platforms** (+ 2 read-only signal sources) for arbitrage and trading opportunities across **29 strategies / 5 risk layers**, executes against them subject to risk gates, and runs on Railway. Full-stack: detection, execution, risk management, market making, monitoring, backtesting.

## Users
**One — the operator (Jonathon Tamm).** No external users.

## Definition of done (acceptance)
All three, over a 7-day live trading window:
1. **Net-positive P&L** in `trades.db`.
2. **<5% false-positive rate** on detected opportunities (manually verified against platforms).
3. **≥1 profitable round-trip trade** executed without human intervention.

## Product surface
- **Layer 1 — Pure arbitrage** (risk-free): same-platform overrounds, cross-platform 2-way (28 pairs), multi-outcome, triangular, crossed-book.
- **Layer 2 — Near-arbitrage:** resolution sniping, stale-price exploitation, fee-promo arbitrage.
- **Layer 3 — Market making:** passive, cross-platform, inventory-hedged, liquidity-reward farming.
- **Layer 4 — Informed trading:** event divergence, convergence, multi-source signal aggregation, + variants (imbalance, logical, time-decay, news-snipe, whale-copy, correlated).
- **Layer 5 — Capital optimization:** fee routing, Kelly sizing, rebalancing, latency, backtesting-driven tuning.
- Platform grid + status: `docs/PLATFORM-MATRIX.md`. Strategy status: `docs/strategy-framework-v2.md`.

## Risk tolerance
Conservative defaults (`docs/RISK-POLICY.md`): `DRY_RUN=true` by default, all strategy flags off, small trade sizes ($5 default), $25 daily-loss limit, hard block on unsigned SX Bet trading, auto-rebalance limited to one corridor.

## Non-goals (out of scope)
- Public-facing product, SaaS interface, user accounts, or selling access — **this is a personal tool.**
- Trading venues that route through an already-integrated book (e.g. Robinhood→Kalshi) — no independent arb edge.
- Fund movement beyond the Gemini↔Polymarket USDC corridor.

## Constraints
- Backward-compatible with the live Railway deployment; execution-path changes must not break running trading.
- Regulatory: prediction-market legality varies by US state (Michigan, the operator's state, has active litigation) — venue access is a blocking input to any expansion.
