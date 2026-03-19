# Phase 1: Wire & Enable - Research

**Researched:** 2026-03-19
**Domain:** Python integration — wiring existing modules into live pipeline, feature flag enablement
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Fee Routing Placement (INTEG-01)**
- Dual-layer approach: scan attaches `_fee_path` hint, executor re-validates at trade time
- Scan-time: `find_lowest_fee_path()` runs during scanning, attaches single best path as `_fee_path` key on opportunity dict
- Execution-time: executor calls `find_lowest_fee_path()` again with fresh fee data to confirm or override the scan hint
- Scope: apply to ALL cross-platform opportunities, not just multi-path arbs
- Path data: best path only (single `_fee_path` entry), not ranked list — keeps opportunity dicts lean

**Market Making Parameters (ENABLE-01)**
- Platforms: enable MM on ALL platforms with bid/ask support (not just Polymarket)
- Inventory limit: $500 per market initial cap
- Minimum spread: 2% minimum spread width for quotes (e.g., bid 0.48, ask 0.52)
- `MM_ENABLED` flag gates the entire engine — when true, all supported platforms participate

**Feature Enablement Strategy**
- Enable all 4 flags simultaneously in this phase: `MM_ENABLED`, `SNAPSHOT_ENABLED`, `DYNAMIC_FEE_ENABLED`, `EVENT_MONITOR_ENABLED`
- Rationale: Phase 1 makes everything reachable; Phase 2 validates with real data. `DRY_RUN` stays true so nothing trades yet.

**Bankroll Refresh (INTEG-04)**
- Trigger: both timer-based (every 5 minutes) AND post-trade (immediate refresh after any trade execution)
- Scope: query ALL 8 platform balances, not just platforms with active positions
- Wire `update_bankroll()` in `continuous.py` with both trigger paths

### Claude's Discretion
- Whether to change `config.py` defaults to `true` or keep as `false` with Railway env var overrides (leaning toward keeping defaults safe for local dev)
- Exact error handling for platform balance queries that fail during bankroll refresh (skip platform vs use cached value)
- Internal implementation of fee path hint attachment (new helper function vs inline in scan modules)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| INTEG-01 | Wire `find_lowest_fee_path()` into cross-platform scans | `find_lowest_fee_path()` is fully built (fees.py:948-1004) and tested; needs call-site in `scan_cross_platform()` (after CLOB refinement) and in `executor.py:_build_legs()` for the Cross family of types |
| INTEG-02 | Fix MM one-shot `dry_run` hardcode | Single-line fix at cli.py:488 — replace `dry_run=True` with `dry_run=executor.dry_run` (or `dry_run=args.dry_run`) |
| INTEG-03 | Add Kalshi to continuous mode resolution scan | continuous.py:1009-1017 calls `scan_resolution_snipes(poly_markets, ...)` only; needs a parallel call for `kalshi_markets` (already available in scope) |
| INTEG-04 | Wire `update_bankroll()` into continuous mode | `update_bankroll()` exists in position_sizer.py:328-337; continuous.py has no bankroll refresh — needs timer + post-trade triggers |
| INTEG-05 | Document stale scan one-shot behavior | No code change; CLAUDE.md update only |
| ENABLE-01 | Enable market making with safe defaults | MM_ENABLED config flag exists (config.py:243); continuous.py already checks `MM_ENABLED` and initializes MarketMaker correctly when true; config defaults and Railway vars are the only work |
| ENABLE-02 | Enable snapshot recording for backtesting | SNAPSHOT_ENABLED config flag exists (config.py:292); continuous.py already gates snapshot_recorder on this flag; Railway env var is the only work |
| ENABLE-03 | Enable dynamic fee monitoring | DYNAMIC_FEE_ENABLED config flag exists (config.py:231); Railway env var + POLYGON_RPC_URL are the only work |
| ENABLE-04 | Enable event monitor for signal aggregation | EVENT_MONITOR_ENABLED config flag exists (config.py:236); Railway env var is the only work |
| ENABLE-05 | Configure all 8 platform credentials in Railway | Documentation + Railway dashboard configuration; no code change |
</phase_requirements>

---

## Summary

This phase is a pure integration and enablement phase — zero new algorithms, only wiring. Every asset targeted for integration already exists and is tested in isolation; the gaps are all at call-site level. The riskiest change is INTEG-01 (fee path wiring) because it touches two files (`scans/cross.py` and `executor.py`) and must not break the existing opportunity dict contract that flows through the system. The remaining four integrations (INTEG-02 through INTEG-05) are each one to ten lines. All five ENABLE items are configuration-only — the engines already initialize themselves from flags.

