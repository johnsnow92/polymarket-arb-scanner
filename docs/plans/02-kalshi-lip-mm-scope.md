# Kalshi LIP Market-Making — Implementation Scope

**Date:** 2026-06-11 · **Author:** scoping pass (read-only audit) · **Strategy source:** arbgrid-strategy-analysis-v4 (2026-06-10)
**Goal:** quote 3-5 Kalshi non-sports LIP markets to farm Liquidity Incentive Program rewards — first as paper-quoting with measured hypothetical reward capture, then tiny-size live.
**Clock:** LIP expires **Sep 1, 2026** (~11-12 weeks of runway from today). Build speed matters more than generality.

---

## 1. Code-Readiness Audit

Files read: `market_maker.py`, `scans/rewards.py`, `continuous.py`, `config.py`, `kalshi_api.py`, `executor.py`, `scans/toxic_flow_pause.py`, `scans/volatility_adjusted_mm.py`, `db.py` (reward tables).

### Verdict: **NOT READY — the MM machinery is a Polymarket-oriented dry-run skeleton. For the Kalshi LIP use case, the quote loop never touches Kalshi, no live order path exists, no LIP model exists, and the fill-feedback loop is unwired.**

### (a) Does QuoteEngine/MarketMaker quote on Kalshi?

**No.** Two independent blockers:

1. **Market registration is Polymarket-only.** `continuous.py` (~line 1690) registers MM markets exclusively from `poly_markets[:20]` via `_market_maker.add_market(cid, "polymarket", price)`. No code path ever calls `add_market(..., "kalshi", ...)`.
2. **QuoteManager has no live order path for ANY platform.** `QuoteManager.place_quote()` (`market_maker.py:209-252`): when `trader is None` it fabricates a `dry_...` order ID and tracks it in memory; when a trader IS passed it logs `"MM quote: ..."` and **returns `None`** — the comment reads *"Live order placement would go here"*. The MM engine cannot place a real order on any platform today.

The separate `KalshiRewards` opportunity path (scan → executor) nominally can place Kalshi orders, but it is broken (see (e) bugs below) and architecturally wrong for resting quotes (arb-style fill-confirm + cancel semantics).

### (b) Does anything model LIP qualification?

**No.** Specifics:

- `KalshiRewardTracker` (`market_maker.py:924-1037`) docstring claims *"Kalshi has no public reward API"* — **stale/false**. Kalshi exposes `GET /trade-api/v2/incentive_programs` (status/type filters, per-market `market_ticker`, `period_reward` in centi-cents, `start_date`/`end_date`, `discount_factor_bps`, `target_size_fp`). The codebase never calls it.
- `estimate_daily_reward()` is an invented heuristic (`(resting_time/86400) × (1 - spread×100) × $0.50`) with no relation to the real formula. The real LIP scoring is: **once-per-second random order-book snapshots; per-order score = size × distance multiplier (1.0x at best bid/ask, discounted per the program's Discount Factor further away); payout = (your score ÷ all participants' scores) × pool**, pro-rata, daily pools $10-$1,000 per market, $1 minimum payout.
- No max-spread band, no min-size, no both-sides/below-10c rule, no proximity-to-mid scoring anywhere. `_calculate_optimal_quotes()` in `scans/rewards.py` targets "60% of max_incentive_spread" — that is **Polymarket's** reward metadata schema (`min_incentive_size`, `max_incentive_spread`, `pool_size_usdc` from Gamma); the Kalshi side uses a hardcoded "3% of mid" spread with no qualification logic.

### (c) Does the rewards scanner select markets by reward pool size?

**Polymarket: yes** (`pool_size_usdc >= min_pool_usdc`). **Kalshi: no.** `scan_kalshi_rewards()` (`scans/rewards.py:275-362`) filters only on `volume_24h >= $1000` and a sane `last_price`. No pool size (it never queries `/incentive_programs`), no competition estimate, no category/non-sports filter, no time-to-resolution filter.

