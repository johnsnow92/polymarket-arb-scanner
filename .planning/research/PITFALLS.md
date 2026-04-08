# Pitfalls Research

**Domain:** Prediction market arbitrage bot — adding profitability tuning and new strategies to an existing production system
**Researched:** 2026-04-01
**Confidence:** HIGH (verified against official Polymarket/Betfair API docs, August 2025 IMDEA academic research, March 2025/2026 platform post-mortems, community operator reports)

---

## Critical Pitfalls

### Pitfall 1: Revalidation Thresholds Calibrated in Isolation from Live Market Conditions

**What goes wrong:**
The 10% drop tolerance in `executor.py`'s `_revalidate` step is set without observed live price-drift data. Either it stays too tight (100% rejection — the current production state) or it is loosened blindly and starts accepting stale opportunities that lose money after slippage. Both extremes cause losses; only one is visible.

**Why it happens:**
Developers set the threshold based on intuition or a synthetic test case, then move on. The two-stage detection pattern (mid-price scan → CLOB refinement) passes candidates that already survived CLOB refinement, so operators assume these candidates are "safe." The actual distribution of mid-to-execution price drift per strategy type is unknown until live data accumulates. The fix commits an API error tolerance and widened WS cache — but neither of these changes have been validated against real market behavior.

**How to avoid:**
Deploy with `DRY_RUN=true` for a minimum of 48–72 hours after the fix is deployed to Railway. Log every revalidation decision with: candidate ROI at scan time, ROI at revalidation, delta, rejection reason, and time elapsed between scan and execution attempt. Use those logs to calculate the actual 80th-percentile price drift per strategy class (Layer 1 arb, Layer 2 near-arb, Layer 3 MM). Set the tolerance to the 80th-percentile value, not a round number. Only flip `DRY_RUN=false` after this calibration is complete and the observed pass rate is 5–30% (not 0%, not 80%).

**Warning signs:**
- Pass rate is 0% or near-0% (threshold still too tight)
- Pass rate jumps to >50% immediately after loosening (threshold too loose — picking up stale scans)
- Net P&L is negative despite a healthy pass rate (threshold correct but slippage is higher than modeled)
- Logged delta distribution is bimodal — one cluster near 0%, another at 5%+ — means per-strategy-class thresholds are needed, not one global value

**Phase to address:**
Phase 1 (deploy revalidation fix and run 72h dry-run calibration before any live capital)

**Severity:** CRITICAL — this is the single blocking issue preventing any production trade execution today.

---

### Pitfall 2: Oracle/Resolution Mismatch Converting "Risk-Free" Arb Into Max-Loss

**What goes wrong:**
A cross-platform position appears risk-free: YES on Polymarket, NO on Kalshi, same event. The two platforms write their own resolution rules. The same real-world event settles YES on one platform and NO on the other. Both legs lose simultaneously. The bot has no mechanism to detect this.

**Why it happens:**
Market matching in `matcher.py` uses fuzzy title similarity — it identifies "same event" by title proximity, not by comparing resolution criteria. Documented cases include: 2024 government shutdown — Polymarket required "OPM issues shutdown announcement," Kalshi required "actual shutdown exceeding 24 hours." These markets had identical titles but opposite resolutions. Additionally, Polymarket's UMA oracle is governance-attackable: in March 2025, a whale holding 25% of UMA voting power manipulated a $7M Ukraine market resolution. Kalshi is CFTC-regulated with a separate, non-manipulable settlement mechanism.

**How to avoid:**
(1) Add a resolution-rule metadata field to matched market pairs in `matcher.py`. For any cross-platform execution, require that resolution criteria are semantically aligned before placing orders. (2) Add a UMA oracle monitor: if a Polymarket market's probability diverges >15% from Kalshi on the same matched event for >30 minutes without an external news trigger, treat it as a potential oracle manipulation and halt cross-platform execution on that market. (3) Add a minimum spread floor of 15 cents (not 2–3 cents) for cross-platform arbs on markets with governance-dependent resolution criteria (anything involving UMA oracle). The 15-cent floor is the community-recommended compensation for unquantifiable resolution risk.