The key architectural insight is that `continuous.py` already correctly gates `MarketMaker`, `SnapshotRecorder`, and other feature-flagged components on their respective config booleans. The only gap is that the flags default to `false` in `config.py` and no Railway env vars override them yet. For `update_bankroll()`, there is literally no call site in `continuous.py` — the `PositionSizer` is initialized in `cli.py` and passed to `executor.py` via `position_sizer=` but `continuous.py` never imports or calls `update_bankroll()`.

**Primary recommendation:** Keep `config.py` defaults as `false` for local safety; set `MM_ENABLED=true`, `SNAPSHOT_ENABLED=true`, `DYNAMIC_FEE_ENABLED=true`, `EVENT_MONITOR_ENABLED=true` as Railway env vars. Wire `_fee_path` at the bottom of `scan_cross_platform()` and `scan_cross_all()` after CLOB refinement, and add executor-side re-validation in the Cross branch of `_build_legs()`.

---

## Standard Stack

This phase requires no new dependencies. All tools are already present.

| Module | Location | Role in This Phase |
|--------|----------|--------------------|
| `fees.find_lowest_fee_path()` | fees.py:948 | Fee path hint at scan time and revalidation at execution time |
| `position_sizer.PositionSizer.update_bankroll()` | position_sizer.py:328 | Called post-trade and on 5-minute timer in continuous loop |
| `market_maker.MarketMaker` | market_maker.py | Already initialized in continuous.py when `MM_ENABLED` is true |
| `snapshot.SnapshotRecorder` | snapshot.py | Already initialized in continuous.py when `SNAPSHOT_ENABLED` is true |
| `scans.resolution.scan_resolution_snipes()` | scans/resolution.py | Platform-agnostic; just needs a second call with Kalshi markets |
| `executor._fetch_balances()` | executor.py:744 | Reuse for bankroll refresh — fetches all 8 platform balances in parallel |

**Installation:** No new packages needed.

---

## Architecture Patterns

### Opportunity Dict Contract (CRITICAL — do not break)

Internal metadata keys use leading underscore prefix. New `_fee_path` key follows this convention:

```python
# Source: scans/cross.py:269-288 pattern, fees.py:994-1002 return value
opp["_fee_path"] = {
    "best_yes_platform": "sxbet",
    "best_no_platform": "polymarket",
    "yes_price": 0.40,
    "no_price": 0.55,
    "total_cost": 0.95,
    "estimated_fees": 0.002,
    "net_profit": 0.048,
}
```

The `_fee_path` key is attached only when `find_lowest_fee_path()` returns non-None. Never mutate the existing `net_profit`, `prices`, or `total_cost` keys at scan time — those reflect the detected arb, not the fee-optimized routing. `_fee_path` is advisory metadata; the executor uses it to confirm or override routing.

### INTEG-01: Fee Path Wiring Pattern

**Scan side — attach hint after CLOB refinement:**

```python
# In scan_cross_platform(), after _refine_cross_with_clob() call
# Source: fees.py:948-1004 function signature
from fees import find_lowest_fee_path

for opp in opportunities:
    platforms = list({opp.get("_platform_a", "polymarket"), opp.get("_platform_b", "kalshi")})
    yes_prices = {p: opp.get(f"_{p}_yes") or 0 for p in platforms}
    no_prices  = {p: opp.get(f"_{p}_no") or 0 for p in platforms}
    fee_path = find_lowest_fee_path(platforms, yes_prices, no_prices)
    if fee_path:
        opp["_fee_path"] = fee_path
```

For `scan_cross_all()` (multi-platform), the prices dict is wider — include all platform prices already attached to the opp.

**Executor side — re-validate in Cross branch of `_build_legs()`:**

```python
# In executor.py:_build_legs(), in the elif opp_type.startswith("Cross") branch
# Re-run find_lowest_fee_path() with fresh prices; use result if net_profit improved
from fees import find_lowest_fee_path

fee_path = opportunity.get("_fee_path")
if fee_path:
    # Confirm or override the scan-time hint with live prices
    fresh = find_lowest_fee_path(
        [fee_path["best_yes_platform"], fee_path["best_no_platform"]],
        {fee_path["best_yes_platform"]: fee_path["yes_price"]},
        {fee_path["best_no_platform"]: fee_path["no_price"]},
    )
    if fresh and fresh["net_profit"] > 0:
        fee_path = fresh
```

