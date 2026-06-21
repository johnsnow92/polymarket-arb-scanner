# Codebase Audit Report — polymarket-arb-scanner

**Date:** 2026-06-21  
**Dimensions:** Bugs · Security  
**Method:** Scout → fan-out finders (26 agents, 95 source modules) → 3-lens adversarial verification → completeness critic → ranked synthesis  
**Status:** Read-only. No code was modified.

---

## CRITICAL

### C1 — `continuous.py:1543` | `FINNHUB_API_KEY` NameError crashes news-snipe at runtime
**Severity:** critical · Bug · Uncontested  
`continuous.py` references `FINNHUB_API_KEY` as a bare name in the news-snipe branch but never imports it from `config`. Every activation of the news-snipe continuous-mode path raises `NameError`, crashing the async event loop. `cli.py` imports it correctly; `continuous.py` does not.

### C2 — `market_discovery.py:554` | `KalshiClient()` used without authentication — crashes on first API call
**Severity:** critical · Bug · Confirmed (Verifier 1)  
`KalshiClient()` constructor sets `self.private_key = None`. When `fetch_all_events()` is called, `_auth_headers()` → `_sign_pss(None, msg)` → `None.sign(...)` → `AttributeError`. The live discovery run fails immediately.

### C3 — `latency_monitor.py:366–373` | Self-deadlock when `GEOGRAPHIC_LATENCY_ENABLED=true`
**Severity:** critical · Bug · Confirmed (Verifier 1)  
`should_route_externally()` acquires `self._lock` then calls `get_best_region()`, which also tries to acquire `self._lock`. Python's `threading.Lock` is not reentrant — the calling thread blocks forever. Any execution path that uses geographic routing while the feature flag is on deadlocks the routing layer.

### C4 — `treasury.py:233–237` | No validation on `GEMINI_DEPOSIT_ADDRESS` — env injection redirects USDC
**Severity:** critical · Security · Uncontested  
`GEMINI_DEPOSIT_ADDRESS` is read from env with no format validation (no checksum, no length check, no allowlist). A misconfigured or injected Railway env var silently routes real USDC bridge withdrawals to an arbitrary address with no error or alert.

---

## HIGH — Bugs

### H1 — `scans/cross.py:537,621` | Cross-all CLOB refinement result never applied
**Severity:** high · Bug · Confirmed (Verifiers 1 & 3)  
`_refine_cross_all_with_clob()` builds `refined_out = []` but never populates or returns it. The caller at line 537 does not capture the return value. Opportunities that fail ask-price checks remain in the output with inflated mid-price profits and proceed to the executor.

### H2 — `scans/triangular.py:411–430` | Triangular CLOB refinement reads fields that `scan_triangular` never sets — always 0
**Severity:** high · Bug · Confirmed (Verifier 2)  
`scan_triangular` sets `_platform_a`/`_platform_b` but not `_price_a`/`_price_b`. The refiner reads `o.get("_price_b", 0)` → `0` for every `TriangularCross` opp. All refinement profit calculations use 0 as the other-platform leg price, making CLOB refinement meaningless.

### H3 — `backtest.py:322–330` | Multi-leg fee functions called with only 2-leg prices
**Severity:** high · Bug · Confirmed (Verifier 3)  
`net_profit_negrisk_internal([price_a, price_b])` and `net_profit_kalshi_multi([price_a, price_b])` expect full multi-leg price lists (3–5 legs). Only 2 prices are stored per snapshot. `net_profit_triangular` also receives only 2 prices for a 3-leg structure. All NegRisk, KalshiMulti, and Triangular backtest P&L records are systematically wrong, corrupting Strategy #20 tuning recommendations.

### H4 — `recovery.py:266` | `token_id` populated from `order_id` DB column — hedges fail after crash recovery
**Severity:** high · Bug · Confirmed (Verifier 3)  
`_convert_orphans_to_partial_fills` uses `trade.get("order_id", "")` for the `token_id` parameter. The hedger needs the CLOB token identifier to place opposing orders; an order reference ID causes all post-recovery hedge placements to silently fail, leaving partial positions unhedged.

### H5 — `executor.py:2652` | `_whale_copy_position_count` never decremented — permanently blocks WhaleCopy
**Severity:** high · Bug · Uncontested  
Counter is incremented on every successful WhaleCopy fill but never decremented when positions close or settle. After `MAX_WHALE_COPY_POSITIONS` fills, no further WhaleCopy opportunities execute — permanently, for the process lifetime.

