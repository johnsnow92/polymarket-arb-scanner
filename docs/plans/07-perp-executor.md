# 07 — US Perp Dead-Band Carry Executor

**Status:** spec / not yet built · **Owner:** Claude (build) + Codex (adversarial review) · **Capital:** ~$5K (≈$2.5K/leg), ~$10K notional
**Related:** `02-kalshi-lip-mm-scope.md`, command-center `02-UNIFIED-OPPORTUNITY-MATRIX-v2` (T2 brief), `09-FINANCIAL-FORECAST`, `11-PRD §5.2`.

## 0. TL;DR
Delta-neutral two-leg carry: **long Kalshi BTC perp** (funding pinned ≈0 in the dead band) vs **short Coinbase US-regulated-futures BTC** (funding ≈+6%/yr). Harvest the funding differential while delta-neutral. Built to run **autonomous** behind a deterministic safety stack; graduates shadow → micro → scale. **No LLM in the hot path.** The edge is transient (decays over ~1 quarter) — this executor is also the reusable substrate for future carry lanes, which is the real payoff.

## 1. Thesis & edge
- Kalshi perp funding sits pinned at 0.0000% in the majority of periods (dead-band mechanic, confirmed live: portfolio read 2026-06-13 showed Fund 0.0000% across contracts). Coinbase/other US futures pay ~+6%/yr.
- Long the 0%-funding leg, short the +funding leg → collect the spread, delta-neutral.
- **Transient:** compresses as participants arrive; fee holiday lowers entry cost only (~$15–40/leg). The funding *mechanic* outlives the holiday.
- **Pre-registered kill (from T2 brief):** kill if median diff <3%/yr OR <3 qualifying episodes/wk OR Kalshi depth fails (<$10K within 2bp). Day-14 verdict 2026-06-26.

## 2. Venues & legality (HARD ALLOWLIST)
| Leg | Venue | Allowed? |
|---|---|---|
| Long (0%-funding) | **Kalshi margin/perp** (US, MI-eligible, CFTC) | ✅ |
| Short (+funding) | **Coinbase Financial Markets — US regulated futures** (CFTC/Nodal) | ✅ |
| — | Coinbase **INTX** (international perps) | ❌ off-allowlist (not US retail) |
| — | Onchain perps (Hyperliquid/GMX/dYdX) via agentic wallet | ❌ off-allowlist (not US-legal) |

Requirement: the executor refuses any order whose venue is not in `{kalshi-perp, coinbase-futures}`. Reuse arbgrid's allowlist gate.

## 3. Architecture
```
kalshi-perp adapter ─┐                        ┌─► Supabase P&L (engine/lane/tax_bucket)
coinbase-fut adapter ┼─► strategy core ─► pre-trade gates ─► atomic 2-leg executor
                     │         ▲                                   │
   reconciliation daemon ◄─────┴───────────────────────────────────┘
        │ (every N s: positions, delta, margin, funding accrual; auto-flatten on breach)
        └─► ClaudeClaw Telegram (fills, breaches, kill-switch, daily P&L)
```
Single-writer per venue (one process owns order placement). Distinct API key per surface (perp key ≠ predictions key, per Kalshi docs).

## 4. Components & requirements

### 4.1 Adapters
- `kalshi-perp`: production margin/perp REST + WS (`external-api.kalshi.com/trade-api/v2/margin/…`); RSA-PSS signing (same as predictions key, distinct key). Order place/cancel, orderbook, positions, margin, funding.
- `coinbase-futures`: CDP/Advanced Trade **futures (CFM)** endpoints — order, positions, `cfm/balance_summary`, funding. NOT spot, NOT INTX. (The connected `coinbase` CLI reads spot only — futures needs the CFM-scoped API.)

### 4.2 Pre-trade gates (deterministic, all must pass)
1. **Allowlist** — venue ∈ {kalshi-perp, coinbase-futures}.
2. **Net-delta cap** — |net BTC delta| ≤ ε after both legs.
3. **Per-venue leverage cap** — Kalshi ≤ configured (start ≤3x), Coinbase ≤ configured (overnight ~30% margin).
4. **Edge-clears-fees** — projected funding differential over hold ≥ round-trip fees + buffer.
5. **Liquidation-buffer** — both legs ≥ X% above maintenance margin at entry.
6. **Daily-loss kill-switch** — cumulative day P&L > −$cap halts new entries.
7. **Max-position** — per-leg notional cap (start $2.5K).

### 4.3 Atomic two-leg execution
- Place both legs with **idempotent client order IDs**.
- If one fills and the other doesn't within `LEG_TIMEOUT`: either complete the missing hedge (preferred, if price still in band) or **immediately unwind the filled leg**. Never hold a one-legged (naked) position past the timeout.
- Cancel-both-legs safety on any error.

### 4.4 Reconciliation daemon
- Every `RECON_INTERVAL` s: pull positions from both venues; assert delta-neutral within ε; assert margin buffers on both legs (watch **Coinbase 4pm ET overnight-margin step-up**); verify funding accrual posting.
- On breach: auto-flatten both legs + Telegram alert.
- **Dead-man's-switch:** if the daemon stops heart-beating (no recon write in `HEARTBEAT_MAX`), flatten.

### 4.5 Funding-convention verification (critical — #1 silent-P&L risk)
- On first N round-trips, reconcile each venue's funding interval + sign against realized cash. Halt + alert on mismatch (8h vs hourly, rate vs price-denominated — the Kraken normalization quirk the logger already caught).