### INTEG-02: Fix MM dry_run Hardcode

**Current (cli.py:488):**
```python
mm = MarketMaker(
    min_spread=MM_MIN_SPREAD,
    quote_size=MM_QUOTE_SIZE,
    max_inventory=MM_MAX_INVENTORY,
    max_total_exposure=MM_MAX_TOTAL_EXPOSURE,
    dry_run=True,  # BUG: hardcoded
)
```

**Fix:**
```python
mm = MarketMaker(
    min_spread=MM_MIN_SPREAD,
    quote_size=MM_QUOTE_SIZE,
    max_inventory=MM_MAX_INVENTORY,
    max_total_exposure=MM_MAX_TOTAL_EXPOSURE,
    dry_run=executor.dry_run,  # respect --exec-mode flag
)
```

`executor` is already in scope at line 488; `executor.dry_run` is the canonical source of truth for whether live trading is active. Note: continuous mode already does this correctly at continuous.py:616 (`dry_run=executor.dry_run`). The one-shot path was the only place with the bug.

### INTEG-03: Add Kalshi to Resolution Scan

**Current (continuous.py:1009-1017):**
```python
if args.mode in ("all", "resolution") and poly_markets:
    res_opps = scan_resolution_snipes(poly_markets, platform="polymarket", min_profit=min_profit)
    all_opportunities.extend(res_opps)
```

**Fix — add Kalshi call immediately after:**
```python
if args.mode in ("all", "resolution") and poly_markets:
    res_opps = scan_resolution_snipes(poly_markets, platform="polymarket", min_profit=min_profit)
    all_opportunities.extend(res_opps)

# Add Kalshi resolution snipes
if args.mode in ("all", "resolution") and kalshi_data:
    kalshi_markets = kalshi_data[1] if kalshi_data and len(kalshi_data) > 1 else []
    if kalshi_markets:
        k_res_opps = scan_resolution_snipes(kalshi_markets, platform="kalshi", min_profit=min_profit)
        all_opportunities.extend(k_res_opps)
```

`scan_resolution_snipes()` is platform-agnostic by design — it accepts any `markets` list and a `platform` string. `kalshi_data` is a `(events, markets)` tuple already fetched earlier in the loop; the markets list is `kalshi_data[1]`.

### INTEG-04: Bankroll Refresh in Continuous Mode

`continuous.py` has no reference to `position_sizer` or `update_bankroll()`. The `PositionSizer` is created in `cli.py:1082-1084` and passed to `executor` via `position_sizer=pos_sizer` at `cli.py:1115`. The executor stores it as `self.position_sizer`.

**Pattern — timer-based (every 5 minutes):**

Add a `_last_bankroll_refresh` timestamp alongside existing `_last_snapshot_time`. Inside the continuous loop, after execution, check elapsed time and call refresh:

```python
# At loop-level initialization (alongside _last_snapshot_time = 0.0):
_last_bankroll_refresh = 0.0
_bankroll_refresh_interval = 300.0  # 5 minutes

# Inside the scan loop, after execution block:
now = time.time()
if now - _last_bankroll_refresh >= _bankroll_refresh_interval:
    try:
        balances = executor._fetch_balances("Cross")  # fetches all 8 platforms
        if balances:
            total = sum(v for v in balances.values() if isinstance(v, (int, float)))
            if total > 0 and executor.position_sizer:
                executor.position_sizer.update_bankroll(total)
                logger.info("Bankroll refreshed: $%.2f across %d platforms", total, len(balances))
        _last_bankroll_refresh = now
    except Exception as exc:
        logger.debug("Bankroll refresh failed: %s", exc)
```

**Pattern — post-trade refresh:**

After `executor.execute(opp)` returns `True` (trade executed), trigger an immediate refresh:

```python
if executor.execute(opp):
    executed += 1
    # Immediate bankroll refresh after any trade
    try:
        balances = executor._fetch_balances("Cross")
        if balances and executor.position_sizer:
            total = sum(v for v in balances.values() if isinstance(v, (int, float)))
            if total > 0:
                executor.position_sizer.update_bankroll(total)
    except Exception as exc:
        logger.debug("Post-trade bankroll refresh failed: %s", exc)
```

