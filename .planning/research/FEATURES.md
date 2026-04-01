# Feature Research

**Domain:** Automated Prediction Market Arbitrage Bot — Strategy Expansion & Profitability
**Researched:** 2026-04-01
**Confidence:** MEDIUM (strong on strategies and landscape; specific ROI figures are community-reported, not independently audited)

---

## Context: What Already Exists (v1.0)

The bot ships 20 strategies across 5 layers. This research focuses exclusively on what is NOT yet built and what execution problems block profitability. Do not re-examine existing strategies — they are baseline.

**Existing coverage (do not re-implement):**
- Layer 1: Binary/NegRisk internal, Back-all/Back-lay, Cross-platform 2-way, Multi-outcome cross-platform, Triangular
- Layer 2: Resolution sniping, Stale price exploitation, Fee promotional arbitrage
- Layer 3: Passive MM, Cross-platform MM, Inventory-hedged MM
- Layer 4: Event divergence, Cross-platform convergence, Multi-source signal aggregation
- Layer 5: Dynamic fee routing, Kelly sizing, Platform rebalancing, Latency optimization, Backtesting, Spread detection

---

## Feature Landscape

### Table Stakes (Profitability Foundations — Missing These = Bot Doesn't Make Money)

These are not new "strategies" — they are execution-layer requirements that the research consistently identifies as the primary reason bots fail to profit despite detecting valid opportunities.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Maker-order preference routing | Taker fees now exist on Polymarket (up to 1.8% at 50% prob) and Kalshi. Routing all orders as takers destroys margins. Every profitable MM bot uses limit orders exclusively or near-exclusively. | MEDIUM | Limit orders = zero fee on both platforms. Need order type selector in executor. |
| Polymarket Liquidity Rewards capture | Polymarket pays daily USDC rebates to resting limit orders via quadratic scoring. Missing this leaves real money on the table — reported $150-300/day per market for serious MMs. | MEDIUM | Requires posting tight two-sided quotes within 0.10-0.90 midpoint range. Scoring formula: S(v,s) = ((v-s)/v)^2 * b. |
| Kalshi Liquidity Incentive Program integration | Kalshi rebates up to $7,000/week capped per participant. Volume Incentive Program active through September 1, 2026. | MEDIUM | Rebates require resting orders. Kalshi fee formula: ceil(0.07 * C * P * (1-P)). |
| Revalidation timeout tuning | 100% revalidation rejection was the blocker at v1.0 completion. The fix was committed but not deployed. Without this working, zero trades execute regardless of strategy quality. | LOW | Confirmed as known gap. Deploy fix. Tune >= 2% ROI floor for API error tolerance. |
| Sub-second execution path for structural arb | Average arb window dropped to 2.7 seconds in late 2025 (from 12.3s in 2024). 73% of arb profits go to sub-100ms bots. Any structural arb detected by mid-price scan must execute immediately, not queue behind slower strategies. | HIGH | Priority queue exists. Confirm structural arb (binary, negrisk, cross) uses highest priority tier. |

### Differentiators (New Strategy Categories — Highest Expected ROI)

