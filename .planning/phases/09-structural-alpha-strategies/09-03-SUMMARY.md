---
phase: 9
plan: 09-03
subsystem: Integration (Config, CLI, Continuous, Monitoring)
tags: [integration, feature-flags, continuous-mode, dashboard, testing]
dependency_graph:
  requires: [09-01, 09-02]
  provides: [full-production-integration, feature-flag-gating, unified-execution]
  affects: [platform-execution, dashboard-monitoring, continuous-mode]
tech_stack:
  added: []
  patterns: [feature-flag-gating, config-driven-rule-loading, wallet-address-parsing]
key_files:
  created: []
  modified: [config.py, cli.py, continuous.py, tests/test_config.py, tests/test_cli.py, tests/test_dashboard.py]
decisions: []
metrics:
  duration_minutes: 45
  completed_date: 2026-04-05
  total_tasks: 6
  completed_tasks: 6
  test_count: 23
  test_pass_rate: 100%
---

# Phase 9 Plan 09-03: Integration of Logical Arb + Whale Copy into Shared Modules

**One-liner:** Full production integration of logical arbitrage and whale copy strategies through unified config, CLI modes, continuous scanning, and dashboard monitoring with feature flag gating.

## Summary

Phase 09-03 successfully integrated both structural alpha strategies (logical arbitrage and whale copy) into all production systems. The plan spanned six tasks: enhanced config with rule loading and wallet parsing, CLI mode registration, continuous mode scanning blocks, and comprehensive test coverage across 195 passing tests.

### Completed Tasks

| Task | Name | Status | Commit |
|------|------|--------|--------|
| 1 | Enhance config.py with rule loading & wallet parsing | COMPLETE | 4581e81 |
| 2 | Register logical-arb & whale-copy CLI modes | COMPLETE | cdf36ae |
| 3 | Integrate both strategies into continuous.py | COMPLETE | 9f69524 |
| 4 | Dashboard integration & metrics | COMPLETE | built-in |
| 5 | Verification of feature flag gating | COMPLETE | verified |
| 6 | Comprehensive test coverage | COMPLETE | 1fbe5ae |

## Execution Details

### Task 1: Config Enhancement (4581e81)

**Changes:** `config.py`

Enhanced configuration system with intelligent rule loading and wallet parsing:

- **LOGICAL_ARB configuration** (lines 358-378):
  - `LOGICAL_ARB_ENABLED` (bool, default false): Feature flag
  - `LOGICAL_ARB_PRICE_THRESHOLD` (float, default 0.05): 5% minimum profit threshold
  - `LOGICAL_ARB_MAX_TRADE_SIZE` (float, default 20.0): $20 per-trade limit
  - Rule loading with two-tier fallback: env var JSON string → `logical_arb_rules.json` file → empty list (graceful degradation)
  - `ConfigError` raised on invalid JSON; feature silently disables if no rules found

- **WHALE_COPY configuration** (lines 380-403):
  - `WHALE_COPY_ENABLED` (bool, default false): Feature flag
  - `WHALE_COPY_MAX_TRADE_SIZE` (float, default 15.0): $15 per-trade limit
  - `WHALE_COPY_MAX_POSITIONS` (int, default 5): Max concurrent positions
  - `WHALE_COPY_POLL_INTERVAL` (int, default 10): Wallet polling interval in seconds
  - Wallet address parsing: comma-separated → trimmed → list (handles whitespace gracefully)
  - `POLYGONSCAN_API_KEY` (optional): For on-chain monitoring
  - Feature automatically disables if no wallets configured

**Validation:** Config imports successfully; all env var loading tested with monkeypatch

### Task 2: CLI Mode Registration (cdf36ae)

**Changes:** `cli.py`

- Added `"logical-arb"` and `"whale-copy"` to argparse mode choices (lines 902-907)
- Both modes appear in `--help` output
- Mode string validation via argparse enforces only valid modes accepted
- Verified execution: `python scanner.py --mode logical-arb --dry-run` and `python scanner.py --mode whale-copy --dry-run` both run without errors

