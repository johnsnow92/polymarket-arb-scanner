---

# Codebase Audit — Bugs & Security
**Date:** 2026-06-13
**Dimensions:** bugs, security
**Method:** Scout → 16 parallel finders (8 chunks × 2 dimensions) → 3-lens adversarial verification (majority vote) → completeness critic → synthesis
**False positives rejected:** 10 findings (including mis-claimed else-dead-code in executor.py, Kalshi maker-fee 100× error, triangular._price_b always-0, price_tracker dict-mutation crash, dry-run env-var bypass, SQL injection in dashboard purge, and recovery.py deadlock)

---

## CRITICAL — Fix Before Next Deploy

### C-1 · `continuous.py:1517` · NameError crash when news-snipe mode active
`FINNHUB_API_KEY` is referenced on line 1517 of `continuous.py` but is never imported in the module's import block (only `NEWS_SNIPE_CONFIDENCE_THRESHOLD` and `NEWS_SNIPE_MAX_TRADE` are locally imported at line 1516). Raises `NameError` at runtime whenever `news-snipe` or `all` mode is active with `NEWS_SNIPE_ENABLED=true`.
**Verified by:** 2/3 lenses (Skeptic + Confirmer). **Fix:** add `FINNHUB_API_KEY` to the import at line 1516.

### C-2 · `gemini_api.py:564–591` · `withdraw_usdc` sends funds to unvalidated address
`withdraw_usdc(address, amount)` checks only that `address` is non-empty. No EIP-55 checksum, no `0x` prefix, no 40-hex-char length validation is performed before the irreversible Gemini withdrawal API call. A one-character typo, a bug in any upstream caller, or an injected env var (`GEMINI_DEPOSIT_ADDRESS` read at call time in `treasury.py:234`) results in permanent loss of funds.
**Verified by:** 3/3 lenses. **Fix:** validate address against `re.fullmatch(r"0x[0-9a-fA-F]{40}", address)` and add EIP-55 checksum via `web3.to_checksum_address`. Also validate `GEMINI_DEPOSIT_ADDRESS` at startup in `validate_config()`.

### C-3 · `continuous.py:736` · asyncio signal handler is not thread-safe
`_signal_handler` calls `shutdown_event.set()` directly on an `asyncio.Event`. Signal handlers run in the OS signal-delivery context (main thread), but `asyncio.Event.set()` must be scheduled via `loop.call_soon_threadsafe()` when called from outside the running event loop. On CPython this usually works due to the GIL, but it is undefined behavior and can corrupt asyncio internal state under PyPy or any future free-threaded Python build.
**Verified by:** 2/3 lenses. **Fix:** `loop.call_soon_threadsafe(shutdown_event.set)`.

### C-4 · `ws_feeds.py:397,526` · WebSocket messages drive live execution with zero schema validation
Polymarket and Kalshi WebSocket message handlers (`_handle_polymarket_message`, `_handle_kalshi_message`) call `json.loads(raw)` on server-supplied data and pass the parsed dict directly into the shared `price_cache`, which then triggers `OpportunityIndex`-based live trade execution in continuous mode. No field-type or range validation is applied before values enter the execution pipeline. A BGP hijack, MitM on the WebSocket TLS connection, or a compromised upstream can inject `best_ask=0.001` (a valid float) and cause losing trades.
**Verified by:** Completeness critic. **Fix:** validate message schema (required keys, type, range 0 < price < 1) before writing to cache.

### C-5 · `treasury.py:234` · Auto-rebalance destination address read from env at transfer time
`_polymarket_to_gemini()` reads `GEMINI_DEPOSIT_ADDRESS` via `os.getenv()` at call time rather than at startup in `validate_config()`. On Railway, env vars can be updated live by any operator with deploy access; the updated value takes effect on the next `os.getenv()` call without a process restart. Combined with the lack of on-chain address validation (same as C-2), this creates a window where a compromised deployment config can redirect an auto-rebalance transfer to an arbitrary address.
**Verified by:** Completeness critic. **Fix:** read and validate address at startup; store in a module constant.

