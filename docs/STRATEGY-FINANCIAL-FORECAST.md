# Per-Strategy Financial Forecast — arbgrid (Lane A engine)

> **Date:** 2026-06-13 · **Owner:** Jonathon Tamm · **Scope:** the 29 strategies / 34 scan modes in *this* engine. The 34th mode (`seerium`) is a new x402 paid-data scan with **no taxonomy strategy and no P&L line yet** — see [`MODE-STRATEGY-MAP.md`](MODE-STRATEGY-MAP.md).
> **This is the engine-level companion** to the command center's per-*lane* forecast (`~/Financial Markets with AI/09-FINANCIAL-FORECAST.md`). That doc forecasts 9 portfolio lanes; this one decomposes **Lane A (prediction markets)** down to the individual strategy. The two reconcile: the roll-up here ties to Lane A's **base ≈ $3.0K / bull $8K / bear −$1K** in `09-FINANCIAL-FORECAST`.
> **Pairs with:** [`MODE-STRATEGY-MAP.md`](MODE-STRATEGY-MAP.md) (which strategy each mode is), [`strategy-framework-v2.md`](strategy-framework-v2.md) (status), [`RISK-POLICY.md`](RISK-POLICY.md), `PLATFORM-MATRIX.md` (fees/auth).

## Method & honesty rules

- Figures are **year-1, net of fees + estimated tax + labor**, on tranche-gated Lane-A capital. **Assumptions are labeled inline** — none of these are measured returns.
- **Base** = realistic expectation · **Bull** = strategy goes right · **Bear** = underperforms / kill-gate trips. The **Bull column is NOT additive** — use the roll-up, not the sum.
- Headline ROIs from source research (15–40%+) are **ceilings, not expectations** — excluded.
- **Hurdle floor 4.70%** (LOC rate): a strategy that can't beat it after tax+labor should send its capital to the LOC. Portfolio benchmark ≈14% (VOO).

## The binding constraint: the Michigan venue allowlist

This is the single largest driver of every number below, so it leads. Per the command-center allowlist (a hard, test-enforced gate):

| Venue | Status from MI | Effect on strategies routing there |
|---|---|---|
| **Kalshi** (non-sports) | ✅ **Executable** | Real dollars |
| **IBKR ForecastEx** | ✅ **Executable** (BUY-only; ~3.1–3.8% coupon on resting balance) | Real dollars (Tranche 2) |
| **US-regulated crypto futures** (Coinbase/Kraken/Kalshi perp) | ✅ **Executable** | Perp lane (separate doc — Plan 07) |
| **Polymarket** | 🔍 **Read-only** (MI-geoblocked; TRO denied 2026-03-11) | **Detection-only — $0 until cleared** |
| **Betfair / Smarkets / SX Bet / Matchbook** | ⛔ **Execution disabled** | Detection-only |

**Consequence:** the bulk of the 29 strategies are implemented and *detect* correctly, but route to venues that are read-only or disabled from Michigan. **The executable surface today is narrow: Kalshi non-sports + IBKR ForecastEx.** That is why Lane A's honest base is ~$3.0K, not the headline figures — and why the dollar weight below concentrates in a handful of rows.

**Production reality (from `00-STRATEGY-SYNTHESIS`, Railway — not local):** live since 2026-03-02, **−$11.18 realized**, **154 Kalshi fills** (~$308 traded), **$0 reward capture** (LIP not yet harvested), **0 Polymarket fills** (geoblocked). Local `trades.db` is an empty dry-run copy; the 2026-06-12 dry run logged **35 `KalshiMulti(10)` opportunities at 25–50% ROI**, all skipped on a $0.01 Kalshi balance — i.e. the edge is being detected and is waiting on funding, not on logic.

## Per-strategy forecast (Lane A)

Legend — **Exec MI:** ✅ executable · 🔍 detection-only (Polymarket) · ⛔ disabled venue · ➿ multiplier (applies to others).