These are strategy categories NOT present in the existing 20 that research identifies as currently profitable or structurally sound.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Combinatorial/logical arbitrage | Spans RELATED markets on the same platform. If "Trump wins 2028" = 35% and "Republican wins 2028" = 30%, the second is structurally impossible since Trump is a Republican. Research found ~$11M in cross-market combinatorial profits on Polymarket Apr 2024-Apr 2025. This is structurally different from cross-platform arb — it's semantic inconsistency within one platform. | HIGH | Requires LLM-assisted semantic relationship detection between markets. Academic paper (2508.03474, Aug 2025) documents the methodology. Not in existing 20 strategies. |
| News-driven repricing (momentum inform) | When breaking news hits, prediction markets take 30 seconds to 5 minutes to reprice. A bot consuming news APIs (NewsAPI, Twitter/X) and computing probability deltas can trade the lag window. One profiled bot made $2.2M in 2 months using ensemble models. The edge is speed of information processing, not prediction accuracy. | HIGH | Requires news feed integration, LLM probability estimation, and <1s execution. Overlaps with existing "event divergence" but targets much shorter windows (news lag vs. Metaculus drift). |
| Betfair/Smarkets in-play scalping | Exchange-based prediction markets (Betfair, Smarkets) allow in-play trading during live events. Scalping 1-2 ticks (minimum odds increments) on in-play markets using automated back/lay pairs is a proven strategy on these platforms, distinct from the back-all/back-lay arb already built. Automated scalpers report 15-25% bankroll returns in stable edge periods. | HIGH | Requires in-play data feeds (live score APIs), event timing integration, and very fast order cancellation. Different from existing back-all/back-lay which targets whole-market overrounds. |
| Polymarket Liquidity Rewards farming (dedicated strategy) | Treat liquidity rewards as primary revenue, not a side effect. Post tight two-sided quotes in high-volume markets, collect daily USDC rewards regardless of directional outcome. The quadratic scoring means 2x tighter spreads = 4x rewards. This is a Layer 3 enhancement but needs its own dedicated scan/execution path optimized for reward maximization rather than spread capture. | MEDIUM | Builds on existing passive MM. Needs reward score estimator and market selection by reward-per-capital metrics. |
| Contrarian sentiment trading | Prediction markets are documented as sentiment indicators and "often a contrarian signal" per research. When a market reaches extreme one-sided consensus (>85% on one side), the opposite position historically has positive EV due to overconfidence bias. This is distinct from convergence strategy — it exploits behavioral bias, not price divergence. | MEDIUM | Requires sentiment threshold detection + position timing. Works best on political/cultural markets. LOW confidence on profitability — needs backtesting. |
| Whale/smart-money copy trading (automated) | Every Polymarket transaction is on-chain. 14 of 20 most profitable wallets are bots. Tracking wallets with documented long-term P&L records and auto-following their large new positions within seconds captures information asymmetry. Multiple commercial tools exist (Polywhaler, PolyTrack) proving viability. | MEDIUM | Requires Polygon blockchain monitoring, wallet P&L scoring, and position-following logic. No API needed — on-chain data is public. |
| Resolution timing arbitrage | Platforms resolve at different speeds. Kalshi resolves faster on regulated events; Polymarket resolution can lag by hours on governance-dependent markets. When the outcome is known (live observable event) but a platform hasn't resolved, selling the winning side before resolution captures remaining premium. Different from resolution sniping (which buys near-certain outcomes) — this sells into the lag. | MEDIUM | Requires oracle/resolution status monitoring per platform. High-confidence signals only to avoid resolution divergence risk. |

### Anti-Features (Commonly Tempting, Actually Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Cross-platform arb with wide resolution divergence tolerance | Appears to double profit potential by taking larger position sizes when spreads are wider. | Polymarket uses UMA optimistic oracle (manipulated in March 2025 — $7M market flipped by whale with 25% voting power). Kalshi uses CFTC-regulated resolution. Different interpretations of the same event already caused total loss on a 2024 government shutdown market. The research explicitly recommends avoiding cross-platform positions unless spreads exceed 15 cents. | Keep existing cross-platform arb with strict spread requirements. Add oracle type classifier to flag when platforms use incompatible resolution mechanisms. |
| Full Kelly sizing on informed/news trades | Kelly criterion is already implemented. Applying full Kelly to news-driven or convergence trades seems optimal by theory. | News-based probability estimates have high error rates. Full Kelly on estimates with 20%+ error produces ruinous drawdowns. Research confirms prediction markets have high accuracy variance — PredictIt 93%, Kalshi 78%, Polymarket 67% outcome accuracy. | Fractional Kelly (quarter-Kelly or half-Kelly) for all non-structural strategies. Full Kelly only on structural arb where the profit is mathematically guaranteed. |
| Wash trading / volume inflation to boost rewards | Inflated volume could theoretically increase scoring or appear in reward calculations. | A Columbia University study found ~25% of Polymarket volume is artificial. Polymarket developed detection algorithms based on wallet behavior patterns. Risk of wallet ban, reward clawback, and regulatory scrutiny (Massachusetts sued Kalshi in September 2025). | Earn rewards through legitimate tight quoting. |
| High-frequency crypto minute/5-minute market trading at scale | These markets (BTC/ETH/SOL 15-min, 5-min) have shown extreme profits ($313 → $414,000 in a month). | Polymarket introduced 1.56% taker fees specifically in high-frequency crypto markets as of January-February 2026, making thin-margin strategies unprofitable. Order book depth is $5,000-$15,000 per side — deploying meaningful capital moves the market against you. | Focus HFT resources on structural arb in standard markets with 0% taker fees (limit orders). |
| Building a Polymarket token/reputation farming strategy | Token speculation as secondary revenue stream seems attractive given wash trading incentives around a potential token. | Regulatory risk. Polymarket US now operates as a CFTC DCM with strict compliance requirements. Token speculation as a trading motive exposes the operation to regulatory scrutiny. | Trade legitimately. Any token upside is incidental. |
| Multi-market simultaneous execution at full size | Expanding from one market to many appears to multiply profits linearly. | Case study: A developer bot made $764/day on BTC single market, then crashed when expanded to BTC+ETH+SOL+XRP simultaneously, retreating to single-market strategies. Leg risk multiplies with market count — one fill failure creates unhedged exposure across many positions. | Scale up market count gradually with capital-aware position sizing per market. |