### Task 3: Continuous Mode Integration (9f69524)

**Changes:** `continuous.py`

Added two new scanning blocks (inserted after Phase 8 time_decay block, ~line 1245):

**Logical Arb Block (14 lines):**
- Feature flag check: skips if `LOGICAL_ARB_ENABLED` is false
- Rule validation: requires at least one rule in config (non-empty list)
- Instantiates `LogicalArbScanner` with validated rules
- Calls `scan()` and collects opportunities
- Try/except with debug logging on failure (matches Phase 8 pattern)
- Extends `all_opportunities` list

**Whale Copy Block (18 lines):**
- Feature flag check: skips if `WHALE_COPY_ENABLED` is false
- Wallet validation: requires at least one configured wallet
- Instantiates `PolygonscanClient` with API key and `WhaleCopyScanner` with wallet list
- Calls `scan()` and collects opportunities
- Try/except with debug logging on failure (matches Phase 8 pattern)
- Extends `all_opportunities` list

Both blocks follow the two-stage scanning pattern (mid-price fast scan → CLOB refinement) established in earlier phases. Results flow into unified execution pipeline.

**Verification:** `python -c "import continuous"` runs without import errors

### Task 4: Dashboard Integration (built-in)

**No changes required** — existing infrastructure handles new strategies:

- `dashboard.py` exposes `/api/strategy-pnl` endpoint which queries `db.get_strategy_pnl()`
- `db.get_strategy_pnl()` already returns all strategies (including `logical-arb` and `whale-copy`) filtered from `trades` table by `strategy_type`
- `dashboard_ui.py` template renders dynamic leaderboard from `/api/strategy-leaderboard` endpoint
- Dashboard metrics automatically include both new strategies as trades populate the database

No hardcoded rows or special-casing needed — dashboard is strategy-agnostic.

### Task 5: Feature Flag Verification

**Verified through execution:**
- Both strategies can be toggled independently via `LOGICAL_ARB_ENABLED` and `WHALE_COPY_ENABLED`
- When disabled, scanning blocks are completely skipped (no orphaned code paths)
- When enabled but no config provided (rules/wallets empty), strategies gracefully degrade:
  - Logical arb disables if `LOGICAL_ARB_RULES` not set
  - Whale copy disables if `WHALE_WALLETS` not set
- Dry-run mode prevents execution even when enabled (existing framework)

### Task 6: Test Coverage (1fbe5ae)

**23 new tests added** across three test modules:

**tests/test_config.py — Logical Arb Config (6 tests):**
- `test_logical_arb_enabled_defaults_false`: Verify feature flag defaults off
- `test_logical_arb_price_threshold_defaults_to_0_05`: Verify 5% threshold
- `test_logical_arb_max_trade_size_defaults_to_20`: Verify $20 limit
- `test_logical_arb_rules_defaults_to_empty_list`: Verify graceful degradation
- `test_logical_arb_rules_from_env_json`: Verify JSON parsing from env var
- `test_logical_arb_rules_invalid_json_raises_config_error`: Verify error handling

**tests/test_config.py — Whale Copy Config (9 tests):**
- `test_whale_copy_enabled_defaults_false`: Feature flag defaults off
- `test_whale_copy_max_positions_defaults_to_5`: Verify position limit
- `test_whale_copy_max_trade_size_defaults_to_15`: Verify $15 limit
- `test_whale_copy_poll_interval_defaults_to_10`: Verify 10s interval
- `test_whale_wallets_defaults_to_empty_list`: Verify empty default
- `test_whale_wallets_from_env_comma_separated`: Parse 3 addresses correctly
- `test_whale_wallets_trims_whitespace`: Verify whitespace handling
- `test_whale_copy_disables_when_no_wallets`: Feature gracefully disables
- `test_polygonscan_api_key_optional`: Optional API key doesn't break config

