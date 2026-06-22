---

# Codebase Audit — Bugs & Security
**Date:** 2026-06-22  
**Scope:** All production Python files (root + scans/)  
**Dimensions:** bugs, security  
**Method:** 19 finder agents × 2 dimensions, 3 adversarial verifiers, completeness critic  

Findings are ranked by verified severity. CONFIRMED = independently verified by a second agent reading the code; PARTIAL = real issue but narrower scope or milder impact than initially reported; REFUTED = removed. Only CONFIRMED and PARTIAL findings are listed.

---

## CRITICAL

### C-1 · `kalshi_api.py:142` — @retry decorator retries non-idempotent POST orders → duplicate fills  
**Dimension:** bugs/security · **Verified:** CONFIRMED  
The `@retry(stop_after_attempt(3))` decorator wraps `_request()`, which handles all HTTP calls including `place_order`. If the POST reaches the Kalshi server and a `ConnectionError` or `Timeout` occurs on the *response read*, tenacity retries the same POST body up to two more times. With default `time_in_force="fill_or_kill"`, each retry creates a fresh independent FOK order; all three may fill, tripling intended position size.

### C-2 · `betfair_api.py:108` — Same @retry-on-POST duplicate-order risk on Betfair  
**Dimension:** bugs/security · **Verified:** CONFIRMED  
`place_orders` routes through `_request` which carries `@retry(stop_after_attempt(3))`. No `customerRef` idempotency key is set in the instructions payload, so Betfair cannot deduplicate retries. A TCP drop after server acceptance triggers up to two additional identical instruction sets.

### C-3 · `smarkets_api.py:90` — Same @retry-on-POST duplicate-order risk on Smarkets  
**Dimension:** bugs/security · **Verified:** CONFIRMED  
`place_order` calls `self._request("POST", "/orders/", ...)` decorated with `@retry`. Smarkets limit orders have no `time_in_force` restriction, so all three retried orders can rest in the book simultaneously, producing triple exposure on a single arb leg.

### C-4 · `continuous.py:1520` — ImportError for nonexistent config names silently kills 4 strategy scans every cycle  
**Dimension:** bugs · **Verified:** CONFIRMED  
`from config import IMBALANCE_MAX_TRADE` raises `ImportError` (actual name: `IMBALANCE_MAX_TRADE_SIZE`); similarly `NEWS_SNIPE_MAX_TRADE` vs `NEWS_SNIPE_MAX_TRADE_SIZE`. Both imports are wrapped in `try/except Exception` that catches `ImportError` and logs only at DEBUG. The imbalance, news-snipe, correlated, and time-decay strategies produce zero opportunities every scan cycle with no visible error, even when fully configured and enabled.

### C-5 · `treasury.py:141-145` — DB read failure silently resets daily transfer cap to $0 (fail-open)  
**Dimension:** bugs/security · **Verified:** CONFIRMED  
On any exception from `db.get_transfers_today()`, the except block sets `recent = []`, making `used_today = 0.0`. The subsequent cap check only verifies `amount_usd > MAX_AUTO_TRANSFER_PER_DAY`, allowing a full daily cap transfer even if the limit was nearly exhausted. A transient SQLite lock or disk-full error becomes a complete bypass of the daily transfer limit.

---

## HIGH — Financial Logic

### H-1 · `fees.py:34` — `_select_fees` probability weights are SWAPPED  
**Dimension:** bugs · **Verified:** CONFIRMED  
`return prob_a * case_b_fees + (1.0 - prob_a) * case_a_fees` multiplies the probability that A wins (`prob_a`) against the fees when B wins (`case_b_fees`), and vice versa. Correct EV is `prob_a × case_a_fees + (1−prob_a) × case_b_fees`. For a high-confidence A-side trade (e.g. `prob_a=0.9`), 90% weight is applied to B-win fees and only 10% to A-win fees — the opposite of correct. This corrupts expected-fee calculations for all cross-platform pairs using the EV fee model.

### H-2 · `fees.py:479` — `net_profit_betfair_backall` applies commission to wrong base  
**Dimension:** bugs · **Verified:** PARTIAL  
The formula uses `net_winnings = 1.0 - min(implied_probs)` (payout minus the cheapest single bet) as the Betfair commission base. Betfair commission applies to total net market profit = `1.0 - total_cost` (the gross spread). For a 3-runner book with implied probs `[0.30, 0.35, 0.32]`, the spread is 0.03, correct fee ≈ $0.0015, but the formula calculates `net_winnings = 0.70`, fee = $0.035 — a ~23× overestimate. This causes valid back-all arbs to be rejected as unprofitable (conservative error, no money lost, but real opportunities missed).