### C-6 · `kalshi_api.py:131` · RSA signature message includes duplicate base-path prefix
`_auth_headers` constructs the signed message as `timestamp_ms + method.upper() + KALSHI_API_PATH + path_no_query`, prepending `KALSHI_API_PATH` (`"/trade-api/v2"`) to the already-relative path. If Kalshi's signature spec expects only the relative path (not the API base prefix), every authenticated request produces an invalid signature. Confirmed present in code; API spec must confirm expected message format.
**Verified by:** 1 lens (background bug-finder). **Fix:** verify Kalshi API signature spec and remove `KALSHI_API_PATH` from the message string if the spec uses the path relative to the base.

---

## HIGH — Fix This Sprint

### H-1 · `fees.py:34` · Expected-value fee formula has cases swapped
`return prob_a * case_b_fees + (1.0 - prob_a) * case_a_fees` — `prob_a` is the probability side-A wins, so the formula should multiply `prob_a` by `case_a_fees` (the fees when A wins). The operands are reversed. Any strategy using `FEE_MODEL="expected_value"` systematically computes the wrong fee, biasing apparent profitability.
**Verified by:** 2/3 lenses.

### H-2 · `event_monitor.py:271` · Platform price contaminates the consensus it is compared against
`signal_aggregator.add_signal(market_key, platform_name, platform_price)` adds the trading platform's own price as a source before calling `get_consensus`. The resulting consensus therefore includes the platform's own price, diluting divergence and causing the scanner to systematically under-report mispricing signals. The comment at line 276 ("excluding the platform's own price") is aspirational but not implemented.
**Verified by:** 2/3 lenses (Confirmer + secondary verifier).

### H-3 · `scans/cross.py:537` · `_refine_cross_all_with_clob` never removes failing candidates
`_refine_cross_all_with_clob` builds an internal `refined_out = []` list but never populates it and never uses it to filter `opportunities`. Candidates that fail CLOB revalidation (missing token IDs, no fee function, zero-liquidity depth) remain in the output list with their original stale mid-price `net_profit` values, producing false-positive arbitrage signals that reach the executor.
**Verified by:** 3/3 lenses.

### H-4 · `scans/correlated.py:282–283` · Correlated-pairs `net_profit` is a gross spread — no fees deducted
`opportunity["net_profit"] = gross_spread = abs(short_price - long_price)` is assigned directly as net profit. No fee calculation is invoked. The comment says "simplified — actual calc in fees.py" but there is no downstream refinement step. The executor receives this inflated value and applies `MIN_NET_ROI` against a gross figure, causing the correlated-pairs strategy to execute trades that are profitable gross but lossy net.
**Verified by:** 3/3 lenses.

### H-5 · `market_maker.py:1036` · Daily earnings estimate produces negative values for any realistic spread
`estimated_daily = (total_resting / 86400) * (1 - avg_spread * 100) * 0.50` — `avg_spread` is a ratio (e.g., 0.03 for a 3% spread), so `avg_spread * 100 = 3.0`, making `(1 - 3.0) = -2.0`. The estimate is clamped by `max(0.0, ...)` downstream, but the formula is wrong: any spread > 1% produces a negative intermediate value. Performance metrics and quoting decisions that depend on this estimate are incorrect.
**Verified by:** 3/3 lenses.

### H-6 · `alerting.py:192–209` · Loss-alert dedup flags read/written without lock
`_loss_80_fired` and `_loss_100_fired` are read and set in `check_daily_loss` without holding `self._lock`. Two threads calling `check_daily_loss` concurrently can both observe `_loss_100_fired = False`, both set it to `True`, and both fire the alert — defeating the one-shot dedup and potentially triggering duplicate notifications or side-effects.
**Verified by:** 3/3 lenses.

### H-7 · `betfair_api.py:139` · Circuit-open error is retried by tenacity, defeating the circuit breaker
When `_circuit.is_open()`, `_request` raises `_RateLimitError("Circuit open")`. `_RateLimitError` is listed in the tenacity `retry_if_exception_type` set. Tenacity retries up to 3 times, each time re-entering `_request`, re-checking the still-open circuit, and raising again. The circuit breaker provides zero protection against a degraded Betfair connection.
**Verified by:** 3/3 lenses.