**Warning signs:**
- Cross-platform P&L is negative despite correct fee calculations (resolution mismatch eating the hedge)
- A Polymarket market's price diverges from Kalshi by >15 cents on an event that hasn't seen news
- Any matched market pair has resolution phrases like "announces/signals/intends" on one side vs. "executes/completes/takes effect" on the other

**Phase to address:**
Phase 1 — before funding any cross-platform execution

**Severity:** CRITICAL — can cause total loss on both legs simultaneously.

---

### Pitfall 3: Partial Fill Leaving Naked Directional Exposure, Especially on IBKR

**What goes wrong:**
Leg 1 of a 2-leg arb fills completely. Leg 2 fills partially or not at all. The bot holds a directional position it never intended. If `hedger.py` fails (network error, rate limit, Betfair market suspension) or if the leg is on IBKR (BUY-only — cannot sell to unwind), the exposure stays open indefinitely. A $100 arb target turns into a $400 directional loss.

**Why it happens:**
Order execution is not atomic across platforms. Prices move between leg 1 fill confirmation and leg 2 submission. Leg 2 may hit the visible liquidity limit at the quoted ask. For Betfair/Smarkets: market suspension during a back-all arb freezes outstanding orders — they cannot be cancelled and cannot fill until the suspension lifts. For IBKR: the `ibkr.py` client is explicitly BUY-only (architecture doc confirms this); there is no mechanism to sell an IBKR position before contract expiry. This constraint means IBKR can never serve as a hedge leg in a 2-leg cross-platform arb.

**How to avoid:**
(1) Add an explicit guard in `executor.py:_build_legs()` that throws if IBKR appears as leg 2 in any cross-platform opportunity type. IBKR must only appear in standalone binary scans. (2) Set leg-2 timeout shorter than leg-1 fill confirmation time — if leg 2 is not filled within 5 seconds of leg 1 fill, trigger `hedger.py` immediately rather than waiting for the full polling loop. (3) Add a position-age monitor: any position open longer than `MAX_POSITION_AGE_MINUTES` (suggest: 60 minutes for binary arbs, 10 minutes for exchange arbs) triggers an immediate alert via `alerting.py` and optional forced close. (4) Log and alert on every partial-fill event the moment it occurs — do not allow partial fills to silently accumulate in `trades.db`.

**Warning signs:**
- `trades.db` contains rows with `leg1_filled=True, leg2_filled=False` older than 10 minutes
- `hedger.py` is generating errors in logs (cannot unwind)
- Platform balance on one side growing while the other side shrinks (capital imbalance from stranded hedges)
- Any IBKR position in `trades.db` showing as an open cross-platform leg

**Phase to address:**
Phase 1 — verify hedger and IBKR guard before any live execution; Phase 3 — production hardening with position-age monitor

**Severity:** CRITICAL — partial fills without working hedges are the most common cause of arb bot capital destruction.

---

### Pitfall 4: Market Making Running Into News Events Without Quote Protection

**What goes wrong:**
`market_maker.py` quotes a market at 0.50/0.52. Breaking news moves the true probability to 0.90 within 10 seconds. Before the bot cancels its offer at 0.52, informed traders take every available offer. The bot is now long a position worth $0.10 that it sold for $0.52 — a $0.42/contract loss. At $500 inventory per market (`MM_MAX_INVENTORY` default), this is a $210 loss on a single news event.

**Why it happens:**
Market making in prediction markets depends on being the fastest canceler when news breaks. Prediction markets have documented 40–50 point moves in under 10 seconds on major event developments. The `market_maker.py` engine was built but has never run in production — its cancel latency under load, network round-trip to Railway, and quote refresh rate are all unknown quantities. Running untested market making code on a Railway deployment with $500 inventory limits is a known-bad first deployment pattern.