### H6 — `continuous.py:737,861,895` | `asyncio.Event` created at module scope; `get_event_loop()` called from background thread
**Severity:** high · Bug · Uncontested  
`asyncio.Event()` constructed outside `asyncio.run()` binds to no loop in Python 3.10+. `asyncio.get_event_loop()` from WebSocket callback threads returns the wrong or closed loop. Both patterns cause silent mis-wiring of price-update triggers in continuous mode.

### H7 — `ibkr_api.py:150–167` | `reqMktData` snapshots never cancelled — subscriptions accumulate until TWS limit
**Severity:** high · Bug · Confirmed (Verifier 3)  
Each call to `fetch_all_markets` adds market data subscriptions without calling `cancelMktData`. TWS enforces a 100-subscription hard limit. After enough scan cycles all IBKR market data requests fail silently with error 354, zeroing all IBKR price feeds.

### H8 — `fees.py:492–494` | Betfair back-all commission charged on wrong leg
**Severity:** high · Bug · Uncontested  
Commission is applied to `1.0 - cheapest_implied_prob` instead of `gross_spread = 1 - total_cost`. Systematically understates Betfair commission when the cheapest leg is not the net-profit leg, overstating net profit for all back-all arbs.

### H9 — `risk_manager.py:133,136` | Dedup keyed on display title; re-entry bypass when `existing_pnl ≤ 0`
**Severity:** high · Bug · Confirmed  
(a) `opportunity.get("market", "")` — the truncated human-readable title — is used as dedup key. Title variants of the same underlying market both pass. (b) When `existing_pnl ≤ 0`, `net_profit > existing_pnl * (1 + threshold)` is satisfied by any positive `net_profit`, unconditionally bypassing position dedup.

### H10 — `hedger.py:188` | Pending-hedge reads wrong DB field keys (`_market_id` vs `market_id`)
**Severity:** high · Bug · Uncontested  
`process_pending_hedges` reads `pf.get("_market_id", "")` (underscore-prefixed) but the `partial_fills` table stores the column as `market_id`. The guard `if not market_id` fires for every non-Polymarket hedge, silently skipping all pending hedges.

### H11 — `smarkets_api.py:252–253` | NO price calculation inverted — Smarkets lay price mis-mapped
**Severity:** high · Bug · Confirmed (Verifier 3)  
A lay at 45% means the layer accepts a 45% NO probability. The code returns `no_price = 1.0 − 0.45 = 0.55` — which is the YES price — as the NO price. All Smarkets cross-platform arb valuations use the wrong side, generating inverted or phantom opportunities.

### H12 — `alerting.py:324–325` | Loss-streak alert fires only at exactly 3 losses — silent thereafter
**Severity:** high · Bug · Confirmed (Verifier 3)  
`if losses == 3:` is exact equality. A strategy accumulating 4, 5, or more consecutive losses never fires an alert. Runaway losses past the 3rd go silently unnoticed by the operator.

### H13 — `event_monitor.py:271` | Platform price self-included in consensus used to compute divergence against same platform
**Severity:** high · Bug · Confirmed (Verifier 3)  
`add_signal(market_key, platform_name, platform_price)` is called, then `get_consensus()` is used as `consensus_prob`. Divergence is computed against a consensus that already contains `platform_price`, systematically understating divergence and suppressing real Layer 4 opportunities.

### H14 — `gemini_api.py:193–196` | `requests.Timeout` swallowed — Gemini requests never retried on timeout
**Severity:** high · Bug · Confirmed (Verifiers 2 & 3)  
The `@retry` decorator lists `requests.Timeout` as a retry trigger, but `except requests.RequestException` catches it first and returns `None` without re-raising. Tenacity never sees the exception. A single timeout silently drops the entire Gemini scan result.

### H15 — `scans/cross_mm.py:91` | `price_b = 1 − sell_price` incorrectly inverts the sell-side price before fee function
**Severity:** high · Bug · Uncontested  
For a cross-MM opportunity the sell price is passed as `1 − sell_price` to `net_profit_cross_generic`, inverting the sell leg's implied probability. The fee function receives a complement price, producing wrong costs and net_spread.