### H-3 · `scans/cross.py:537` — `_refine_cross_all_with_clob` return value discarded; filtering silently skipped  
**Dimension:** bugs · **Verified:** CONFIRMED  
`scan_cross_all` calls `_refine_cross_all_with_clob(opportunities, ...)` at line 537 without assigning the return. The function builds a `refined_out` list locally but neither returns it nor modifies the caller's `opportunities` list in-place to remove failing entries. Only the `net_profit` of passing items is updated. All cross-all opportunities that fail CLOB ask-price checks pass through to the executor as if they were profitable.

### H-4 · `scans/triangular.py:411-430` — Triangular CLOB refinement uses absent keys, all opportunities pass with phantom profit  
**Dimension:** bugs · **Verified:** CONFIRMED  
`scan_triangular` builds `TriangularCross` opportunity dicts without `_side_a`, `_price_b`, `_side_b`, or `_price_a` keys. The refine function defaults `other_price = o.get("_price_b", 0)` to 0 and calls `net_profit_triangular(pm_ask, 0, pa, pb)`, yielding `total_cost ≈ pm_ask` and `gross_spread ≈ 1 − pm_ask` — always a large positive number. Every triangular opportunity passes refinement with wildly inflated profit regardless of actual ask prices.

### H-5 · `hedger.py:178` — Kalshi hedge contract count uses current bid as denominator instead of contract face value  
**Dimension:** bugs · **Verified:** CONFIRMED  
`count = max(1, int(size / bid))`. Kalshi contracts have $1.00 face value; `size` is already in dollars. Dividing by `bid` (e.g. 0.45) yields ~2.2× the correct number of contracts. For a $10 fill at bid 0.45, the code places 22 contracts instead of ~10, converting a hedge into an oversized directional position.

### H-6 · `cross_pair_index.py:258-265` — Inverted pair YES/NO prices stored with swapped labels  
**Dimension:** bugs · **Verified:** CONFIRMED  
After `k_yes, k_no = k_no, k_yes` swap for inverted pairs, the output dict writes `"_kalshi_yes": k_yes` (which now holds the original NO price) and `"_kalshi_no": k_no` (original YES price). The executor's `_build_legs` parses these fields to determine which Kalshi side to trade, executing the YES leg at NO price and vice versa — systematically mispricing one leg of every inverted pair.

### H-7 · `market_maker.py:1036` — `estimate_daily_reward` formula always returns 0.0  
**Dimension:** bugs · **Verified:** CONFIRMED  
`estimated_daily = (total_resting / 86400) * (1 - avg_spread * 100) * 0.50`. With `avg_spread` a fraction (e.g. 0.04), `avg_spread * 100 = 4.0`, so `1 - 4.0 = -3.0`. Any positive resting amount yields a negative result, clamped to 0.0 by `max(0.0, ...)`. Kalshi reward estimation is non-functional — all estimates return $0.

### H-8 · `capital_optimizer.py:309-311` — `get_harvest_candidates` deadlocks: plain `threading.Lock` acquired twice  
**Dimension:** bugs · **Verified:** CONFIRMED  
`get_harvest_candidates` acquires `self._lock` (a plain `threading.Lock`) then calls `self.is_long_term(position_id)`, which also calls `with self._lock`. Plain locks are not re-entrant; the second acquisition blocks forever. Any call to `get_harvest_candidates` when `TAX_AWARE_ENABLED=true` hangs the calling thread permanently.

### H-9 · `event_monitor.py:271` — Platform's own price included in consensus before divergence is computed against that consensus  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested by any verifier)  
`add_signal(market_key, platform_name, platform_price)` inserts the trading platform's own price into the signal aggregator. `get_consensus()` then returns a weighted average that includes this platform price. The resulting `consensus_prob` is polluted by the very price being compared, artificially compressing the measured divergence. The comment on line 276 says "excluding the platform's own price" — this is false.

### H-10 · `gas_monitor.py:141` — Gas cost formula uses ETH base-transfer gas (21,000) for Polygon contract calls  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
`cost = gas_gwei * 21000 * matic_price / 1e9` uses the ETH base-transfer gas limit. Polymarket trades are ERC-1155 CTF contract interactions on Polygon consuming 80,000–150,000 gas. The formula underestimates actual gas cost by 4–7×, setting the execution threshold too low and letting gas-unprofitable trades pass the gate.

