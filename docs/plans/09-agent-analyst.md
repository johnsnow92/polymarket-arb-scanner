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
- **Plan 08 dependency (build-order prerequisite):** Stage D imports plan 08's shadow-fill
  classifier, which exists today only as a spec (PR #82) — no importable code. Stages A-C may be
  built and run immediately; **Stage D is blocked until plan 08's shadow-fill classifier (defined in plan 08
  Phase B) is merged to master** as a concrete module (target: `shadow_fill.py::classify_fill(order, book_snapshot,
  eviction_age)`; plan 09 v1 uses only its FOK contract, which returns FILLED|UNFILLED —
  PARTIAL exists only for GTC and is out of plan-09 scope; exact module/symbol pinned in the
  Stage-D build PR to the merged commit). If plan 08 stalls, Stage D ships its own classifier implementing the
  identical plan-08 semantics and plan 08 later adopts it — one implementation either way.
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
- Access is through a **read-only transport**: a scanner-owned client that talks only to
  public read endpoints and is constructed WITHOUT credentials — not the signing-capable
  `KalshiClient`/CLOB classes (which expose `place_order()`); the structural paper-only test
  asserts this at the import/constructor level.
- **Completeness contract (reader-level, not wrapper-level):** the current readers return
  accumulated pages even after request/page-budget failures and discard the error state, so an
  outer wrapper cannot detect truncation. The read-only transport's enumeration RAISES or
  returns explicit completeness metadata on any failed/aborted pagination; an incomplete
  enumeration fails that scan cycle closed (no cohort additions, cycle logged as failed).
- Reuses `snapshot.py` recorder for book snapshots.

### Stage B — LLM analyst read
- Per candidate: one structured read producing a **tagged union**: a `VALID` read carries
  `{fair_value ∈ [0,1], confidence ∈ [0,1], base_rate_source, resolution_risk_notes}` (non-finite
  or out-of-range values fail schema validation → ERROR); an `ABSTAIN` carries only
  `{abstain_reason}`; `ERROR` is client-generated, never model-declared. Inputs per read: (a) full resolution-rule text, (b) base rates the model
  must cite (historical frequency, reference-class data), (c) optional news snippets — reusing the existing Finnhub client
  (STRAT-02's primary source; Firecrawl is a possible later addition, default off, same flag
  discipline as STRAT-02).
- **Read status taxonomy (mutually exclusive, one per read):** `VALID` (schema-conforming
  priced read), `ABSTAIN` (model-declared: ambiguous rules, missing base rates, low
  confidence — logged with `abstain_reason`), `ERROR` (transport/schema/validation failure).
  G1's error rate = ERROR/total; valid-read rate = VALID/total; abstain rate = ABSTAIN/total.
  ABSTAIN and ERROR never price and never trade (fail-closed).
- Model calls go through a thin `llm_client.py` (retry, cost metering, response-schema
  validation). Prompt + response persisted per read for post-hoc audit.

### Stage C — Calibration store
- Append-only SQLite (later Supabase-synced, additive migration only). **Grain: one row per
  READ**, keyed by a unique `read_id` (venue + market_id + read timestamp); re-reads append new rows,
  never update. Each row: model fair value, confidence, market mid at read time, spread,
  category, venue, timestamps; joined on resolution to a **public per-venue settlement
  adapter** — Kalshi: the public market `result` field; Polymarket: the public
  resolution/oracle outcome (UMA-disputed markets are held un-scored until finality). Paper
  positions never appear in account-scoped endpoints (PR #46's portfolio reconciliation is for
  real fills and is NOT reused here). Outcome encoding: YES=1/NO=0 for the canonical
  instrument; voided/cancelled markets are excluded from scoring; post-settlement corrections
  reopen the row via an appended correction record, never an update. **Leakage fields are
  Stage C's responsibility:** each read row stores the market's earliest
  outcome-determining public-information time (with provenance) when determinable, the
  model's knowledge-cutoff date, and per-source snapshot timestamps for every news/document
  input — the G2 leakage exclusions are computed from these stored fields, never ad hoc. **Metric identity:**
  G2 Brier/calibration use exactly one prediction per market — the final VALID read before the
  market's trading close; earlier reads are retained for drift analysis only. **Baseline
  time-lock:** the market-implied baseline uses the market mid stored in that SAME selected
  final read, with the same outcome encoding and settlement timestamp — never a re-fetched or
  different-snapshot price, so the gate is reproducible from the store alone.
