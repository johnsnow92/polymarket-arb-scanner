# Codebase Audit — Bugs & Security — 2026-06-09

Audit dimensions: bugs, security  
Files scanned: 96 production source files  
Findings: 30 total (6 critical, 11 high, 4 medium, 2 low); 8 notable false positives rejected

---

## CRITICAL

### 1. bracket.py:298 — Overround check inverted
**Dimension:** bug  
**Verification:** confirmed by L1 (static analysis), L3 (semantic analysis)  
**Detail:** The condition `if overround < 0: continue` inverts the profitability filter, skipping all valid arbitrage opportunities (where overround is negative/underrounded) while only permitting unprofitable ones (overround ≥ 0). This causes the entire bracket strategy to discard all legitimate arbs and pass only losing trades. All bracket opportunities are systematically filtered out.

### 2. expert_divergence.py:99 — Double-negation of expert probability for BUY_NO
**Dimension:** bug  
**Verification:** confirmed by L3 (semantic analysis), independent resolver  
**Detail:** For BUY_NO direction, the code inverts `expert_prob` to `1.0 - expert_prob` before passing to the fee calculation function. However, the fee function expects a straight probability for the traded outcome and uses it to compute gross_spread against entry_price. The double-inversion causes fees to be calculated against an already-inverted entry price, systematically misstating profit for BUY_NO positions by inverting the fee impact.

### 3. cross_mm.py:91 — Inverted price in fee function call
**Dimension:** bug  
**Verification:** confirmed by L1 (static analysis)  
**Detail:** The fee calculation calls `net_profit_cross_generic()` with `1 - sell_price` instead of `sell_price`, inverting the probability used in fee computation. This produces incorrect profit/loss calculations for all cross-platform market-making opportunities, causing mispriced position sizing and rank-ordered opportunity selection.

### 4. kalshi_api.py:52,63 — RSA key validation bypass
**Dimension:** security  
**Verification:** confirmed by L1 (static analysis)  
**Detail:** Setting `unsafe_skip_rsa_key_validation=True` when loading Kalshi private keys disables RSA key structure validation. Malformed, weakened, or corrupted keys are silently accepted, potentially allowing operation with compromised keys without detection or warning.

### 5. notifier.py:199-202 — CallMeBot API key in GET query string
**Dimension:** security  
**Verification:** confirmed by L1 (static analysis)  
**Detail:** The CallMeBot API key is embedded as a URL query parameter in a GET request: `https://api.callmebot.com/whatsapp.php?phone=...&text=...&apikey=...`. Query parameters are logged in server access logs, HTTP proxies, and referer headers, exposing the API key to any infrastructure that handles the request.

### 6. notifier.py:172 — Telegram bot token in URL path
**Dimension:** security  
**Verification:** confirmed by L1 (static analysis)  
**Detail:** The Telegram bot token is embedded in the URL path: `/bot{token}/sendMessage`. This exposes the token in HTTP access logs, proxy logs, and referer headers across the network stack, allowing any logging system to capture the credential.

---

## HIGH

### 7. metrics.py:78-80 — Prometheus histogram bucket accumulation bug
**Dimension:** bug  
**Verification:** confirmed by L1 (static analysis), L2 (code flow analysis)  
**Detail:** The code increments all histogram buckets for every observation instead of only the matching bucket. This causes cumulative counts to inflate across the entire histogram, breaking percentile calculations and producing meaningless latency/metric distributions.

### 8. position_sizer.py:280 — Kelly multiplier division instead of multiplication
**Dimension:** bug  
**Verification:** confirmed by L2 (code flow analysis)  
**Detail:** The Kelly multiplier for market-making divides the spread fraction by `raw_kelly` instead of multiplying: `spread_fraction / raw_kelly` produces a value that is clamped to 1.0, severely undersizing all market-making positions. This causes the strategy to take minimal positions when it should be sizing based on edge.

### 9. smarkets_api.py:253 — Lay price conversion inverted
**Dimension:** bug  
**Verification:** confirmed by L2 (code flow analysis)  
**Detail:** Lay price conversion uses `1.0 - (price_pct / 100)` where the correct formula is `price_pct / 100`. This inverts the probability estimate for the lay side, causing the arb detection to misinterpret yes/no directions on Smarkets, leading to oppositely-hedged positions.

### 10. correlated.py:283 — Net profit inflated without fee deduction
**Dimension:** bug  
**Verification:** confirmed by L3 (semantic analysis)  
**Detail:** The variable `net_profit` is set to `gross_spread` without subtracting fees. This inflated profit figure is then used in `capital_efficiency_score()` for ranking opportunities, causing fee-negative (losing) opportunities to be ranked as if profitable, inverting strategy profitability.

### 11. imbalance.py:69 — Collapse threshold sign-agnostic comparison
**Dimension:** bug  
**Verification:** confirmed by L3 (semantic analysis)  
**Detail:** The collapse threshold check `0.7 * abs(original_ratio)` doesn't validate the sign of the ratio change. A ratio flip from +0.5 to -0.5 has absolute value 0.5 > threshold 0.35 and incorrectly passes validation, allowing contradictory-direction trades (selling when intending to buy, or vice versa) to execute.