### H16 — `superforecaster_api.py:269` | `MetaculusClient.get_community_prediction_by_title()` does not exist — AttributeError at runtime
**Severity:** high · Bug · Confirmed (completeness critic)  
`metaculus_client.get_community_prediction_by_title(market_title)` is called, but `MetaculusClient` exposes no such method. When `EXPERT_DIVERGENCE_ENABLED=true` with a Metaculus client configured, this raises `AttributeError` at every call. The exception is caught by the outer `try/except Exception` block, silently suppressing the entire Metaculus signal path for expert-divergence.

---

## HIGH — Security

### S1 — `dashboard.py:321–327` | `TradeDB` opened per HTTP request and never closed — file-descriptor leak
**Severity:** high · Security · Uncontested  
`_get_db()` constructs a new `TradeDB()`/SQLite connection for each of 16 API endpoints per refresh cycle with no `close()`. Under Railway's 15 s refresh interval this exhausts OS file descriptors over time, eventually crashing the dashboard and health check endpoint.

### S2 — `hedger.py:225–240` | SX Bet unsigned hedge order placed despite documented read-only constraint
**Severity:** high · Security · Confirmed (Verifier 1)  
`_hedge_sxbet` calls `sxbet_client.place_order()` with no guard against SX Bet's read-only status. The `validate_config()` guard only blocks SX Bet in the main execution path when `ENABLED_EXECUTION_PLATFORMS` is configured. A partial fill from any SX Bet leg triggers the hedger regardless, sending an unsigned JSON order.

### S3 — `continuous.py:344,356` | DB-sourced `market_id` embedded in API URL paths without encoding
**Severity:** high · Security · Uncontested  
`/markets/{market_id}` and `{GAMMA_BASE}/markets/{market_id}` — if the `trades` table contains a corrupted or injected ticker string, this becomes a path traversal against the platform REST API.

### S4 — SSRF via unvalidated proxy/base-URL env vars across multiple API clients
**Severity:** high · Security · Uncontested  
`BETFAIR_PROXY_URL` (`betfair_api.py:50`), `SXBET_API_BASE_URL` (`sxbet_api.py:16`), `SXBET_PROXY_URL` (`sxbet_api.py:53`), `MATCHBOOK_PROXY_URL` (`matchbook_api.py:44`), `POLYMARKET_PROXY_URL` (`polymarket_api.py:34`) — all applied to HTTP sessions without URL validation. A misconfigured Railway env var redirects authenticated trading traffic through an attacker-controlled host.

### S5 — `sxbet_api.py:61` | Ethereum private key stored as plain `str` attribute on `SxBetClient` instance
**Severity:** high · Security · Confirmed (Verifier 2)  
`self.private_key: str | None` holds the raw hex key for the object lifetime. Any stack trace, debug dump, or repr exposes the key in cleartext.

### S6 — `gemini_api.py:565` | `withdraw_usdc` sends funds to caller-supplied address with no format validation
**Severity:** high · Security · Confirmed (Verifier 2)  
Only an empty-string check and `amount > 0` guard the address parameter. No checksum, no length check, no allowlist comparison. A typo or upstream API-sourced address routes USDC to an unintended wallet.

### S7 — `notifier.py:204` | Telegram bot token in URL path — leaked to logs, exceptions, and HTTP intermediaries
**Severity:** high · Security · Uncontested  
The URL `https://api.telegram.org/bot{token}/sendMessage` appears in DEBUG logs and HTTP exception messages. Any log aggregator or TLS-terminating proxy receives the full bot token.

### S8 — `dashboard.py:747–748` | Direct `db.conn.execute()` from HTTP handler bypasses `TradeDB` lock
**Severity:** high · Security · Uncontested  
`_handle_validation()` accesses `db.conn` directly while a scanner thread may be mid-write on the same connection object. Causes `sqlite3.ProgrammingError` or corrupted query results under concurrency.

### S9 — `risk_manager.py:133` | Dedup bypass via market title manipulation allows unbounded re-entry
**Severity:** high · Security · Confirmed  
Human-readable market title used as dedup identity key. A crafted opportunity dict with a title variant for the same underlying bypasses `is_market_active`, allowing unlimited re-entry on the same economic position.

---

## MEDIUM — Bugs (selected)

