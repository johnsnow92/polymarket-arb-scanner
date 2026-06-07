# Prediction-Market Platform Research — 2026-05-31

> Evidence artifact for claudex plan loop `20260531-230458-iBXAqE`.
> Retrieved via Firecrawl search + scrape on 2026-05-31. Each candidate carries source URL(s),
> an API-availability verdict, and a confidence level. This backs the PLAN.md Phase-2 recommendation.

## Current grid (baseline — already integrated)
8 trading platforms: Polymarket, Kalshi, Betfair, Smarkets, SX Bet (read-only/quarantined), Matchbook, Gemini Predictions, IBKR ForecastEx. 2 read-only signal sources: Metaculus, Manifold.

## Method & caveats
- Sources are secondary aggregators + primary API docs where reachable. **API verdicts marked "doc-confirmed" were verified against the platform's own developer docs URL; "secondary" means only third-party listing confirms it.**
- No account/KYC/geo eligibility was tested. **Regulatory access is a BLOCKING input** resolved per-platform before any integration (see PLAN.md WS-4). Michigan (operator's state) has active anti-prediction-market litigation as of May 2026 (next.io state table, 2026-05-29).
- "Arb edge" = whether the venue runs an **independent order book** (vs. routing through an already-integrated venue's book).

## Candidate register

| Platform | Type / chain | API verdict | Source(s) | Confidence | Independent book? (arb edge) | Effort | Tier |
|---|---|---|---|---|---|---|---|
| **Sporttrade** | US-regulated CLOB betting exchange | Trading API; **also normalized via OpticOdds (already integrated here)** | new.getsporttrade.com; opticodds.com; bettoredge.com | High (data), Med (exec API specifics) | **Yes** — independent US sports CLOB | Low–Med | **1** |
| **Novig** | US P2P betting exchange | Data via OpticOdds + PredictionData.io; exec API less documented | predictiondata.io/us/api-data/novig; bettoredge.com | Med | **Yes** — zero-vig P2P book | Low–Med | **1** |
| **ProphetX** | US betting exchange | Explicit "automation & API access" (App Store listing); OpticOdds covered | apps.apple.com/.../prophetx; opticodds.com/sportsbooks/prophet-x-api; predictiondata.io | Med–High | **Yes** — independent book, custom-odds maker | Low–Med | **1** |
| **Predict.fun** | On-chain (BNB/Linea/Abstract), USDC+USDT | **Doc-confirmed** full REST trading API: orderbook, order placement, settlement | dev.predict.fun (multiple endpoints) | High | **Yes** — on-chain book; yield on idle capital | Med | **2** |
| **Myriad Markets** | Hybrid off-chain/on-chain CLOB | **Doc-confirmed** EIP-712 signed orders, REST submit | docs.myriad.markets/builders/myriad-order-book | High | **Yes** — shares EIP-712 primitive SX Bet (#6) needs | Med | **2** |
| **Limitless** | On-chain (Base), global | **Doc-confirmed** public API ("build your own integrations") | help.limitless.ai/en/articles/11106060-limitless-api | High | **Yes** — on-chain book | Med | **2** |
| **Drift BET** | Solana DLOB | **Doc-confirmed** market-maker orderbook/matching API | docs.drift.trade/developers/market-makers | High | **Yes** — Solana-native (new chain) | High | **3 (defer)** |
| **Crypto.com / OG.com** | Licensed sports prediction | Public *trading* API unclear | next.io; rotowire; laikalabs.ai | Low | Partial | Med–High | **3 (watch)** |
| **PredictIt** | CFTC legacy | Data API only; political focus | laikalabs.ai; next.io | Med | Thin/illiquid | Low | Read-only signal (optional) |
| **Robinhood Predictions** | Routes through **Kalshi** book | n/a for arb | laikalabs.ai (powered by Kalshi) | High | **No** — same book as Kalshi | — | **Skip** |
| **DraftKings Predict / FanDuel Predicts** | Route through **CME** | No public trading API | laikalabs.ai (CME-routed) | High | **No** — shared CME pricing | — | **Skip (monitor)** |

## Recommendation rationale
- **Tier 1 leverages the OpticOdds CLI already wired into this repo** (CLAUDE.md OpticOdds section lists Sporttrade, Novig, ProphetX, BetDEX as covered). Detection cost is low *for data*, BUT cross-platform arb still requires solving market-identity mapping, settlement normalization, depth/liquidity semantics, fee modeling, rate limits, and freshness — see the **mandatory OpticOdds spike** (PLAN.md WS-4) before ranking effort as final.
- **Tier 2 Myriad is deliberately paired with the SX Bet #6 EIP-712 fix** — same signing primitive; doing them together amortizes the hard part.
- **Skips are principled:** Robinhood/DraftKings/FanDuel route through Kalshi or CME → no independent book → zero arbitrage edge.

## Regulatory watch (blocking inputs, not yet resolved)
- MI litigation status (operator's home state) — confirm which venues/markets are legally accessible before integrating.
- Per-venue: KYC availability, geo-blocking, ToS clause on automated/API trading, US-person eligibility for on-chain venues.
