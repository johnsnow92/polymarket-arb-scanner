# Roadmap

> **Owner:** Jonathon Tamm · **Review cadence:** monthly.
> Sequencing view. The "done" bar lives in `PRD.md`; per-strategy status in `docs/strategy-framework-v2.md`.

## Now (in flight)
- **Strategy #20 — wire the tuning loop into production.** Loop is implemented + tested; remaining work: auto-apply recommendations at `continuous.py` startup behind `BACKTEST_TUNING_ENABLED`, and alert when recommendations change. (`docs/BACKTESTING.md`)
- **Content/doc truth-alignment** (this audit) — WS-1 done; WS-2 docs created; WS-5 code cleanup pending.

## Next
- **WS-5 code cleanup** — remove dead `GEMINI_FEE_RATE` (config.py:352) + fix stale `fees.py:565` comment, behind the WS-5 compatibility gate (reference scan + import/`--dry-run` smoke). Register rows A7/A8.
- **#6 SX Bet EIP-712 signing** — implement order signing so `place_order()` works; lifts SX Bet from read-only. Pairs naturally with **Myriad** (Tier 2) which shares the EIP-712 primitive.
- **Mode→strategy reconciliation** — produce the 1:1 table mapping the 32 `cli.py` `--mode` values (+ the internal #30–#49 series from PR #22) onto the 29-strategy taxonomy.
- **Platform expansion — Tier 1** (Sporttrade / Novig / ProphetX): run the mandatory OpticOdds detection spike first (market-identity mapping, settlement normalization, depth, fees, rate limits, freshness), gated on MI regulatory eligibility. Memo: `docs/audit/PLATFORM-RESEARCH-2026-05-31.md`.

## Later
- **Platform expansion — Tier 2** (Predict.fun / Myriad / Limitless): on-chain venues; Myriad bundled with the #6 EIP-712 work.
- **#18 auto-rebalance** — remains Gemini↔Polymarket-only by design (six other platforms have no transfer API). Revisit only if a platform exposes one.
- **Observability gaps** (from `RUNBOOK.md`): dedicated 429/rate-limit alert, partial-fill metric, DB-write-failure alert, process heartbeat/uptime alert, kill-switch audit event.

## Definition-of-done milestone (from `PRD.md` / CLAUDE.md scope)
The product is "done" when all three hold over a 7-day live window:
1. Net-positive P&L in `trades.db`.
2. <5% false-positive rate on detected opportunities (manually verified).
3. ≥1 profitable round-trip trade executed with no human intervention.