---

## Feature Dependencies

```
Maker-order preference routing
    └──required by──> Polymarket Liquidity Rewards capture
    └──required by──> Kalshi Liquidity Incentive Program
    └──enhances──> All MM strategies (Layer 3)

Revalidation timeout fix (CRITICAL — deploy first)
    └──required by──> ALL strategies executing live trades
    └──unblocks──> Every other feature in this list

Sub-second execution path
    └──enhances──> Combinatorial/logical arbitrage
    └──enhances──> News-driven repricing (critical — 30s-5min window)
    └──required by──> Betfair in-play scalping

News-driven repricing
    └──requires──> News feed integration (new: NewsAPI/Twitter)
    └──requires──> LLM probability estimator (new component)
    └──conflicts with──> Revalidation threshold (news moves fast; revalidation may kill the trade)

Combinatorial/logical arbitrage
    └──requires──> LLM semantic market relationship detector
    └──requires──> Same-platform multi-market data fetch
    └──enhances──> Existing triangular arb (shares semantic detection logic)

Whale copy trading
    └──requires──> Polygon blockchain monitor (new: on-chain event listener)
    └──conflicts with──> Resolution timing arb (whale may be wrong at close)

Betfair in-play scalping
    └──requires──> Live sports data feed (score/time APIs)
    └──requires──> In-play market detection in betfair_api.py
    └──conflicts with──> Back-all/back-lay arb (separate strategy, different timing)
```

### Dependency Notes

- **Revalidation fix is gate zero**: Until the revalidation floor is correctly tuned and deployed, no execution path works. This must be the very first deliverable.
- **Maker routing enhances all Layer 3**: Posting limit orders instead of market orders eliminates taker fees across both Polymarket and Kalshi, directly improving profitability of every MM strategy.
- **News repricing conflicts with revalidation**: By design, revalidation re-checks prices before execution. News-driven trades are time-sensitive enough that revalidation may kill the trade. Need a fast-path revalidation mode (5-second window instead of 10+) for news-flagged opportunities.
- **Combinatorial arb shares LLM infrastructure with news repricing**: If the LLM component is built for one, the other gets it nearly free.

---

## MVP Definition

### Fix First (Pre-Strategy — Unblocks Everything)

- [ ] **Deploy revalidation fix** — The committed fix (>= 2% ROI API error tolerance, widened WS cache, lowered floor) must be deployed and validated. Without this, zero strategies execute. Complexity: LOW. Already coded.
- [ ] **Maker-order routing in executor** — Switch all limit-eligible orders to maker type. Eliminates taker fees. Directly improves net ROI on every detected opportunity. Complexity: MEDIUM.
- [ ] **Polymarket Liquidity Rewards tracking** — Implement reward score estimator to track daily USDC rebate potential per market. Even without optimizing for it, knowing your reward earnings informs strategy prioritization. Complexity: MEDIUM.

### Layer 1 Strategy Additions (Highest Confidence, Structural)

- [ ] **Combinatorial/logical arbitrage** — Semantic inconsistency detection across related markets on the same platform. Research-documented $11M in profits. Requires LLM market relationship classifier. Complexity: HIGH.
- [ ] **Resolution timing arbitrage** — Sell winning position during platform resolution lag. Observable-outcome monitoring per platform. Complexity: MEDIUM.

### Layer 4 Informed Trading Additions

- [ ] **News-driven repricing** — News API + LLM probability delta + fast-path execution. 30s-5min window. Complexity: HIGH.
- [ ] **Whale/smart-money following** — Polygon on-chain wallet tracker, P&L scorer, position follower. Complexity: MEDIUM.

### Layer 3 Market Making Additions

- [ ] **Betfair/Smarkets in-play scalping** — Tick-offset scalp during live events. Requires live score feeds. Complexity: HIGH.
- [ ] **Liquidity rewards farming (dedicated path)** — Reward-score-optimized quoting for high-reward markets. Complexity: MEDIUM.

### Add After Validation (v2.x)

- [ ] **Contrarian sentiment trading** — Needs backtesting validation before live deployment. Build backtester integration first.
- [ ] **Kalshi Liquidity Incentive Program optimization** — After maker routing is working, optimize Kalshi-specific reward claiming.

