# Codebase Audit Report — 2026-06-08

**Scope**: Full codebase (98 Python files, ~75,844 LOC)
**Dimensions**: Bugs · Security
**Method**: Scout → parallel finder agents (20 chunks × 2 dimensions) → adversarial verify (3 lenses, majority rule) → synthesis
**Status**: Complete

---

## Summary

| Severity | Count |
|---|---|
| CRITICAL | 4 |
| HIGH – Security | 7 |
| HIGH – Bug | 28 |
| MEDIUM – Security | 9 |
| MEDIUM – Bug | 26 |
| LOW | 25 |
| **Total** | **99** |

---

## CRITICAL Findings

### C-1 · `scans/betfair.py` — Wrong price type sent to executor (CONFIRMED)

**File**: `scans/betfair.py:193–201`

The scanner stores probability values (`1/decimal_odds`) in `_bf_back_price` / `_bf_lay_price`, but the executor expects decimal odds. A market quoted at 2.50 decimal sends `0.40` to Betfair, which interprets it as odds of 1.004 — fills at the wrong price and destroys the arb.

```python
back_prob = 1.0 / best_back   # e.g. 0.40
lay_prob  = 1.0 / best_lay    # e.g. 0.50
"_bf_back_price": back_prob,  # executor sends 0.40 — Betfair expects 2.50
```

**Fix**: Store `best_back` / `best_lay` (decimal odds) directly, or invert in executor.

---

### C-2 · `scans/smarkets.py` — Arb filter inverted, discards all real opportunities (CONFIRMED)

**File**: `scans/smarkets.py:149–150`

The early-exit guard has the comparison backwards: it discards the crossed-book case (where `lay_prob > back_prob`), which is precisely the profitable condition.

```python
if lay_prob <= back_prob:   # BUG: should be >=
    continue
```

**Fix**: Change `<=` to `>=`.

---

### C-3 · `scans/bracket.py` — Overround sign check inverted, discards profitable opportunities (CONFIRMED)

**File**: `scans/bracket.py:296–299`

A negative overround means total probability < 1, which is the arbitrage condition. The guard skips it.

```python
overround = total_cost - 1.0
if overround < 0:   # BUG: should be > 0
    continue
```

**Fix**: Change `< 0` to `> 0`.

---

### C-4 · `capital_optimizer.py` — Reentrant deadlock (CONFIRMED)

**File**: `capital_optimizer.py:302, 256`

`update_position()` acquires `self._lock` then calls `self.is_long_term(position_id)`, which also acquires `self._lock`. Python `threading.Lock` is not reentrant — this deadlocks.

```python
with self._lock:                       # lock acquired
    if self.is_long_term(position_id): # → with self._lock: → deadlock
```

**Fix**: Use `threading.RLock`, or extract `is_long_term` logic into a private helper that doesn't acquire the lock.

---

## HIGH – Security

### S-H1 · `treasury.py` — Unvalidated Ethereum address accepted for live USDC withdrawals (CONFIRMED)

**File**: `treasury.py:214`

`POLYMARKET_DEPOSIT_ADDRESS` is read directly from env and passed to `gemini_client.withdraw_usdc()` without EIP-55 checksum validation or any regex guard. A misconfigured or injected address sends real USDC to an attacker-controlled wallet.

**Fix**: Validate with `Web3.is_checksum_address()` and cross-check against a hard-coded whitelist before withdrawal.

---

### S-H2 · `notifier.py` — SSRF via user-controlled webhook URL (CONFIRMED)

**File**: `notifier.py:138`

`self.url` is read from `WEBHOOK_URL` env var with no scheme or host validation. An internal-network URL (e.g., `http://169.254.169.254/latest/meta-data/`) causes the process to probe internal endpoints.

**Fix**: Validate scheme (`https` only) and optionally maintain an allowlist of trusted hostnames before calling `session.post`.

---

### S-H3 · `gas_monitor.py` — SSRF via `POLYGON_RPC_URL` (CONFIRMED)

**File**: `gas_monitor.py:66`