### H-11 · `calibration_tracker.py:104-106` — Cache invalidation runs outside the lock  
**Dimension:** bugs · **Verified:** CONFIRMED  
The `_in_memory_cache` dict comprehension and deletions (lines 104–106) execute after `self._lock` is released. A concurrent `get_platform_brier_score` call can read a stale cache entry that should have been invalidated, or — if a concurrent write is happening — produce `RuntimeError: dictionary changed size during iteration`.

### H-12 · `gemini_api.py:565` — `withdraw_usdc` accepts any destination address with no allowlist  
**Dimension:** security · **Verified:** CONFIRMED  
The method performs only `if not address` and `if amount <= 0` checks. Treasury calls `withdraw_usdc` without validating the destination against any allowlist of known-safe addresses. A bug or injection in the address-resolution path in `treasury.py` would result in irreversible fund loss to an arbitrary Ethereum address.

### H-13 · `config.py:9` — `load_dotenv("~/.claude/.env")` loads secrets from shared Claude Code directory  
**Dimension:** security · **Verified:** CONFIRMED  
Both `config.py` and `cli.py` call `load_dotenv(os.path.expanduser("~/.claude/.env"))`. This directory is Claude Code's shared configuration directory. Any secrets in `~/.claude/.env` — or secrets written there by the Claude Code CLI — are silently loaded into this process and can override `POLYMARKET_PRIVATE_KEY`, `DRY_RUN`, and `EXECUTION_MODE`.

### H-14 · `rate_limiter.py:53-67` — Circuit breaker has no half-open state; resets directly to closed  
**Dimension:** bugs · **Verified:** CONFIRMED  
When `elapsed >= reset_timeout`, `is_open()` sets `_failures = 0`, `_open_since = None`, and returns `False` immediately — fully closed without any trial call. If the underlying service is still degraded, all queued requests immediately hammer it again until 3 more consecutive failures re-open the circuit.

### H-15 · `alerting.py:324-325` — Strategy loss streak alert fires on exactly the 3rd loss only (`==` instead of `>=`)  
**Dimension:** bugs · **Verified:** CONFIRMED  
`if losses == 3` fires once at the third consecutive loss. The 4th, 5th, and all subsequent losses in the same streak produce no alert. A strategy burning money on its 10th consecutive loss generates no notification after the initial one.

### H-16 · `db.py:993` — `get_transfers_today` uses `strftime('%s', ...)` which is unsupported on many SQLite builds  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
`strftime('%s', ...)` (Unix timestamp format) is not a standard SQLite format specifier. On systems where it is unsupported, the expression returns NULL and the WHERE clause never matches, returning zero rows. This makes the treasury daily-limit check always report $0 transferred, bypassing the daily cap on every transfer.

### H-17 · `backtest.py:176-228` — Survivorship bias + look-ahead bias render backtest metrics unreliable  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
Only snapshots where `net_profit > 0` at scan time are replayed (line 187), and `_recalc_profit_with_fees` uses scan-time prices with zero slippage assumption. Win rate and P&L are systematically overstated, and `_suggest_min_roi` threshold recommendations derived from the backtest calibrate toward under-filtering.

### H-18 · `kalshi_api.py:164` — Circuit breaker records success on non-429 HTTP errors (4xx/5xx)  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
`_circuit.record_success()` is called unconditionally after the 429 check, before inspecting the actual status code. A persistent 5xx Kalshi outage never trips the circuit breaker; all requests continue to be dispatched rather than fast-failing.

---

## HIGH — Security

### HS-1 · `continuous.py:861` — `asyncio.get_event_loop()` from background thread raises `RuntimeError` in Python 3.10+  
**Dimension:** security/bugs · **Verified:** CONFIRMED (not contested; Python 3.12 CI)  
`on_price_update` is a callback invoked from WebSocket feed threads. Inside it, `asyncio.get_event_loop()` is called to get the loop for `asyncio.run_coroutine_threadsafe`. In Python 3.10+ this raises `DeprecationWarning` from non-main threads; in 3.12+ it raises `RuntimeError`. The `except` block falls back to direct execution, bypassing the priority queue entirely. Same pattern at lines 895, 2009, 2065.

### HS-2 · `ws_feeds.py:385-396` — Kalshi pending subscription list mutated from two threads without a lock  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
`_pending_kalshi_subs` is a plain list. The asyncio loop calls `.pop(0)` while external threads call `.extend()` via `update_subscriptions()`. Items appended mid-pop can be silently lost, causing WebSocket subscriptions to never be sent.

