# Backtesting & Tuning Methodology

> **Owner:** Jonathon Tamm · **Review cadence:** quarterly, or when the replay/tuning logic changes.

## Pipeline
```
snapshot.py  →  snapshots.db  →  backtest.py (replay)  →  scripts/tune.py  →  recommendations
 (record)        (history)        (simulate fills)         (per-strategy)      (config apply)
```

## Snapshot recording (`snapshot.py`)
- Records historical price snapshots to SQLite (`snapshots.db`, thread-safe), gated by `SNAPSHOT_ENABLED`.
- Source data for both staleness detection (continuous mode) and backtest replay.

## Replay engine (`backtest.py`)
- Standalone CLI: `python backtest.py`.
- Simulates execution over recorded snapshots. Models entry/exit and partial-fill behavior to estimate net profit per strategy.
- `build_recommendations()` aggregates results; `_suggest_strategy_thresholds()` computes per-strategy `MIN_NET_ROI` + `FUZZY_MATCH_THRESHOLD`. **Per-strategy entries are emitted only when a strategy has ≥10 backtested trades** (avoids tuning on noise). Recommendations are clamped (ROI 0.001–0.05; fuzzy 60–90).

## Tuning loop — Strategy #20 (in-flight, Sprint 6)
- `scripts/tune.py` runs the replay over a rolling window and writes recommendations. Two output modes: `--output <dir>` (JSON + markdown) or `--output <file>.json` (JSON only).
- `config.load_backtest_recommendations(path)` reads the JSON defensively (returns `None` on missing/malformed/non-object payloads — never raises).
- `config.apply_backtest_recommendations(path)` applies overrides to `MIN_NET_ROI` / `FUZZY_MATCH_THRESHOLD` / `RECOMMENDED_BY_STRATEGY`, behind `BACKTEST_TUNING_ENABLED` (default off).
- **Status: PARTIAL.** Applied only on manual invocation — not auto-wired into `continuous.py` startup, and no alert fires when recommendations change. See `ROADMAP.md`.

## How to tune
1. Run with `SNAPSHOT_ENABLED=true` for a representative window (≥ several days; need ≥10 trades/strategy for per-strategy tuning).
2. `python scripts/tune.py --output data/backtest_recommendations.json`
3. Review the JSON/markdown; sanity-check against `RISK-POLICY.md` limits.
4. Apply manually via `BACKTEST_TUNING_ENABLED=true` + `apply_backtest_recommendations()` — review before trusting in live mode.

## Caveats
- Backtest fills are *simulated*; real slippage/latency differ. Treat recommendations as priors, not truth.
- Thin-data strategies (<10 trades) are intentionally not auto-tuned.