**How to avoid:**
(1) Before enabling `MM_ENABLED=true`, override `MM_MAX_INVENTORY` to $50–100 in Railway variables, not the $500 default. Only raise after observing multiple complete market cycles. (2) Do not run market making on any market resolving within 48 hours — resolution certainty collapses the spread benefit while maintaining full adverse selection risk. (3) Subscribe `market_maker.py` to the same WebSocket feed used by `continuous.py` and implement a news/event kill switch: when Metaculus fires a significant probability update on a matched market, pause all quoting on that market immediately. (4) Implement quote-poisoning detection: if the bot gets filled on both bid and ask within the same 5-second window, an informed trader is front-running — pause that market's quoting for 60 minutes. (5) Initial market making deployment should target illiquid, long-horizon (>30 day) markets where news moves are gradual, not short-horizon political markets.

**Warning signs:**
- Inventory on a single market accumulates to >50% of `MM_MAX_INVENTORY` in under 1 hour (one-sided fill pressure)
- Fill rate on offers (sells) consistently higher than fill rate on bids (buys) — informed traders systematically taking your offers
- Market making P&L is positive in early hours then reverses sharply — early wins masking adverse selection buildup

**Phase to address:**
Phase 2 — market making initial deployment with conservative limits and kill switches in place

**Severity:** HIGH — untested market making in prediction markets is one of the most common causes of rapid capital loss for bot operators.

---

### Pitfall 5: Adding Layer 4/5 Strategies Before Layer 1 Is Proven Profitable

**What goes wrong:**
New strategies (informed trading, convergence, Kelly sizing, fund rebalancing) are enabled before the Layer 1 pure arbitrage strategies have executed a single profitable trade. The new strategies have different risk profiles, different execution paths, and share the executor, risk manager, and SQLite database with existing strategies. When something breaks, it is impossible to isolate whether the failure is in the new strategy, the existing strategies, or shared infrastructure.

**Why it happens:**
The 20-strategy architecture creates pressure to enable all strategies simultaneously. Operators underestimate interaction effects: multiple strategies competing for the same limited capital in `risk_manager.py`, Kelly sizing amplifying losses on uninvalidated signals, convergence trades based on cross-platform price history that doesn't exist yet. Knight Capital's 2012 $440M loss in 45 minutes is the canonical example of enabling untested strategy code alongside production code without isolation.

**How to avoid:**
Enforce a strict strategy activation sequence keyed to production P&L milestones. Layer 1 pure arb goes live first; all other layers are disabled. Layer 2 near-arb (resolution sniping, stale price) is enabled only after Layer 1 shows positive P&L over 7 days. Layer 3 market making is enabled only after Layer 2 validation, with its own P&L bucket. Layer 4 informed trading (event divergence, convergence) is the last thing enabled before Layer 5 capital optimization — informed trading requires at minimum 30 days of live price history from `snapshot.py` to have any signal quality. Layer 5 Kelly sizing must never be applied to unvalidated strategies — it amplifies both gains and losses; a bad Kelly-sized informed trade can exceed all arb gains in one position.

**Warning signs:**
- Net P&L is negative but individual trade logs show no obvious losing trades (strategy interaction effects)
- `RiskManager` rejection rate increasing as more strategies are enabled (shared capital limits being exhausted)
- `trades.db` contains positions from strategy types that were not supposed to be active yet
- Kelly sizing is enabled on strategies with fewer than 50 historical trades (insufficient sample for Kelly estimate)

**Phase to address:**
Every phase — strategy activation sequence is the primary mitigation

**Severity:** HIGH — complexity added before core validation is proven is the leading architectural cause of trading bot failure.

---

### Pitfall 6: Betfair/Smarkets Market Suspension Stranding Orders Mid-Execution

**What goes wrong:**
A back-all scan identifies a Betfair arb opportunity. Leg 1 (back team A) places successfully. Before leg 2 (lay all other teams) can be placed, the market suspends (in-play event). Outstanding orders are frozen — they cannot be cancelled and cannot fill until the suspension lifts or the market settles. The bot is now exposed to the outcome with a partial hedge.

**Why it happens:**
The back-all/back-lay scans run against live prices but do not check `MarketStatus` before submitting orders. Betfair REST polling can miss the transition from ACTIVE to SUSPENDED between poll intervals. The Exchange Stream API (WebSocket) is the only mechanism that delivers real-time suspension events — a REST-only integration has a blind spot here.

