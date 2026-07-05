# Mode → Strategy Reconciliation

> **Date:** 2026-06-13 · **Owner:** Jonathon Tamm · **Status:** canonical
> Closes the tracked TODO in [`strategy-framework-v2.md`](strategy-framework-v2.md) (§Status Roll-Up, 2026-05-31 note) and [`ROADMAP.md`](ROADMAP.md) ("Mode→strategy reconciliation").
>
> **Two source-of-truth layers, now mapped 1:1:**
> - The **`--mode` set in `cli.py`** is the source of truth for *runnable scans*.
> - The **29-strategy / 5-layer taxonomy** in `strategy-framework-v2.md` is the source of truth for the *risk-layer taxonomy*.
> This table is the bridge. Re-run it whenever a `--mode` value is added or the taxonomy changes.

## Count reconciliation (as of 2026-06-13)

- **`cli.py` exposes 35 `--mode` values** including `all` → **34 runnable scan modes**.
- **CLAUDE.md still says 33.** The drift: **`seerium`** was added (the `scans/x402_seerium.py` x402 paid-data scan, currently untracked in git) and is **not yet in CLAUDE.md's mode list or the 29-strategy taxonomy**. Action: update CLAUDE.md `33 → 34` and slot `seerium` (tracked as a Linear issue).
- The 34 modes map onto **29 taxonomy strategies** because: (a) several modes are *execution variants* of one strategy, (b) some modes cover *two* strategies at once (`kalshi`, `gemini` each run binary **and** multi), and (c) Layer 5 strategies (#16–#20) are **cross-cutting multipliers with no `--mode`** — they apply to every scan rather than running as one.

## The map

| `--mode` | Strategy # | Layer | Name | Notes |
|---|---|---|---|---|
| `binary` | 1 | L1 | Same-platform binary overround | Polymarket binary |
| `negrisk` | 2 | L1 | Same-platform multi-outcome overround | Polymarket NegRisk (Σ YES > 1) |
| `negrisk-no` | 2a | L1 | NegRisk **NO-side** (Σ NO < N−1) | Risk-free complement to #2 (Plan 01); flag `NEGRISK_NO_SIDE_ENABLED` |
| `kalshi` | 1 **+** 2 | L1 | Kalshi binary **+** multi-outcome | One mode, two strategies |
| `gemini` | 1 **+** 2 | L1 | Gemini binary **+** multi-outcome | One mode, two strategies |
| `ibkr` | 1 | L1 | IBKR ForecastEx binary | BUY-only, $0 commission |
| `cross` | 3 | L1 | Cross-platform 2-way | Pairwise |
| `cross-all` | 3 | L1 | Cross-platform 2-way (all 28 pairs) | Variant of #3 |
| `multi-cross` | 4 | L1 | Multi-outcome cross-platform | Cheapest YES per outcome |
| `nway` | 4 | L1 | N-way multi-outcome cross | Execution variant of #4 |
| `triangular` | 5 | L1 | Triangular (3-way) | Union-find grouping |
| `betfair` | 6 | L1 | Back-all / back-lay (Betfair) | **Exec disabled (allowlist)** |
| `smarkets` | 6 | L1 | Back-all / back-lay (Smarkets) | **Exec disabled (allowlist)** |
| `sxbet` | 6 | L1 | Back-all / back-lay (SX Bet) | **Quarantined** — unsigned JSON (#6 EIP-712 gap) |
| `matchbook` | 6 | L1 | Back-all / back-lay (Matchbook) | **Exec disabled (allowlist)** |
| `spread` | 21 | L1 (L5 dual) | Crossed-book / spread detection | Feeds MM spread sizing |
| `resolution` | 7 | L2 | Resolution sniping | `RESOLUTION_SNIPE_WINDOW_HOURS` |
| `stale` | 8 | L2 | Stale price exploitation | Needs `--continuous` WS history |
| `fee-promo` | 9 | L2 | Fee promotional arbitrage | Flag `FEE_PROMO_ENABLED` |
| `mm` | 10 | L3 | Passive market making | `QuoteEngine` / `QuoteManager` |
| `cross-mm` | 11 | L3 | Cross-platform market making | Flag `CROSS_MM_ENABLED` |
| `lead-lag-mm` | 11 (variant) | L3 | Lead-lag cross-platform MM | Execution variant of #11 |
| `vol-mm` | 10 (variant) | L3 | Volatility-aware MM | Execution variant of #10 |
| `toxic-flow` | 10/12 (control) | L3 | Adverse-selection / toxic-flow gate | MM safety control, not a standalone edge; the `12` is a cross-link to the mode-less #12 hedge trigger — not a claim that #12 has its own mode (coverage still counts 22 mode-mapped strategies) |
| `rewards` | 22 **+** 23 | L3 | Liquidity reward farming (Polymarket **+** Kalshi) | One mode, two strategies |
| `event` | 13 | L4 | Event divergence (multi-source) | `event_monitor` + `signal_aggregator` |
| `convergence` | 14 | L4 | Cross-platform convergence | Outlier → median |
| `imbalance` | 24 | L4 | Order-book imbalance | Two-stage refinement |
| `logical-arb` | 25 | L4 | Logical / combinatorial arb | Plan 02 (Fréchet) extends this |
| `time-decay` | 26 | L4 | Time-decay convergence | Refiner `_refine_time_decay_with_prices` |
| `news-snipe` | 27 | L4 | News-driven sniping | `finnhub_api` |
| `whale-copy` | 28 | L4 | Whale copy | `whale_copy_decoder` |
| `correlated` | 29 | L4 | Correlated pairs | `correlation_tracker` |
| `seerium` | **— (new)** | L4? | x402 paid-data scan (`x402_seerium.py`) | **Unmapped** — propose taxonomy slot; ties to JOH agentic-wallet x402 work |

## Strategies with no `--mode` (by design)

| Strategy # | Layer | Name | Why no mode |
|---|---|---|---|
| 12 | L3 | Inventory-hedged MM | On-fill wiring (`MarketMaker.on_fill` → `hedger.hedge_inventory`), not a standalone scan. Flag `MM_AUTO_HEDGE_ENABLED` |
| 15 | L4 | Multi-source signal aggregation | Feeds `event` / `convergence`; not run alone |
| 16 | L5 | Dynamic fee routing | Cross-cutting multiplier (`gas_monitor` + `fees`) |
| 17 | L5 | Kelly criterion sizing | Cross-cutting (`position_sizer`) |
| 18 | L5 | Platform fund rebalancing | `treasury` corridor (Gemini↔Polymarket); flag `AUTO_REBALANCE_ENABLED` |
| 19 | L5 | Latency optimization | Cross-cutting (`continuous._execution_priority`) |
| 20 | L5 | Backtesting-driven tuning | Cross-cutting (`config.apply_backtest_recommendations`); shipped PR #48 |

## Coverage check

- **All 29 taxonomy strategies are accounted for**: 22 map to a `--mode` (some sharing a mode), 7 are mode-less by design (#12, #15–#20).
- **All 34 runnable modes are accounted for**: 33 map to a taxonomy strategy or a labeled variant; **1 (`seerium`) is new and unmapped** — the only open reconciliation item.
- **Variants flagged** (`cross-all`, `nway`, `lead-lag-mm`, `vol-mm`, `toxic-flow`) so the surplus mode count never reads as "extra strategies."

## Follow-ups (→ Linear engine board)

1. Update `CLAUDE.md` mode count `33 → 34`; add `seerium` to the documented mode list.
2. Decide `seerium`'s taxonomy slot (likely a Layer-4 informed/data-access variant) or mark it experimental.
3. `logical-arb` is in `cli.py` `choices` but **not dispatched in `_run_oneshot`** (continuous-only) — orphaned in one-shot. Wire or document.