| ID | Location | Description |
|---|---|---|
| M1 | `ws_feeds.py:387–396,511–513` | `_pending_kalshi_subs`/`_pending_poly_subs` mutated from async loop and scan thread without lock — dropped subscriptions or `IndexError` |
| M2 | `scans/correlated.py:283` | `net_profit` set to pre-fee gross spread — executor may gate on overstated profit |
| M3 | `scans/correlated.py:453–457` | Layer 4 spread-collapse safety gate is a dead `pass` statement — check never executes |
| M4 | `scans/kalshi.py:172–174,297–301` | `future.result()` without `try/except` — any fetch exception crashes the entire depth-fetch stage |
| M5 | `calibration_tracker.py:104–106` | `_in_memory_cache` iterated and mutated outside `self._lock` — `RuntimeError` under concurrency |
| M6 | `db.py:993–1001` | `strftime('%s', timestamp)` is Linux-only SQLite — silently returns no rows on macOS/Windows |
| M7 | `alerting.py:192–208` | `_loss_80_fired`/`_loss_100_fired` flags read/written outside `_lock` — duplicate 80%/100% alerts under concurrency |
| M8 | `snapshot.py:222–223` | All `Cross*`-type snapshots hard-coded to `(polymarket, kalshi)` platform pair — corrupts metadata for all other cross-arb pairs |
| M9 | `signal_aggregator.py:231–248` | First search result from Metaculus/Manifold used without title similarity validation — unrelated question silently substitutes its probability |
| M10 | `reddit_api.py:40`, `matchbook_api.py:35` | `time.sleep()` called while holding `_rate_lock` — serializes all concurrent API callers for full sleep duration |
| M11 | `ibkr_api.py:130` | `underConId or conId` — zero `underConId` treated as falsy, every contract grouped as solo event instead of shared parent |
| M12 | `ws_feeds.py:241` | New WS feed with no messages defaults `last_msg_time` to `now` — feed failure on startup never marked stale |
| M13 | `recovery.py:79–93` | TOCTOU: `get_trades_for_opportunity` status read outside lock; concurrent thread can fill same trade, causing double-processing |
| M14 | `scans/settlement_timing.py:254–255` | `_refine_settlement_with_clob`: `opp.get("_slow_market")` can be `None` — unhandled `AttributeError` crash |
| M15 | `scans/bracket.py:124–136` | `_brackets_are_complete` returns `True` early when `upper_bound=inf` appears mid-sequence — falsely declares incomplete bracket set complete |
| M16 | `scans/bracket.py:173–177` | Zero-price brackets silently excluded from Stage 1 cost but included in CLOB refinement — Stage 1/2 bracket counts diverge |
| M17 | `twitter_api.py:250–252` | Weighted counts accumulated as floats, then `int()` truncated — reported counts inconsistent with float-computed `sentiment_score` |
| M18 | `treasury.py:168` | Idempotency key uses 1-hour time bucket — two same-amount transfers in same hour collide on UNIQUE constraint, blocking legitimate retry |
| M19 | `backtest.py:519` | `datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"` — naive datetime with literal Z suffix is not valid ISO-8601 |
| M20 | `gas_monitor.py:170` | When both platforms are off-chain and have 0% fees, `get_effective_threshold` returns `0.0` — approves all zero-profit trades |

## MEDIUM — Security (selected)

| ID | Location | Description |
|---|---|---|
| MS1 | `dashboard.py:400–404` | POST body read unbounded via `Content-Length` — OOM/DoS |
| MS2 | `url_guard.py:46–47` | SSRF bypass flag `ALLOW_PRIVATE_INTERNAL_URLS` re-read from env on every call — togglable at runtime without restart |
| MS3 | `metrics.py:102` | Prometheus label values unescaped — label injection via platform names/tickers corrupts `/metrics` output |
| MS4 | `dashboard.py:378–379` | Internal exception messages serialized into 500 JSON response — leaks file paths and DB schema |
| MS5 | `scans/imbalance.py:74–77,95–98` | CLOB unavailability degrades to keeping unvalidated Stage 1 candidates — spoofed mid-prices reach execution |
| MS6 | `notifier.py:40,47` | Telegram/CallMeBot URL prefix check bypassed by `telegram.attacker.com` — skips `assert_public_url` SSRF guard |
| MS7 | `market_discovery.py:350` | LLM-generated `pair_id` with embedded `|` chars corrupts cache market ID mapping |
| MS8 | `kalshi_api.py:52,63` | `unsafe_skip_rsa_key_validation=True` bypasses key integrity checks at load time *(Verifier 2: operational workaround)* |
| MS9 | `hedger.py:192–194` | `side` field from DB used without allowlist validation before `place_orders` — inversion turns hedge into position-doubling trade |

---

## Rejected Findings (false positives)

