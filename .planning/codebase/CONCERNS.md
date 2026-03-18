# Codebase Concerns

**Analysis Date:** 2025-03-17

## Tech Debt

**Oversized modules lacking separation of concerns:**
- Files: `executor.py` (2247 lines), `continuous.py` (1310 lines), `cli.py` (1155 lines)
- Issue: Core business logic intertwined with orchestration, making modifications risky. `executor.py` alone handles risk checks, price revalidation across 8 platforms, leg building, execution sequencing, hedging coordination, and DB persistence.
- Impact: Bug fixes in one area risk breaking another. Adding new platforms requires touching multiple 1000+ line files.
- Fix approach: Extract platform-specific execution into a platform dispatcher factory. Move revalidation logic into separate strategy classes per opportunity type.

**Revalidation logic fragmented across executor:**
- Files: `executor.py` (lines 450–650 spread across `_revalidate_binary`, `_revalidate_negrisk`, `_revalidate_kalshi_binary`, `_revalidate_kalshi_multi`, `_revalidate_triangular`, `_revalidate_multi_cross`)
- Issue: Eight distinct `_revalidate_*` methods with significant duplication. Each re-implements price-fetch → profit-recalc → threshold-check. Threshold calculation itself has three variants.
- Impact: Inconsistent revalidation behavior across opportunity types. Adding a new strategy requires copy-paste + manual testing.
- Fix approach: Create a `RevalidationStrategy` base class with `fetch_prices()` → `recalculate_profit()` → `meets_threshold()` pipeline. Subclass per opportunity type.

**Price cache eviction and staleness detection split across modules:**
- Files: `continuous.py` (lines 673–682, 624–650), `ws_feeds.py` (staleness check on line 93), `executor.py` (WS_CACHE_MAX_AGE_REVALIDATION on line 14)
- Issue: No single source of truth for "how old is too old?" Config values live in different files. `continuous.py` evicts stale entries every scan cycle, but WS feeds also track `_last_message_time` per platform with no coordination.
- Impact: Race conditions possible: a price could be marked stale in cache but live in WS handler. WS feeds may keep sending stale data if handler crashes between updates.
- Fix approach: Centralize cache metadata into a `PriceCache` class that owns eviction policy, staleness detection, and WS handler coordination. Single expiry source.

**String parsing for prices and costs:**
- Files: `risk_manager.py` (lines 54–55, 94–95), `executor.py` (multiple locations with identical parsing)
- Issue: Total cost comes in as `"$0.95"` string and gets parsed with `.replace("$", "")` in multiple places. No validation that parsing succeeded. If string format changes, silent failures.
- Impact: Wrong position sizes due to parsing failures. No audit trail of what was parsed as what.
- Fix approach: Create a `Cost` value type with guaranteed safe parsing. Parse once at opportunity creation, pass as float everywhere.

## Known Bugs

**Flaky time-sensitive test:**
- Symptom: `tests/test_helpers.py::TestWithinResolutionWindow::test_uses_config_default` fails intermittently depending on system clock
- Files: `tests/test_helpers.py`
- Trigger: Run test at 23:59:59, crosses midnight during execution
- Workaround: Run with `-x` to stop on first failure, re-run immediately
- Root cause: Test uses `datetime.now()` without freezing time; resolution window logic is time-dependent

**Partial fill hedge logic may double-sell:**
- Symptom: "Already trading this market" error after a partial fill hedge succeeds
- Files: `hedger.py` (lines 59–83), `executor.py` (lines 1566–1593)
- Trigger: (1) Both legs execute, one partial fill occurs, (2) hedge sells filled leg, (3) market enters active markets dedup check with original opp still in DB
- Cause: Partial fill creates a separate hedge position in DB without closing the original opportunity record. Risk manager sees the original opp as "still active market" and blocks re-entry.
- Fix approach: Mark original opportunity as "partial-filled" in DB when hedge is queued. Dedup check must skip "partial-filled" status.

**IBKR BUY-only constraint not enforced at execution time:**
- Symptom: Executor may attempt to sell on IBKR, which fails
- Files: `executor.py` (lines 1775–1850 in `_execute_single_leg`), `ibkr_api.py` (no sell method exists)
- Trigger: A triangular/cross-platform opportunity tries to use IBKR as the sell leg
- Cause: Risk manager and scan layers assume all platforms are symmetric. IBKR is only for YES legs (BUY). No pre-execution check.
- Fix approach: Add platform capability matrix to config. Risk check rejects opportunities that require unsupported platform/side combinations before executor runs.