### 12. helpers.py:149-151 — Cache freshness check falsy ts bypass
**Dimension:** bug  
**Verification:** confirmed by L3 (semantic analysis)  
**Detail:** The freshness check uses `if ts and ...`, which evaluates to false when `_ts=0` (the default/uninitialized value). Cache entries with timestamp 0 bypass TTL checks entirely, allowing stale CLOB prices from the previous cycle to be treated as fresh data in the refinement stage.

### 13. whale_copy_decoder.py:293 — Wrong swap side returned for SELL order
**Dimension:** bug  
**Verification:** confirmed by L3 (semantic analysis)  
**Detail:** For SELL orders, the function returns `maker_amt / USDC_SCALE` (the bot's USDC counter-leg) instead of `taker_amt` (the whale's actual token amount). Copy-trade position sizes are therefore based on the inverted side of the swap, causing incorrect position scaling and hedge ratios.

### 14. resolution.py:184 — Unsafe list indexing without bounds check
**Dimension:** bug  
**Verification:** confirmed by L2 (code flow analysis)  
**Detail:** The code accesses `outcomePrices[0]` and `[1]` without checking the list length. If the list exists but has fewer than 2 elements, an IndexError is raised, crashing the resolution scan and blocking market updates.

### 15. scans/api_outage.py:110 — Falsy check treats 0.0 price as missing
**Dimension:** bug  
**Verification:** confirmed by L2 (code flow analysis)  
**Detail:** The condition `if not stale_yes` incorrectly treats a legitimate price of 0.0 as missing/stale. This skips valid markets that trade at zero and should be `if stale_yes is None` to distinguish between actual data and absence.

### 16. executor.py:~1930 — Unknown opportunity type handling gap
**Dimension:** bug  
**Verification:** completeness critic flagged  
**Detail:** The `_build_legs()` method has no `else` clause for unknown opportunity types. Unrecognized types silently return an empty legs list with only a debug log, masking routing gaps when new strategies are added to the system.

### 17. continuous.py:~1131 — API fetch failures silently continue
**Dimension:** bug  
**Verification:** completeness critic flagged  
**Detail:** API fetch failures in ThreadPoolExecutor are caught per-future but no cycle-level alert fires. The bot continues with empty market data, wasting the entire scan interval silently without alerting operations that data is unavailable.

### 18. db.py:151 — SQL injection pattern with f-string column name
**Dimension:** security  
**Verification:** confirmed by L1 (static analysis)  
**Detail:** The code uses `f"ALTER TABLE partial_fills ADD COLUMN {col} TEXT"` with an f-string column name. While currently safe due to hardcoded column names, this violates parameterized query principles and is a code smell that could lead to injection if column names are later dynamicized.

### 19. db.py:754 — SQL injection pattern with f-string table name
**Dimension:** security  
**Verification:** confirmed by L1 (static analysis)  
**Detail:** The code uses `f"SELECT COUNT(*) as cnt FROM {table}"` with an f-string table name. Like the column injection above, this violates query parameterization and is a violation vector if table names become dynamic.

### 20. matchbook_api.py:44-46 — Unvalidated proxy URL from environment
**Dimension:** security  
**Verification:** confirmed by L2 (code flow analysis)  
**Detail:** The `MATCHBOOK_PROXY_URL` environment variable is assigned directly to `session.proxies` without URL validation. An attacker controlling the environment can redirect all Matchbook API traffic to an arbitrary SSRF target, intercepting or modifying order placement.

### 21. treasury.py:234 — Unvalidated Ethereum address from environment
**Dimension:** security  
**Verification:** confirmed by L2 (code flow analysis)  
**Detail:** The `GEMINI_DEPOSIT_ADDRESS` environment variable is passed to `web3_send_usdc()` without Ethereum address format or checksum validation. A misconfigured address silently sends funds to the wrong destination with no validation error.

### 22. finnhub_api.py:84 — API key in query parameter
**Dimension:** security  
**Verification:** confirmed by L2 (code flow analysis)  
**Detail:** The Finnhub API key is passed as a query parameter `"token": self.api_key`. Query parameters are logged in all HTTP access logs and reverse proxy logs, exposing the credential to logging infrastructure.

### 23. kalshi_api.py:43-45 — Unvalidated file path in key loading
**Dimension:** security  
**Verification:** confirmed by L2 (code flow analysis)  
**Detail:** The `_load_private_key(file_path)` function opens arbitrary file paths without validation. If `file_path` is derived from untrusted input, path traversal attacks are possible (e.g., `../../etc/passwd`), potentially leaking sensitive files or loading malicious keys.

---

## MEDIUM

### 24. alerting.py:323 — Loss streak alert fires only on exact count
**Dimension:** bug  
**Verification:** confirmed by L1 (static analysis)  
**Detail:** The condition `if losses == 3` fires an alert only on exactly 3 consecutive losses. The 4th, 5th, and subsequent consecutive losses produce no alert. Should be `if losses >= 3` to alert on all streaks of 3 or more.