**Error handling (Claude's discretion):** Skip failed platforms, use sum of available balances. This is the right call — a platform balance fetch failure should not block Kelly sizing for the others. `executor._fetch_balances()` already handles per-platform exceptions with `logger.debug` and skips failed platforms.

### ENABLE-01: Market Making Config

Current `config.py` defaults:
```python
MM_ENABLED = _env_bool("MM_ENABLED", "false")   # line 243
MM_MIN_SPREAD = _env_float("MM_MIN_SPREAD", "0.03")      # 3% default
MM_QUOTE_SIZE = _env_float("MM_QUOTE_SIZE", "5.0")
MM_MAX_INVENTORY = _env_float("MM_MAX_INVENTORY", "50.0")
MM_MAX_TOTAL_EXPOSURE = _env_float("MM_MAX_TOTAL_EXPOSURE", "500.0")
```

The user decision specifies $500/market inventory limit and 2% minimum spread. Current defaults are 3% spread and $50/market inventory — both need updating for production. Recommended approach: keep `config.py` defaults conservative (safe for local dev), override via Railway env vars:

```
MM_ENABLED=true
MM_MIN_SPREAD=0.02
MM_MAX_INVENTORY=500.0
```

`MM_MAX_TOTAL_EXPOSURE=500.0` already matches the user's $500 cap — this is per-engine total, not per-market. To get $500/market, set `MM_MAX_INVENTORY=500.0`. The existing continuous.py MarketMaker initialization (lines 608-619) already correctly uses `executor.dry_run` — no code change needed there.

### Anti-Patterns to Avoid

- **Don't attach `_fee_path` during CLOB refinement stage** — the refinement loop already has significant logic; add the fee path attachment as a post-refinement pass to keep concerns separate.
- **Don't call `update_bankroll()` inside the WS price update callback** (`on_price_update`) — that callback runs in a WS thread and must be minimal latency. Keep bankroll refresh in the main scan loop only.
- **Don't import `find_lowest_fee_path` inside the loop** — import at module top-level in both `cross.py` and `executor.py`. `fees` is already imported in both files.
- **Don't change the existing `_refine_cross_with_clob()` return behavior** — it already mutates opportunity dicts in place; the `_fee_path` attachment should be a separate pass after the function returns, not inside it.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Fee-optimal platform routing | Custom sorting/comparison logic | `find_lowest_fee_path()` in fees.py:948 | Already handles all 8 platform fee structures, gas costs, and net-profit comparison |
| Platform balance aggregation | Per-platform balance calls in continuous.py | `executor._fetch_balances("Cross")` at executor.py:744 | Already parallel-fetches all 8 platforms with per-platform exception handling |
| Kelly-adjusted position sizing | Manual fraction calculation | `executor.position_sizer.size_for_opportunity()` | PositionSizer is already wired into executor; just update the bankroll it uses |
| Kalshi market list in continuous loop | Re-fetching Kalshi data | `kalshi_data[1]` already fetched at top of loop | `kalshi_data = (events, markets)` tuple; markets list is index 1 |

---

## Common Pitfalls

### Pitfall 1: kalshi_data Structure in Continuous Mode

**What goes wrong:** Accessing `kalshi_data` as a flat list vs a tuple.

**Why it happens:** `_fetch_kalshi_data()` returns `(events, markets_by_event_dict)` — a two-element tuple. Code that does `kalshi_data[0]` gets events (list), `kalshi_data[1]` gets markets by event dict. The resolution scan needs the flat markets list, not the dict.

**How to avoid:** Check what `_fetch_kalshi_data` actually returns before passing to `scan_resolution_snipes`. The function is in `scans/__init__.py`. Resolution scan expects `list[dict]` where each dict is a Kalshi market, not a dict of dicts keyed by event ticker.

**Warning signs:** `scan_resolution_snipes` returns empty results for Kalshi despite markets being available.

### Pitfall 2: find_lowest_fee_path Returns None for 2-Platform Opportunities

**What goes wrong:** Attaching `_fee_path = None` to the opp dict, then the executor tries to dereference it.

**Why it happens:** `find_lowest_fee_path()` returns `None` when no profitable path exists (total_cost >= 1.0 after fees). For a 2-platform binary arb that's already been validated by CLOB refinement, this shouldn't happen — but edge cases exist (e.g., high Kalshi taker fees erasing profit).

**How to avoid:** Always guard: `fee_path = find_lowest_fee_path(...); if fee_path: opp["_fee_path"] = fee_path`. Never set `opp["_fee_path"] = None`. The executor should treat missing `_fee_path` key as "use default routing" and the presence of the key as "routing hint available."

### Pitfall 3: Position Sizer Not Accessible from continuous.py Scope

**What goes wrong:** Trying to call `executor.position_sizer.update_bankroll()` when `position_sizer` is `None`.

**Why it happens:** `PositionSizer` is initialized in `cli.py` only when position sizing config is enabled and passed to `ArbitrageExecutor.__init__(position_sizer=pos_sizer)`. If not configured, `executor.position_sizer` is `None`.

**How to avoid:** Always guard: `if executor.position_sizer: executor.position_sizer.update_bankroll(total)`. This is consistent with how `executor.py:206` already guards `if self.position_sizer:` before calling `size_for_opportunity()`.

### Pitfall 4: MM Inventory Limit Confusion ($500/market vs total exposure)

**What goes wrong:** Thinking `MM_MAX_TOTAL_EXPOSURE` controls per-market cap.

**Why it happens:** `MM_MAX_TOTAL_EXPOSURE` is the engine-wide total exposure cap. `MM_MAX_INVENTORY` is the per-market cap. The user wants $500/market, which means `MM_MAX_INVENTORY=500.0`, not changing `MM_MAX_TOTAL_EXPOSURE`.

**How to avoid:** Set `MM_MAX_INVENTORY=500.0` in Railway. Leave `MM_MAX_TOTAL_EXPOSURE` at its current default of 500 or increase it if running >1 market simultaneously.

### Pitfall 5: Test Coverage Gap for Integrated Behavior

**What goes wrong:** The existing `test_find_lowest_fee_path_basic` test (test_integration.py:252) tests the function in isolation, not as part of the scan. After wiring, the integrated behavior (fee path attached to opp dict from scan, then read by executor) is not tested.

**How to avoid:** Add a test that runs `scan_cross_platform()` with mocked platform clients and asserts `_fee_path` is present on returned opportunities. Add a test that runs `executor._build_legs()` with a Cross-type opp that has a `_fee_path` key and verifies the legs use the fee-path's platforms.

---

## Code Examples

### How find_lowest_fee_path() is called (from test_integration.py:252)

```python
# Source: tests/test_integration.py:252-261
from fees import find_lowest_fee_path

platforms = ["polymarket", "kalshi", "sxbet"]
yes_prices = {"polymarket": 0.40, "kalshi": 0.42, "sxbet": 0.41}
no_prices  = {"polymarket": 0.55, "kalshi": 0.53, "sxbet": 0.54}
result = find_lowest_fee_path(platforms, yes_prices, no_prices)
# result = {
#   "best_yes_platform": "sxbet",
#   "best_no_platform": "polymarket",
#   "yes_price": 0.41, "no_price": 0.55,
#   "total_cost": 0.96, "estimated_fees": 0.002, "net_profit": 0.038
# } or None
```

### How scan_resolution_snipes() signature works (from scans/resolution.py)

```python
# Source: scans/resolution.py — platform-agnostic signature
scan_resolution_snipes(
    markets: list[dict],
    platform: str,          # "polymarket" or "kalshi"
    min_profit: float,
) -> list[dict]             # returns ResolutionSnipeOpp dicts
```

### How executor._fetch_balances() works (executor.py:744)

```python
# Source: executor.py:744-803
# Accepts an opp_type string to decide which platforms to query.
# "Cross" triggers all 8 platforms.
balances = executor._fetch_balances("Cross")
# Returns: {"polymarket": 127.50, "kalshi": 84.20, "sxbet": 45.00, ...} or None
```

### How update_bankroll() works (position_sizer.py:328)

```python
# Source: position_sizer.py:328-337
# Simple setter — logs old → new, updates self.bankroll
if executor.position_sizer:
    executor.position_sizer.update_bankroll(total_balance_float)
```

### Config flag pattern used throughout config.py

```python
# Source: config.py lines 231, 236, 243, 292
MM_ENABLED = _env_bool("MM_ENABLED", "false")           # keep false for local dev
SNAPSHOT_ENABLED = _env_bool("SNAPSHOT_ENABLED", "false")  # enable via Railway
DYNAMIC_FEE_ENABLED = _env_bool("DYNAMIC_FEE_ENABLED", "false")
EVENT_MONITOR_ENABLED = _env_bool("EVENT_MONITOR_ENABLED", "false")
```

Railway env vars (no code change required):
```
MM_ENABLED=true
MM_MIN_SPREAD=0.02
MM_MAX_INVENTORY=500.0
SNAPSHOT_ENABLED=true
DYNAMIC_FEE_ENABLED=true
POLYGON_RPC_URL=https://polygon-rpc.com
EVENT_MONITOR_ENABLED=true
```

---

## State of the Art

| Old State | Current State | Impact |
|-----------|---------------|--------|
| `find_lowest_fee_path()` exists but is never called in production code | Needs wiring into scan and executor | INTEG-01 |
| MM in one-shot mode always `dry_run=True` | Fix to respect `executor.dry_run` | INTEG-02 |
| Resolution scan in continuous mode: Polymarket only | Needs Kalshi call added | INTEG-03 |
| `update_bankroll()` never called in continuous mode | Needs timer + post-trade triggers | INTEG-04 |
| All 4 feature flags default `false` in config.py | Keep defaults, set Railway env vars | ENABLE-01 through ENABLE-04 |
| Continuous mode `MarketMaker` already gates on `MM_ENABLED` | Ready — just flip the flag | ENABLE-01 |
| Continuous mode `SnapshotRecorder` already gates on `SNAPSHOT_ENABLED` | Ready — just flip the flag | ENABLE-02 |

**No deprecated patterns involved in this phase.** All target code is current.

---

## Open Questions

1. **kalshi_data tuple structure for resolution scan (INTEG-03)**
   - What we know: `_fetch_kalshi_data()` is imported from `scans` and called in continuous loop; `kalshi_data` is stored as the result. The result is a 2-tuple `(events, ...)`.
   - What's unclear: The second element — is it `list[dict]` (flat market list) or `dict[str, list]` (markets by event ticker)? `scan_resolution_snipes` needs a flat list.
   - Recommendation: Read `scans/__init__.py` and `_fetch_kalshi_data` source before implementing INTEG-03 to confirm the tuple structure and whether a flatten step is needed.

2. **fee path price inputs for scan_cross_all (INTEG-01 edge case)**
   - What we know: `scan_cross_all()` handles all 8 platforms and builds multi-platform opportunity dicts. The price keys differ from `scan_cross_platform()` because the opportunity may span non-Polymarket/Kalshi platforms.
   - What's unclear: The exact price key names attached to opportunities in the `scan_cross_all` path (e.g., `_betfair_yes`, `_sxbet_yes`, etc.).
   - Recommendation: Read the opportunity-building code in `scan_cross_all()` beyond line 374 to confirm price key names before implementing the `_fee_path` attachment for the cross-all scan.

3. **MM_MAX_INVENTORY vs MM_MAX_TOTAL_EXPOSURE for user's $500/market intent**
   - What we know: User said "$500 per market initial cap." `MM_MAX_INVENTORY` is per-market, `MM_MAX_TOTAL_EXPOSURE` is engine total.
   - What's unclear: Whether the user means $500 maximum inventory per market OR $500 total engine exposure cap.
   - Recommendation: Interpret as per-market (`MM_MAX_INVENTORY=500`); leave `MM_MAX_TOTAL_EXPOSURE` at its current default (also 500 — coincidentally the same). If the user runs MM on multiple markets simultaneously, the engine total will cap at $500, effectively limiting to one active market at a time. This is the safe interpretation for Phase 1.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (version in requirements-dev.txt) |
| Config file | none (no pytest.ini — uses default discovery) |
| Quick run command | `pytest tests/test_fees.py tests/test_cli.py tests/test_continuous.py tests/test_integration.py -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INTEG-01 | `_fee_path` key present on cross-platform opp dicts after scan | unit | `pytest tests/test_cross.py -x -k fee_path` | Wave 0 gap |
| INTEG-01 | Executor reads `_fee_path` and builds legs using fee-optimal platforms | unit | `pytest tests/test_executor.py -x -k fee_path` | Wave 0 gap |
| INTEG-02 | `--mode mm --exec-mode full-auto` creates MarketMaker with `dry_run=False` | unit | `pytest tests/test_cli.py -x -k market_maker_dry_run` | Wave 0 gap |
| INTEG-03 | Kalshi markets fed to resolution scan in continuous mode | unit | `pytest tests/test_continuous.py -x -k kalshi_resolution` | Wave 0 gap |
| INTEG-04 | `update_bankroll()` called after trade execution and on 5-min timer | unit | `pytest tests/test_continuous.py -x -k bankroll` | Wave 0 gap |
| ENABLE-01 | When `MM_ENABLED=true`, MarketMaker initializes in continuous mode | unit | `pytest tests/test_continuous.py -x -k mm_enabled` | Wave 0 gap |
| ENABLE-02 | When `SNAPSHOT_ENABLED=true`, SnapshotRecorder initializes | unit | `pytest tests/test_snapshot.py -x` | ✅ (test_snapshot.py exists) |
| ENABLE-03 | When `DYNAMIC_FEE_ENABLED=true`, GasMonitor blocks trades below threshold | unit | `pytest tests/test_gas_monitor.py -x` | ✅ (test_gas_monitor.py exists) |
| ENABLE-04 | When `EVENT_MONITOR_ENABLED=true`, EventMonitor runs | unit | `pytest tests/test_event_monitor.py -x` | ✅ (test_event_monitor.py exists) |
| INTEG-05 | CLAUDE.md updated to describe stale scan one-shot behavior | manual | Read CLAUDE.md and verify text | N/A |

### Sampling Rate
- **Per task commit:** `pytest tests/test_fees.py tests/test_cli.py tests/test_continuous.py tests/test_integration.py -x -q`
- **Per wave merge:** `pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_cross.py` — add `TestFeePath` class with: (1) test `_fee_path` present on returned opps, (2) test `_fee_path` absent when `find_lowest_fee_path` returns None
- [ ] `tests/test_executor.py` — add `TestFeePathExecution` class: verify `_build_legs()` reads `_fee_path` for Cross-type opps
- [ ] `tests/test_cli.py` — add `test_market_maker_respects_exec_mode`: create MM with `exec_mode=full-auto`, assert `mm.dry_run is False`
- [ ] `tests/test_continuous.py` — add `TestKalshiResolutionScan`: mock kalshi_data, assert `scan_resolution_snipes` called with kalshi markets
- [ ] `tests/test_continuous.py` — add `TestBankrollRefresh`: mock `executor._fetch_balances` and `position_sizer.update_bankroll`, assert called after trade + on timer

*(Existing test_snapshot.py, test_gas_monitor.py, test_event_monitor.py already cover ENABLE-02, ENABLE-03, ENABLE-04 feature mechanics. The ENABLE tests just need env var overrides in test setup.)*

---

## Sources

### Primary (HIGH confidence)

- Direct source file reads: `fees.py:948-1004`, `cli.py:480-504`, `continuous.py:1009-1017`, `continuous.py:605-620`, `continuous.py:700-722`, `position_sizer.py:328-337`, `executor.py:744-803`, `executor.py:805-1050`, `config.py:231-292`, `scans/cross.py:1-410`
- `.planning/codebase/ARCHITECTURE.md` — layer structure and data flow
- `.planning/codebase/CONVENTIONS.md` — opportunity dict contract, underscore-prefix convention
- `.planning/phases/01-wire-enable/01-CONTEXT.md` — all locked decisions
- `tests/test_integration.py:252-270` — verified `find_lowest_fee_path()` call pattern

### Secondary (MEDIUM confidence)

- `tests/test_continuous.py` — existing continuous mode test patterns (not fully read but existence confirmed)
- `scans/__init__.py` — `_fetch_kalshi_data` import confirmed; exact return structure of tuple needs verification (see Open Questions)

### Tertiary (LOW confidence — mark for validation)

- Assumption that `kalshi_data[1]` yields a flat list for `scan_resolution_snipes` — confirm by reading `_fetch_kalshi_data` source before implementing INTEG-03

---

## Metadata

**Confidence breakdown:**
- Integration targets (INTEG-01 through INTEG-04): HIGH — source lines verified directly
- Config defaults (ENABLE-01 through ENABLE-04): HIGH — config.py lines read directly; continuous.py init guards confirmed
- Railway credential setup (ENABLE-05): HIGH — env var names confirmed in config.py; no code changes needed
- kalshi_data tuple structure for INTEG-03: MEDIUM — function found, return type not fully traced

**Research date:** 2026-03-19
**Valid until:** 2026-04-19 (stable Python codebase, no external API dependencies for this phase)