### HS-3 · `ws_feeds.py:511-522` — Polymarket pending subscription items lost between snapshot and `.clear()`  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
`pending = list(self._pending_poly_subs)` snapshots, then `self._pending_poly_subs.clear()`. Items appended by external threads between the snapshot and the clear are silently discarded — those token IDs will never be subscribed.

---

## MEDIUM

### M-1 · `executor.py:715` — API errors on high-ROI opportunities silently accepted without live price verification  
**Dimension:** security · **Verified:** PARTIAL  
When `_RevalidationAPIError` is raised and scan-time ROI ≥ 2%, the opportunity is accepted with the original scan-time profit. An adversary who can cause targeted API failures during the revalidation window for a specific market can ensure stale prices are traded. The 2% threshold is met by most real arb opportunities.

### M-2 · `gemini_api.py:194,229` — `requests.Timeout` silently swallowed by broad `RequestException` handler; retry never fires  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
Both `_private_request` and `_public_request` catch `ConnectionError` (re-raise for retry) and then `RequestException` (log + return None). Since `Timeout` is a subclass of `RequestException` but not `ConnectionError`, timeouts fall into the second handler and are never retried. Every Gemini API call that times out silently returns None.

### M-3 · `matchbook_api.py:155` — `_RateLimitError` raised in `_request` but no `@retry` decorator; propagates uncaught  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
`_request` raises `_RateLimitError` when the circuit breaker is open, but `_request` has no `@retry` decorator. The exception propagates through `fetch_all_events`, `list_runners`, etc., crashing the scan thread rather than returning an empty result.

### M-4 · `dashboard.py:237` — Unauthenticated read access when `DASHBOARD_PASS` is unset  
**Dimension:** security · **Verified:** CONFIRMED  
`_check_auth` returns `True` unconditionally when `DASHBOARD_PASS` is empty. `validate_config()` only raises if `DASHBOARD_HOST` is non-loopback AND password is empty, but in Railway container environments traffic may reach loopback, leaving the dashboard publicly accessible with no auth at the default host binding.

### M-5 · `risk_manager.py:185` — `clamp_size` depth cap mixes contract counts with dollar amounts  
**Dimension:** bugs · **Verified:** PARTIAL  
`size = min(size, depth * 0.5)` where `_clob_depth` for Polymarket is a contract count, not dollar value. For a 0.05-priced contract with 1000 depth, the dollar value is $50 but `depth * 0.5 = 500`, making the cap 10× too large. Execution attempts orders far larger than available liquidity, producing partial fills and orphaned positions.

### M-6 · `scans/stale.py:56` — Fee calculated as percentage of gross profit instead of trade cost  
**Dimension:** bugs · **Verified:** PARTIAL (fee bug confirmed; ROI denominator not a bug)  
`net_profit = gross_profit - (gross_profit * estimated_fee_pct)`. For a 5¢ gain on a 40¢ position, correct fee ≈ $0.012 but code charges $0.0015 — an 8× underestimate. Near-break-even stale opportunities appear profitable.

### M-7 · `scans/resolution.py:75` — Same fee-on-profit bug in resolution sniping  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
`estimated_fee = 0.02 * gross_profit`. Polymarket's winner fee is levied on settlement proceeds, not the gain. A token at $0.96 should incur ~$0.02 fee; the code charges ~$0.0008, making nearly all resolution snipes appear more profitable than they are.

### M-8 · `scans/correlated.py:283` — `net_profit` set to `gross_spread` with no fee deduction  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
`opportunity["net_profit"] = gross_spread` uses raw price difference with zero fee subtraction. The executor ranks and filters by `net_profit`, so all correlated-pair opportunities appear more profitable than they are by approximately the round-trip fee (typically 4–8%).

### M-9 · `scans/cross_category.py:206` — "below" direction logic copy-pasted from "above", produces inverted probabilities  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
`distance_pct = (current_value - threshold) / current_value` is the fraction the current value is *above* the threshold. When `current_value < threshold` (the typical "below" case), this is negative and the function returns 0.90 — implying 90% probability that the condition is met when it is not.

### M-10 · `scans/conditional.py:299` — Stage 2 CLOB refinement uses `yes_ask` for all legs including those being sold  
**Dimension:** bugs · **Verified:** CONFIRMED (not contested)  
Sell legs should be valued at `yes_bid`, not `yes_ask`. Using ask prices for sell legs understates proceeds, making some profitable opportunities appear break-even, and may make `BUY_CONDITIONAL` opportunities appear profitable when they are not.

### M-11 · `executor.py:288` — Idempotency check uses market title string; different Kalshi markets can collide  
**Dimension:** bugs · **Verified:** CONFIRMED (finder; not refuted)  
`has_recent_trade` called with `market` (human-readable title) can collide across different markets sharing a prefix, blocking legitimate executions for 60 seconds.