### 25. backtest.py:391 — Min/max clamp inverted for threshold lowering
**Dimension:** bug  
**Verification:** confirmed by L1 (static analysis)  
**Detail:** The clamping for the `win_rate > 0.7` path returns `min(clamped, current)` instead of `max(clamped, current)`, preventing the threshold from being lowered as intended. Win rates that should be raised to the minimum level are instead lowered.

### 26. cross_pair_index.py:72 — Cache freshness falsy ts bypass
**Dimension:** bug  
**Verification:** confirmed by L3 (semantic analysis)  
**Detail:** Identical to finding #12: cache entries with `_ts=0` (uninitialized) have falsy ts and bypass TTL checks, allowing stale prices to be treated as fresh without expiration enforcement.

### 27. negrisk.py:163 — Calculation proceeds with incomplete data after warning
**Dimension:** bug  
**Verification:** confirmed by L3 (semantic analysis)  
**Detail:** After logging a warning about missing outcomes, the code proceeds to call `net_profit_negrisk_internal(yes_prices)` with an incomplete prices array instead of skipping or returning early. This computes unreliable profit with missing data points.

### 28. fees.py:587-588 — Gemini fee over-estimated with ceil
**Dimension:** bug  
**Verification:** completeness critic flagged  
**Detail:** Gemini fees are rounded up with `math.ceil` instead of `round`, systematically over-estimating fees by up to $0.01 per contract. This inflates total fee costs across the portfolio and reduces apparent profitability.

### 29. dashboard.py:264 — Timing-attack vulnerable password check
**Dimension:** security  
**Verification:** confirmed by L2 (pattern analysis)  
**Detail:** The code uses direct string equality `user == DASHBOARD_USER and pwd == DASHBOARD_PASS` instead of `hmac.compare_digest()`. This is vulnerable to timing attacks where the comparison time varies with password similarity, potentially allowing brute-force or character-by-character attacks.

---

## LOW

### 30. ws_feeds.py:905 — Unnecessary feed re-subscription churn
**Dimension:** bug  
**Verification:** confirmed by L3 (semantic analysis), downgraded to LOW  
**Detail:** The Betfair feed re-subscribes to the full `_market_ids` list instead of just `_pending_market_ids`, causing unnecessary subscription churn. This doesn't corrupt data but wastes bandwidth and increases reconnection overhead.

### 31. superforecaster_api.py:53 — Boundary condition staleness
**Dimension:** bug  
**Verification:** confirmed by L3 (semantic analysis), downgraded to LOW  
**Detail:** The cache expiration check uses `>` instead of `>=`, meaning data at the exact expiration boundary is served as fresh. This adds <1 second of extra staleness at the cache boundary, which is negligible in practice.

---

## FALSE POSITIVES (notable rejections)

- **db.py:937-943** (ISO timestamp parsing): SQLite's `strftime('%s', iso)` correctly parses ISO 8601 timestamp strings. No parsing bug exists.

- **cross.py:537** (CLOB refinement return): The function modifies the list in-place and doesn't require an explicit return value for mutations. No missing return.

- **triangular.py:412-413** (_side_a/_side_b unset): Lines 585-586 set these fields upstream in the code path. No uninitialized variable.

- **ws_feeds.py:771** (potential deadlock): The lock is released before the `get_best_prices()` call. No re-entrancy or deadlock risk.

- **polymarket_api.py:96-98** (retry bypass): The decorator correctly handles both `_RateLimitError` and `requests.Timeout`. No exception handler gap.

- **price_tracker.py:149** (direction inverted): Delta is computed as `fresh - stale`; delta > 0 means stale is lower, so buy_yes is the correct direction. No inversion.

- **news_snipe.py:332** (NO sentiment inverted): Correctly drops opportunities when the NO ask is already expensive (sentiment bearish). Logic is sound.

- **db.py:720,724,728** (IN clause SQL injection): Placeholders are static "?" tokens; values are passed as separate params tuple, not interpolated. No injection risk.

---

## Completeness gaps found

1. **No centralized API rate-limit alerting**: Individual API rate limits are caught locally, but no system-wide alert fires when multiple platforms are throttled simultaneously. Operations cannot detect coordinated degradation.

2. **Silent data cycle skips**: When market data fetch fails entirely (network, API outage), the bot continues to the next cycle without alerting. A 5-minute data gap is indistinguishable from a 5-minute quiet period.

3. **Missing bounds validation on dynamically loaded configs**: Environment variables and config files are read but not validated for range/type on load. Invalid values fail at runtime rather than startup.

4. **No execution dry-run mode**: The system has no way to validate execution chains (legs, hedges, account requirements) without actually placing orders. Routing bugs are caught only after live execution.

5. **Incomplete error recovery in ThreadPoolExecutor chains**: Futures are awaited but individual future exceptions are caught without marking the parent task for retry or rollback. Partial failures may leave inconsistent state.

---

**Report generated:** 2026-06-09  
**Audit scope:** Production source files in `/home/user/polymarket-arb-scanner/src`  
**Methodology:** 3-lens adversarial verification (static analysis, code flow, semantic); completeness critic review; false positive rejection