**How to avoid:**
(1) Before every Betfair/Smarkets order submission, call `list_market_catalogue` and verify `MarketStatus == ACTIVE`. Reject any market with `IN_PLAY` status unless the scan explicitly supports in-play. (2) Subscribe to the Betfair Exchange Stream API for real-time suspension events. When a suspension event arrives for a market with pending orders, immediately halt new submissions and trigger `hedger.py` for any filled-but-unhedged legs. (3) Configure the `betfair_api.py` circuit breaker to trip on `MARKET_SUSPENDED` error codes — do not retry suspended market orders, as suspension can last minutes. (4) Treat Betfair/Smarkets back-all arbs as highest-urgency for the leg-2 timeout described in Pitfall 3 — use a 3-second leg-2 deadline, not 5.

**Warning signs:**
- Betfair orders sitting in `EXECUTION_COMPLETE: PENDING` status for more than 5 minutes
- Market status returns `SUSPENDED` on a market with open positions
- Back-all scan finding arbs on same-day-resolution markets (highest suspension risk)

**Phase to address:**
Phase 1 — Betfair/Smarkets production integration; Phase 3 — Exchange Stream API subscription

**Severity:** HIGH — this is the mechanism by which exchange arbitrage goes from risk-free to stranded most often.

---

### Pitfall 7: Polymarket Gas Wallet Depletion Causing Silent Execution Failures

**What goes wrong:**
Polymarket requires MATIC/POL in the gas wallet (the address derived from `POLYMARKET_PRIVATE_KEY`) for Polygon transaction fees, even with gasless CLOB trading. When the gas wallet balance drops to zero, every Polymarket order fails at the transaction layer. The bot continues scanning, finding opportunities, and attempting execution — all returning success at the API layer but failing silently at the chain layer. The operator has no indication until they notice the trade volume has gone to zero.

**Why it happens:**
`gas_monitor.py` was built to monitor Polygon gas price for fee threshold calculations — it does not monitor the wallet's native token balance. In 24/7 production mode, gas is consumed on every Polymarket order. Under high-frequency execution, the wallet can deplete within days without an alert.

**How to avoid:**
Add the gas wallet's MATIC/POL balance as a Prometheus gauge in `metrics.py`. Expose it on the `/metrics` endpoint and display it on the dashboard. Set a Railway alert or webhook alert at <$10 equivalent (suggest immediate execution halt at <$2 equivalent). Add a startup pre-flight check in `continuous.py` that refuses to enable Polymarket execution if the wallet balance is below the minimum. Additionally, verify CLOB operator allowance status — if the operator approval expired, all orders fail silently for a different reason than gas.

**Warning signs:**
- Polymarket scan finds opportunities, executor logs "attempting execution," but zero fills appear in `trades.db`
- `polymarket_api.py` returns HTTP 200 on order submission but the order never appears in active orders
- Gas wallet balance metric is absent from dashboard (monitoring gap itself is a warning sign)

**Phase to address:**
Phase 1 — pre-deployment production checklist; must be in place before any live capital

**Severity:** HIGH — silent failures with real capital deployed are the highest-priority monitoring gap.

---

### Pitfall 8: Fuzzy Market Matching Pairing Correlated-But-Different Markets

**What goes wrong:**
`matcher.py` uses Levenshtein distance to pair markets across platforms. A title match above `FUZZY_MATCH_THRESHOLD` is treated as "same event." Markets about correlated-but-different events score high: "Will the Fed raise rates in Q3?" (Kalshi) matches "Will the Fed raise rates before year-end?" (Polymarket) at 0.82 similarity. The bot holds opposing positions on what it believes is the same event — but the resolution dates are different, making this a directional timing bet, not a risk-free arb.

**Why it happens:**
`thefuzz` uses Levenshtein distance, which treats substring matches as semantically close. Short market titles with shared keywords score high regardless of whether the resolution criteria match. Unit tests for `matcher.py` typically test exact or near-exact match scenarios — they do not cover the "semantically related but different" failure mode.