### H-8 · `ibkr_api.py:152,220` · `reqMktData` subscriptions leak — exhausts IB subscription limit
`self.ib.reqMktData()` is called in a loop in `fetch_all_markets()` (line 152) and on every invocation of `get_market_price()` (line 220) with no corresponding `cancelMktData()` call. Each call opens a new market data subscription. IB Gateway enforces a per-account subscription limit (typically 100 concurrent); after that limit is reached, subsequent subscriptions silently fail, causing `get_market_price` to return stale or empty data for all markets beyond the limit.
**Verified by:** 3/3 lenses (with note that `snapshot=True` may auto-cancel in some IB API versions — verify empirically).

### H-9 · `backtest.py:181–184` · Duplicate snapshots cause P&L double-counting and bias live thresholds downward
The backtest replay loop processes every snapshot row with `net_profit > 0` without deduplicating opportunities snapshot-recorded multiple times per scan cycle. The same opportunity can be snapshot-recorded N times per scan interval. Replaying all N copies inflates win-rate and Sharpe, causing `build_recommendations()` to lower `MIN_NET_ROI` — a recommendation that then gets wired into live trading via `config.apply_backtest_recommendations()`.
**Verified by:** Completeness critic.

### H-10 · `treasury.py:246–251` · Hour-boundary retry bypasses idempotency for fund transfers
`_build_idempotency_key()` uses `int(time.time() // 3600)` as the replay-prevention bucket. A network retry that straddles an hour boundary (submitted at H:59:59, retried at H+1:00:00) generates two different idempotency keys and executes the transfer twice. For auto-rebalance fund movements, this results in a double transfer.
**Verified by:** Completeness critic.

### H-11 · `config.py:9` · `load_dotenv("~/.claude/.env")` loads from the Claude CLI config directory
Any process on the same host that can write `~/.claude/.env` (including Claude Code hooks, extensions, or a compromised shell session) can inject arbitrary env vars — including `POLYMARKET_PRIVATE_KEY`, `KALSHI_PRIVATE_KEY_PATH`, `GEMINI_API_SECRET` — into the scanner's runtime without touching the project `.env`.
**Verified by:** 3/3 lenses.

### H-12 · `dashboard.py:244` · HTTP auth silently disabled when `DASHBOARD_PASS` is empty
`_check_auth` returns `True` unconditionally when `DASHBOARD_PASS` is empty. A `WARNING` log fires only when `EXECUTION_MODE == "full-auto"`. In dry-run or semi-auto modes, and when `DASHBOARD_HOST=127.0.0.1`, `validate_config()` does not raise — the dashboard is fully unauthenticated with no startup signal.
**Verified by:** 3/3 lenses.

### H-13 · `gemini_api.py:26` / `gemini_api.py:79` · `GEMINI_BASE_URL` and proxy URL from env redirect all authenticated traffic
`GEMINI_BASE_URL` is read from env at module load and used as the base for all requests including private-key-signed ones. An attacker-controlled env var redirects HMAC-signed requests (exposing API key + signature) to an arbitrary host. `GEMINI_PROXY_URL` (line 79) has the same effect for all requests including `withdraw_usdc`.
**Verified by:** Security finder (1 lens). Fix: allowlist base URLs; validate proxy scheme.

### H-14 · `kalshi_api.py:52,63` · `unsafe_skip_rsa_key_validation=True` loads malformed private keys silently
Both `_load_private_key` and `_load_private_key_from_base64` pass `unsafe_skip_rsa_key_validation=True` to `load_pem_private_key`. This disables CRT parameter consistency validation, allowing a malformed or deliberately backdoored key to be accepted without error. In fault-injection attacks, incorrect CRT parameters can leak the private key through signature side channels.
**Verified by:** 2/3 lenses (PARTIAL — narrow but real risk).

### H-15 · `matchbook_api.py:112` · `_RateLimitError` from circuit-open propagates uncaught through all public methods
`_request()` raises `_RateLimitError` when the circuit is open, but `_request()` has no tenacity decorator. All callers (`fetch_all_events`, `fetch_event_markets`, `list_runners`, `get_order_status`, `get_market_status`, `get_balance`) do not catch `_RateLimitError`, so a circuit-open condition raises an unexpected exception through the entire Matchbook API surface, which the executor and scan layer also do not catch.
**Verified by:** Bug-finder (1 lens — adversarial verification not run on this finding).

