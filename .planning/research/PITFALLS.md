# Pitfalls Research

**Domain:** Prediction market trading bot — production deployment and operational risks
**Researched:** 2026-04-01
**Confidence:** HIGH (deployment patterns well-documented), MEDIUM (prediction market-specific operational risks)

---

## Critical Pitfalls (Severity: HIGH — can lose money)

### P1: Deploying Revalidation Fix Without Staged Rollout
- **Warning sign:** Pushing revalidation fix directly to production Railway, bot immediately starts executing trades with untested thresholds
- **What goes wrong:** The >= 2% ROI floor may be too low for some strategy types, allowing marginal trades that lose money after slippage. Or too high, continuing to reject everything.
- **Prevention:** Deploy with `DRY_RUN=true` first. Monitor revalidation pass/fail rates for 24h. Compare passed opportunities against actual platform prices manually. Only flip `DRY_RUN=false` after confirming pass rate is reasonable (5-30%) and passed opportunities are real.
- **Phase:** First thing in v2.0 — before any new strategy work

### P2: Starting with Too Much Capital
- **Warning sign:** Funding all 8 platforms with significant capital before any successful trade
- **What goes wrong:** A bug in leg construction, partial fill handling, or fee calculation could cause systematic losses across all platforms simultaneously. The hedger may not activate correctly on the first real partial fill.
- **Prevention:** Start with minimum viable capital on 2 platforms (Polymarket + Kalshi — best-tested). Run for 7 days. Scale up capital and platforms only after net positive P&L confirmed.
- **Phase:** Early v2.0 — alongside revalidation fix