**How to avoid:**
(1) Raise `FUZZY_MATCH_THRESHOLD` to 0.90+ for cross-platform execution (current value may be set lower for discovery). (2) Add a resolution-date proximity check alongside title matching: only treat markets as matched if resolution dates are within 7 days of each other. Refuse to execute cross-platform arbs on pairs with >7-day resolution date spread. (3) During Phase 1 dry-run, log all fuzzy matches with their score. Manually review the top-50 lowest-scoring accepted matches and add false positives to a deny-list in `matcher.py`. (4) After 30 days of production, use `trades.db` cross-platform P&L per market pair to retrospectively identify matches that were profitable vs. losers — losers should be reviewed for resolution-criteria mismatch.

**Warning signs:**
- Cross-platform P&L negative despite correct fee calculations and confirmed fills on both legs
- Multi-cross scan producing opportunities on pairs with >30-day resolution date spread
- Any logged match below 0.88 score that would have triggered execution in dry-run

**Phase to address:**
Phase 1 — raise threshold and add resolution-date check before first cross-platform live execution

**Severity:** MEDIUM-HIGH — resolution-criteria mismatch converts apparent arb into directional bet.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Single global revalidation threshold for all 20 strategies | One knob to tune | Layer 1 arbs have tight 1–3% margins; Layer 4 informed trades have 10–15% margins; one threshold causes either over-rejection on arb or under-rejection on informed — both states lose money | Never for Layer 4+ — implement per-strategy-class thresholds before Phase 2 |
| `scanner.py` facade re-exporting everything | Backward compat for tests | Test patches `scanner.X` not the source module; obscures which module is actually running; mispatches cause false test passes | Acceptable indefinitely IF tests are updated to patch at source module, not facade |
| SQLite WAL for all trade writes | Simple, no external DB dependency | Single writer constraint; ThreadPoolExecutor with 20 strategy types at high frequency will produce lock contention; lock timeouts cause missed DB records for executed trades | Acceptable for Phase 1 volume (<20 trades/hour); evaluate queue-serialized writes or PostgreSQL if throughput exceeds this |
| HARDEN-04 skip (no Retry-After header parsing) | Faster to implement; tenacity backoff covers most cases | Reactive 429 handling means violations accumulate before tenacity kicks in; at higher scan frequency, exchange APIs can issue temporary bans (Betfair: 20-minute ban after 100 logins/minute) | Acceptable for Phase 1 low-volume; must fix before enabling Layer 2 scan frequency |
| `MM_MAX_INVENTORY=500` default in config.py | Realistic production target | Too large for untested market making; first news event causes $210 loss per market before inventory can be adjusted | Acceptable in config.py but Railway variables MUST override to $50–100 before `MM_ENABLED=true` |
| IBKR as standalone binary scanner only (no sell) | Simplifies auth/execution | Capital locked in IBKR positions until contract expiry; any bug in position sizing or strategy logic strands capital for weeks | Acceptable — architecture doc correctly documents this constraint; enforce with code guard in `_build_legs()` |

---

## Integration Gotchas

Common mistakes when connecting to external services.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Polymarket CLOB revalidation | Fetching each token's order book individually during revalidation | Use batch `/books` endpoint (500 req/10s vs. 1,500/10s for single) — up to 50x fewer requests per revalidation cycle |
| Polymarket rate limits | Treating burst limit (3,500/10s for POST /order) as the operational limit | Sustained limit is 36,000/10min (60/s avg); burst headroom depletes in <10 seconds of high-frequency scanning; monitor `X-RateLimit-Remaining` header and apply backpressure at 20% remaining |
| Kalshi | Not distinguishing read vs. write rate limits | Read: 20 req/s (Basic tier); Write: 10 req/s — scan code that calls CLOB write endpoints in a scan loop will hit the lower write limit, not the read limit shown in docs |
| Betfair | Using REST polling for market status | REST polling can miss ACTIVE→SUSPENDED transitions between polls; Exchange Stream API is the only source of real-time suspension events |
| IBKR ForecastEx | Expecting sub-second fills like CLOB platforms | IBKR LMT orders on thin ForecastEx markets can take hours to fill or never fill; never use IBKR as a time-sensitive execution leg |
| Gemini Predictions | Using millisecond-precision nonces | Gemini requires second-precision nonces (`str(int(time.time()))`) within 30s of server time; millisecond nonces cause silent auth failure |
| Matchbook | Not refreshing session auth | Matchbook uses username/password session auth; sessions expire and the circuit breaker will trip on expiry errors, pausing all Matchbook execution until restart |
| Railway env vars | Updating Railway variables assuming the bot picks them up immediately | Railway injects env vars at container start; updating a variable requires a new deployment. `config.py`'s attribute-access reload works for runtime changes but only if the var was in the original deployment. |
| SQLite WAL concurrent writes | Assuming WAL mode eliminates write contention | WAL mode allows concurrent readers and one writer; multiple `ThreadPoolExecutor` threads competing for writes produce lock timeouts; the application-level threading lock in `TradeDB` must be used consistently on all write paths |