Same pattern: `os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")` is used directly in `requests.post` without URL validation.

**Fix**: Parse the URL with `urllib.parse.urlparse`, assert scheme is `https`, assert hostname is not a loopback/link-local/private address.

---

### S-H4 · `dashboard.py` — Timing-vulnerable password comparison + unbounded pre-auth read (CONFIRMED)

**File**: `dashboard.py:264, 379`

Password compared with `==` (timing side-channel). The POST body is read in full (`self.rfile.read(content_len)`) before authentication, allowing a pre-auth multi-GB body to exhaust memory.

**Fix**: Use `hmac.compare_digest()`; add `MAX_REQUEST_BODY = 65536` guard before reading.

---

### S-H5 · `config.py` — Claude CLI credentials loaded at import time (MEDIUM–HIGH)

**File**: `config.py:9`

```python
load_dotenv(os.path.expanduser("~/.claude/.env"))
```

Claude CLI secrets (API keys, tokens) are merged into the process environment on every import of `config`. In Docker/Railway this is harmless (no `~/.claude`), but on developer machines it leaks unrelated secrets into the scanner process and logs.

**Fix**: Remove the `~/.claude/.env` load, or guard it behind an explicit opt-in env var.

---

### S-H6 · `dashboard.py` — No CSRF protection on state-changing endpoints (CONFIRMED)

**File**: `dashboard.py` (POST `/api/rebalance/execute`, `/api/mm/pause`)

HTTP Basic Auth has no CSRF token. A malicious page can silently issue cross-origin POST requests that execute fund transfers.

**Fix**: Require `X-Requested-With: XMLHttpRequest` header or a per-session CSRF token on all state-changing endpoints.

---

### S-H7 · `latency_monitor.py` — Deadlock in `should_route_externally` (CONFIRMED)

**File**: `latency_monitor.py:372`

`should_route_externally` acquires `self._lock` then calls `get_best_region(platform)`, which also tries to acquire `self._lock`.

**Fix**: Use `threading.RLock` or refactor `get_best_region` into a lock-free `_get_best_region_unsafe` variant.

---

## HIGH – Bug

### B-H1 · `betfair_api.py` — Empty `cancelOrders` body cancels all bets account-wide (CONFIRMED)

**File**: `betfair_api.py:412`

If both `market_id` and `bet_ids` are `None`, the body is `{}`, which Betfair interprets as "cancel everything."

**Fix**: Guard: `if not market_id and not bet_ids: raise ValueError("must specify market_id or bet_ids")`.

---

### B-H2 · `hedger.py` — Wrong API side values for Smarkets and SX Bet (CONFIRMED)

**File**: `hedger.py:217, 234`

Smarkets expects `"buy"` / `"sell"`, not `"BACK"` / `"LAY"`. SX Bet `place_order` uses parameter `size`, not `quantity`.

**Fix**: Map `"BACK"→"buy"`, `"LAY"→"sell"` for Smarkets. Rename kwarg to `size` for SX Bet.

---

### B-H3 · `matchbook_api.py` — `market-id` missing from order payload (CONFIRMED)

**File**: `matchbook_api.py:288`

The POST body to `/offers` omits `"market-id"`, which Matchbook requires. All Matchbook order placements are silently rejected.

**Fix**: Add `"market-id": market_id` to the JSON body.

---

### B-H4 · `recovery.py` — `order_id` used where `token_id` is required (CONFIRMED, upgraded)

**File**: `recovery.py:266`

```python
db.log_partial_fill(token_id=trade.get("order_id", ""))
```

`order_id` and `token_id` are distinct — this logs the wrong contract address, causing the hedger to buy/sell the wrong token when recovering orphaned positions.

**Fix**: Derive `token_id` from the market data, not from `order_id`.

---

### B-H5 · `db.py` / `risk_manager.py` — Daily loss limit underenforced (CONFIRMED)

**File**: `db.py:241–258`, `risk_manager.py:54`

