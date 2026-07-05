# 08 — Earnings-Mention NO-Harvest OOS Logger

**Status:** spec / v1 build-ready (Kalshi-data-only, **no new API keys**) · **Owner:** Claude (build) + Codex (adversarial review) · **Capital:** $0 (detection/logging only)
**Related:** command-center `02-UNIFIED-OPPORTUNITY-MATRIX-v2` (R2-1, the in-sample pilot), `01-EXECUTION-TRACKER` (8/3 verdict task), `20-INTEGRATION-DECISION-AND-BUILD-PLAN` (Phase 2), `17-STRATEGY-VALIDITY-REVIEW` (R2-1 still valid 6/24). House-style template: `07-perp-executor.md`.

## 0. TL;DR
Re-run the round-2 **earnings-mention NO-harvest** pilot **out-of-sample** on newly-settled Kalshi company-KPI/earnings-mention markets, to confirm the in-sample finding: **11–50c YES contracts are ~10pts too rich at T-24h** (+10.3pts, z=2.41, 240 markets) vs ~4.6c round-trip cost. This is a **forward logger** — snapshot the YES price at T-24h→T-6h on open mention/KPI markets, join to the realized settlement, accumulate an OOS sample, compute the richness z-score. **Detection/logging only — places no orders** (deterministic statistical logger; no LLM in any path). Feeds the pre-registered **8/3 verdict**: OOS 11–50c gap **≥9pts & z≥2 → PURSUE-NOW** (passive NO ladders, ≤10% market volume, ≤$300/market — a *separate* executor build); **<4.5pts → kill**; else keep logging.
**v1 = Kalshi data only (zero new keys).** v2 layers financialdatasets.ai KPI-guidance + FMP transcript base-rates as per-event priors to sharpen *which* contracts to fade.

## 1. Thesis & edge (from R2-1)
At T-24h, company-KPI / earnings-mention YES contracts priced 11–50c settle systematically **below** their price (overpriced ~10pts) vs ~4.6c cost; institutional MM share ~5% of bid matches; median market $1–2K notional → small, **capacity-limited**, behavioral (retail over-buys "will they mention X" YES). Fade = sell YES / buy NO in the 11–50c band, T-24h→T-6h, **only in categories that test rich OOS**.

## 2. Scope & rules
- **Logger only.** No order placement in this module. Sizing constants (≤10% market volume, ≤$300/market) are defined for the *future* passive-NO-ladder executor that the 8/3 PURSUE verdict would authorize — **not built here**.
- Venue: Kalshi only (CFTC, MI-eligible, non-sports). On-allowlist.
- Deterministic; no LLM. (v2 priors are an *offline* pre-compute, never in a hot path.)

## 3. Data (v1 — Kalshi only, all already wired)
Exact methods in `kalshi_api.py` (verified 2026-06-25):
- **Identify markets:** `fetch_all_events(with_nested_markets=True)` → events carry nested `markets`. Filter by the mention/KPI family via `classify_market()` (config `SERIES_PREFIXES` + `TITLE_PATTERNS`; calibrate against the in-sample pilot market list in command-center `05-opportunity-sweep-round2/`).
- **Snapshot at T-24h→T-6h:** for each tracked open market inside `[close−24h, close−6h]`, record YES bid/ask/mid via `get_market_price(market)` (returns `(yes_price, no_price)` in $0–1; note it reads the **ask** — also pull `fetch_order_book(ticker)` for mid + depth/volume to support the ≤10%-volume rule later). Persist: `{ticker, snapshot_ts, hours_to_close, yes_price, no_price, volume, series}`.
- **Realized settlement — IMPORTANT:** `get_settlements()` hits `/portfolio/settlements` and is **ACCOUNT-scoped** (only markets we traded) → **NOT usable for OOS** over markets we don't trade. v1 adds a small **additive** `fetch_market(ticker)` (`GET /markets/{ticker}`, any status) to read `status` + `result` ("yes"/"no") for tracked tickers after close. Additive, low merge-conflict risk.
- **Join:** `richness_i = snapshot_yes_price_i − outcome_i`, `outcome_i ∈ {0,1}` (1.0 if `result=="yes"`).