- Metrics computed from settled rows only: **Brier score** (overall + per category + per
  confidence bucket), calibration curve, edge realization (did |model − market| gaps close in the
  model's favor).

### Stage D — Paper execution (mid-curve doctrine)
- **v1 is binary markets only.** Canonical instrument identity is `{venue, market_id,
  outcome=YES}`; `fair_value` is always P(YES) for that instrument; multi-outcome events enter
  only as their binary legs. Cross-venue equivalents remain distinct instruments (deduplicated
  only in G2 scoring).
- **Decision-time snapshot:** after the LLM read completes, Stage D takes a FRESH book
  snapshot (the decision-time snapshot); signaling, pricing, and fill classification all use
  this snapshot — never the pre-inference Stage-A book, so liquidity that vanished during model
  latency can never count as filled.
- Signal: edge is computed against the **executable price** from the decision-time snapshot —
  the best resting ask on the side being bought (best YES ask for BUY YES; best NO ask for BUY
  NO) — not the mid: `edge = fair_value_of_bought_side − executable_price`, and it must clear
  the category fee model + slippage buffer by a configurable threshold. **Signed**: fair_value
  above the YES ask → BUY YES; (1 − fair_value) above the NO ask → BUY NO.
- **Paper intent schema (deterministic):** `{read_id, venue, market_id, side, limit_price =
  executable price rounded toward the less aggressive tick (down for buys), order_type = FOK,
  quantity = floor_to_venue_lot(min(kelly_fraction, confidence × per_market_cap_fraction) ×
  virtual_bankroll / limit_price)}` where `kelly_fraction` is computed from the bought side's
  probability, the limit price, the venue payoff, and modeled fees. This is the ONLY sizing
  formula — confidence enters solely through the cap term. The virtual bankroll is static and
  non-compounding (config, default $10,000).
  State machine (FOK ⇒ no partials): SIGNALED → FILLED | UNFILLED → SETTLED | VOID. Every
  position links to exactly one originating read_id. **Position ownership:** the FIRST
  Stage-D-eligible signal per instrument creates the sole position; all later signals on that
  instrument (repeat or opposing) are logged and ignored.
- **Mid-curve only:** paper orders restricted to prices in [0.15, 0.85]; no extreme-price
  longshot/near-certainty orders regardless of model output (that is where losing retail
  concentrates and where fee/variance asymmetry bites).
- Paper fills use plan 08's deterministic conservative-taker shadow-fill classifier verbatim
  (FOK contract: FILLED/UNFILLED; runtime `PRICE_CACHE_EVICTION_AGE` staleness, applied to the
  decision-time snapshot) — one classifier, two consumers, no drift.
- Position/sizing discipline recorded but not capital-bound (paper): Kelly fraction computed
  from `fair_value` (as the calibrated probability), the paper execution price, the venue's
  payoff terms, and modeled fees — never from the confidence score alone; confidence only
  scales the per-market cap downward. Caps per market and per category are config constants,
  ratified before live.

### Stage E — Verdict reporting
- Weekly digest section (existing `notifier.py`/P&L-digest pattern): settled count, Brier by
  bucket, paper P&L after modeled fees, abstain rate, cost per read.
- **Earnings-mention relationship:** the existing earnings-mention OOS pipeline (branch
  `feat/earnings-mention-oos`, 8/3 verdict) is a deterministic post-settlement Kalshi logger —
  no LLM, no paper trades. It is NOT a Stage B reader. Its historical `{ticker, price, outcome,
  series}` observations enter the store only through a dedicated seed adapter with an explicit
  field mapping, tagged `source=earnings-mention-seed`, reported separately. Earnings-mention
  becomes the first *category focus* for Stage B's own reads (new reads, made by this
  pipeline); the OOS pipeline and its verdict date are untouched. **Seed rows do NOT count toward G2's
  300-settled-market requirement or its P&L/Brier computation** — they are reported separately
  unless and until they demonstrably meet the same provenance, timestamp, settlement-join, and
  leakage controls, at which point their inclusion is an explicit [OP] decision.

## 3. Gates (pre-registered, in order)

**Pre-registration protocol (before the first qualifying observation):** the operator ratifies
and the repo hashes a frozen pipeline definition — model ID + knowledge cutoff, prompt
template, retrieval/news configuration, scan cadence, long-tail filter thresholds, fee model
version, settlement adapters, shadow-fill classifier version, sizing constants, and all gate
thresholds below. Any material change to any of these **resets the evaluation cohort**. An
**inception cohort** is then frozen: every unique eligible market entering the scanner under
the frozen config joins the cohort at first sight, before its outcome is knowable; G2's "300
settled markets" means 300 settled *cohort* members with a scoreable final VALID read — never
successes selected after filtering. G1's coverage/error limits continue to bind throughout the
G2 window (selective abstention that drops valid-read coverage below the G1 floor invalidates
the window). A terminal evaluation date (or cohort-size cap) is declared at ratification;
there is no open-ended peek-until-pass. **Statistical procedures are part of the frozen
definition:** event clustering (cluster = markets resolving on the same underlying real-world
event, matched by the `market_discovery.py` equivalence machinery plus a manually-curated
mapping frozen at ratification; representative = the cluster member with the largest paper
exposure, ties broken by earliest cohort entry), the Brier test (one-sided t-test on
per-cluster paired Brier differences; pass requires the lower bound above δ), and the P&L
bound (cluster bootstrap of standardized return: resample event clusters with replacement,
equal cluster weight, 10,000 resamples, fixed seed, percentile method for the one-sided 95%
lower bound) are all named and hashed at ratification — no post-hoc method choice. **Abstention coverage binds through G2:** the
abstain-rate cap and a minimum cohort coverage floor (settled-and-scored cohort members ÷
settled cohort members ≥ pre-registered floor) are gate conditions; falling below the floor
invalidates the window rather than shrinking the denominator.