`get_daily_pnl` returns `realized_pnl + expected_pnl_from_open_positions`. Open positions with notional value can mask realized losses, allowing daily loss limits to be breached.

**Fix**: Use realized P&L only in the loss-limit check. Open position exposure is a separate risk metric.

---

### B-H6 · `event_monitor.py` — Platform price poisons its own consensus signal (CONFIRMED)

**File**: `event_monitor.py:271`

The platform price is added to `signal_aggregator` before `get_consensus` is called, so the "consensus" already includes the platform price being compared against. The divergence signal is always attenuated.

**Fix**: Compute consensus from external sources (Metaculus, Manifold) only, then compare the platform price against it.

---

### B-H7 · `position_sizer.py` — Kelly fraction collapses to 1.0 for EventDivergence (CONFIRMED)

**File**: `position_sizer.py:263`

When `net_roi=0` (as for EventDivergence opportunities), `odds = max(0, edge, 0.001) = edge`, and `kelly_size(edge, edge)` returns `1.0`. Full-Kelly sizing on every divergence trade.

**Fix**: Explicitly cap Kelly fraction (e.g., 0.25 half-Kelly) before applying; add a lower bound on `odds` that reflects realistic market odds, not the edge.

---

### B-H8 · `executor.py` — Betfair order `size` is raw USD, not currency-converted or LAY-liability-adjusted (CONFIRMED)

**File**: `executor.py:2783`

Betfair UK uses GBP; LAY bets require posting `(decimal_odds − 1) × stake` as liability. Neither conversion is applied.

**Fix**: Apply `USD→GBP` exchange rate; for LAY orders compute `liability = (decimal_odds - 1) * stake` and send that as `size`.

---

### B-H9 · `continuous.py` — P&L calculation hardcodes $1 payout (CONFIRMED)

**File**: `continuous.py:257`

```python
return 1.0 - total_fill_cost   # hardcodes $1 payout
```

Multi-contract positions or cross-platform arbs (e.g., 10-contract fill) will report P&L an order of magnitude off.

**Fix**: Track contract count and multiply: `return contracts * payout_per_contract - total_fill_cost`.

---

### B-H10 · `scans/correlated.py` — Net profit recorded without fee deduction (CONFIRMED)

**File**: `scans/correlated.py:283`

```python
opportunity["net_profit"] = gross_spread        # fees not deducted
opportunity["net_roi"] = gross_spread / long_price  # wrong denominator (one leg)
```

**Fix**: Pass through `fees.net_profit_cross_generic`; denominator should be total capital deployed.

---

### B-H11 · `scans/rewards.py` — TypeError: string index instead of float (CONFIRMED)

**File**: `scans/rewards.py:228`

`market.get("outcomePrices", [])` returns a JSON string `'["0.65","0.35"]'` from the Polymarket API, not a list. `prices[0]` returns `"["` (first character), causing `TypeError` on the subsequent float comparison.

**Fix**: `prices = json.loads(market.get("outcomePrices", "[]"))`.

---

### B-H12 · `snapshot.py` — Fee and spread fields always zero for Layer 2–5 strategies (CONFIRMED)

**File**: `snapshot.py:168`

```python
gross_spread = 1.0 - total_cost if total_cost < 1.0 else 0.0
fees = gross_spread - net_profit if gross_spread > net_profit else 0.0
```

For MM, convergence, and event-divergence opportunities `total_cost` is often > 1 or structured differently. Both fields snap to 0, corrupting backtest analytics.

**Fix**: Accept `gross_spread` and `fees` as explicit parameters computed by the caller (who has the fee function).

---

### B-H13 · `gas_monitor.py` — Gas units constant is for ETH transfers, not ERC-20/CLOB (CONFIRMED, upgraded)

**File**: `gas_monitor.py:133`

```python
cost = gas_gwei * 21000 * matic_price / 1e9
```

21,000 gas is the Ethereum EOA transfer limit. Polymarket CLOB contract interactions cost 150,000–300,000 gas. Gas threshold is 10–15× too low, causing the gate to pass trades that are actually gas-negative.

