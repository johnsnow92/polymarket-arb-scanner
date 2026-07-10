# Plan 09 — Agent-Analyst Long-Tail Pricing (dual-venue, paper-first)

**Status:** SPEC — build authorized for PAPER MODE only. **This plan authorizes no live order and
no capital.** Live execution requires: policy broker merged (PR #78) and passing, the ≥300
settled-market Brier calibration gate below, and an operator go decision through the broker.
**Authority:** Warroom W1 (two-tier execution) + W7 lane #2 (2026-07-07, out-of-repo command
center `24-STRATEGY-WARROOM-2026-07-07.md` §3.1 and §6) — LLM fleet as cheap analysts over the
thin long tail, dual-venue (Kalshi + Polymarket books), paper-first, mid-curve sizing doctrine.
**Queue position:** W7 priority queue item #3 (spec) / lane #2 (build).
**Written:** 2026-07-10.

---

## 1. Thesis and constraints

Analyst attention is the scarce input in thin, long-cycle markets: nobody reads the resolution
rules and base rates of a $400-volume market. An LLM that does — carefully, with calibrated
confidence — can price the long tail cheaply. Evidence anchors (warroom KB): bots win only in
judgment-free niches while losing long-cycle judgment markets; losing retail orders concentrate
at price extremes; the top 0.1% of wallets order **mid-curve** — adopted here as sizing doctrine.
Countervailing evidence: pure LLM direction-picking ≈ coin flip in liquid markets (Alpha Arena).
Both are honored by the same design choice: **the model prices only thin/long-tail markets, from
documents (resolution rules, base rates), never momentum/direction in liquid books.**

Hard constraints:
- **Paper-first.** Every stage below runs paper until the calibration gate passes. No live-order
  code path is built in this plan; live placement happens only as broker INTENTs in a follow-on
  plan after the gate.
- **Two-tier rule (W1):** judgment loops may eventually be agent-executed only inside
  policy-broker caps; fast loops stay deterministic. Nothing here touches the fast loops.
- **Sports excluded** on both venues (MI posture; plan 08 §1 veto applies).
- **Polymarket leg inherits plan 08's gates** — shadow-only until the W5 authority contradiction
  (US close-only per official geoblock docs, flagged 7/9) is operator-resolved; the Kalshi leg is
  independent and proceeds regardless.
- **LLM cost ceiling:** per-market pricing budget capped (config, default ≤$0.05/market-read) and
  a daily token budget; exceeding it halts the scanner, not the wallet.

## 2. Pipeline (five stages, all paper)

### Stage A — Long-tail scanner
- Enumerate all open markets on Kalshi (later: every CFTC venue via the venue-watcher) and
  Polymarket (read-only Gamma).