**Recovery reconciliation may miss filled orders:**
- Symptom: Orphaned positions reported as "unknown status" even though they actually filled
- Files: `recovery.py` (lines 76–100), `polymarket_api.py`, `kalshi_api.py` (order lookup methods)
- Trigger: Order fills during the crash. Recovery attempts `fetch_order()` but API returns 404 because order age exceeds retention window (e.g., Polymarket deletes 7-day-old orders)
- Cause: No retry logic with exponential backoff for transient API failures. Single query determines "unknown" status.
- Fix approach: Add 3-attempt retry with 1s backoff to order status checks. If API returns "not found" after retries, assume filled (safer assumption than unknown).

**Price cache not thread-safe with concurrent execution:**
- Symptom: Stale/inconsistent prices used in revalidation despite WS updates happening in parallel
- Files: `continuous.py` (lines 624–650 using plain dict), `executor.py` (lines 94 stores reference), `ws_feeds.py` (updates from async thread)
- Trigger: WS feed updates price at same millisecond executor reads it for revalidation. Dict not locked during read in executor.
- Cause: `price_cache` is a plain Python dict. Updates from `_on_price_update` (WS thread) and reads from executor (main thread) have no synchronization.
- Fix approach: Wrap price cache in a `threading.RLock()` in `continuous.py`. All reads/writes must hold lock. Or use `dict.copy()` snapshot for revalidation.

## Security Considerations

**API credentials logged in debug output:**
- Risk: Private keys, API secrets visible in log files if LOG_LEVEL=DEBUG
- Files: `cli.py` (line 927 logs "Authenticating with Kalshi (API key)..."), `kalshi_api.py` (line 107 logs "No Kalshi private key"), `gemini_api.py` (line 76 logs "Gemini credentials not provided")
- Current mitigation: Logs don't include actual key values. Presence/absence logged only.
- Recommendations: (1) Use structured logging with a secrets filter. (2) Never log variable contents if they could contain credentials. (3) Add pre-commit hook to scan logs for patterns like `key=`, `secret=`, `token=`.

**Order placement not idempotent:**
- Risk: Network timeout between order placement and confirmation loop could result in duplicate orders
- Files: `executor.py` (lines 1772–1850), platform trader implementations
- Current mitigation: None. Orders lack unique client-side IDs for deduplication.
- Recommendations: (1) Generate client-side `idempotency_key` per order (UUID). (2) Platform traders must support idempotent submission. (3) On retry, submit with same key. (4) Kalshi and Polymarket CLOBs may already support this — verify and use.

**Webhook URL stored in plaintext config:**
- Risk: WEBHOOK_URL in .env or config exposes alerting endpoint
- Files: `config.py` (line 292 WEBHOOK_URL), `notifier.py` (uses to POST alerts)
- Current mitigation: Environment variable only (not in code). User responsible for .env security.
- Recommendations: (1) Document .env.example without actual URL. (2) Use symmetric encryption for webhook URLs if stored in DB. (3) Rotate webhook URL on any suspected leak.

## Performance Bottlenecks

**N+1 order book fetches during revalidation:**
- Problem: Executor fetches order book for every leg in an opportunity during revalidation. For a 3-leg cross-platform opp, that's 3 sequential API calls.
- Files: `executor.py` (lines 500–520 in `_revalidate_kalshi_binary` and similar methods)
- Cause: Each revalidation method calls `fetch_order_book()` independently. No batching.
- Scalability: At 30s rescan interval with 100 active opportunities, worst case is 300 API calls per cycle. Kalshi/Polymarket may rate-limit.
- Improvement path: (1) Pre-fetch all order books in parallel before scanning. (2) Cache order books for 100ms. (3) Revalidation uses cache snapshot instead of live fetch.

**Fuzzy matcher runs on every cross-platform scan:**
- Problem: Every scan re-computes title similarity for 5000+ Polymarket × 100+ Kalshi event pairs
- Files: `matcher.py` (lines 150–250), called from `continuous.py` on every rescan
- Cause: No caching of match results. Title changes infrequent, but matcher runs O(n²) string comparisons.
- Scalability: With 5000 Polymarket markets and 100 Kalshi events, that's 500k comparisons per scan. At 30s intervals, 16.7k comparisons/sec.
- Improvement path: (1) Cache matches in DB with event ID pairs. (2) Invalidate only when market list changes. (3) Use bloom filter for non-matches to skip expensive comparisons.

**WebSocket subscriptions rebuild on every price update:**
- Problem: `OpportunityIndex` rebuilds subscription list from full opportunity set after every price trigger
- Files: `continuous.py` (lines 520–530, index rebuild called on every WS price update)
- Cause: Subscriptions are derived fresh from the index instead of being tracked incrementally.
- Scalability: With 100 concurrent opportunities, that's O(100) work per price update. At 1 update/ms, that's 100 full rebuilds/sec.
- Improvement path: (1) Track subscription adds/removes as deltas. (2) Batch subscription updates. (3) Update subscriptions only on full scan, not per price.