**Likely critical unit bug:** the scan rejects markets where `last_price <= 0 or last_price >= 1`. Kalshi's `last_price` is an **integer in cents** (the codebase's own `get_market_price()` divides cent fields by 100). Any market with last_price ≥ 1¢ — i.e., all of them — is rejected, so `scan_kalshi_rewards` almost certainly emits **zero opportunities in production**. Must verify against a live payload (`last_price_dollars` may exist on newer responses), but as written this scan is dead code in effect.

### (d) Cancel/replace path on Kalshi — exists? rate-limit-aware?

**Partial.**
- `KalshiClient.cancel_order()` exists (`DELETE /portfolio/orders/{id}`); `place_order()` supports `time_in_force="gtc"`. There is **no amend/replace** call and no batched cancel — replace = cancel + place (2 requests).
- Rate limiting: client-level `_rate_limit()` enforces `KALSHI_RATE_LIMIT=0.05s` min inter-request gap (~20 req/s ceiling) shared across ALL Kalshi calls (scans, orderbooks, orders), plus 429-triggered tenacity retry and a circuit breaker (3 failures → 30s open). This is a global throttle, **not** an order-action budget — there is no awareness of Kalshi's per-tier transaction limits, and a quote-refresh storm across 5 markets × 2 sides (20 cancel+place actions per refresh) would compete with scan traffic on the same throttle. Acceptable for 3-5 markets at a 10-30s refresh cadence, but needs an explicit per-cycle order-action budget and jitter before live.
- **Architectural mismatch:** the only live Kalshi order path is `executor._execute_leg()`, which is built for arbs: it polls for fill and **cancels GTC orders after `GTC_ORDER_TIMEOUT` (30s)**. LIP farming requires orders resting for minutes-to-hours. The MM loop must own its own place/rest/cancel-on-reprice lifecycle via `KalshiClient` directly — it must NOT go through `executor.execute()`.

### (e) How does dry-run MM behave — can we paper-quote and measure hypothetical LIP score?

**Paper-quote: yes (mechanically). Measure LIP score: no.**
- Dry-run `place_quote` tracks fake orders in `QuoteManager._active_orders` with price/size/placed_at — sufficient raw material for paper quoting.
- `db.reward_metrics` table + `db.log_reward_metric()` exist and persist `placed`/`cancelled` events with size, spread, resting_seconds — but **`KalshiRewardTracker.log_order_placed/cancelled` is never called from anywhere** (grep confirms zero call sites). The reward ledger is plumbing without a faucet.
- No per-second snapshot scoring simulation exists, so "hypothetical LIP capture" cannot be measured today.
- **Fill-feedback loop is entirely unwired:** `MarketMaker.on_fill()` has zero callers — no WS user-fills channel subscription, no order-status poller. Consequences: inventory never updates from real fills, `ToxicFlowDetector.record_fill()` is never fed (toxicity is permanently 0.0), and `MM_AUTO_HEDGE_ENABLED` can never fire. In dry-run this also means paper quotes never "fill", so paper P&L/adverse-selection can't be simulated without a fill simulator.
- **Latent bug:** `continuous.py:671` calls `get_lead_lag_mm().record_price(...)` but `LeadLagMM` only defines `record_update(...)` — AttributeError silently swallowed by the surrounding try/except. LeadLagMM has never received a data point.
- **Executor key-mismatch bug:** the `KalshiRewards` branch in `executor._build_legs()` reads `opportunity.get("market_ticker")`, but `scan_kalshi_rewards` emits the key as `ticker`. Legs are always `[]`; KalshiRewards opportunities can never execute even if everything else worked. (Moot if the MM loop bypasses the executor, but symptomatic of this path never having been run end-to-end.)

### What works today (genuinely reusable)