### P3: Revalidation Threshold Not Strategy-Aware
- **Warning sign:** Single MIN_NET_ROI threshold for all 20 strategy types
- **What goes wrong:** Structural arbs (Layer 1) have tight, deterministic margins — a 2% floor is appropriate. But resolution sniping and news-driven trades have wider, faster-moving margins — a 2% floor may reject trades that would have been 15% profitable because the price was moving during revalidation.
- **Prevention:** Implement per-strategy-layer revalidation thresholds: Layer 1 (2%), Layer 2 (5%), Layer 3 (3%), Layer 4 (10%), Layer 5 (N/A — capital ops don't need revalidation). This is a config change, not architectural.
- **Phase:** Revalidation tuning phase

### P4: Cross-Platform Oracle Resolution Divergence
- **Warning sign:** Bot holds opposing positions on Polymarket and Kalshi for the same event
- **What goes wrong:** Polymarket uses UMA optimistic oracle (manipulable — March 2025 incident). Kalshi uses CFTC-regulated resolution. Same event can resolve differently across platforms, turning a "risk-free" arb into a total loss.
- **Prevention:** Tag markets by oracle type. For cross-platform positions, require minimum spread of 15 cents (not 2-3 cents) to compensate for oracle divergence risk. Flag high-risk markets (governance-dependent outcomes).
- **Phase:** Cross-platform arb hardening

### P5: Taker Fee Erosion Under New Polymarket Fee Structure
- **Warning sign:** Bot profitably detecting arbs at mid-price but losing money after execution
- **What goes wrong:** Polymarket introduced taker fees up to 1.8% at 50% probability (March 2026). A 3% gross arb with 1.8% taker fee on each leg = net loss. The existing fee calculations may use outdated rates.
- **Prevention:** Verify `fees.py` uses current Polymarket taker fee formula: `ceil(0.07 * C * P * (1-P))` per contract. Switch to maker (limit) orders wherever possible — 0% fee. Audit `GEMINI_FEE_RATE` and Kalshi fee formula too.
- **Phase:** Fee audit — immediate

### P6: Partial Fill Creates Unhedged Directional Exposure
- **Warning sign:** Leg 1 of a 2-leg arb fills, leg 2 doesn't
- **What goes wrong:** Bot is now long one side of a market with no hedge. If the market moves against it, losses can exceed the original arb profit.
- **Prevention:** `hedger.py` exists for this — confirm it activates within 5 seconds of leg 2 failure. Confirm hedger covers all 8 trading platforms (not just Polymarket/Kalshi). Test with intentional partial fill simulation.
- **Phase:** Execution hardening

---

## Moderate Pitfalls (Severity: MEDIUM — degrades profitability)

### P7: Adding New Strategies Before Proving Existing Ones
- **Warning sign:** Building S21-S30 while the existing 20 strategies haven't executed a single real trade
- **What goes wrong:** Complexity increases without validation. New strategies may interact with existing ones in unexpected ways (competing for capital, triggering risk limits, creating conflicting positions).
- **Prevention:** Prove profitability on the simplest strategies first (binary arb, cross-platform 2-way). Only add new strategies after at least 3 existing strategies are profitable in production.
- **Phase:** After initial profitability validation

### P8: WebSocket Feed Disconnection Not Detected
- **Warning sign:** Bot continues operating with stale prices after WS disconnection
- **What goes wrong:** Stale prices in the price cache cause revalidation to pass (prices match cached data) but the actual market has moved. Trades execute at stale prices and lose money.
- **Prevention:** Add WS heartbeat monitoring. If no WS message received in 30s, mark price cache as stale. Raise revalidation threshold for stale-tagged prices. Alert on prolonged WS disconnection.
- **Phase:** Monitoring hardening

### P9: SQLite Write Contention Under Concurrent Execution
- **Warning sign:** Multiple WS-triggered executions happening simultaneously in continuous mode
- **What goes wrong:** SQLite WAL mode handles concurrent reads well but concurrent writes can timeout. If `db.log_trade()` and `db.log_opportunity()` contend, one may fail and the trade isn't logged — creating ghost positions.
- **Prevention:** WAL mode with busy_timeout already set. Verify `busy_timeout` is >= 5000ms. Monitor SQLite lock errors in logs. If contention appears, batch writes or use write queue.
- **Phase:** Operational monitoring

### P10: API Credential Rotation and Expiry
- **Warning sign:** API keys or sessions expire while bot is running
- **What goes wrong:** Circuit breaker trips on auth errors. Bot stops trading on that platform. If it's a hedging platform, partial fills on other platforms become unhedgeable.
- **Prevention:** Monitor auth status on all 8 platforms at startup and periodically (every 30 min). Alert on approaching expiry. Kalshi sessions expire; Betfair SSO tokens expire; Smarkets sessions can expire.
- **Phase:** Operational monitoring

### P11: Market Making Without Position Limits Enforced
- **Warning sign:** MM engine accumulates large inventory in one direction
- **What goes wrong:** If the market resolves against the accumulated position, losses can be substantial. The `inventory_tracker.py` exists but its limits need to be validated against real capital.
- **Prevention:** Set `MM_MAX_INVENTORY` conservatively ($100-200 per market initially, not $500). Add position-level alerts when inventory exceeds 50% of max. Confirm inventory hedging (cross-platform MM) works before activating passive MM.
- **Phase:** Market making activation

### P12: Gas Spikes on Polygon During High-Activity Periods
- **Warning sign:** Election night, major event resolution — gas prices spike 10-50x
- **What goes wrong:** Polymarket trades on Polygon. Gas costs eat into or exceed arb profits. `gas_monitor.py` exists but thresholds may not be calibrated for extreme events.
- **Prevention:** Set gas ceiling at 2x normal. During known high-activity events, pre-position rather than trying to arb during the spike. Monitor `POLYGON_RPC_URL` reliability.
- **Phase:** Gas monitoring tuning

---

## Low Pitfalls (Severity: LOW — minor inefficiency)

### P13: Backtesting Overfitting Thresholds
- **Warning sign:** Optimizing MIN_NET_ROI using historical data produces suspiciously good results
- **What goes wrong:** Thresholds overfit to past market conditions. When conditions change, optimized thresholds perform worse than defaults.
- **Prevention:** Use out-of-sample validation. Split historical data: 70% train, 30% test. Only adopt thresholds that improve on both sets. Keep thresholds conservative.
- **Phase:** Backtesting optimization

### P14: Dashboard Performance Degradation Over Time
- **Warning sign:** Dashboard responses slow down as trades.db grows
- **What goes wrong:** P&L queries over growing trade history take increasingly long. Dashboard becomes unusable.
- **Prevention:** Use pre-aggregated `strategy_equity` table for dashboard queries (from ARCHITECTURE research). Only run full P&L recalculation as background job. Add index on `trades.created_at`.
- **Phase:** Dashboard optimization

### P15: Rate Limit Exhaustion During Multi-Platform Scans
- **Warning sign:** 429 errors increasing during scan cycles with all 20 strategies active
- **What goes wrong:** Each scan cycle makes dozens of API calls across 8 platforms. During high-activity periods, rate limits are hit and opportunities are missed.
- **Prevention:** Implement scan deduplication — skip markets with no recent price change. Stagger platform scans across the interval rather than hitting all simultaneously. Monitor rate limit headers.
- **Phase:** Scan optimization

---

## Pitfall Prevention Checklist (Pre-Deployment)

- [ ] Revalidation fix deployed with DRY_RUN=true for 24h first
- [ ] Fee calculations verified against current platform fee structures
- [ ] Hedger tested with simulated partial fill on each platform
- [ ] Capital deployed on 2 platforms only (Polymarket + Kalshi) initially
- [ ] WS heartbeat monitoring enabled
- [ ] API credential health check on all 8 platforms
- [ ] MM inventory limits set conservatively ($100-200/market)
- [ ] Gas ceiling configured for Polygon
- [ ] Circuit breaker states verified clean on startup
- [ ] Kill switch tested (dashboard pause button stops all trading)
- [ ] 7-day dry run shows reasonable revalidation pass rate (5-30%)
- [ ] At least 1 manual trade on each platform to verify credentials

---

*Pitfalls research for: Polymarket Arb Scanner v2.0 — production deployment risks*
*Researched: 2026-04-01*