---

## Performance Traps

Patterns that work at small scale but fail as usage grows.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Scanning all 20 strategy types every cycle | Scan cycle duration grows to exceed the rescan interval; opportunities discovered are already stale by the time execution is attempted | Tier strategies by urgency — Layer 1 pure arb scans every cycle; Layer 4 informed trading scans every 5 cycles; Layer 5 capital ops run once per hour | Immediately at >60s rescan interval with 20 strategies active in continuous mode |
| WebSocket subscription to all active markets | `WS_SUBSCRIPTION_LIMIT` hit; new markets never get real-time feeds; OpportunityIndex grows unbounded | Subscribe to markets with recent historical opportunity hits first; prune subscriptions for markets with no hits in 24 hours | At 200+ active market subscriptions |
| SQLite single-writer under ThreadPoolExecutor | `database is locked` errors in logs; DB write failures for executed trades (ghost positions) | Serialize all DB writes through `TradeDB`'s threading lock; consider a single dedicated writer thread queue at high throughput | At >10 concurrent trade executions |
| Price cache as plain dict with 60s TTL | Stale prices used for revalidation; opportunities that no longer exist pass validation and execute at a loss | Enforce TTL check on every cache read in executor — not just on writes; add explicit staleness assertion before `_revalidate` | At scan cycles faster than 60s TTL, which is the steady state in continuous mode |
| OpportunityIndex accumulating closed market entries | Index grows unbounded; closed-market entries trigger spurious WS-triggered executions | Prune OpportunityIndex on market resolution events; add a periodic cleanup sweep for entries older than 24 hours | After weeks of continuous operation — O(1) lookup degrades to noisy false positives |

---

## Security Mistakes

Domain-specific security issues.

| Mistake | Risk | Prevention |
|---------|------|------------|
| Ethereum private key in Railway env vars without rotation schedule | Full Polymarket account compromise if Railway credentials are accessed | Rotate quarterly minimum; use Railway sealed variables so the value is never retrievable via UI or API after setting |
| Kalshi RSA private key stored as base64 env var | RSA key exposure gives full Kalshi account access including withdrawals | Prefer file-based key injection via Railway volumes over base64 env var; rotate annually; confirm key is set to "no withdrawal" permission |
| IBKR TWS socket accessible from Railway directly | TWS API open to network means any actor who discovers the Railway internal IP can submit IBKR orders | IB Gateway must run on a private host behind a VPN or SSH tunnel, not on Railway; Railway → IB Gateway must be an authenticated connection |
| Betfair/Smarkets/Gemini API keys with withdrawal permissions | Leaked key enables draining exchange balance, not just trading | Scope every exchange API key to "place orders only" — disable withdrawal rights on all platforms that support permission scoping |
| Error logging including API key values | Keys logged on auth failures are stored in Railway log history indefinitely | Audit all `logger.error` and `logger.exception` calls in `*_api.py` modules — ensure API key variables are never included in log message strings |

---

## "Looks Done But Isn't" Checklist