| Component | Status |
|---|---|
| `QuoteEngine.calculate_quotes` (spread, inventory skew, clamps) | Solid; needs LIP-aware spread targeting layered on |
| `InventoryTracker` (per-market/total caps, needs_hedge) | Solid as-is |
| `QuoteManager` dry-run bookkeeping | Reusable as the paper-quote ledger |
| `VolatilityTracker` + WS price feed wiring (`MM_VOLATILITY_ADJUSTED_ENABLED`) | Wired into `calculate_quotes` hot path; receives real WS ticks |
| `ToxicFlowDetector.should_pause` hook in `refresh_quotes` | Wired into hot path, but starved of fill data |
| `KalshiClient` auth, GTC orders, cancel, orderbook parsing (`parse_orderbook`/`best_*`), circuit breaker | Production-quality |
| `db.reward_metrics` schema + `log_reward_metric` | Ready; needs call sites |
| WS feed → `MarketMaker.update_price` (Kalshi tickers flow if registered) | Wired; works once Kalshi markets are registered |

---

## 2. Market Selection

### What exists
Nothing usable: `scan_kalshi_rewards` selects by 24h volume only (and is likely zero-yield due to the cent/dollar bug). No pool data, no category filter, no competition proxy, no duration filter.

### What the bot can poll
**`GET https://api.elections.kalshi.com/trade-api/v2/incentive_programs?status=active&type=liquidity`** — paginated; per-program fields include `market_ticker`, `incentive_type`, `period_reward` (centi-cents — divide by 10,000 for dollars), `start_date`/`end_date`, `discount_factor_bps`, `target_size_fp`. This is the per-market pool list behind kalshi.com/incentives. `KalshiClient` needs one new method (`fetch_incentive_programs`) — trivial, same auth/throttle stack.

### Selection logic needed (new module: `scans/lip_select.py` or extend `scans/rewards.py`)

Score = `daily_pool_dollars / competition_proxy`, filtered by:

1. **Non-sports:** join `market_ticker` → event via `fetch_all_events(with_nested_markets=True)`; keep `category` ∉ {Sports, ...} (politics/economics/finance/entertainment ≈ 94% of pool per strategy report).
2. **Competition proxy:** from `fetch_order_book` — total resting size within the discount-factor band around mid. Crowded books (deep size at best bid/ask) dilute pro-rata share; prefer pools with thin competitive depth.
3. **Adequate duration:** program `end_date` ≥ N days out AND market `close_time` ≥ N days out (avoid quoting into resolution).
4. **Price band:** mid in roughly [0.10, 0.90] — tails have binary gap risk disproportionate to reward.
5. **Low news velocity:** reuse `VolatilityTracker.get_volatility()` over a trailing window as the proxy; exclude markets above threshold. (Optionally cross-check `event_monitor` divergence signals later — not required for v1.)
6. Output: top 3-5 tickers, re-evaluated every `LIP_SELECT_INTERVAL` (e.g., hourly), with hysteresis so the maker doesn't churn markets.

---

## 3. Risk Controls Checklist

| Documented MM risk | Existing control | Wired into Kalshi MM hot path? |
|---|---|---|
| **Adverse selection on news (informed flow picking off quotes)** | `ToxicFlowDetector` (`MM_TOXIC_FLOW_ENABLED`, threshold 0.60, 60s pause) — `should_pause()` IS checked inside `MarketMaker.refresh_quotes` | **Hook wired, detector starved.** `record_fill()` has zero callers, so toxicity is always 0.0 and the pause can never trigger. Needs the fill-feedback loop (Task 6). |
| **Quote staleness during fast moves** | `VolatilityTracker` (`MM_VOLATILITY_ADJUSTED_ENABLED`) widens half-spread inside `QuoteEngine.calculate_quotes`; WS ticks feed it via `_feed_sprint3_trackers` | **Yes — fully wired**, for any market the WS subscribes to. Caveat: Kalshi LIP markets must be in the WS subscription set (`WS_SUBSCRIPTION_LIMIT`); also no max-quote-age kill switch — if the WS feed dies, quotes rest at stale prices indefinitely. Add a staleness cancel (Task 7). |
| **Inventory accumulation** | `InventoryTracker` caps (`MM_MAX_INVENTORY` $500/market, `MM_MAX_TOTAL_EXPOSURE` $500) — checked in `refresh_quotes` via `can_trade` | **Yes — wired**, but inventory only updates via `on_fill`, which is never called. With no fill loop the caps are checked against a perpetual zero. Real after Task 6. |
| **Inventory carried into resolution (binary gap risk)** | None. `hedger.hedge_inventory` (behind `MM_AUTO_HEDGE_ENABLED`) sells back excess at best bid — but it is fill-triggered, not time-triggered, and `on_fill` never fires. No close-time awareness in the MM at all. | **No.** Needs: (1) Task 6 to make auto-hedge live, (2) a new pre-resolution flatten rule — cancel quotes and flatten inventory T-hours before `close_time` (Task 7). |
| **Order rate limits (Kalshi)** | Global `_rate_limit()` (0.05s gap), 429 retry, circuit breaker in `KalshiClient` | **Partially.** It throttles, but it isn't an order-action budget and is shared with scan traffic. The MM refresh loop needs: only cancel/replace when reprice exceeds a tick threshold (skip-if-unchanged), per-cycle action cap, jitter (Task 4). |
| **Crossing own book / self-match** | None | **No.** Live quoting must check book before posting (Kalshi may reject or, worse, self-match). Cheap guard in the quote loop (Task 5). |

