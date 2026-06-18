---

# Codebase Audit — Bugs & Security
**Date**: 2026-06-18  
**Scope**: All production Python modules (bugs + security dimensions)  
**Method**: Scout → 36 parallel Finder agents → 3 adversarial Verifier lenses → Synthesis  
**Status**: READ-ONLY — no code modified

---

## CRITICAL (5 findings — trade execution completely broken or data layer unusable)

### C-1 · `scans/sxbet.py:78-91` — SX Bet back-all trades silently no-op
`SXBetBackAll` opportunities never populate `_sx_outcome_ids`. Executor's `zip(legs, opp["_sx_outcome_ids"])` zips against a missing key (KeyError) or empty list, producing zero execution legs. All SX Bet back-all strategies are completely non-functional.  
**Verdict**: CONFIRMED

### C-2 · `scans/multi_cross.py:254` — Multi-outcome cross-platform scan crashes on every invocation
`_parallel_fetch_kalshi` is called without the required `tickers` positional argument and returns a wrong type. Every invocation of the multi-outcome cross-platform scan (`--mode multi-cross`) raises `TypeError` and crashes.  
**Verdict**: CONFIRMED

### C-3 · `smarkets_api.py:301` — All Smarkets orders placed at 100× intended price
`place_order` computes `int(price * 10000)` (basis points) but the Smarkets API expects `int(price * 100)` (percentage). A 50% probability order is sent as 5000 instead of 50. Every live Smarkets order goes through at a wildly wrong price.  
**Verdict**: CONFIRMED

### C-4 · `capital_optimizer.py:312-316` — Deadlock in capital optimizer thread
`get_harvest_candidates` acquires `self._lock`, then calls `is_long_term` which also acquires the same non-reentrant `threading.Lock`. Any harvest-candidate query deadlocks the entire capital-optimization thread permanently.  
**Verdict**: CONFIRMED

### C-5 · `scripts/analytics.py:68` — Analytics produces no data (DuckDB on SQLite file)
The analytics script opens `trades.db` (SQLite binary format) with DuckDB. The formats are incompatible; DuckDB silently reads zero rows. All P&L reports, Sharpe calculations, and drawdown figures are computed over empty datasets.  
**Verdict**: CONFIRMED

---

## HIGH (43 findings — financial loss, silent failures, or exploitable security issues)

### H-1 · `executor.py:2799-2806` — Concurrent legs never record fill price; hedger sends wrong-sized orders
`_execute_legs_concurrent` writes fill quantities but never sets `leg["_fill_price"]`. The hedger reads a zero/missing fill price for all concurrent executions and computes incorrect hedge sizes.  
**Verdict**: CONFIRMED

### H-2 · `continuous.py:1520` — `IMBALANCE_MAX_TRADE` import crashes imbalance mode
`from config import IMBALANCE_MAX_TRADE` — config defines `IMBALANCE_MAX_TRADE_SIZE`. The import fails with `ImportError` whenever imbalance mode is loaded in continuous operation.  
**Verdict**: CONFIRMED

### H-3 · `fees.py:34` — Fee EV formula has inverted probability weights
`_select_fees` computes `prob_a * case_b_fees + (1 - prob_a) * case_a_fees` — the weights are swapped. Expected fees are systematically wrong for any non-symmetric outcome, causing the fee model to undercharge or overcharge across all strategies that use it.  
**Verdict**: CONFIRMED

### H-4 · `recovery.py:266` — Polymarket recovery uses `order_id` as `token_id`
Position reconciliation passes `order_id` where the CLOB API expects a `token_id`. All Polymarket orphaned-position lookups fail silently; recovery never actually reconciles Polymarket positions.  
**Verdict**: CONFIRMED

### H-5 · `polymarket_api.py:34-36` — `POLYMARKET_PROXY_URL` accepted without SSRF validation
Any value injected via the environment variable is used verbatim as the proxy for all Polymarket API calls. An attacker who can influence environment variables can redirect Polymarket credentials and order flow.  
**Verdict**: CONFIRMED