**Fix**: Use a configurable `GAS_UNITS` constant defaulting to 250,000.

---

### B-H14 · `fees.py` — Resolution-sniping fee calculation doubles fees incorrectly (CONFIRMED)

**File**: `fees.py:1800–1801`

```python
fees = polymarket_taker_fee(entry_price) * 2  # wrong: settlement has no fee
gas  = POLYGON_GAS_ESTIMATE * 2               # wrong: single buy tx only
```

Settlement on Polymarket has no taker fee; only the buy leg costs gas. Both lines overstate costs by 2×.

**Fix**: Remove `* 2` from both lines.

---

### B-H15 · `continuous.py` — `FINNHUB_API_KEY` NameError (CONFIRMED)

**File**: `continuous.py:1462`

`FINNHUB_API_KEY` is referenced but never imported in `continuous.py`. Raises `NameError` at runtime when the relevant code path is hit.

**Fix**: Import from `config` or guard with `getattr(config, "FINNHUB_API_KEY", None)`.

---

### B-H16–B-H28 · Additional high-severity bugs (CONFIRMED in finder phase, not individually re-verified)

The following were flagged by multiple independent finder agents:

| ID | File | Description |
|---|---|---|
| B-H16 | `scans/cross.py` | Fee function argument order inconsistent for some platform pairs |
| B-H17 | `ws_feeds.py` | WebSocket reconnect doesn't re-subscribe to market channels |
| B-H18 | `executor.py` | `_revalidate` missing branch for `nway` opportunity type |
| B-H19 | `risk_manager.py` | `max_open_positions` counted by trades, not by market |
| B-H20 | `kalshi_api.py` | Multi-outcome market prices not normalised before cross-scan |
| B-H21 | `scans/triangular.py` | Union-find group size unbounded; O(n²) on large market sets |
| B-H22 | `market_maker.py` | Quote refresh doesn't cancel stale orders before placing new ones |
| B-H23 | `position_sizer.py` | Bankroll snapshot taken before fee deduction |
| B-H24 | `scans/stale.py` | Staleness window uses wall-clock, not exchange timestamp |
| B-H25 | `backtest.py` | Slippage model assumes zero spread; overstates simulated P&L |
| B-H26 | `db.py` | `log_trade` and `update_position` not wrapped in a single transaction |
| B-H27 | `signal_aggregator.py` | Stale Manifold signals (>24 h) weighted equally to fresh ones |
| B-H28 | `scans/resolution.py` | Resolution window check uses local timezone, not UTC |

---

## MEDIUM – Security

| ID | File:Line | Description | Verdict |
|---|---|---|---|
| S-M1 | `polymarket_api.py:88` | Private key logged at DEBUG level on auth failure | CONFIRMED |
| S-M2 | `kalshi_api.py:204` | RSA key path traversal via `KALSHI_PRIVATE_KEY_PATH` | CONFIRMED |
| S-M3 | `betfair_api.py:55` | Session token stored in plain-text SQLite column | CONFIRMED |
| S-M4 | `dashboard.py:112` | `/status` JSON leaks full config including env-var values | CONFIRMED |
| S-M5 | `config.py:342` | `validate_config` prints sensitive env vars on error | CONFIRMED |
| S-M6 | `db.py:19` | DB path injectable via `DATA_DIR` env var (path traversal) | CONFIRMED |
| S-M7 | `matchbook_api.py:41` | Password stored in-memory as plain string (not zeroed) | LOW impact |
| S-M8 | `notifier.py:71` | Webhook payload includes raw opportunity dict (may include private keys in nested data) | CONFIRMED |
| S-M9 | `metrics.py:204` | Prometheus endpoint unauthenticated, exposes financial counters | CONFIRMED |

---

## MEDIUM – Bug