| Gate | Criterion | On pass | On fail |
|---|---|---|---|
| G1 — pipeline health | 2 weeks of scheduled runs (cadence fixed at ratification, e.g. every 6h) with ≥95% run-completion uptime, ≥200 **unique markets** attempted (predetermined attempts, not reads — re-reads don't add), error rate ≤10% of attempts, valid-read rate ≥30% of attempts, abstain rate reported separately, zero crashes (crash = unhandled exception terminating a scheduled run) — thresholds fixed at ratification | continue | fix or halt lane |
| G2 — calibration | **≥300 settled inception-cohort markets** with scoreable final VALID reads (seed rows excluded; dual-venue duplicates and related contracts deduplicated to one representative per event cluster) AND **paired Brier superiority**: the one-sided 95% LOWER confidence bound of the mean per-cluster (Brier_market − Brier_model) difference is above the pre-registered margin δ, using event-clustered inference — not a bare ≤ comparison, which a market-copying model passes AND paper P&L: **≥50 settled INDEPENDENT EVENT CLUSTERS with traded positions, spanning ≥3 categories, no category >50% of settled traded clusters**, with **standardized return (net P&L ÷ total exposure, where exposure = Σ limit_price × quantity over FILLED positions) whose one-sided 95% lower confidence bound is above 0** — cohort is non-cherry-pickable: EVERY Stage-D-eligible signal under the frozen config enters (no manual selection), one position per instrument owned by the FIRST eligible signal (later repeat/opposing signals are logged, never add or offset), UNFILLED positions are recorded and excluded from P&L but reported | request [OP] live decision via broker | REFINE (per-category breakdown) or KILL; no live |
| G3 — live (out of scope here) | operator go + broker merged AND its reconciliation/tests passing + caps configured | separate plan | — |

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
- LLM client: schema-validation rejects malformed output → ERROR status (never priced, never ABSTAIN); cost-meter halt; retry caps.
- Calibration store: append-only enforcement, settlement join against authoritative results,
  Brier/bucket math on fixture sets with known answers.
- Paper executor: mid-curve bounds (0.15/0.85 edges), fee-threshold signal math per category,
  shadow-fill classifier reuse (no forked logic — import, don't copy).
- Digest: verdict math (G1/G2 criteria) on synthetic settled sets, provisional-vs-final labeling.
- Gate math: clustered paired t-test, cluster bootstrap (fixed seed reproducibility, percentile
  bound), exposure and category-concentration denominators, event-cluster counting — all on
  fixture sets with hand-computed answers.
- Intent determinism: identical inputs → byte-identical paper intents (executable-price
  selection, tick rounding, sizing formula incl. the confidence-capped Kelly min()).
- Leakage: outcome-info-time/provenance and per-source snapshot timestamps persisted per read;
  G2 exclusion predicates computed from stored fields on fixture sets (undeterminable event
  time → excluded; post-cutoff and post-outcome reads → excluded).
- **Structural paper-only negative tests:** the pipeline's modules cannot construct a signing/
  writer client and cannot reach order-placement endpoints (import-graph assertion + a test that
  the paper executor raises if handed a live client).
- Regression: full suite ×2 fixed-seed per push.

## 6. Risks
- **Calibration-by-luck:** 300 settled markets across few categories can pass G2 on category
  concentration; G2 reports per-category Brier and the [OP] decision sees the breakdown, not
  just the aggregate.
- **LLM data leakage:** two independent fail-closed controls. (1) *Training-data leakage:* G2
  excludes any market whose earliest outcome-determining public-information time is ≤ the
  model's recorded knowledge-cutoff date; **if that event time cannot be determined, the market
  is excluded** (no trading-close fallback — outcomes can be public before close). (2)
  *Read-time leakage:* a read (and every news/source snapshot it consumes) must strictly
  precede the market's earliest outcome-determining public-information time; reads that cannot
  establish this are excluded from G2 scoring.
- **Cost runaway:** long-tail enumeration × LLM reads is unbounded by default — the daily token
  budget halt is load-bearing, tested, and alerts on trip.
- **Judgment-loop scope creep:** any attempt to point Stage B at liquid markets or extremes is a
  thesis violation — the mid-curve and long-tail filters are code gates with tests, not guidance.