### H-6 · `kalshi_api.py:164-166` — Circuit breaker never opens on HTTP 5xx errors
`record_success()` is called before `raise_for_status()`. Any non-429 error (500, 503, etc.) increments the success counter. The circuit breaker never opens regardless of error rate, providing no protection against cascading Kalshi API failures.  
**Verdict**: CONFIRMED

### H-7 · `ws_feeds.py:384-396` — Kalshi subscription TOCTOU race condition
`_pending_kalshi_subs` is checked and then written in two separate non-atomic steps without a lock. Concurrent subscription requests produce duplicate subscriptions and corrupt the subscription index.  
**Verdict**: CONFIRMED

### H-8 · `ws_feeds.py:119-145` — Subscription lists mutated without lock
`update_subscriptions` and `prune_subscriptions` iterate and mutate the same shared list without any mutex. Concurrent WebSocket events and scan cycles produce `RuntimeError: list changed size during iteration`.  
**Verdict**: CONFIRMED

### H-9 · `sxbet_api.py:61` — Ethereum private key stored as plaintext instance attribute
`self.private_key = config.SXBET_PRIVATE_KEY` stores the raw key string. It appears in `repr(client)`, stack traces, and any debug logging that serializes the object.  
**Verdict**: CONFIRMED

### H-10 · `sxbet_api.py:16` — `SXBET_API_BASE_URL` not SSRF-validated
The base URL is read from environment with no allowlist check. Env-var injection redirects all SX Bet API traffic to an arbitrary host.  
**Verdict**: CONFIRMED

### H-11 · `sxbet_api.py:122` — `_RateLimitError` raised with no retry handler or caller catch
The custom exception is raised but `_make_request` has no `@retry` decorator and no caller catches it. All Smarkets rate-limit responses propagate uncaught and crash the scan worker.  
**Verdict**: CONFIRMED

### H-12 · `matchbook_api.py:113` — Same uncaught `_RateLimitError` as SX Bet
Identical pattern: `_RateLimitError` raised in `_make_request` with no retry/catch anywhere in the call chain. All Matchbook 429 responses crash the scan worker.  
**Verdict**: CONFIRMED

### H-13 · `gemini_api.py:194-199` — `requests.Timeout` silently swallowed, bypasses retry
`except requests.Timeout: pass` inside the retry loop discards the exception without decrementing the retry counter or re-raising. Gemini timeouts are silently ignored; the function returns `None` as if successful.  
**Verdict**: CONFIRMED

### H-14 · `gemini_api.py:545-547` — `cancel_order` returns `True` when cancellation failed
The function checks `response.get("isCancelled")` but returns `True` unconditionally when the key is absent or `False`. Failed order cancellations are silently reported as successful.  
**Verdict**: CONFIRMED

### H-15 · `ibkr_api.py:130` — `underConId or conId` falsy-zero bug splits binary contracts
When `underConId == 0` (falsy), `conId` is used instead. IBKR binary YES/NO contracts share a `conId` but have distinct `underConId` values; the substitution merges them, causing `get_market_price` to return `(None, None)` for all IBKR binary markets.  
**Verdict**: CONFIRMED

### H-16 · `signal_aggregator.py:231-242` — First Metaculus/Manifold result used without title similarity check
`_fetch_metaculus` and `_fetch_manifold` return the probability of the first search result with no fuzzy-title comparison against the target market. An unrelated top-ranked question silently drives trade decisions with full confidence weight.  
**Verdict**: CONFIRMED

### H-17 · `event_monitor.py:275-278` — Platform's own price included in the consensus it's measured against
`add_signal` records the platform's price into the aggregator at line 271, then `get_consensus` at line 274 includes that same price in the weighted average. The platform's divergence from "consensus" is self-diluted, suppressing valid divergence signals.  
**Verdict**: CONFIRMED