## 4. OOS statistical method
- Filter to the **11–50c YES band** at the T-24h(±window) snapshot.
- `richness_i = yes_price_i − outcome_i` (positive mean ⇒ YES overpriced ⇒ fade works).
- `mean_richness` (pts), `n`, `z = mean / (std/√n)`. Bucket by category/series for the **category-conditional** read (R2-1: the edge reversed in some categories — only deploy where it tests rich).
- Rolling OOS accumulation across weekly runs → persist to Supabase (`engine='arbgrid'`, `lane='earnings_mention'`, `tax_bucket` per the 3-bucket ruleset).
- **Verdict toward 8/3:** PURSUE if `gap ≥ 9pts AND z ≥ 2 AND n ≥ 100`; KILL if `gap < 4.5pts`; else CONTINUE.

## 5. Module design
`earnings_mention.py` — pure functions + a thin `EarningsMentionLogger`:
- `classify_market(market) -> bool` — series/title match (conservative, false-negative-biased).
- `in_snapshot_window(market, now) -> bool` — within [close−24h, close−6h].
- `snapshot_open_markets(client, now) -> list[Snapshot]`.
- `resolve_settlements(client, pending) -> list[Resolved]` — uses `fetch_market`.
- `compute_oos_stats(resolved) -> OosStats` — band filter, mean richness, z, by-category.
- `verdict(stats) -> "pursue"|"kill"|"continue"` — pre-registered thresholds.
- Pure/deterministic; no order surface; structurally cannot place a trade.

`scripts/run_earnings_mention_oos.py` — weekly entry: load prior snapshots (Supabase/local), snapshot new, resolve matured, compute, persist, Telegram-ticket the running verdict. `requests`-only; secrets Infisical-injected; Telegram failures never log the tokenized URL (match `run_edgar_scan.py`/`run_pnl_digest.py`).

`.github/workflows/earnings-mention-oos.yml` — weekly cron + `workflow_dispatch` always_alert; env-routed; lean install. Matches the existing detection-cron pattern.

## 6. Tests (`tests/test_earnings_mention.py` — mocked, verifiable with NO live keys)
- `classify_market`: mention/KPI fixtures vs unrelated (BTC price, election) → True/False.
- `in_snapshot_window`: boundaries at T-24h, T-6h, outside.
- `snapshot_open_markets`: mocked `KalshiClient.fetch_all_events` (nested markets) → only in-window mention markets snapshotted.
- `resolve_settlements`: mocked `fetch_market` returns settled `result` → correct join/outcome.
- `compute_oos_stats`: fixed fixture → **exact** mean richness + z (deterministic); band filter excludes <11c/>50c.
- `verdict`: pursue/kill/continue at thresholds (incl. n<100 → continue).
- Determinism: same input → same output; no network in unit tests.

## 7. Build sequence
1. **Sync first** — local checkout is on stale `fix/layer4-continuous-wiring` (12 behind origin/master); branch `feat/earnings-mention-oos` **off origin/master** (which has edgar/fallen-angel/pnl/lip merged).
2. Add additive `fetch_market(ticker)` to `kalshi_api.py` + unit test.
3. Build `earnings_mention.py` + mocked tests → `pytest` green.
4. Add run script + workflow.
5. Codex adversarial review (no self-review).
6. **[OP]** add Supabase + Telegram secrets → activate weekly cron → accumulate OOS to 8/3.

## 8. v2 (after keys — sharpens, not required for 8/3)
financialdatasets.ai **KPI guidance** + FMP **transcript base-rates** → per-event prior `p̂` (how often the company actually mentions X / hits the KPI) → fade only where `market_yes − p̂ ≥ threshold`, instead of blanket 11–50c. Offline pre-compute; deterministic gate. Wire via the Phase-2 MCP/data layer (command-center doc 20 §C).

## 9. Pre-registered gate (do not move)
**8/3 verdict:** OOS 11–50c gap **≥9pts & z≥2 (n≥100)** → PURSUE-NOW (authorize the separate passive-NO-ladder executor, ≤10% volume, ≤$300/market). gap **<4.5pts** → kill the lane. Else continue logging. No discretionary override.