**Concurrent leg execution without thread pool bounding:**
- Problem: `ThreadPoolExecutor` for cross-platform execution created with default `max_workers` (os.cpu_count())
- Files: `executor.py` (lines 1480–1490, ThreadPoolExecutor created inline)
- Cause: No explicit thread pool sizing. On a 16-core machine, could create 16 threads per opportunity execution.
- Scalability: If 50 opportunities execute in parallel, that's 800 threads competing for order book locks.
- Improvement path: (1) Use single module-level ThreadPoolExecutor with 4 workers. (2) Queue leg executions instead of spawning threads per opp. (3) Monitor thread pool saturation.

## Fragile Areas

**Cross-platform match quality depends on title normalization:**
- Files: `matcher.py` (lines 33–46), `scans/multi_cross.py` (fuzzy match threshold)
- Why fragile: Markets with similar titles but different outcomes can false-match (e.g., "Will X happen before date Y?" vs "Will X happen after date Y?"). Normalization regex removes punctuation, which can hide negation.
- Safe modification: (1) Add semantic validation: check that matched outcomes are logically aligned (both YES = bullish on same event). (2) Require human review for MEDIUM/LOW confidence matches before execution. (3) Add outcome text to match scoring, not just titles.
- Test coverage: Matching tests exist, but no regression suite for false matches with real market data.

**Revalidation threshold adaptive floor may prevent legitimate re-entry:**
- Files: `executor.py` (lines 168–180, revalidation_min_floor = 0.3%)
- Why fragile: If original opportunity had 0.5% profit and price moved 0.2% against us, remaining profit is 0.3%. Adaptive floor check (profit >= original * 0.9) passes, but min_floor check (0.3% >= 0.3%) fails due to floating-point rounding.
- Safe modification: (1) Use integer cents for threshold comparisons, not floats. (2) Add 1-cent buffer: accept if profit >= floor + 0.01. (3) Log threshold calculations when they block execution (helps debug edge cases).
- Test coverage: `test_executor.py` has coverage for happy path, but missing edge cases for floating-point near-boundaries.

**Partial fill detection relies on list equality:**
- Files: `executor.py` (lines 1544–1545, `all(results.values())`)
- Why fragile: If one leg reports "pending" (polling timed out) instead of "filled", the whole trade is marked partial-fill and hedging is triggered. Network latency can cause false positives.
- Safe modification: (1) Distinguish "pending" (unknown status) from "failed". (2) Pending legs should trigger a retry loop, not immediate hedging. (3) Hedging only on confirmed failures. (4) Add idempotency to hedge attempts.
- Test coverage: `test_executor.py` mocks fills with simple True/False. Missing chaos tests with timeout scenarios.

## Scaling Limits

**WebSocket subscription limit at 2000 tokens:**
- Current capacity: 2000 Polymarket token IDs max per WS connection
- Limit: At 5000+ Polymarket markets, can only monitor 40% of opportunities in real-time
- Scaling path: (1) Split subscriptions across multiple WS connections (requires account/auth duplication). (2) Rotate subscriptions every 30s (trade latency for coverage). (3) Fall back to periodic polling for markets below subscription limit.

**Concurrent execution thread pool at 4 workers:**
- Current capacity: 4 simultaneous cross-platform trades
- Limit: If 100 opportunities detected simultaneously, 96 queue while waiting. Execution latency compounds.
- Scaling path: (1) Increase pool to 8–16 (CPU-bound + some I/O wait). (2) Add execution priority queue: high-ROI opportunities execute first. (3) Batch same-platform legs into parallel execution within single platform API calls.

**Database WAL mode file locking on high-write volume:**
- Current capacity: ~100 trades/day without contention
- Limit: Concurrent writes from executor + continuous mode + WebSocket triggers can cause `database is locked` errors at >10 writes/sec
- Scaling path: (1) Batch DB writes: buffer opportunities in memory, write once per 100ms. (2) Move to PostgreSQL for true concurrent writes. (3) Add exponential backoff retry for locked errors.

**Price cache unbounded growth in continuous mode:**
- Current capacity: ~1000 unique markets cached before eviction
- Limit: At 30-day continuous run with 5000 markets scanned, cache can accumulate 150k old entries
- Scaling path: (1) Implement LRU eviction (discard least-recently-used price). (2) Cap cache size at N entries. (3) Periodic full cache clear on full scan (current approach works but memory creeps).