### H-18 · `market_discovery.py:271-278` — External market titles injected into LLM prompt without sanitization (prompt injection)
`_format_pairs` directly interpolates `p.question_a`, `p.question_b`, and `p.pair_id` — all sourced from external platform APIs — into the LLM user message. A crafted market title can manipulate equivalence judgments and redirect the discovery model's outputs.  
**Verdict**: CONFIRMED

### H-19 · `scans/cross.py:662-667` — CLOB refinement uses same `other_price` for both cross strategies
A single `other_price` value is parsed for both the YES-buy-on-PM/NO-buy-on-other and the NO-buy-on-PM/YES-buy-on-other refinements. The second refinement always uses the wrong counterpart price, systematically mis-pricing half of all cross-platform opportunities.  
**Verdict**: CONFIRMED

### H-20 · `scans/triangular.py:311-326` — TriangularCross dict missing price/side fields; CLOB refinement uses price=0
The opportunity dict lacks `_price_a`, `_price_b`, `_side_a`, `_side_b`. CLOB refinement defaults both legs to `side="yes"` and `price=0`, making every triangular opportunity appear maximally profitable and pass the refinement filter.  
**Verdict**: CONFIRMED

### H-21 · `scans/cross_mm.py:91` — Cross-MM fee always computed as zero
`net_profit_cross_generic(qty, buy_price, 1 - sell_price, ...)` passes the complement of the sell price instead of the sell price itself. The fee function returns 0.0 for all cross-MM quotes; every cross-MM opportunity is overvalued by the full fee amount.  
**Verdict**: CONFIRMED

### H-22 · `scans/rewards.py:231` — `outcomePrices[0]` used as string; TypeError crashes rewards scan
`outcomePrices` elements are JSON strings, not floats. The arithmetic at line 231 raises `TypeError: unsupported operand type(s) for +: 'float' and 'str'`, crashing the entire rewards scan.  
**Verdict**: CONFIRMED

### H-23 · `scans/news_snipe.py:131-134` — News keywords drive 0.8-confidence trade signals with no NLP (security)
`_score_sentiment` does substring search for words like "approved", "failed", "delayed" anywhere in headline+body. Negations ("not approved") score the same as affirmations. Any news source manipulation or adversarial headline can inject spurious high-confidence trade signals.  
**Verdict**: CONFIRMED

### H-24 · `scans/correlated.py:449-457` — Layer-4 ROI floor gate is dead code
The minimum-expected-value guard block is `pass`. All correlated-pair opportunities pass the profitability filter unconditionally, regardless of expected value.  
**Verdict**: CONFIRMED

### H-25 · `scans/conditional.py:291-305` — CLOB refinement uses `yes_ask` for all legs regardless of direction
Sell-NO legs are refined using the YES ask price instead of the NO ask price. Conditional arb profitability is systematically overstated for any opportunity where YES ≠ NO prices.  
**Verdict**: CONFIRMED

### H-26 · `scans/bracket.py:122-136` — `_brackets_are_complete` returns True on first `inf` upper bound without contiguity check
The function returns `True` immediately when it finds any outcome with an `inf` upper bound, without verifying that all intermediate brackets are contiguous and gap-free. Incomplete bracket sets pass as valid.  
**Verdict**: CONFIRMED

### H-27 · `scans/cross_category.py:185-228` — CoinGecko price feed controls trade direction with no cross-validation (security)
External CoinGecko API responses determine trade direction for cross-category arbs. No secondary price source validates these values. A compromised or spoofed CoinGecko response directly controls execution direction.  
**Verdict**: CONFIRMED

### H-28 · `market_maker.py:173-174` — Inventory skew applied in wrong direction for ask quotes
When inventory is long, both bid and ask are adjusted by subtracting the skew. The ask should ADD skew to widen the spread and reduce long exposure. Current logic makes the ask cheaper, increasing long inventory further instead of reducing it.  
**Verdict**: CONFIRMED