- [ ] **Revalidation fix:** Committed locally but Railway is still running the pre-fix version. Verify: Railway deployment commit hash matches the fix commit (`5506f0c` or later).
- [ ] **Market making inventory limit:** `MM_MAX_INVENTORY` default is $500 in config.py. Verify: Railway variables explicitly set `MM_MAX_INVENTORY=50` before `MM_ENABLED=true` is set.
- [ ] **IBKR guard in executor:** Architecture doc says IBKR is BUY-only. Verify: `executor.py:_build_legs()` has an explicit guard that raises if IBKR appears as leg 2 in any cross-platform opportunity.
- [ ] **Integration tests:** 18/19 HARDEN-01 integration tests skip without live credentials. Verify: at minimum the cross-platform execution happy-path test runs with real Polymarket+Kalshi credentials before Phase 1 declares complete.
- [ ] **Stale price detection:** `stale.py` logs an informational warning that it produces no results in one-shot mode. Verify: Railway deployment uses `--continuous` flag in the Docker entrypoint (check Dockerfile CMD).
- [ ] **Backtest feedback loop:** Nightly backtest is built. Verify: a Railway cron job or equivalent is actually scheduled — backtest does not self-trigger.
- [ ] **Platform balance monitoring:** All 8 platforms require funded accounts. Verify: platform balance metrics are visible in the dashboard with non-zero values before enabling each platform's strategies.
- [ ] **Gas wallet balance:** `gas_monitor.py` monitors gas price, not wallet balance. Verify: MATIC/POL wallet balance is exposed as a Prometheus gauge and has an alert threshold configured.
- [ ] **Resolution sniping threshold:** `resolution.py` uses "near-certain outcome at a discount" logic with a threshold not validated against live data. Verify: threshold produces <5% false positive rate in 72h dry-run before enabling live execution.
- [ ] **Betfair market status check:** Back-all scan does not verify `MarketStatus == ACTIVE` before submitting orders. Verify: `betfair_api.py` calls `list_market_catalogue` before every order submission and rejects `IN_PLAY` or `SUSPENDED` markets.

---

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Oracle manipulation destroying cross-platform position | HIGH | (1) Close whichever leg can be closed immediately. (2) For Polymarket UMA manipulation: file a dispute during the 2-hour dispute window via the UMA governance portal. (3) Accept max loss on the non-disputable leg. (4) Add market to `matcher.py` deny-list. (5) Post-mortem: increase minimum spread floor for governance-dependent markets. |
| Partial fill with unwindable IBKR leg | MEDIUM | (1) Size IBKR positions small enough ($25–50) that full loss is acceptable at expiry. (2) Classify IBKR open positions in `risk_manager.py` as "unhedgeable" category — they count against daily loss limit but cannot be force-closed. (3) Wait for contract expiry. |
| Market making adverse selection accumulation | HIGH | (1) Set `MM_ENABLED=false` immediately via Railway variables + redeploy. (2) Close inventory positions manually via each platform's UI — accept slippage. (3) Post-mortem: was it quote-poisoning (cancel lag) or event risk (news before kill switch fired)? (4) Re-enable only after implementing the specific prevention that failed. |
| Betfair temporary ban (TEMPORARY_BAN_TOO_MANY_REQUESTS) | LOW | (1) 20-minute automatic recovery — circuit breaker will resume when the ban window expires. (2) Reduce Betfair scan frequency for the following 24 hours. (3) Implement Retry-After header parsing (HARDEN-04) to detect approaching limits before violations occur. |
| SQLite DB corruption after crash | MEDIUM | (1) Run `recovery.py:reconcile_orphaned_positions()` — this is already built and handles all 8 platforms. (2) SQLite WAL mode provides integrity guarantees — most corruption recovers via WAL rollback automatically on next open. (3) Restore from Railway volume backup snapshot if WAL rollback fails. |
| 100% revalidation rejection regressing after fix deployment | LOW | (1) Check Railway deployment commit hash matches the fix. (2) Enable `DRY_RUN=true` and inspect rejection logs — check if the 10% tolerance is being applied vs. a config override. (3) Verify WS cache TTL is not 0 (which would cause all WS-triggered revalidations to use stale prices). |
| Polymarket gas wallet depleted | LOW | (1) Fund the gas wallet address with MATIC/POL via Polygon bridge or exchange transfer. (2) Bot resumes automatically — no Railway restart needed. (3) Add balance alert to prevent recurrence (see Pitfall 7 prevention). |
| Fuzzy match false positive causing directional loss | MEDIUM | (1) Close the losing leg at market price immediately. (2) Add the false-positive market pair to `matcher.py` deny-list. (3) Raise `FUZZY_MATCH_THRESHOLD` and add resolution-date proximity filter (Pitfall 8 prevention). (4) Post-mortem review of all existing matches below new threshold. |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Revalidation threshold miscalibrated | Phase 1 — deploy fix + 72h dry-run | Pass rate 5–30%; distribution logged per strategy class |
| Oracle/resolution mismatch | Phase 1 — before any cross-platform execution | Resolution metadata added to matcher; zero cross-platform positions on divergent-criteria markets |
| Partial fill with naked position | Phase 1 — verify hedger + IBKR guard before any live capital | No `trades.db` orphaned single-leg positions after 24h live trading; IBKR guard unit tested |
| Market making adverse selection | Phase 2 — conservative MM with kill switch | MM P&L positive after 7 days at $50–100 max inventory; no single news-event wipeout |
| Strategy complexity before core proven | Every phase — strategy activation sequence | Each layer only enabled after previous layer shows positive P&L over 7 days |
| Betfair market suspension | Phase 1 — market status check before orders | Zero Betfair orders placed on suspended markets in dry-run logs |
| Polymarket gas wallet depletion | Phase 1 — balance metric before first live trade | Balance gauge visible in dashboard; alert fires when tested with near-empty wallet |
| Fuzzy match misidentification | Phase 1 — raise threshold + resolution-date check | Dry-run match log reviewed; all matches below 0.88 inspected; deny-list updated |
| SQLite write contention | Phase 3 — if throughput exceeds 20 trades/hour | DB write latency p99 under 100ms in load test |
| API credential expiry | Phase 3 — auth health check in continuous.py | Health check runs every 30 minutes; alert fires on auth failure |