### H-16 · `gemini_api.py:228–229` · `requests.Timeout` silently swallowed in `_public_request`
`_public_request` catches `requests.exceptions.ConnectionError` (re-raises it for tenacity) but not `requests.Timeout`. A GET timeout falls through to the generic `requests.RequestException` handler, which returns `None` instead of re-raising. Tenacity's `retry_if_exception_type(..., requests.Timeout)` never fires, so public-endpoint timeouts are silently swallowed and callers receive `None` without retry.
**Verified by:** Bug-finder (background agent, 1 lens).

### H-17 · `credential_health.py:193–198` · asyncio timeout does not cancel blocking executor threads
`_async_call` wraps sync platform-client methods in `loop.run_in_executor(None, ...)` and applies `asyncio.wait_for(..., timeout=10.0)`. The asyncio timeout cancels the *coroutine* but not the underlying thread running in the thread pool. If a platform client's sync method hangs (TCP connect with no OS-level timeout), the thread leaks indefinitely, eventually exhausting the executor pool and starving the continuous-mode event loop.
**Verified by:** Completeness critic.

### H-18 · `finnhub_api.py:84` · Finnhub API key exposed in URL query string
`params={"token": self.api_key}` causes the API key to appear as a URL query parameter in HTTP access logs at Finnhub's servers, any intermediate proxy logs, and any monitoring that captures full request URLs.
**Verified by:** 2/3 lenses.

### H-19 · `notifier.py:196–203` · CallMeBot API key and phone number in GET URL
`CALLMEBOT_APIKEY` and `CALLMEBOT_PHONE` are passed as query parameters in a GET request to the third-party CallMeBot service. Both values appear in CallMeBot's access logs and any proxy or network monitoring capturing URLs.
**Verified by:** 2/3 lenses.

---

## MEDIUM — Fix Within Sprint

### M-1 · `db.py:231–250` · Trade status update is not atomic — crash between two SQL statements corrupts DB
`update_trade_status` runs two separate `UPDATE` statements (status+fill_price, then slippage) without wrapping them in an explicit `BEGIN TRANSACTION`. A process crash between the two leaves a trade with its status updated but slippage missing, causing permanent DB inconsistency that `recovery.py` cannot reconcile.

### M-2 · `db.py:954–963` · `strftime('%s')` not portable across SQLite builds
The `get_transfers_today` query uses `strftime('%s', timestamp)` to compute a Unix epoch. `%s` is a SQLite extension not supported on all platforms (notably Windows-built SQLite and some embedded builds). On unsupported platforms, `strftime('%s', ...)` returns NULL for every row, causing the daily transfer limit to always appear as zero (unlimited transfers).

### M-3 · `risk_manager.py:94` · Cross-platform balance check assumes two equal-cost legs
`half_cost = trade_cost / 2` is used to check both platforms in a cross-platform opportunity. For 3-leg triangular arbitrage, this divides the total cost in half instead of by 3, potentially allowing trades that exceed per-platform balance.

### M-4 · `hedger.py:114` · `max_loss` approaches zero for low-price positions, blocking all hedges
`max_loss = fill_price * HEDGE_MAX_SPREAD_LOSS_PCT` — for a fill at $0.01, `max_loss` ≈ $0.001 (a fraction of a cent). Any non-trivial ask spread exceeds this floor and blocks every hedge attempt, leaving the position fully unhedged on a partial fill.

### M-5 · `recovery.py:262–263` · Orphaned-trade recovery passes exchange order ID as Polymarket token ID
`_convert_orphans_to_partial_fills` calls `log_partial_fill(token_id=trade.get("order_id", ""))`. For Polymarket legs, `partial_fills.token_id` is the CLOB token ID used by `_hedge_polymarket` to fetch the opposing order book. Passing the Betfair/Kalshi/Matchbook exchange order ID here causes Polymarket hedge lookups to fail silently.

### M-6 · `gas_monitor.py:103,247` · TOCTOU race in gas price and MATIC price fetches
The lock is released between the staleness check and the fetch; then re-acquired for the write. Two concurrent threads can both see a stale cache, both fetch independently, and the slower thread's result can overwrite the faster thread's newer result with a misleadingly recent timestamp.