| # | Strategy | Layer | Exec MI | Capital need | EV driver (assumption) | Base | Bull | Bear | Status |
|---|---|---|---|---|---|---|---|---|---|
| 2 | Kalshi multi-outcome overround (`KalshiMulti`) | L1 | ✅ | $1–2.5K | Complete-set < $1.00; thin books cap size. **The live edge** — 35 opps/run detected | **$1.2K** | $3K | $0 | BUILT |
| 1 | Kalshi binary overround | L1 | ✅ | shares C | Rare crossed books on liquid Kalshi binaries | $0.2K | $0.6K | $0 | BUILT |
| 7 | Resolution sniping (IBKR ForecastEx) | L2 | ✅ | $1–2K (T2) | Near-certain at a discount **+ ~3.1–3.8% coupon** while held | $0.5K | $1.2K | $0 | BUILT |
| 22 | Liquidity reward farming — Kalshi LIP | L3 | ✅ | $2–3K posted | Paid to post resting orders; pool $10–1,000/day/mkt. **Ends Sep 1** | **$1.0K** | $3K | $0.3K | BUILT |
| 10 | Passive market making (Kalshi non-sports) | L3 | ✅ | $2–3K (≤$300/mkt) | Spread capture, capped pilot; adverse-selection is the risk | $0.4K | $1.5K | −$0.5K | BUILT |
| 12 | Inventory-hedged MM | L3 | ✅ | (rides #10) | Cross-venue hedge neutralizes MM inventory | (in #10) | (in #10) | (in #10) | BUILT |
| 5 | Triangular (Kalshi/IBKR-only legs) | L1 | ✅ | $0.5–1K | 3-way mispricing where all legs are executable venues | $0.1K | $0.4K | $0 | BUILT |
| 3 | Cross-platform 2-way (executable legs) | L1 | ✅⧫ | $0.5–1K | Only Kalshi×IBKR×crypto-futures pairs are live; Polymarket legs detection-only | $0.2K | $0.6K | $0 | BUILT |
| 8 | Stale price exploitation | L2 | ✅⧫ | (rides scans) | WS-history-gated; executable only on Kalshi/IBKR side | $0.1K | $0.4K | $0 | BUILT |
| 23 | Liquidity reward farming — Polymarket | L3 | 🔍 | — | Maker rebate 25%/20% + pools — **not capturable from MI** (geoblock) | $0 | $0 | $0 | BUILT (blocked) |
| 2a | NegRisk NO-side (Polymarket) | L1 | 🔍 | — | Σ NO < N−1; risk-free, **but Polymarket read-only from MI** | $0 | $0 | $0 | BUILT (blocked) |
| 2 | Polymarket NegRisk overround | L1 | 🔍 | — | Same — detection-only from MI | $0 | $0 | $0 | BUILT (blocked) |
| 21 | Crossed-book / spread detection | L1/L5 | 🔍✅ | — | Feeds MM sizing; PM side detection-only, Kalshi side feeds #10 | (in #10) | (in #10) | $0 | BUILT |
| 4 | Multi-outcome cross-platform | L1 | 🔍 | — | Needs a Polymarket or disabled leg in practice | $0 | $0.3K | $0 | BUILT (mostly blocked) |
| 6 | Back-all / back-lay (Betfair/Smarkets/SX/Matchbook) | L1 | ⛔ | — | All four venues execution-disabled; SX Bet also unsigned-JSON quarantined | $0 | $0 | $0 | PARTIAL/blocked |
| 9 | Fee promotional arbitrage | L2 | 🔍⧫ | — | Routes to lowest-fee path; most paths disabled from MI | $0 | $0.2K | $0 | BUILT |
| 11 | Cross-platform MM | L3 | 🔍 | — | Opposing quotes across venues — needs ≥2 executable venues | $0 | $0.3K | $0 | BUILT |
| 13 | Event divergence (multi-source) | L4 | ✅⧫ | $0.5K | Signal can drive a Kalshi-side directional bet; moderate risk | $0.1K | $0.5K | −$0.2K | BUILT |
| 14 | Cross-platform convergence | L4 | 🔍 | — | Outlier→median; needs executable outlier venue | $0 | $0.3K | −$0.1K | BUILT |
| 24 | Order-book imbalance | L4 | ✅⧫ | $0.3K | Kalshi-side microstructure signal | $0.05K | $0.3K | −$0.1K | BUILT |
| 25 | Logical / combinatorial arb | L4 | 🔍 | — | Semantic rule violations on Polymarket (read-only); Plan 02 (Fréchet) extends | $0 | $0.2K | $0 | BUILT |
| 26 | Time-decay convergence | L4 | ✅⧫ | $0.3K | Near-expiry consensus vs price, Kalshi side | $0.05K | $0.3K | −$0.1K | BUILT |
| 27 | News-driven sniping | L4 | ✅⧫ | $0.3K | Fresh news vs slow Kalshi price | $0.05K | $0.3K | −$0.1K | BUILT |
| 28 | Whale copy | L4 | 🔍 | — | On-chain Polymarket whale decode — read-only from MI | $0 | $0.2K | −$0.1K | BUILT |
| 29 | Correlated pairs | L4 | ✅⧫ | $0.3K | 2σ divergence on correlated Kalshi pairs | $0.05K | $0.3K | −$0.1K | BUILT |
| 15 | Multi-source signal aggregation | L4 | ➿ | — | Feeds #13/#14 — quality multiplier, not a standalone P&L line | — | — | — | BUILT |
| 16 | Dynamic fee routing | L5 | ➿ | — | Picks lowest-fee path; raises net on every executable line | +5–15% on net | — | — | BUILT |
| 17 | Kelly criterion sizing | L5 | ➿ | — | Sizes each bet by edge; protects bear case | risk control | — | — | BUILT |
| 18 | Platform fund rebalancing | L5 | ➿ | — | Gemini↔Polymarket corridor only (both non-exec from MI) — dormant | — | — | — | PARTIAL |
| 19 | Latency optimization | L5 | ➿ | — | WS-triggered priority execution — wins races on executable lines | edge capture | — | — | BUILT |
| 20 | Backtesting-driven tuning | L5 | ➿ | — | Auto-tunes thresholds (PR #48) — compounds all lines | compounding | — | — | BUILT |

⧫ = executable in principle but practically narrow today because the richest opportunities sit on read-only/disabled venues.

## Roll-up (reconciles to command-center Lane A)

Component sums first (sum of the per-strategy columns), then the explicit haircut to the Lane A anchor. Base and bull are **not** naive sums: capacity, book depth, and solo labor mean the engine can't realize every line at once, and bull cases don't peak simultaneously.

| Component | Base | Bull | Bear |
|---|---|---|---|
| **Executable today** — #2 multi, #1, #7, #22 LIP, #10 MM, #5, #3 | $3.6K | $10K | **−$0.2K** (only #10 MM −$0.5K, offset by #22 LIP +$0.3K; the rest $0) |
| **Narrowly-executable L4** — #13, #24, #26, #27, #29 (Kalshi-side directional) | $0.3K | $1.7K | −$0.6K |
| **Detection-only (Polymarket/disabled)** — #2/#2a/#23/#25/#28 etc. | $0 (banked for when MI clears) | upside optionality | $0 |
| **Component sum** | ~$3.9K | (not additive) | **−$0.8K** |
| **Haircut to Lane A anchor** (capacity/depth/labor; bear adds a labeled labor+tax + MM-adverse-selection drag) | −$0.9K | — | −$0.2K |
| **Lane A total (this engine)** | **≈ $3.0K** | **≈ $8K** | **≈ −$1K** |

Ties to `09-FINANCIAL-FORECAST` Lane A (**$3.0K / $8K / −$1K**), now arithmetically: base = the ~$3.9K component sum haircut −$0.9K for capacity/depth/labor (can't realize every line at once); bear = the −$0.8K component bear (#10 MM net of #22 LIP, plus the narrow-L4 rows) plus a labeled ~−$0.2K labor/tax + adverse-selection drag. The engine view shows *which strategies produce it*: **Kalshi multi-outcome arb + Kalshi LIP rewards + the capped MM pilot + ForecastEx resolution sniping** carry the base; everything Polymarket-side is banked optionality worth ~$0 from Michigan today.

## Capacity & capital notes (assumptions, labeled)

- **Kalshi book depth is the cap, not capital.** The 35 detected `KalshiMulti` opps are real but thin — sizing to depth (not ambition) is why multi-outcome base is ~$1.2K, not the 25–50% headline ROI × capital.
- **LIP is a clock, not a level.** Kalshi LIP/VIP **end Sep 1, 2026** — the $1.0K base assumes ~2.5 months of capture from a ~July start. Harvested-to-date: **$0** (the gap to close first).
- **ForecastEx needs a persistent IB Gateway** (not Railway) — its $0.5K base is Tranche-2-gated on that infra.
- **Margin efficiency** (Layer 5) does not apply to Lane A cash positions (Kalshi predictions balance earns no interest) — it matters for the perp lane (Plan 07), not here.

## What moves these numbers (watch items)

- **▲ Up:** MI clears Polymarket → unlocks #2/#2a/#23/#25/#28 (the largest banked block); LIP capture verified at scale before Sep 1; ForecastEx funded (T2).
- **▼ Down:** Kalshi LIP/VIP end Sep 1 (rewards cliff); a settlement-divergence loss halts the cross-venue lane; thin Kalshi depth caps multi-outcome size; MI regulatory action widens from sports to non-sports Kalshi.

*Caveat: every executable line here is small in absolute dollars and labor-/depth-gated. Size expectations to the base column. The durable asset is the detection+execution substrate — when MI legality opens or a new executable venue lands, the detection-only rows convert to dollars at near-zero marginal build cost.*