### H-29 · `dashboard.py:384-396` — CSRF on all POST endpoints including `/api/rebalance/execute`
No CSRF token or `Origin` header check on any POST endpoint. A malicious cross-origin request can trigger live fund-rebalancing operations from any browser tab.  
**Verdict**: CONFIRMED

### H-30 · `dashboard.py:402-404` — Unbounded POST body read
`request.rfile.read(int(content_length))` with no maximum size guard. An attacker can send a `Content-Length: 10000000000` header and exhaust process memory.  
**Verdict**: CONFIRMED

### H-31 · `dashboard.py:747` — Direct `db.conn` access from HTTP thread bypasses TradeDB lock
The HTTP handler accesses `db.conn` directly, circumventing the `threading.Lock` in `TradeDB`. Concurrent dashboard reads and executor writes produce SQLite `DatabaseError: database is locked` or silent data corruption.  
**Verdict**: CONFIRMED

### H-32 · `backtest.py:222-228` — Backtest balance never debited; all recommendations are invalid
`balance += realized_profit` credits P&L but never deducts `trade_size` (capital deployed). The simulated balance inflates monotonically, inflating subsequent position sizes. All backtest-driven threshold recommendations are computed over an unrealistic account.  
**Verdict**: CONFIRMED

### H-33 · `scripts/analytics.py:106` — Sharpe ratio formula computes raw volatility, not Sharpe
`sharpe = np.std(daily_returns) * np.sqrt(252)` is annualised volatility. Sharpe requires `mean(returns) / std(returns) * sqrt(252)`. All reported Sharpe values are wrong by a factor that varies with mean return.  
**Verdict**: CONFIRMED

### H-34 · `scripts/analytics.py:109` — Max drawdown formula not path-dependent
The running peak maximum is not tracked; the formula computes a single-pass drawdown that understates true peak-to-trough drawdown for any return series with multiple local peaks.  
**Verdict**: CONFIRMED

### H-35 · `scans/sxbet.py:55-56` — Bid prices used instead of ask prices for SX Bet back-all
`best_bid` is used to assess profitability of a back-all strategy that requires filling at ask. Opportunities are overvalued by the full bid-ask spread on every SX Bet market.  
**Verdict**: CONFIRMED

### H-36 · `scans/whale_copy.py:166,186` — Block cache inclusive; all highest-block transactions reprocessed every cycle
`last_block_cache[wallet] = highest_block` stores the last seen block, then the next scan calls with `startblock=highest_block` (inclusive). Every transaction in the highest block is re-fetched and re-processed as a new opportunity.  
**Verdict**: CONFIRMED

### H-37 · `time_decay.py:67-81` — `guaranteed_gain` formula uses threshold not current price
`guaranteed_gain = buy_below_price - target_price` uses the config threshold as if it were the entry price. The correct formula is `target_price - current_price`. When `consensus_side == "YES"`, the result is typically negative despite the trade being profitable.  
**Verdict**: CONFIRMED

### H-38 · `calibration_tracker.py:104-106` — Cache eviction runs outside threading lock
The LRU eviction loop at lines 104-106 reads and deletes from the cache dict without holding the lock that protects all other cache operations. Concurrent access can raise `RuntimeError: dictionary changed size during iteration` or silently delete wrong entries.  
**Verdict**: CONFIRMED

### H-39 · `polygonscan_api.py:86` — API key exposed in URL query string (security)
`f"?apikey={self.api_key}"` appended to every request URL. The key appears in server access logs, proxy logs, browser history, and any HTTP-level observability tool.  
**Verdict**: CONFIRMED

### H-40 · `polygonscan_api.py:119` — Broad `except` swallows all retriable errors
`except Exception: return []` catches connection errors, timeouts, and HTTP errors indistinguishably. Retriable transient failures are silently suppressed, producing data gaps that affect whale-copy and gas-monitor decisions.  
**Verdict**: CONFIRMED

