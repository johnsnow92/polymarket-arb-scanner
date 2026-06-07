# Risk Management Policy

> **Owner:** Jonathon Tamm · **Review cadence:** monthly, and before any move off `DRY_RUN`.
> Audit-ready statement of the risk gates. Authoritative values live in `config.py`; `risk_manager.py` consumes them.

## Gate sequence (`risk_manager.py:RiskManager.check`)
Every opportunity passes these gates, in order, before execution:

1. **Daily P&L limit** — reject if `daily_pnl < -DAILY_LOSS_LIMIT`.
2. **Daily trade count** — reject if `MAX_DAILY_TRADES > 0` and count reached (0 = unlimited).
3. **Open positions** — reject if `open_count >= MAX_OPEN_POSITIONS`.
4. **Balance** — per-platform balance must cover `min(MAX_TRADE_SIZE, total_cost)` (Kalshi/Gemini/IBKR-only arbs checked against that platform's balance).
5. **Depth / liquidity** — `MIN_LIQUIDITY` (or `MIN_LIQUIDITY_HIGH_ROI` for high-ROI opps), except for `_SKIP_DEPTH_TYPES` (MM, signal-based, and reward strategies — liquidity verified at scan-refinement or revalidation instead).
6. **Re-entry** — duplicate-market re-entry allowed only if price improved by `REENTRY_IMPROVEMENT_THRESHOLD`.

Then in the executor: **gas-monitor gate** (reject if profit < dynamic gas+fee threshold when `DYNAMIC_FEE_ENABLED`) and **price revalidation** (reject if profit dropped >10% vs. scan).

## Authoritative limits (`config.py` defaults)
| Limit | Env var | Default |
|---|---|---|
| Max trade size | `MAX_TRADE_SIZE` | $5.00 |
| Daily loss limit | `DAILY_LOSS_LIMIT` | $25.00 |
| Max open positions | `MAX_OPEN_POSITIONS` | 10 |
| Min liquidity | `MIN_LIQUIDITY` | $10.00 |
| Min liquidity (high-ROI) | `MIN_LIQUIDITY_HIGH_ROI` | $5.00 |
| Min net ROI | `MIN_NET_ROI` | 0 |
| Re-entry improvement | `REENTRY_IMPROVEMENT_THRESHOLD` | 0.20 (20%) |
| MM inventory / market | `MM_MAX_INVENTORY` | $50 (Railway prod: 500) |
| MM total exposure | (`mm_max_total_exposure`) | $500 |

> Note: `risk_manager.py` carries higher *fallback* defaults (e.g. 25 positions) used only if config keys are absent; the values above (from `config.py`) are what actually flows through in normal operation. Reconcile before relying on either in a review.

## Circuit breakers & alerts (`alerting.py:AlertManager`)
Active checks, each emitting an `AlertType` at a `Severity` (INFO/WARNING/CRITICAL) and a webhook via `notifier.py`:
- `check_daily_loss` → `DAILY_LOSS_LIMIT`
- `check_loss_streak` / `check_strategy_loss_streak` → `LOSS_STREAK`
- `check_loss_spike` → `LOSS_SPIKE`
- `check_position_limit` → `POSITION_LIMIT`
- `check_zero_opp_period[_per_strategy]` → `ZERO_OPP_PERIOD` / `ZERO_OPP`
- execution/scan/WS/credential failures → `EXECUTION_FAILURE`, `SCAN_FAILURE`, `WS_DISCONNECT`, `CREDENTIAL_FAILURE`, `BALANCE_LOW`

## Defaults that bound blast radius
- `DRY_RUN=true` by default — no live orders until explicitly disabled.
- All strategy feature flags default `false`.
- SX Bet live trading hard-blocked at startup (no EIP-712 signing).
- Auto-rebalance limited to the Gemini↔Polymarket USDC corridor only.

## Kill / pause
- Set `DRY_RUN=true` and redeploy to stop live order placement (see `RUNBOOK.md` rollback).
- SIGINT/SIGTERM triggers graceful shutdown; `recovery.py` reconciles orphaned positions on next start.
