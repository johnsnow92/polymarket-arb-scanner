# Platform Expansion — Recommendation Memo

> **Owner:** Jonathon Tamm · **Review cadence:** revisit when a candidate's API/regulatory status changes, or before any integration build.
> **Type:** decision record (memo-only). Per the WS-3 decision, this ranks and gates candidates; it does **not** pick the build order or run the OpticOdds spike — the operator decides sequencing later.
> **Evidence:** `docs/audit/PLATFORM-RESEARCH-2026-05-31.md` (sources, dates, confidence). **Canonical platform state:** `docs/PLATFORM-MATRIX.md`.

## Context
The grid already trades 8 venues (+2 read-only). Expansion value = **independent order books** that create genuine cross-platform arb edge. Venues that route through an already-integrated book (Robinhood→Kalshi; DraftKings/FanDuel→CME) add **no** edge and are excluded.

A recurring theme refined during research: **OpticOdds (already integrated) makes *detection* cheap for several US venues, but *execution* requires each venue's own trading API — and that is the hard, uncertain part.** Detection ≠ tradable.

## Recommendation (ranked, NOT sequenced)

### Tier 1 — US central-limit-order-book venues (detection via OpticOdds; execution varies)
| Venue | Independent book | Detection | Direct trading API | Net read |
|---|---|---|---|---|
| **ProphetX** | ✅ US sports exchange | ✅ OpticOdds-covered | ✅ **`docs.prophetx.co`** — place/manage limit orders, wallet mgmt, "for algorithmic traders" — but **permissioned (requires an agreement)** | Strongest direct-execution candidate; gated on getting API access approved |
| **Sporttrade** | ✅ US CLOB marketplace | ✅ OpticOdds-covered | ⚠️ **not publicly documented** | Detection cheap; execution path unconfirmed — needs DD before ranking effort |
| **Novig** | ✅ US P2P exchange (35+ states) | ✅ OpticOdds (trial) | ⚠️ **not publicly confirmed** | Same as Sporttrade — data yes, trade-API unknown |

### Tier 2 — on-chain venues (doc-confirmed trading APIs)
| Venue | Trading API | Note |
|---|---|---|
| **Predict.fun** | ✅ `dev.predict.fun` (orderbook, place, settle) | On-chain (BNB/Linea/Abstract); yield on idle capital → feeds Layer 5 |
| **Myriad** | ✅ `docs.myriad.markets` (EIP-712 CLOB) | **Bundle with the #6 SX Bet EIP-712 fix** — shared signing primitive |
| **Limitless** | ✅ `help.limitless.ai` public API | On-chain (Base), global |

### Tier 3 — defer / watch
Drift BET (Solana, high effort, new stack); Crypto.com / OG.com (public *trading* API unclear).

### Excluded
Robinhood (→Kalshi book), DraftKings Predict / FanDuel Predicts (→CME, no public trading API), PredictIt (data-only, thin/political).

## Gates every candidate must clear before greenlight
A candidate is **not** greenlit until **all** gates pass. Status today is mostly UNKNOWN — that is the point of the pre-build due diligence.

1. **Regulatory eligibility (blocking)** — legal to use from Michigan (operator's state; active anti-prediction-market litigation as of May 2026), KYC available, no geo-block, ToS permits automated/API trading. *Status: UNKNOWN — resolve first.*
2. **Execution-API reality (blocking)** — a real, accessible order-placement API (not just OpticOdds data). *ProphetX: yes-but-permissioned; Sporttrade/Novig: UNKNOWN; Tier 2: doc-confirmed.*
3. **Operational readiness (blocking, round-3 M4)** — API status/incident history, sandbox/testnet, documented rate limits, WS reconnect semantics, idempotency/order-retry behavior, support/escalation path. *Status: UNKNOWN per venue.*
4. **Authz/custody (round-2 M2)** — key scopes, trade-vs-withdraw separation, signing model, rotation, IP/geo. Capture in `PLATFORM-MATRIX.md` before building.
5. **Default-off feature flag** — every new venue ships behind `<VENUE>_ENABLED=false`, following the CLAUDE.md "add a new opportunity type" recipe.

## Mandatory first step for any Tier-1 build (deferred — WS-4)
Before writing a Tier-1 execution client, run the throwaway **OpticOdds detection spike** (`spikes/`, no production code): measure market-identity mapping across venues, settlement/grading normalization, depth/liquidity semantics, fee modeling, OpticOdds rate limits, and data freshness vs. the arb window. Spike output = a written verdict on whether cross-platform arb is *detectable* from OpticOdds data with acceptable freshness. This is what converts "low effort" from assumption to fact.

## Bottom line
- If the goal is **fastest path to a confirmed direct trading API**: **ProphetX** (pursue API-access agreement) or the **Tier-2 on-chain venues** (no gatekeeper).
- If the goal is **broadest US detection leverage from existing infra**: Tier-1 via OpticOdds — but only after the spike + execution-API DD.
- **Myriad** is the highest-synergy pick because it shares the EIP-712 work the existing SX Bet #6 gap already needs.

Sequencing is the operator's call (per WS-3 decision).