---

## Sources

- [Polymarket Rate Limits Guide (March 2026)](https://agentbets.ai/guides/polymarket-rate-limits-guide/) — Official tier limits, throttling behavior, retry patterns with jitter
- [Building a Prediction Market Arbitrage Bot: Technical Implementation](https://navnoorbawa.substack.com/p/building-a-prediction-market-arbitrage) — Leg risk, timing constraints, oracle manipulation case study, execution failure modes
- [Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets (IMDEA, Aug 2025)](https://arxiv.org/abs/2508.03474) — $40M extracted from Polymarket; 78% of arb opportunities in low-volume markets failed due to execution inefficiencies
- [Market Making on Prediction Markets: Complete 2026 Guide](https://newyorkcityservers.com/blog/prediction-market-making-guide) — Inventory risk, adverse selection, event risk, quote poisoning in production
- [Oracle Manipulation in Polymarket 2025](https://orochi.network/blog/oracle-manipulation-in-polymarket-2025) — March 2025 UMA whale manipulation of $7M market; governance attack mechanics
- [How Kalshi and Polymarket Settle Event Contracts](https://defirate.com/prediction-markets/how-contracts-settle/) — Resolution divergence cases including government shutdown, Cardi B Super Bowl
- [Betfair Exchange API FAQ](https://developer.betfair.com/en/exchange-api/faq/) — Market suspension handling, login rate limit (100/min = 20-min ban)
- [SQLite WAL Mode Concurrent Write Analysis](https://blog.skypilot.co/abusing-sqlite-to-handle-concurrency/) — Single writer constraint, production throughput limits, serialized-writer queue pattern
- [Common Pitfalls to Avoid When Building Your First Crypto Trading Bot](https://coinbureau.com/guides/crypto-trading-bot-mistakes-to-avoid/) — Knight Capital Group $440M case study, deployment stage gates
- [Crypto Trading Bot Pitfalls, Risks & Mistakes to Avoid in 2025](https://www.gate.com/news/detail/13225882) — 73% of automated accounts fail within 6 months, monitoring requirements
- [Cross-Market Arbitrage on Polymarket: Bots vs Sportsbooks](https://www.quantvps.com/blog/cross-market-arbitrage-polymarket) — Practical execution timing, order book depth limits

---

*Pitfalls research for: Polymarket Arb Scanner v2.0 — profitability tuning and strategy expansion on existing production system*
*Researched: 2026-04-01*
