 The Earning Layers

  The key insight: pure arbitrage is rare and small. To maximize earnings on autopilot, you layer multiple strategy types with
   increasing risk tolerance. Here's the full picture:

  Layer 1: Pure Arbitrage (Risk-Free — Guaranteed Profit)

  These are mathematically locked: you buy a complete set of outcomes for less than the guaranteed payout. Zero market risk.

  ┌─────┬───────────────────────────────────┬────────┬────────────────────────────────────────────────────────────────────┐
  │  #  │             Strategy              │ Status │                            How It Works                            │
  ├─────┼───────────────────────────────────┼────────┼────────────────────────────────────────────────────────────────────┤
  │ 1   │ Same-platform binary overround    │ BUILT  │ Buy YES + NO on one platform < $1.00                               │
  ├─────┼───────────────────────────────────┼────────┼────────────────────────────────────────────────────────────────────┤
  │ 2   │ Same-platform multi-outcome       │ BUILT  │ Buy all outcomes on one platform < $1.00 (NegRisk, Kalshi multi,   │
  │     │ overround                         │        │ Gemini multi)                                                      │
  ├─────┼───────────────────────────────────┼────────┼────────────────────────────────────────────────────────────────────┤
  │ 3   │ Cross-platform 2-way              │ BUILT  │ Buy YES on Platform A + NO on Platform B < $1.00                   │
  ├─────┼───────────────────────────────────┼────────┼────────────────────────────────────────────────────────────────────┤
  │ 4   │ Multi-outcome cross-platform      │ BUILT  │ Cheapest YES per outcome across platforms, total < $1.00           │
  ├─────┼───────────────────────────────────┼────────┼────────────────────────────────────────────────────────────────────┤
  │ 5   │ Triangular (3-way)                │ BUILT  │ 3+ platform mispricings where cheapest combination < $1.00         │
  ├─────┼───────────────────────────────────┼────────┼────────────────────────────────────────────────────────────────────┤
  │ 6   │ Back-all / Back-lay               │ BUILT  │ Exchange overround arbs (Betfair, Smarkets, SX Bet, Matchbook)     │
  └─────┴───────────────────────────────────┴────────┴────────────────────────────────────────────────────────────────────┘

  Current state: All 6 are implemented. Earnings limited by opportunity frequency and depth (liquidity).

  ---
  Layer 2: Near-Arbitrage (Near Risk-Free — Very High Certainty)

  Not mathematically guaranteed, but the probability of loss is extremely low.

  ┌─────┬───────────────────┬─────────┬───────────────────────────────────────────────────────────────────────────────────┐
  │  #  │     Strategy      │ Status  │                                   How It Works                                    │
  ├─────┼───────────────────┼─────────┼───────────────────────────────────────────────────────────────────────────────────┤
  │ 7   │ Resolution        │ NOT     │ Buy near-certain outcomes (95%+ probability) at a discount before resolution.     │
  │     │ sniping           │ BUILT   │ E.g., election called but market still shows $0.92 — buy at $0.92, collect $1.00  │
  ├─────┼───────────────────┼─────────┼───────────────────────────────────────────────────────────────────────────────────┤
  │ 8   │ Stale price       │ NOT     │ When a price moves on a liquid platform (Polymarket), trade on less liquid        │
  │     │ exploitation      │ BUILT   │ platforms (Matchbook, SX Bet) before their prices update                          │
  ├─────┼───────────────────┼─────────┼───────────────────────────────────────────────────────────────────────────────────┤
  │     │ Fee promotional   │ NOT     │ Systematically route trades through platforms with temporary 0% fees (Gemini      │
  │ 9   │ arbitrage         │ BUILT   │ promo, Matchbook predictions). Same arb that's unprofitable at 2% fees becomes    │
  │     │                   │         │ profitable at 0%                                                                  │
  └─────┴───────────────────┴─────────┴───────────────────────────────────────────────────────────────────────────────────┘

  Risk: Tiny — resolution sniping has ~5% risk of surprise reversal. Stale pricing has execution timing risk.

  ---
  Layer 3: Market Making (Low Risk — Steady Income Stream)

  This is likely the biggest untapped revenue stream. Instead of waiting for mispricings, you provide liquidity and earn the
  bid-ask spread on every trade.

  ┌─────┬──────────────────────┬─────────┬────────────────────────────────────────────────────────────────────────────────┐
  │  #  │       Strategy       │ Status  │                                  How It Works                                  │
  ├─────┼──────────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────┤
  │     │ Passive market       │ NOT     │ Post limit buy and sell orders on both sides of a market. Capture the spread   │
  │ 10  │ making               │ BUILT   │ when both sides fill. E.g., bid $0.48 / ask $0.52 = $0.04 profit per round     │
  │     │                      │         │ trip                                                                           │
  ├─────┼──────────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────┤
  │     │ Cross-platform       │ NOT     │ Post opposing limit orders on different platforms for the same event. Buy at   │
  │ 11  │ market making        │ BUILT   │ $0.47 on Kalshi, sell at $0.53 on Polymarket. Like cross-platform arb but with │
  │     │                      │         │  limit orders instead of market orders                                         │
  ├─────┼──────────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────┤
  │ 12  │ Inventory-hedged MM  │ NOT     │ When market making creates an imbalanced position (long YES), hedge by buying  │
  │     │                      │ BUILT   │ NO on another platform or adjusting quotes                                     │
  └─────┴──────────────────────┴─────────┴────────────────────────────────────────────────────────────────────────────────┘

  Risk: Inventory risk (holding a directional position if only one side fills). Mitigated by position limits, cross-platform
  hedging, and tight spreads.

  Why this matters: Market making generates income even when no arb opportunities exist. Professional market makers earn
  consistently from flow, not mispricings.

  ---
  Layer 4: Informed / Statistical Edge (Moderate Risk — Highest Upside)

  Directional bets based on information advantages. Not risk-free, but positive expected value.

  ┌─────┬──────────────────────┬─────────┬────────────────────────────────────────────────────────────────────────────────┐
  │  #  │       Strategy       │ Status  │                                  How It Works                                  │
  ├─────┼──────────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────┤
  │ 13  │ Event divergence     │ BUILT   │ Trade toward Metaculus consensus when platform prices diverge significantly    │
  │     │ (Metaculus)          │         │                                                                                │
  ├─────┼──────────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────┤
  │ 14  │ Cross-platform       │ NOT     │ When one platform is significantly mispriced vs others (but fees prevent pure  │
  │     │ convergence          │ BUILT   │ arb), bet that prices will converge                                            │
  ├─────┼──────────────────────┼─────────┼────────────────────────────────────────────────────────────────────────────────┤
  │     │ Multi-source signal  │ NOT     │ Aggregate probability estimates from multiple forecasting sources (Metaculus,  │
  │ 15  │ aggregation          │ BUILT   │ Manifold, prediction polls) to build a "consensus probability" — trade against │
  │     │                      │         │  platforms that deviate                                                        │
  └─────┴──────────────────────┴─────────┴────────────────────────────────────────────────────────────────────────────────┘

  Risk: Real directional risk. Metaculus can be wrong. Mitigated by only trading when divergence is extreme and using position
   sizing.

  ---
  Layer 5: Capital & Execution Optimization (Multiplier on All Other Layers)

  Not strategies themselves, but force multipliers that increase returns from all the above.

  ┌─────┬─────────────────────────┬──────────┬────────────────────────────────────────────────────────────────────────────┐
  │  #  │        Strategy         │  Status  │                                How It Works                                │
  ├─────┼─────────────────────────┼──────────┼────────────────────────────────────────────────────────────────────────────┤
  │ 16  │ Dynamic fee routing     │ PARTIAL  │ Route identical arbs through lowest-fee platform path. Already have        │
  │     │                         │          │ gas_monitor; need fee-aware routing                                        │
  ├─────┼─────────────────────────┼──────────┼────────────────────────────────────────────────────────────────────────────┤
  │ 17  │ Optimal capital         │ NOT      │ Allocate capital to highest risk-adjusted return strategies first (Kelly   │
  │     │ allocation              │ BUILT    │ criterion or similar)                                                      │
  ├─────┼─────────────────────────┼──────────┼────────────────────────────────────────────────────────────────────────────┤
  │ 18  │ Platform fund           │ NOT      │ When capital gets trapped on one platform (all positions there),           │
  │     │ rebalancing             │ BUILT    │ automatically move funds to platforms with more opportunities              │
  ├─────┼─────────────────────────┼──────────┼────────────────────────────────────────────────────────────────────────────┤
  │ 19  │ Latency optimization    │ PARTIAL  │ WebSocket feeds exist for real-time detection; could add priority          │
  │     │                         │          │ execution for time-sensitive opps                                          │
  ├─────┼─────────────────────────┼──────────┼────────────────────────────────────────────────────────────────────────────┤
  │ 20  │ Backtesting-driven      │ PARTIAL  │ backtest.py exists but is incomplete — use historical data to tune         │
  │     │ thresholds              │          │ MIN_NET_ROI, position sizes, etc.                                          │
  └─────┴─────────────────────────┴──────────┴────────────────────────────────────────────────────────────────────────────┘