### M-12 · `db.py:162` — f-string column name interpolation in schema migration DDL  
**Dimension:** security · **Verified:** PARTIAL  
`ALTER TABLE partial_fills ADD COLUMN {col} TEXT` where `col` is from a hardcoded tuple. Not currently exploitable, but the pattern is copied to `snapshot.py:74` and is a latent injection risk if column lists ever include external input.

### M-13 · `url_guard.py:116` — `ALLOW_PRIVATE_INTERNAL_URLS=true` bypasses all SSRF checks  
**Dimension:** security · **Verified:** PARTIAL  
Intentional escape hatch, but total — all five SSRF call sites lose protection simultaneously if this env var is set via a compromised Railway environment.

### M-14 · `signal_aggregator.py:81` — Out-of-range signal from Manifold silently discarded; caller unaware consensus was incomplete  
**Dimension:** bugs · **Verified:** PARTIAL  
`add_signal` validates and drops out-of-range values, but returns silently. Callers that expect a full consensus may receive a weakly-supported value with no indication that an external signal was rejected.

---

## LOW

### L-1 · `scans/logical_arb.py:82` — One direction of logical inconsistency never detected  
When `then_price > if_price` (implied outcome priced higher than the implying outcome — an opportunity to sell implied and buy implying), no opportunity is generated. Only one direction of the logical violation is caught.

### L-2 · `executor.py:2054` — `_build_legs` raises `ValueError` for Imbalance/NewsSnipe/TimeDecay missing token IDs instead of returning `[]`  
Unlike every other opportunity type, these three raise uncaught exceptions that can crash the execution loop and leave per-market locks unreleased.

### L-3 · `recovery.py:107-119` — Partially-filled multi-leg opportunities marked "orphaned" without hedging the open leg  
Filled legs for the same `opportunity_id` are not hedged at crash time; they wait for `_convert_orphans_to_partial_fills`, which may never run if the process crashes again.

### L-4 · `snapshot.py:111-120` — `upsert_correlated_pairs` DELETE + INSERT with no transaction rollback protection  
Two separate statements with no explicit `BEGIN/ROLLBACK`; a crash between them leaves the table empty until the next full recompute.

### L-5 · `dashboard_ui.py:1025` — Alert severity used as DOM className without allowlist sanitization  
`sev.className = 'alert-sev sev-' + a.severity` — latent XSS if severity ever becomes influenced by external data.

### L-6 · `notifier.py:107` — Webhook URL platform detection uses substring match on full URL, not hostname  
`"hooks.slack.com" in self.url` matches a query parameter, allowing payload-format confusion.

### L-7 · `polygonscan_api.py:86` — API key transmitted in URL query string  
Routinely captured in server access logs and proxy logs.

### L-8 · `reddit_api.py:226` — `subreddit` and `sort` interpolated into URL path without encoding  
Path-traversal sequences in subreddit names could reach unintended Reddit endpoints.

### L-9 · `latency_monitor.py:73` — P95 uses floor index, returning P96 for a 50-sample buffer  
`int(n * 0.95)` truncates; correct formula is `ceil(n * 0.95) - 1`.

### L-10 · `credential_health.py:93-94` — CRITICAL alert fires only on the exact 3rd failure (`==`), not 4th+  
Same `==` vs `>=` pattern as alerting.py — extended outages go silent after first alert.

### L-11 · `credential_health.py:74-80` — `check_all_platforms` awaits 8 coroutines sequentially instead of with `asyncio.gather`  
Worst-case health check takes 160 s instead of 10–20 s.

### L-12 · `capital_optimizer.py:69-71` — `score()` returns raw ROI (not 0.0) when `days_to_resolution` is missing  
Docstring says "Returns 0.0 if days_to_resolution is missing," code returns `roi`. Stale/mis-tagged opportunities rank higher than intended.

---

## REFUTED (removed from report)

- `db.py:786` SQL injection in `get_db_stats` — hardcoded tuple, no injection vector  
- `db.py:253` `get_daily_pnl` double-counts — sets are mutually exclusive (settled vs open)  
- `hedger.py:194` Betfair decimal odds conversion — fill_price stored as probability; 1/p→odds conversion is correct  
- `scans/news_snipe.py:326` YES signals dropped at ≥0.50 — intentional design (market has already priced in news)  

---

*Report generated by automated multi-agent audit. All CONFIRMED findings were independently verified by a second code-reading agent.*