| Finding | Reason for Rejection |
|---|---|
| `recovery.py:250-253` deadlock | `with db._lock:` closes after `fetchall()`, before `log_partial_fill` is called — no contention |
| `scans/resolution.py:127-128` timestamps /1000 | String timestamps take ISO parse branch; `/1000` only applies to numeric ms-epoch integers |
| `dashboard.py:244-251` no auth in full-auto | `do_POST` fail-closes independently when `DASHBOARD_PASS` is empty — state-changing POSTs are hard-blocked |
| `ws_feeds.py:348` local time for Kalshi auth | `datetime.now().timestamp()` returns correct POSIX epoch regardless of timezone awareness |
| `executor.py:217` DATA_DIR path traversal | Operator-set deployment env var, not user-supplied input; not an exploitable attack surface |
| `scans/betfair.py:57` unbounded list_market_books | `betfair_api.py` already batches at 10 per call — Betfair's 200-item limit is never reached |
| `event_monitor.py:341-342` net_roi string vs float | `executor.py` explicitly handles string `net_roi` format with `replace("%", "")` conversion |
| `scans/correlated.py:198-200` sorted key swaps roles | Long/short direction re-derived from live prices, not key iteration order — economically correct |

---

## Verification Matrix (top findings)

| Finding | Skeptic | Defender | Impact | Verdict |
|---|---|---|---|---|
| `market_discovery.py:554` KalshiClient no auth | CONFIRM | — | — | ✅ |
| `latency_monitor.py:366` deadlock | CONFIRM | — | — | ✅ |
| `scans/cross.py` CLOB refinement ignored | CONFIRM | — | CONFIRM | ✅ |
| `hedger.py` sxbet unsigned | CONFIRM | — | — | ✅ |
| `gemini_api.py` withdraw unvalidated addr | — | CONFIRM | — | ✅ |
| `gemini_api.py` Timeout swallowed | — | CONFIRM | — | ✅ |
| `scans/triangular.py` _price_a never set | — | CONFIRM | — | ✅ |
| `ws_feeds.py` _pending_kalshi_subs race | — | CONFIRM | — | ✅ |
| `alerting.py` loss streak fires at 3 only | — | — | CONFIRM | ✅ |
| `event_monitor.py` self-referential consensus | — | — | CONFIRM | ✅ |
| `smarkets_api.py` NO price inverted | — | — | CONFIRM | ✅ |
| `ibkr_api.py` cancelMktData missing | — | — | CONFIRM | ✅ |
| `backtest.py` 2-leg prices for multi-leg | — | — | CONFIRM | ✅ |
| `recovery.py:266` token_id from order_id | — | — | CONFIRM | ✅ |
| `scans/correlated.py` sorted key role swap | — | REJECT | — | ❌ |
| `scans/resolution.py` timestamps /1000 | REJECT | — | — | ❌ |
| `dashboard.py` POST no size limit | — | REJECT (auth first) | — | ❌ |
| `event_monitor.py` net_roi string | — | — | REJECT | ❌ |
| `kalshi_api.py` unsafe_skip RSA | — | REJECT (workaround) | — | ⚠️ partial |

---

## Top Remediation Priorities

1. **Fix `continuous.py:1543`** — add `from config import FINNHUB_API_KEY` (one line; critical runtime crash)
2. **Fix `latency_monitor.py:366–373`** — extract `get_best_region` call outside the `with self._lock:` block
3. **Fix `market_discovery.py:554`** — call `kalshi_client.login_with_api_key()` before `fetch_all_events()`
4. **Fix `treasury.py:233`** — validate `GEMINI_DEPOSIT_ADDRESS` format (checksummed Ethereum address) at startup in `validate_config()`
5. **Fix `scans/cross.py:537`** — capture and apply the return value of `_refine_cross_all_with_clob`
6. **Fix `recovery.py:266`** — change `trade.get("order_id", "")` to `trade.get("token_id", "")`
7. **Fix `smarkets_api.py:252`** — change `no_price = 1.0 - lay_pct` to `no_price = lay_pct` (lay probability IS the NO price)
8. **Fix `hedger.py:188`** — change `pf.get("_market_id", "")` to `pf.get("market_id", "")`
9. **Fix `hedger.py:225–240`** — add guard: `if platform == "sxbet": logger.warning(...); return` (matches existing read-only policy)
10. **Fix `executor.py:2652`** — decrement `_whale_copy_position_count` in the position-close/settle path