### H-41 · `twitter_api.py:101` — Twitter feed limited to 100 tweets; pagination ignored
The `next_token` field in the response is never followed. Scans see at most 100 recent tweets regardless of volume. High-activity periods produce incomplete signal coverage.  
**Verdict**: CONFIRMED

### H-42 · `executor.py:2054` — `ValueError` from `_build_legs` crashes executor worker thread
`execute()` has no try/except around `_build_legs()`. Multiple branches raise `ValueError` on unexpected opportunity shapes. In continuous mode the WebSocket-trigger path has only a `finally` clause; the exception propagates and terminates the worker thread.  
**Verdict**: CONFIRMED

### H-43 · `continuous.py:261-262` — P&L calculation uses fixed $1 payout for all contract sizes
`_calc_realized_pnl` returns `1.0 - total_fill_cost`, assuming total payout is always $1.00. A 50-contract position at $0.49 each costs $24.50 and should pay $50.00; this formula reports $0.51 P&L instead of $25.50. Running P&L display is wrong for any multi-contract position.  
**Verdict**: CONFIRMED

---

## MEDIUM (33 findings — operational degradation, edge-case crashes, minor security issues)

### M-1 · `db.py:997` — `strftime('%s')` non-standard in SQLite
`strftime('%s', timestamp)` is not a guaranteed SQLite format specifier; returns NULL on some builds. Rebalancing daily-limit queries silently return zero rows on affected platforms, disabling the limit.  
**Verdict**: CONFIRMED

### M-2 · `db.py:786` — f-string table name in raw SQL (structural injection risk)
`f"SELECT ... FROM {table_name}"` — if `table_name` is ever sourced from external input this is SQL injection. Currently internal, but a future refactor could introduce the vulnerability.  
**Verdict**: CONFIRMED

### M-3 · `continuous.py:861,895` — `asyncio.get_event_loop()` in non-main thread
Deprecated in Python 3.10+; raises `DeprecationWarning` and will raise `RuntimeError` in future Python versions when called from a non-main thread with no running loop.  
**Verdict**: CONFIRMED

### M-4 · `kalshi_api.py:52` — RSA key loaded with `unsafe_skip_rsa_key_validation=True`
A malformed or truncated RSA key is silently accepted at load time. Auth failures only surface at first API call.  
**Verdict**: CONFIRMED

### M-5 · `risk_manager.py:73-74` — Balance gate fails open for non-`$`-prefix `total_cost` strings
String parsing expects a `$` prefix; values from opportunity types that format `total_cost` differently bypass the balance check silently.  
**Verdict**: CONFIRMED

### M-6 · `hedger.py:194` — Betfair hedge creates unbounded liability for near-zero fill prices
`decimal_odds = 1 / fill_price` with no maximum guard. A fill_price of 0.001 creates a 1000× liability hedge order.  
**Verdict**: CONFIRMED

### M-7 · `matcher.py:192-194` — `classify_confidence` uses boosted combined score, inflating confidence
Confidence ratings reflect a post-boost combined score rather than the raw fuzzy-match score. Cross-platform market pairs are assigned higher confidence than the underlying title similarity warrants.  
**Verdict**: CONFIRMED

### M-8 · `scans/cross.py:621` — `refined_out` initialized but never appended in `_refine_cross_all_with_clob`
The refinement function creates an empty list and builds candidates but returns the original unfiltered list. All cross-all stale mid-price opportunities pass the CLOB refinement step unchanged.  
**Verdict**: CONFIRMED

### M-9 · `scans/negrisk.py:79-83` — NegRisk scan keeps opportunities with <50% CLOB coverage using mid-price estimates
Mid-price fallbacks are substituted and the loop continues rather than dropping the candidate. Opportunities with <50% real CLOB data are passed to execution.  
**Verdict**: CONFIRMED