- Long-tail filter: volume/liquidity below configurable thresholds, ≥N days to resolution,
  **category is known** (normalized from market metadata; missing/unknown → excluded, same
  fail-closed rule as plan 08's veto), category ∉ {sports}, not on the existing arb paths. Output: candidate list with market
  metadata, resolution-rule text, current book.
- Reuses: `kalshi_api.py`/`polymarket_api.py` read paths, `snapshot.py` recorder.

### Stage B — LLM analyst read
- Per candidate: one structured read producing `{fair_value, confidence, base_rate_source,
  resolution_risk_notes, abstain}` from (a) full resolution-rule text, (b) base rates the model
  must cite (historical frequency, reference-class data), (c) optional news snippets (existing
  Firecrawl source, default off, same flag discipline as STRAT-02).
- **Abstain is a first-class output** — ambiguous rules, missing base rates, or model-declared
  low confidence produce no paper trade, logged with reason. Unparseable/failed reads abstain
  (fail-closed), never default-price.
- Model calls go through a thin `llm_client.py` (retry, cost metering, response-schema
  validation). Prompt + response persisted per read for post-hoc audit.

### Stage C — Calibration store
- Append-only SQLite (later Supabase-synced, additive migration only). **Grain: one row per
  READ**, keyed by a unique `read_id` (market_id + read timestamp); re-reads append new rows,
  never update. Each row: model fair value, confidence, market mid at read time, spread,
  category, venue, timestamps; joined to the venue's own settlement result on resolution
  (authoritative-settlement pattern from the exit-liquidity work, PR #46). **Metric identity:**
  G2 Brier/calibration use exactly one prediction per market — the final read before the
  market's trading close; earlier reads are retained for drift analysis only.
- Metrics computed from settled rows only: **Brier score** (overall + per category + per
  confidence bucket), calibration curve, edge realization (did |model − market| gaps close in the
  model's favor).

### Stage D — Paper execution (mid-curve doctrine)
- Signal: |fair_value − market price| clears the category fee model + slippage buffer by a
  configurable threshold.
- **Mid-curve only:** paper orders restricted to prices in [0.15, 0.85]; no extreme-price
  longshot/near-certainty orders regardless of model output (that is where losing retail
  concentrates and where fee/variance asymmetry bites).
- Paper fills use plan 08's deterministic conservative-taker shadow-fill classifier verbatim
  (FILLED/PARTIAL/UNFILLED, runtime `PRICE_CACHE_EVICTION_AGE` staleness) — one classifier,
  two consumers, no drift.
- Position/sizing discipline recorded but not capital-bound (paper): Kelly fraction computed
  from `fair_value` (as the calibrated probability), the paper execution price, the venue's
  payoff terms, and modeled fees — never from the confidence score alone; confidence only
  scales the per-market cap downward. Caps per market and per category are config constants,
  ratified before live.

### Stage E — Verdict reporting
- Weekly digest section (existing `notifier.py`/P&L-digest pattern): settled count, Brier by
  bucket, paper P&L after modeled fees, abstain rate, cost per read.
- **Earnings-mention is the first production category:** the existing earnings-mention OOS
  pipeline (branch `feat/earnings-mention-oos`, 8/3 verdict) becomes Stage B's first
  specialized reader; its verdict date is unchanged and its pipeline is not modified by this
  plan — the agent-analyst store INGESTS its settled paper trades as seed calibration data
  where schemas align, clearly tagged by source. **Seed rows do NOT count toward G2's
  300-settled-market requirement or its P&L/Brier computation** — they are reported separately
  unless and until they demonstrably meet the same provenance, timestamp, settlement-join, and
  leakage controls, at which point their inclusion is an explicit [OP] decision.

## 3. Gates (pre-registered, in order)

| Gate | Criterion | On pass | On fail |
|---|---|---|---|
| G1 — pipeline health | 2 weeks paper, ≥200 markets read, error rate ≤10% of reads, valid (non-abstain, non-error) reads ≥30%, abstain rate reported separately, zero crashes (thresholds are proposals subject to operator ratification) | continue | fix or halt lane |
| G2 — calibration | **≥300 settled paper markets** (seed rows excluded) AND Brier ≤ market-implied baseline (Brier of "price = probability", computed on the SAME settled cohort) AND positive paper P&L after modeled fees on **≥50 settled traded positions spanning ≥3 categories with no category >50% of the traded sample** (thresholds subject to operator ratification) | request [OP] live decision via broker | REFINE (per-category breakdown) or KILL; no live |
| G3 — live (out of scope here) | operator go + broker merged + caps configured | separate plan | — |

No gate may be evaluated on unsettled markets; partial-window peeks are reported as
"provisional" and never trigger a stage change.

## 4. Explicitly out of scope
- Any live order, any capital, any broker-cap definition — G3 is a separate operator-approved plan.
- Polymarket live-side anything (plan 08 owns that lane's gates).
- Model fine-tuning/training; this is prompt+retrieval only.
- Liquid-market direction-taking (excluded by thesis and by the long-tail filter).

## 5. Test plan
- Scanner: long-tail filter boundaries (volume/day thresholds, sports/unknown-category exclusion),
  venue-read fixtures.
- LLM client: schema-validation rejects malformed output → abstain; cost-meter halt; retry caps.
- Calibration store: append-only enforcement, settlement join against authoritative results,
  Brier/bucket math on fixture sets with known answers.
- Paper executor: mid-curve bounds (0.15/0.85 edges), fee-threshold signal math per category,
  shadow-fill classifier reuse (no forked logic — import, don't copy).
- Digest: verdict math (G1/G2 criteria) on synthetic settled sets, provisional-vs-final labeling.
- Regression: full suite ×2 fixed-seed per push.

## 6. Risks
- **Calibration-by-luck:** 300 settled markets across few categories can pass G2 on category
  concentration; G2 reports per-category Brier and the [OP] decision sees the breakdown, not
  just the aggregate.
- **LLM data leakage/staleness:** the model may "know" outcomes for markets resolving on public
  schedules; reads record the model's knowledge-cutoff and flag markets whose resolution window
  predates it — those rows are excluded from G2.
- **Cost runaway:** long-tail enumeration × LLM reads is unbounded by default — the daily token
  budget halt is load-bearing, tested, and alerts on trip.
- **Judgment-loop scope creep:** any attempt to point Stage B at liquid markets or extremes is a
  thesis violation — the mid-curve and long-tail filters are code gates with tests, not guidance.