### M-7 · `dashboard.py:183,225–229` · Pause state read without `_pause_lock`
`is_paused()` and `get_pause_state()` read `_paused`, `_pause_reason`, and `_pause_timestamp` without holding `_pause_lock`. A concurrent `pause()`/`resume()` call can produce a torn read (e.g., `_paused=True` with already-cleared `_pause_reason`).

### M-8 · `metrics.py:325–330` · Counter creation has unsynchronized outer check
`_get_or_create_counter` checks `name not in self._counters` outside the lock before acquiring `self._lock`. Two threads can both pass the outer check, contend on the lock, and one overwrites the other's freshly-created counter, losing any increments already recorded on it.

### M-9 · `scans/helpers.py:126–166` · Shared `price_cache` written from WebSocket threads without a lock
The module-level `price_cache` dict is written from WebSocket callback threads and read from scan threads without any synchronization. A partially-written cache entry (stale `_ts` alongside new prices) can pass the freshness gate and feed corrupt prices into execution-critical profit calculations.

### M-10 · `db.py:162,772` · Table/column names interpolated into SQL via f-strings
`ALTER TABLE ... ADD COLUMN {col}` (line 162) and `SELECT ... FROM {table}` (line 772) use f-string interpolation. Both are currently safe (hardcoded tuples), but the pattern will cause SQL injection if a future contributor extends the tuple with an externally-sourced value.

### M-11 · `dashboard.py:264` · HTTP Basic Auth uses `==` — timing side-channel
`user == DASHBOARD_USER and pwd == DASHBOARD_PASS` is not constant-time. Should use `hmac.compare_digest`.

### M-12 · `metrics.py:98–103` · Prometheus label values not sanitized for newlines
`_labels_to_prom` embeds label values via f-string without escaping `\n` or `"`. A newline in a market name or platform name (sourced from external API responses) injects arbitrary lines into the `/metrics` Prometheus scrape endpoint.

### M-13 · `continuous.py:1820` · `DATA_DIR` + literal string — path traversal via env var
`db_path = config.DATA_DIR + "/trades.db"` without `os.path.abspath` or containment check. A `DATA_DIR` set to a path containing `..` redirects the primary trade database to an unintended location.

### M-14 · `notifier.py:33,53` · Shared `requests.Session` across threads; daemon threads drop notifications on exit
(a) `notify()`, `notify_promo_warning()`, and `notify_partial_fill()` all spawn concurrent threads sharing `self._session`; `requests.Session` is not thread-safe for concurrent use across threads. (b) The spawned threads are daemon threads — in one-shot mode where `main()` returns before the thread completes, notifications are silently dropped.

### M-15 · `kalshi_api.py:174` · Non-connection HTTP errors do not trip the circuit breaker
The `except requests.RequestException` branch at line 172 returns `None` without calling `_circuit.record_failure()`. SSL errors, `ChunkedEncodingError`, and other transport errors are silently ignored by the circuit breaker.

### M-16 · `polymarket_api.py:77` · Circuit-open condition itself records an additional failure
`_get_with_retry`'s bare `except Exception` catches the `_RateLimitError` raised when the circuit is open, calls `_circuit.record_failure()`, then re-raises. The already-open circuit accumulates additional failure counts on every in-flight request, potentially preventing auto-reset once the backoff window expires.

### M-17 · `backtest.py:793` · CLI `--db` argument used as SQLite path without validation
`SnapshotRecorder(db_path=args.db)` accepts the CLI argument directly. A value of `/etc/passwd` or any writable system path is silently opened and overwritten with SQLite DDL.

### M-18 · `scans/correlated.py:453–457` · Layer floor gate is inert — never rejects any opportunity
The Layer 4 floor check (`if (original_spread - live_spread) > layer_floor * original_spread`) has a `pass` body, making it completely inoperative. The stated purpose of rejecting opportunities where the spread has already compressed is never enforced.

### M-19 · `matchbook_api.py:155` · Pagination exits early when `total` field is absent (defaults to 0)
`total` from the first page is used as the loop termination condition. If the field is missing, `total = 0`, `offset >= total` is immediately true, and only the first page of events is ever fetched.

---

## LOW — Address When Convenient