### M-10 · `scans/binary.py:67` — Division by zero when both CLOB asks are 0
No guard against zero denominator; raises `ZeroDivisionError` on any market where the CLOB returns zero asks.  
**Verdict**: CONFIRMED

### M-11 · `scans/gemini.py:167` — Unchecked `float()` conversion on Gemini price string
A malformed or empty price field raises `ValueError` and crashes the entire Gemini scan without logging which market caused it.  
**Verdict**: CONFIRMED

### M-12 · `scans/spread.py:58` — No bounds check on bid/ask prices
Negative prices and prices >1.0 are passed without rejection. Downstream arithmetic produces nonsensical spread values.  
**Verdict**: CONFIRMED

### M-13 · `scans/stale.py:54` — Flat 3% fee estimate for all platforms
Gemini's taker fee is 7%. Using 3% understates the true cost by 57% for Gemini-side stale opportunities, allowing unprofitable opportunities to pass the profitability threshold.  
**Verdict**: CONFIRMED

### M-14 · `scans/convergence.py:62-96` — Unbounded `net_roi` when `trade_price` rounds near-zero
A `trade_price` approaching zero produces `net_roi = profit / near_zero`, generating arbitrarily large ROI values that dominate the opportunity ranking.  
**Verdict**: CONFIRMED

### M-15 · `scans/fee_promo.py:73-85` — All cached promo fields blindly propagated via `{**entry, ...}`
Stale cached promotional opportunity fields overwrite live computed fields via dict unpacking. Expired promo terms silently drive live execution.  
**Verdict**: CONFIRMED

### M-16 · `scans/correlated.py:259-285` — `total_cost` missing from correlated-pairs opportunity dict
The executor reads `opp["total_cost"]` for risk checks; its absence causes a `KeyError` when correlated-pairs opportunities reach execution.  
**Verdict**: CONFIRMED

### M-17 · `scans/multi_cross.py:434` — Price cache used without staleness check
`price_cache.get(("polymarket", token_id))` returns cached entries regardless of age. Arbitrarily stale prices from prior WebSocket updates are used in CLOB refinement.  
**Verdict**: CONFIRMED

### M-18 · `dashboard.py:245-251` — Dashboard authentication disabled when `DASHBOARD_PASS` unset
Auth middleware returns 200 unconditionally when the password env var is empty. Any process that can reach the dashboard port gets unauthenticated admin access.  
**Verdict**: CONFIRMED

### M-19 · `alerting.py:324` — Loss-streak alert fires only at exactly 3 consecutive losses
`== 3` condition instead of `>= 3`. A streak of 4 or more consecutive strategy losses generates no alert after the 3rd.  
**Verdict**: CONFIRMED

### M-20 · `metrics.py:286` — Prometheus histogram doubly-accumulated
Cumulative bucket counts are stored pre-summed, then re-summed during Prometheus text exposition. Every scraped histogram value is approximately double the actual count.  
**Verdict**: CONFIRMED

### M-21 · `snapshot.py:168` — `gross_spread` stored as 0.0 for any `total_cost >= 1.0`
Silently masks data quality issues; backtest replay for internal arbs with cost-basis ≥ 1.0 sees zero spread, distorting fee model training.  
**Verdict**: CONFIRMED

### M-22 · `treasury.py:168-169` — Idempotency key bucket collision
The key derivation can map distinct rebalancing operations to the same key; one of the pair is silently deduplicated and never executed.  
**Verdict**: CONFIRMED

### M-23 · `treasury.py:233-237` — On-chain USDC withdrawal destination not validated
`withdraw_usdc(destination, amount)` accepts any address string. No allowlist or checksum validation; a misconfigured caller can drain funds to an arbitrary address.  
**Verdict**: CONFIRMED