## Dependencies at Risk

**py-clob-client pinned to pre-release version:**
- Risk: Unknown version number or unstable API. Breaking changes in CLOB API not tracked.
- Impact: Polymarket integration may break silently if CLOB API changes
- Migration plan: (1) Pin explicit version with date in requirements.txt. (2) Add integration test that places actual order on testnet. (3) Subscribe to Polymarket API changelog.

**ib_insync optional dependency with no fallback:**
- Risk: If `ib_insync` import fails silently, IBKR execution disabled but no alert
- Files: `ibkr_api.py` (line 71 catches ImportError only in `login()`)
- Impact: Opportunities detected but silently skipped at execution time
- Migration plan: (1) Check IBKR availability at startup, fail fast. (2) Exclude IBKR opportunities if client unavailable. (3) Log clearly that IBKR disabled.

**tenacity retry library not pinned to version:**
- Risk: tenacity 9.x changed retry decorator syntax. Code uses old API.
- Files: `betfair_api.py`, `kalshi_api.py`, `gemini_api.py` (all use `@retry` decorator)
- Impact: Upgrade could break all platform API calls
- Migration plan: Pin `tenacity==8.x` in requirements.txt. Schedule migration to 9.x with syntax updates.

**requests library proxy support incomplete:**
- Risk: POLYMARKET_PROXY_URL and KALSHI_PROXY_URL support HTTP proxies, but python-socks (SOCKS5) is optional
- Files: `ws_feeds.py` (lines 32–36), `polymarket_api.py` (proxy handling)
- Impact: In firewalled environments, WS feeds may fail to connect
- Migration plan: Document proxy support tier clearly. Add fallback from SOCKS5 → HTTP proxy if available.

## Missing Critical Features

**No concurrent market settlement tracking:**
- Problem: Continuous mode has no mechanism to detect market resolution and close positions automatically
- Blocks: Cannot run truly autonomous 24/7 trading — manual position closure required
- Implementation: (1) Monitor market settlement events via WS feeds. (2) Auto-settle positions at 100% resolution. (3) Log realized P&L.

**No position hedging or inventory limits:**
- Problem: Bot can accumulate directional exposure if it buys 5 "Biden wins" contracts across platforms
- Blocks: Risk management limited to per-opportunity checks, not portfolio-level hedging
- Implementation: (1) Track portfolio greeks (delta, vega by market). (2) Auto-hedge: if portfolio delta > threshold, place opposing orders. (3) Inventory rebalancing.

**No API rate limit awareness or backoff:**
- Problem: If Polymarket rate-limits at 100 req/sec, code has no adaptive backoff
- Blocks: Scan loops may trigger rate limits, causing cascading failures
- Implementation: (1) Parse rate limit headers from API responses. (2) Implement token bucket or leaky bucket rate limiting. (3) Exponential backoff when limits hit.

**No monitoring dashboard for live trading metrics:**
- Problem: Dashboard serves `/status` JSON but no HTML UI shows live P&L, win rate, etc.
- Blocks: Cannot assess bot health without logs
- Implementation: (1) Enhance `dashboard_ui.py` to chart P&L over time. (2) Add live position table. (3) Export metrics to Prometheus.

## Test Coverage Gaps

**Cross-platform arb execution untested with real timing:**
- What's not tested: Order latency, partial fills under network delay, revalidation under actual market movement
- Files: `tests/test_executor.py`, `tests/test_concurrent_execution.py`
- Risk: Code assumes orders fill instantly. Real execution may fail at scale.
- Priority: HIGH — this is the core revenue path

**WebSocket feed disconnection and reconnection untested:**
- What's not tested: WS connection drops mid-stream, reconnect logic, subscription state after reconnect
- Files: `tests/test_ws_feeds.py`
- Risk: Continuous mode may hang or lose price updates without user awareness
- Priority: HIGH — production reliability depends on this

**IBKR integration barely tested:**
- What's not tested: Contract discovery, order placement, fill confirmation, IB Gateway connection/disconnection
- Files: `tests/test_ibkr_api.py`, `tests/test_ibkr_scan.py`
- Risk: IBKR is a new platform with minimal real-world testing
- Priority: MEDIUM — only enabled if user opts in, but should work correctly

**Betfair/Smarkets/SX Bet execution untested:**
- What's not tested: Session auth expiration, order rejection by exchange, partial fills
- Files: `tests/test_*_api.py` exist but mock all API responses
- Risk: Code paths for these platforms never validated against live APIs
- Priority: MEDIUM — used in cross-platform arbs, failures impact P&L

---

*Concerns audit: 2025-03-17*