Modules that exist only as observability scans, not controls: `scans/toxic_flow_pause.py` and `scans/volatility_adjusted_mm.py` emit zero-cost report-only opp dicts (executor hard-blocks them with `defensive_observability`) — they are dashboards over the singletons, fine as-is.

---

## 4. Gap List → Build Sequence

All MM-loop work bypasses `executor.execute()` — the MM owns its order lifecycle via `KalshiClient` directly. New feature flag: `KALSHI_LIP_ENABLED` (default false), per project convention.

### Phase A — Paper-quoting with measured hypothetical LIP capture

| # | Size | Task | File / function / change |
|---|---|---|---|
| 1 | **S** | **Incentives API client.** Add `KalshiClient.fetch_incentive_programs(status="active", type="liquidity")` with cursor pagination; normalize `period_reward` centi-cents → dollars. | `kalshi_api.py` (new method, ~40 lines) + test |
| 2 | **M** | **LIP market selector.** New `scans/lip_select.py`: poll incentives, join to events for `category` (non-sports filter), fetch orderbooks for competition-depth proxy, apply duration/price-band/volatility filters, emit ranked top-N tickers with pool size. Fix or retire the dead `scan_kalshi_rewards` (cent/dollar `last_price` bug) — recommend retiring its Kalshi half in favor of this module. Config: `LIP_MIN_POOL`, `LIP_MAX_MARKETS=5`, `LIP_SELECT_INTERVAL`, `LIP_EXCLUDED_CATEGORIES`. | new `scans/lip_select.py`, `config.py`, tests |
| 3 | **M** | **LIP quote policy.** New `LIPQuotePolicy` in `market_maker.py` (or `lip_policy.py`): given mid, discount_factor_bps, and book, compute the bid/ask that maximizes `size × distance_multiplier` per dollar at risk (quote at/near best bid+ask, both sides, size = `LIP_QUOTE_SIZE`). Layer onto `QuoteEngine.calculate_quotes` output (keep inventory skew + vol widening). Encode the per-second scoring model: `score(order) = size × multiplier(distance, discount_factor)`. | `market_maker.py` + tests |
| 4 | **M** | **Kalshi MM registration + refresh loop.** In `continuous.py`: when `KALSHI_LIP_ENABLED`, register selector output via `_market_maker.add_market(ticker, "kalshi", mid, ticker=ticker)`; ensure those tickers enter the WS subscription set; refresh on `MM_REFRESH_INTERVAL` with **skip-if-unchanged** (reprice only when mid moved ≥1 tick), per-cycle order-action budget, jitter. Also fix the `record_price` → `record_update` LeadLagMM bug while in this function. | `continuous.py` (~lines 660-680, 750-800, 1685-1705) |
| 5 | **M** | **Paper LIP scorekeeper.** New `LIPScoreSimulator`: every second (or per refresh tick), for each paper order in `QuoteManager._active_orders`, fetch/cached book mid → compute hypothetical snapshot score; accumulate per-market per-day; estimate capture = `our_score / (our_score + observed_book_depth_score)` × pool. Wire `KalshiRewardTracker.log_order_placed/cancelled` into `place_quote`/`cancel_quote` so `db.reward_metrics` finally receives data; add a daily summary line to `notifier`/dashboard. Replace the fake `estimate_daily_reward` heuristic with the real formula. | new module + `market_maker.py` hooks + `db.py` (maybe one new column for score) |