### M-24 · `rate_limiter.py:63-67` — Circuit breaker thundering-herd on auto-reset
All waiters are released simultaneously when the circuit breaker timer expires. A burst of concurrent requests to a recovering service can re-trigger the breaker immediately.  
**Verdict**: CONFIRMED

### M-25 · `url_guard.py:128-129` — Trailing-dot hostname bypasses internal-host SSRF check
`"169.254.169.254."` (trailing dot) passes the `endswith` internal-IP checks. SSRF guard is ineffective for trailing-dot variants of blocked hostnames.  
**Verdict**: CONFIRMED

### M-26 · `reddit_api.py:155-156` — Unsanitized subreddit name injected into URL path
If `subreddit` contains path traversal characters (e.g. `../users`), the constructed URL deviates from the intended endpoint.  
**Verdict**: CONFIRMED

### M-27 · `market_maker.py:1036` — Liquidity reward estimate always returns 0.0
`avg_spread * 100` in the denominator makes the reward formula evaluate to zero for any spread < 0.01. The liquidity-reward component of market-maker profitability is completely dead.  
**Verdict**: CONFIRMED

### M-28 · `logical_arb.py:94` — `_market_key` uses bare ID instead of `"polymarket-{id}"` format
Cross-module opportunity lookups (opportunity index, position tracker) expect the `"platform-{id}"` format. Bare IDs produce misses in all downstream lookups.  
**Verdict**: CONFIRMED

### M-29 · `config.py:760-761` — `CROSS_PAIR_WS_MIN_PROFIT_FACTOR` parsed without error handling
`float(os.getenv(...))` with no `try/except`. A malformed env var raises `ValueError` at import time, preventing the entire process from starting.  
**Verdict**: CONFIRMED

### M-30 · `executor.py:534-537` — `total_cost` string-to-float with no bounds check
Malformed strings produce `None` or `0.0` values that silently pass all risk gates.  
**Verdict**: CONFIRMED

### M-31 · `executor.py:288` — Idempotency dedup key derived from attacker-influenced market name
Deduplication relies on the market name string. An adversary who can control platform market names can produce collisions and suppress legitimate executions.  
**Verdict**: CONFIRMED

### M-32 · `notifier.py:40` — SSRF guard bypassable via `"telegram"` substring
`url.startswith("telegram")` allows any URL beginning with "telegram" (e.g. `telegram.evil.com/…`) to bypass the scheme/host SSRF checks.  
**Verdict**: CONFIRMED

### M-33 · `betfair_api.py:92` — No Betfair SSO token refresh
Long-running sessions use an SSO token that expires without any re-authentication logic. Sessions fail silently after token expiry until process restart.  
**Verdict**: CONFIRMED

---

## Summary Statistics

| Severity | Count |
|----------|-------|
| CRITICAL | 5 |
| HIGH | 43 |
| MEDIUM | 33 |
| **Total** | **81** |

### Top Priorities for Immediate Action

1. **C-1, C-2, C-3**: Three strategies completely non-functional (SX Bet back-all, multi-cross, Smarkets orders). Fix before next live run.
2. **C-4**: Deadlock in capital optimizer — will hang the process under normal operation. Fix before re-enabling capital optimization.
3. **C-5**: Analytics script produces no output; all historical reporting is based on empty data.
4. **H-3**: Fee EV weights inverted — affects profitability calculations across all strategies using `_select_fees`.
5. **H-6**: Kalshi circuit breaker never opens — removes all protection against cascading API failures.
6. **H-18**: Prompt injection via external market titles in `market_discovery.py` — direct security risk.
7. **H-29**: CSRF on `/api/rebalance/execute` — anyone who can load the dashboard URL can trigger fund movements.
8. **H-32**: Backtest balance inflation — all `config.apply_backtest_recommendations()` outputs are invalid until fixed.

---
*Generated by automated codebase-audit workflow. All findings are read-only observations; no source files were modified.*