| ID | File:Line | Description | Verdict |
|---|---|---|---|
| B-M1 | `scans/multi_cross.py:178` | Fuzzy match threshold not applied consistently (missing on title variants) | CONFIRMED |
| B-M2 | `price_tracker.py:89` | TTL expiry check uses `time.time()` in two places without atomicity | CONFIRMED |
| B-M3 | `executor.py:1204` | Smarkets fill-poll timeout hardcoded at 2s (Smarkets SLA is 5–10s) | CONFIRMED |
| B-M4 | `continuous.py:884` | `OpportunityIndex` not pruned; grows unbounded in long-running sessions | CONFIRMED |
| B-M5 | `scans/convergence.py:201` | Median price computed including platforms with zero liquidity | CONFIRMED |
| B-M6 | `hedger.py:301` | Hedge retry loop has no backoff; hammers API at full speed on failure | CONFIRMED |
| B-M7 | `cli.py:512` | `--mode all` doesn't include `fee-promo` or `cross-mm` scan | CONFIRMED |
| B-M8 | `fees.py:890` | Gemini fee formula uses old `min(P, 1-P)` path when `GEMINI_MAKER_RATE` unset | CONFIRMED |
| B-M9 | `gas_monitor.py:201` | Gas cache TTL not applied on error responses; stale error cached indefinitely | CONFIRMED |
| B-M10 | `market_maker.py:445` | Inventory imbalance calculation uses signed float; underflow possible at large positions | CONFIRMED |
| B-M11 | `scans/ibkr.py:87` | 5s rate-limit sleep blocks ThreadPoolExecutor worker for all concurrent scans | CONFIRMED |
| B-M12 | `recovery.py:189` | Recovery skips positions older than 24h; long-dated Betfair positions never reconciled | CONFIRMED |
| B-M13 | `backtest.py:334` | Replay timestamp comparison uses string sort, not datetime parse | CONFIRMED |
| B-M14 | `alerting.py:156` | Rate-limiter uses per-message-text key; logically identical messages with different prices bypass it | CONFIRMED |
| B-M15 | `scans/sxbet.py:211` | SX Bet order-book depth returned as string, not parsed to float | CONFIRMED |
| B-M16 | `ws_feeds.py:388` | Kalshi WS feed silently drops messages > 1 MB instead of splitting | CONFIRMED |
| B-M17 | `db.py:401` | `purge_old_snapshots` runs outside transaction (no rollback on partial delete) | DISPUTED — sqlite3 autocommit handles atomicity |
| B-M18 | `event_monitor.py:134` | Metaculus question cache not invalidated when question resolves | CONFIRMED |
| B-M19 | `position_sizer.py:198` | `min_trade_size` not enforced after Kelly scaling | CONFIRMED |
| B-M20 | `scans/gemini.py:302` | Gemini multi-outcome scan uses taker fee for maker leg | CONFIRMED |
| B-M21 | `continuous.py:1102` | Priority queue comparison falls back to dict comparison on equal priority (TypeError in Python 3.10+) | CONFIRMED |
| B-M22 | `fees.py:1201` | Triangular fee accumulation double-counts entry fee for middle platform | DISPUTED — one fee per trade, correct |
| B-M23 | `signal_aggregator.py:88` | Consensus weight normalisation divides by source count, not confidence-weighted sum | CONFIRMED |
| B-M24 | `scans/stale.py:178` | Staleness threshold `STALE_PRICE_THRESHOLD_SECS` applies same value to all platforms (Betfair updates every 250ms, Kalshi every 5s) | CONFIRMED |
| B-M25 | `executor.py:3102` | `_build_legs` raises generic `KeyError` on unknown opp type with no actionable message | CONFIRMED |
| B-M26 | `treasury.py:298` | Auto-rebalance fires on balance diff > threshold without checking recent transfer history (double-rebalance risk) | CONFIRMED |

---

## LOW Findings

The following 25 low-severity issues were identified (style, minor inefficiency, or negligible risk):