| # | Location | Description |
|---|----------|-------------|
| L-1 | `ws_feeds.py:931` | `BetfairFeed._read_line()` uses default 64 KiB readuntil limit; adversarial stream with no `\r\n` can OOM the container |
| L-2 | `rate_limiter.py:63` | Circuit auto-resets directly to closed (not half-open) after timeout; no exponential backoff for repeatedly misbehaving platforms |
| L-3 | `betfair_api.py:157` | `record_failure()` called before tenacity re-raise; each retry of a transient error inflates the failure count, prematurely tripping the circuit |
| L-4 | `betfair_api.py:204,238,304,362` | `list_markets`, `list_runners`, `get_current_orders`, `get_order_status` all bypass circuit breaker via direct `session.post` |
| L-5 | `matchbook_api.py:284,353` | `place_order` and `cancel_order` bypass circuit breaker entirely |
| L-6 | `ibkr_api.py:268` | 100 ms sleep after `placeOrder` too short for order acknowledgment; returned status may be `""` or `"PreSubmitted"` |
| L-7 | `hedger.py:339–355` | `log_partial_fill` called with `trade_id=0`, `opportunity_id=-1`; FK violation if SQLite FK enforcement ever enabled; unauditable class of MM inventory hedges |
| L-8 | `scans/resolution.py:75` | Uses win-time fee model (deprecated March 2026); should use entry-time model consistent with `fees.py:_platform_win_fee` |
| L-9 | `scans/multi_cross.py:447` | `clob_ok` initialized to `True` and never set to `False` — `if not clob_ok: continue` is dead code |
| L-10 | `matcher.py:192` | Combined score > 100 always qualifies as HIGH confidence even when raw fuzzy score was marginal |
| L-11 | `alerting.py:354` | Strategy that has never produced an opportunity defaults `last_opp = now`, so idle alert never fires for it |
| L-12 | `finnhub_api.py:104` | `resp.json()` called up to 3 times on same response; should be parsed once to a variable |
| L-13 | `polygonscan_api.py:119–121` | Broad `except Exception` swallows re-raised `requests.ConnectionError`, breaking tenacity retry |
| L-14 | `credential_health.py:175–184` | Keyword `"invalid"` too broad — matches `"invalid market"` etc., causing false-positive credential-failure alerts |
| L-15 | `snapshot.py:18–23` | Missing `DATA_DIR` silently falls back to `.` (repo root), writing `snapshots.db` alongside source code |
| L-16 | `treasury.py:142–149` | `get_transfers_today` query doesn't filter `amount_usd > 0`; negative-amount rows can bypass daily transfer cap |
| L-17 | `db.py:231–250` | `update_trade_status` slippage column update is outside the lock-held block — two lock acquisitions for one logical update |
| L-18 | `continuous.py:341` | Kalshi market ticker from DB interpolated directly into REST API path — HTTP path traversal if DB is poisoned |
| L-19 | `config.py:634` | `CRYPTO_PRICE_API_URL` has no URL allowlist; SSRF via env var to internal metadata endpoints |
| L-20 | `gas_monitor.py:267–274` | CoinGecko MATIC/USD price fetch has no auth or response integrity check; DNS hijack can set near-zero gas cost |

---

## Summary Statistics

| Severity | Count |
|----------|-------|
| CRITICAL | 6 |
| HIGH | 19 |
| MEDIUM | 19 |
| LOW | 20 |
| **Total confirmed** | **64** |
| False positives rejected | 10 |

**Highest-risk cluster:** The Gemini `withdraw_usdc` path (C-2, C-5, H-13) combines no address validation, an env-var-controlled destination, and a proxy URL with no allowlist — all three hitting irreversible fund transfers. Fix this cluster first.

**Second priority:** Continuous-mode reliability (C-1 NameError crash, C-3 signal handler, C-4 WebSocket message injection, H-3 CLOB refine never filters, H-7 Betfair circuit breaker defeated) — any of these can cause the bot to stop detecting/executing opportunities silently.

**Third priority:** Fee/profit calculation correctness (H-1 EV formula swapped, H-2 event monitor contamination, H-4 correlated net_profit no fees, H-9 backtest double-counting) — these cause the scanner to act on systematically wrong profitability estimates.