**Phase A exit criteria:** 7 consecutive days of paper-quoting 3-5 selected non-sports LIP markets on Railway with daily hypothetical capture report (`$X/day estimated vs pool $Y`), zero rate-limit circuit-breaker trips.

### Phase B — Tiny-size live

| # | Size | Task | File / function / change |
|---|---|---|---|
| 6 | **L** | **Live order lifecycle + fill feedback.** Implement the live branch of `QuoteManager.place_quote/cancel_quote` for Kalshi (GTC via `KalshiClient.place_order(..., time_in_force="gtc")`, real order IDs; no 30s auto-cancel). Add a fill watcher — either Kalshi WS user-fills channel in `ws_feeds.py` (preferred) or a 5-10s order-status poller — that calls `MarketMaker.on_fill()` and `ToxicFlowDetector.record_fill()`. This single task activates inventory tracking, toxic-flow pause, and `MM_AUTO_HEDGE_ENABLED` end to end. Crash recovery: on startup, reconcile open Kalshi orders (extend `recovery.py`) — cancel any orphaned MM quotes. | `market_maker.py` (QuoteManager), `ws_feeds.py` or new poller in `continuous.py`, `recovery.py` |
| 7 | **S** | **Resolution + staleness guards.** In the refresh loop: cancel all quotes and flatten inventory when `now > close_time - LIP_FLATTEN_HOURS` (default 12h) or when last WS tick for the market is older than `LIP_MAX_QUOTE_AGE` (default 60s). Self-cross guard: never post a bid ≥ best ask or ask ≤ best bid from the live book. | `continuous.py` refresh loop + `market_maker.py` |
| 8 | **S** | **Executor/scan hygiene (cleanup).** Fix `market_ticker` vs `ticker` key mismatch in `executor._build_legs` KalshiRewards branch (or delete the branch if the MM loop fully owns LIP), update `KalshiRewardTracker` docstring (reward API exists), update `docs/strategy-framework-v2.md` rows #10/#22-23. | `executor.py`, `market_maker.py`, docs |
| 9 | **S** | **Go-live config.** Railway: `KALSHI_LIP_ENABLED=true`, `MM_ENABLED=true`, `DRY_RUN=false` for the MM path only at tiny size (`LIP_QUOTE_SIZE=$5-10`, `MM_MAX_INVENTORY=$50/market` initially — NOT the $500 default), `MM_TOXIC_FLOW_ENABLED=true`, `MM_VOLATILITY_ADJUSTED_ENABLED=true`, `MM_AUTO_HEDGE_ENABLED=true`. Verify live LIP payouts vs simulator after 3-5 days; recalibrate capture model. | env only + runbook note |

**Total estimate:** Phase A ≈ 1 S + 3 M + 1 M = **~1.5-2 focused weeks**; Phase B ≈ 1 L + 3 S = **~1-1.5 weeks**. Comfortable inside the Sep 1 LIP window, leaving ~8 weeks of farming runway.

### Top risks to the plan
1. **Pro-rata capture estimate is the soft spot** — competition depth is observable but competitors' exact scores aren't; the simulator gives an upper bound. Validate against real payouts in week 1 of live.
2. **Fill feedback (Task 6) is the only L** — if the Kalshi user-fills WS channel is awkward, fall back to order-status polling (works within existing rate budget at 3-5 markets).
3. **`scan_kalshi_rewards` unit bug** suggests this path has never run against live data — budget a half-day live-payload verification spike (incentives response shape, `last_price` units, GTC resting behavior) before Task 2.