### 4.6 Graduation (no first-run autonomous live money)
- **Shadow:** compute + log every decision, place nothing; verify decisions vs the funding logger for ≥3 days.
- **Micro:** smallest tradeable size; verify fills, funding accrual, reconciliation, margin behavior over ≥1 week.
- **Scale:** to $2.5K/leg only after micro is clean AND the day-14 verdict passes.
- Telegram = alerts only (not approvals) once autonomous; manual leg-confirm allowed during micro.

## 5. Config / env (Infisical only)
`KALSHI_PERP_KEY_ID`, `KALSHI_PERP_PRIVATE_KEY`, `COINBASE_CFM_*`, `NET_DELTA_EPS`, `KALSHI_MAX_LEVERAGE`, `COINBASE_MAX_LEVERAGE`, `LEG_TIMEOUT`, `RECON_INTERVAL`, `HEARTBEAT_MAX`, `DAILY_LOSS_CAP`, `MAX_LEG_NOTIONAL`, `MODE` (shadow|micro|live).

## 6. Data model (Supabase)
`perp_positions`, `perp_fills`, `perp_funding_accruals`, `perp_recon_log`, and rows into the shared `pnl` table with `engine=quant`/`arbgrid`, `lane=perp_carry`, `tax_bucket=possible_1256`.

## 7. Failure modes & responses
| Failure | Response |
|---|---|
| One leg fills, other rejects | Complete hedge if in band, else unwind filled leg (≤ LEG_TIMEOUT) |
| Coinbase 4pm margin step-up → liquidation risk | Recon daemon detects buffer breach → auto-flatten |
| Funding sign/interval wrong | Funding-convention check halts before scaling |
| Daemon crash | Dead-man's-switch flattens; restart reconciles orphans first |
| Venue API outage mid-position | Flatten reachable leg; alert; manual review |
| Kalshi perp prod API not yet enabled | Executor stays in shadow; blocks live mode |

## 8. Test plan
- **Unit:** each pre-trade gate; funding math; idempotency; allowlist refusal.
- **Integration:** sandbox/paper two-leg place; forced partial-fill → unwind path; recon-breach → auto-flatten; restart-recovery.
- **Adversarial (Codex):** review for race conditions, naked-leg windows, margin-call blind spots, funding-sign errors. No self-review.

## 9. Build sequence
1. Allowlist gate wired for the two perp venues (reuse existing).
2. `kalshi-perp` + `coinbase-futures` read-only adapters (positions/orderbook/funding/margin).
3. Strategy core + pre-trade gates (pure functions, unit-tested).
4. Atomic two-leg executor + idempotency (paper first).
5. Reconciliation daemon + dead-man's-switch.
6. Funding-convention verification.
7. Shadow mode end-to-end → Codex review → micro → scale.

## 10. Kill criteria & gates (pre-registered)
- Day-14 verdict (6/26): median diff ≥3%/yr AND ≥3 episodes/wk AND depth holds → allow autonomous scale; else stay micro or kill.
- Any naked-leg event in micro → stop, post-mortem before continuing.
- Any funding-convention mismatch → halt until reconciled.

## 11. Open questions
- Kalshi **perp production API** access — verify via read-only `/margin/enabled` (blocked locally by key-file deny rule; operator to run the one-liner or grant a Bash rule).
- Coinbase CFM funding interval + sign (confirm empirically in the funding-convention check).
- §1256 tax treatment of US-DCM perps (Kalshi/Coinbase) — CPA question already logged.

## 12. Codex adversarial-review requirements for the LIVE-ORDER unit (2026-06-13)
The shadow machine is built (quant-engine `feat/perp-two-leg-executor`, 159 tests). A Codex
adversarial pass (spec §8, no self-review) found 9 issues. The 5 that bite at shadow scope are
**fixed** (`ace4c53` — non-finite-data flatten in reconcile + strategy; daemon read-failure /
flatten-failure resilience; funding-check relative-band at micro size). The remaining **4 are
hard requirements for the live-order unit** (they can only bite once an adapter actually places):

1. **Unwind must be reduce-only market/IOC, not a stale-price limit** (two_leg `_unwind_long`).
   The shadow unwind reuses the entry price; a live limit at a stale price can miss during a BTC
   move and leave the long naked. Live `submit_order` must unwind reduce-only at market (slippage-
   capped) **and verify the venue position is actually flat** before returning UNWOUND.
2. **Structural gate enforcement at the order boundary.** `TwoLegExecutor.execute()` accepts raw
   orders; today only the shadow runner runs `check_all()` upstream. A live caller could bypass all
   7 gates. Require an immutable gate-pass token / proposal at the executor (or re-run the gates
   inside it) so no order places without them.
3. **Perp-venue guard at the broker boundary (fail-closed).** `PreTradeGuard.venue` checks the
   broad global allowlist; a miswired live perp broker with `venue="coinbase-spot"`/`"kraken-
   futures"` would pass global legality though it is off the perp `{kalshi-perp, coinbase-futures}`
   allowlist. The live CFM/Kalshi `submit_order` adapters must assert `PERP_VENUES` membership
   directly, defense-in-depth with the gate.
4. **Edge gate: per-leg funding, not long-notional proxy** (perp_gates `gate_edge_clears_fees`).
   Projects funding as `long_notional * (short_rate - long_rate)`; exact only when the two leg
   notionals match. Carry both leg notionals + rates into the proposal and compute
   `short_notional*short_rate - long_notional*long_rate` once marks can diverge live.

Also: adapters should validate finiteness of every numeric field they emit (belt-and-suspenders
with the decision-boundary guards now in reconcile/strategy), and the live fill path must confirm
the PaperBroker fill-or-raise assumption against real venue partial-fill semantics (§4.3).