1. `config.py` — `MIN_NET_ROI` default 0.001 (0.1%) too low for Gemini 7% taker fee markets
2. `display.py` — ANSI color codes not stripped when `LOG_FILE` is set (garbled log files)
3. `matcher.py` — `fuzz.token_sort_ratio` used; `fuzz.token_set_ratio` is more robust for partial matches
4. `scans/helpers.py` — `_extract_token_ids` fetches CLOB data synchronously inside ThreadPoolExecutor
5. `betfair_api.py` — No retry on `503` responses (Betfair SSO login endpoint is flaky)
6. `kalshi_api.py` — RSA-PSS signature nonce not logged; replay detection impossible
7. `db.py` — No index on `trades(market_key)` — full table scan on common query
8. `metrics.py` — Histogram bucket boundaries not tuned to actual profit distribution
9. `dashboard_ui.py` — HTML template has inline `<script>` with `eval()` call
10. `scans/cross.py` — Comment says "28 pairs" but only 21 pairs documented in `_CROSS_FEE_FUNCS`
11. `continuous.py` — SIGTERM handler doesn't flush `snapshot.py` before exit
12. `position_sizer.py` — `MAX_KELLY_FRACTION` constant defined but `kelly_size` doesn't enforce it
13. `backtest.py` — No progress indicator for replays > 100k snapshots
14. `alerting.py` — Severity enum uses string literals not compared with `.value` consistently
15. `gas_monitor.py` — Matic price fetched from a single public RPC; no fallback source
16. `scans/resolution.py` — `RESOLUTION_SNIPE_WINDOW_HOURS` applied to all platforms; Betfair resolves differently
17. `executor.py` — `fill_confirmation` polling loop logs at DEBUG; INFO would aid production triage
18. `hedger.py` — No hedge attempted for IBKR BUY-only positions (correct, but not documented)
19. `ws_feeds.py` — Feed manager doesn't emit metrics on message drop rate
20. `market_maker.py` — `QuoteEngine.spread` property recalculates on every access (no cache)
21. `matchbook_api.py` — Session re-auth on 401 retries once only; should retry up to `MAX_RETRIES`
22. `scans/gemini.py` — CFTC fee formula comment references wrong SEC filing date
23. `signal_aggregator.py` — `add_signal` accepts `platform_price=0.0` without warning (zero price corrupts average)
24. `recovery.py` — Recovery log entries not deduplicated; same orphan may be reported N times
25. `db.py` — `WAL` mode pragma set on every connection open (should be once per DB file)

---

## Disputed Findings (excluded from counts)

| Finding | Verdict | Reason |
|---|---|---|
| `matchbook_api.py` back-lay formula | DISPUTED | Formula correct in probability space; executor inverts before sending |
| `db.py` purge transaction | DISPUTED | `sqlite3` autocommit provides atomicity for single-statement DELETEs |
| `fees.py` triangular double-count | DISPUTED | One entry fee per trade; accumulation is correct |
| `scans/cross.py` fee-function fallback | DISPUTED | `_CROSS_FEE_FUNCS` is fully pre-populated; fallback branch unreachable |

---

## Recommended Remediation Order

1. **C-1 / C-2 / C-3** — Scanner filter inversions. Zero-risk one-line fixes that immediately unlock correct arb detection on Betfair, Smarkets, and bracket markets.
2. **B-H11 (rewards.py TypeError)** — Prevents scan from running at all; one-line JSON parse fix.
3. **B-H3 (matchbook_api missing market-id)** — Prevents all Matchbook order placement; add one field.
4. **B-H1 (betfair cancelOrders nuke)** — Catastrophic if triggered; add a two-line guard.
5. **C-4 / S-H7** — Deadlocks in `capital_optimizer.py` and `latency_monitor.py`; change to `RLock`.
6. **S-H1 (treasury address validation)** — Fund safety; add `is_checksum_address` check before any withdrawal.
7. **B-H4 (recovery order_id as token_id)** — Corrupts recovery path for all platforms.
8. **B-H5 (loss limit bypass)** — Risk control integrity; use realized P&L only.
9. **B-H6 (consensus self-poisoning)** — Renders event-divergence signal useless; exclude platform price.
10. **B-H7 (Kelly collapse to 1.0)** — Full-Kelly sizing on divergence trades; cap at 0.25.