### Future Consideration (v3+)

- [ ] **AI ensemble probability model** — Training a custom model on Polymarket/Kalshi historical data for superior probability estimates. Research shows $2.2M in 2 months for one operator, but requires significant ML infrastructure. Defer until structural strategies are maximized.
- [ ] **Polymarket US DCM regulatory arbitrage** — CFTC-regulated DCM version has different resolution standards (verified data feeds) vs. international Polymarket (UMA oracle). Potential for lower oracle-manipulation risk and new market categories.

---

## Feature Prioritization Matrix

| Feature | Expected ROI | Implementation Cost | Priority |
|---------|--------------|---------------------|----------|
| Revalidation fix deployment | CRITICAL — unblocks all | LOW (already coded) | P0 |
| Maker-order routing | HIGH — eliminates taker fees | MEDIUM | P1 |
| Polymarket Liquidity Rewards capture | HIGH — documented $150-300/day | MEDIUM | P1 |
| Combinatorial/logical arbitrage | HIGH — $11M documented on Polymarket | HIGH | P1 |
| News-driven repricing (news lag) | HIGH — $2.2M operator case study | HIGH | P1 |
| Whale copy trading | MEDIUM — information edge, fast decay | MEDIUM | P2 |
| Resolution timing arbitrage | MEDIUM — works in specific events | MEDIUM | P2 |
| Betfair in-play scalping | MEDIUM — proven on exchanges, high complexity | HIGH | P2 |
| Liquidity rewards farming (dedicated) | MEDIUM — requires tight quoting discipline | MEDIUM | P2 |
| Contrarian sentiment trading | LOW (needs backtest validation) | MEDIUM | P3 |
| Kalshi incentive program optimization | MEDIUM — capped at $7K/week | LOW | P2 |
| AI ensemble probability model | HIGH (long term) — HIGH infrastructure cost | HIGH | P3 |

**Priority key:**
- P0: Deploy immediately — this is the execution blocker
- P1: High-confidence new profit sources, build in v2.0 milestone
- P2: Validated but secondary, add when P1s are stable
- P3: Speculative or infrastructure-heavy, defer to v3+

---

## Execution Pitfall Inventory

These are NOT features to build — they are failure modes to prevent. Research consistently identifies these as the reason bots detect opportunities but don't profit.

### Critical Failure Modes

**Leg risk on multi-leg trades.** One leg executes while the hedge fails, creating directional exposure. The research is emphatic: this is the most dangerous execution flaw. Prevention: sub-5-second timeout on leg 2 after leg 1 fills; hedge failure triggers immediate hedger.py sell of filled leg. The hedger already exists — confirm it activates within the timeout.

**Oracle resolution divergence on cross-platform positions.** Polymarket uses UMA (manipulable — March 2025 incident: $7M market flipped by whale with 25% voting power). Kalshi uses CFTC resolution. Same event, different outcome possible. Prevention: flag markets by oracle type; avoid cross-platform positions on markets where platforms have different resolution criteria.

**Taker fee erosion on new fee structure.** As of March 30, 2026, Polymarket rolled out taker fees across nearly all market categories (peaks at 1.8% at 50% probability). A 1.5% gross arb spread with 1.8% taker fee = a loss. Prevention: maker-order routing (P1) and pre-trade fee calculation using the actual formula, not a flat estimate.

**Revalidation killing time-sensitive trades.** The 10%+ profit drop revalidation threshold is calibrated for structural arb. News-driven and resolution-timing opportunities are legitimately moving fast — the price drop is real. Prevention: strategy-type-aware revalidation thresholds. Structural arb: 10% floor. News/timing: 30% floor (accept more slippage given the time window).

**Shallow order book depth.** High-frequency crypto markets have $5K-$15K depth per side. Large orders move the price against the trader before fill completes. Prevention: order size capped at 20% of available depth on first price level; split orders into tranches for size above $2,000.

**Revalidation over-fetching causing rate limit pressure.** Polymarket CLOB has 1,500 req/10s for single market queries. During high-opportunity periods (e.g., election night), simultaneous revalidation of many opportunities will hit limits. Response time increases before 429s appear (Cloudflare queuing model). Prevention: use X-RateLimit-Remaining header monitoring; batch revalidation queries; rate limit back-off using existing tenacity retry logic.

### Moderate Failure Modes

**Stale WebSocket price cache during execution.** If WS feed disconnects and falls back to REST polling, cached prices may be 30-60+ seconds stale. Prevention: tag cache entries with source (WS vs REST) and age; raise revalidation threshold for REST-sourced prices.