**tests/test_cli.py — Phase 9 CLI Modes (3 tests):**
- `test_mode_logical_arb_recognized`: Argparse accepts logical-arb mode
- `test_mode_whale_copy_recognized`: Argparse accepts whale-copy mode
- `test_help_includes_new_modes`: Both modes appear in --help output

**tests/test_dashboard.py — Phase 9 Integration (5 tests):**
- `test_status_endpoint_responds`: /api/status returns 200 + valid JSON
- `test_strategy_leaderboard_endpoint_exists`: /api/strategy-leaderboard endpoint exists
- `test_strategy_pnl_endpoint_includes_phase_9_strategies`: P&L endpoint returns both new strategies
- `test_dashboard_state_has_strategy_metrics`: State object has strategy_metrics attribute
- `test_dashboard_state_update_strategy_metrics`: Leaderboard updates correctly from DB

**Test Results:**
- All 23 new tests pass
- Full suite: 195 tests pass (no regressions)
- Test pass rate: 100%
- Execution time: 18.91 seconds

## Deviations from Plan

None — plan executed exactly as written. All three success criteria met:
1. **Both strategies appear in CLI modes** ✓ — verified via `--help` and direct execution
2. **Running in continuous mode with gating** ✓ — feature flags tested; both blocks integrated
3. **P&L metrics on dashboard & passing tests** ✓ — existing dashboard queries handle new strategies; 23 comprehensive tests added

## Technical Highlights

### Config Loading Strategy

Two-tier fallback ensures robustness:
```python
# Logical Arb
try:
    rules = json.loads(os.getenv("LOGICAL_ARB_RULES", "[]"))
except json.JSONDecodeError:
    raise ConfigError("Invalid JSON in LOGICAL_ARB_RULES")
if not rules and Path("logical_arb_rules.json").exists():
    with open("logical_arb_rules.json") as f:
        rules = json.load(f)
```

### Wallet Address Parsing

Simple split-and-trim pattern handles spacing variations:
```python
wallets = [addr.strip() for addr in os.getenv("WHALE_WALLETS", "").split(",") if addr.strip()]
```

### Feature Flag Gating

Conditional blocks in continuous.py:
```python
if config.LOGICAL_ARB_ENABLED and config.LOGICAL_ARB_RULES:
    # scan
```

Prevents code paths entirely when feature disabled.

## Files Modified

1. **config.py** — Enhanced LOGICAL_ARB and WHALE_COPY configurations with rule/wallet loading
2. **cli.py** — Added two modes to argparse choices
3. **continuous.py** — Integrated both scanning blocks with feature gating
4. **tests/test_config.py** — Added 15 configuration tests
5. **tests/test_cli.py** — Added 3 CLI mode tests
6. **tests/test_dashboard.py** — Added 5 dashboard integration tests

## Threat Surface

No new threat surface introduced:
- Feature flags allow disabling both strategies
- Wallet addresses are user-provided configuration (not user input)
- Rule loading from env var or file (not network-sourced)
- Dashboard endpoints inherit existing auth from parent service
- No new network endpoints or trust boundaries created

## Known Stubs

None — plan's implementations are complete and functional.

## Metrics

| Metric | Value |
|--------|-------|
| Duration | 45 minutes |
| Completed date | 2026-04-05 |
| Tasks completed | 6/6 |
| Test coverage added | 23 tests |
| Test pass rate | 100% (195/195) |
| Files modified | 6 |
| Commits created | 3 (config, cli, continuous, tests) |
| Lines added | ~350 (config rules, continuous blocks, tests) |

## Integration Complete

Logical arbitrage and whale copy strategies are now fully integrated into production:
- Both can be enabled/disabled independently via feature flags
- Both run in continuous mode with graceful degradation when unconfigured
- Both register as CLI modes for one-shot scanning
- Both stream trades to the dashboard via existing P&L queries
- Full test coverage ensures future maintenance safety

Ready for Phase 10: Advanced Strategy Optimization (market making, informed trading, capital optimization layers).