**Combinatorial arb semantic false positives.** LLM-detected market relationships (e.g., "Trump wins" → "Republican wins") will have false positives. A wrong semantic relationship generates a "guaranteed arb" that isn't. Prevention: confidence threshold on LLM relationship classifier; require at least 3% spread to compensate for semantic uncertainty; human spot-check initially.

**Wash trading detection by platform.** Rapid round-trip trades (buy then immediately sell) match the behavioral signature of wash trading. Columbia study: some detection algorithms look for wallets that open and quickly close positions. Prevention: hold positions for minimum 60 seconds before closing; avoid same-market repeated round-trips within short windows.

**Betfair in-play suspension.** Betfair suspends markets during live action (goals scored, points scored, set changes). Orders submitted during suspension are rejected. Prevention: in-play state monitor; cancel outstanding orders before expected suspension windows.

---

## Competitor Feature Analysis

The "competitors" here are other automated prediction market bots documented in the research.

| Feature | Dominant Bots (documented) | Polywhaler/PolyTrack (tools) | Our Approach |
|---------|--------------------------|------------------------------|--------------|
| Structural arbitrage | Sub-100ms execution, priority on binary/negrisk | Alert service only | Already built; needs execution latency hardening |
| Combinatorial arb | Academic research attributes $11M to this class | Not implemented in tools | Build LLM-assisted version |
| News repricing | Ensemble probability models (GPT-4 + custom), 30s window | Not implemented | Build with fast-path execution mode |
| Whale tracking | Proprietary on-chain monitoring | Polywhaler/PolyTrack offer alerts | Automate the execution piece; use public on-chain data |
| Market making rewards | Professional MMs use quadratic scoring optimization | Not applicable | Build reward score estimator to optimize quote placement |
| Multi-platform coverage | Most top bots focus on Polymarket only | Polymarket-only | Already multi-platform (advantage) |

---

## Sources

- [Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets (Aug 2025, IMDEA Networks)](https://arxiv.org/abs/2508.03474) — HIGH confidence, peer-reviewed academic paper documenting $40M in Polymarket arb profits, combinatorial arb methodology
- [Beyond Simple Arbitrage: 4 Polymarket Strategies Bots Profit From in 2026](https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f) — MEDIUM confidence, community-reported strategies
- [Polymarket Liquidity Rewards Documentation](https://docs.polymarket.com/market-makers/liquidity-rewards) — HIGH confidence, official docs
- [Kalshi Liquidity Incentive Program](https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program) — HIGH confidence, official docs
- [Polymarket Rate Limits Guide (March 2026)](https://agentbets.ai/guides/polymarket-rate-limits-guide/) — MEDIUM confidence, community-maintained, aligns with Polymarket docs
- [Polymarket Fee Structure 2026](https://docs.polymarket.com/trading/fees) — HIGH confidence, official docs
- [Building a Prediction Market Arbitrage Bot: Technical Implementation](https://navnoorbawa.substack.com/p/building-a-prediction-market-arbitrage) — MEDIUM confidence, practitioner analysis
- [Oracle Manipulation in Polymarket 2025](https://orochi.network/blog/oracle-manipulation-in-polymarket-2025) — MEDIUM confidence, documented incident
- [Polymarket Volume Inflated by Artificial Activity (Columbia Study, Nov 2025)](https://fortune.com/2025/11/07/polymarket-wash-trading-inflated-prediction-markets-columbia-research/) — HIGH confidence, peer-reviewed
- [Polymarket US DCM Relaunch (Jan 2026)](https://www.polymarketexchange.com/) — HIGH confidence, official
- [Arbitrage Bots Dominate Polymarket (Yahoo Finance)](https://finance.yahoo.com/news/arbitrage-bots-dominate-polymarket-millions-100000888.html) — MEDIUM confidence
- [How AI is Helping Retail Traders Exploit Prediction Market Glitches (CoinDesk, Feb 2026)](https://www.coindesk.com/markets/2026/02/21/how-ai-is-helping-retail-traders-exploit-prediction-market-glitches-to-make-easy-money) — MEDIUM confidence
- [Betfair Trading Strategies: The Ultimate 2026 Guide](https://botblog.co.uk/betfair-trading-strategies-2/) — MEDIUM confidence, practitioner
- [Prediction Markets Are Turning Into a Bot Playground (Finance Magnates)](https://www.financemagnates.com/trending/prediction-markets-are-turning-into-a-bot-playground/) — MEDIUM confidence

---

*Feature research for: Polymarket Arb Scanner v2.0 — Strategy Expansion & Profitability*
*Researched: 2026-04-01*
