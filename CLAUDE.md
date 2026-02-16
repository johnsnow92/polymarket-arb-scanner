# CLAUDE.md — Polymarket Arb Scanner

## Project Overview

Python CLI tool that scans for arbitrage opportunities across prediction markets. Detects arbs on:
- **Polymarket** — Binary internal (YES + NO < $1.00) and NegRisk (multi-outcome sum < $1.00)
- **Kalshi** — Binary and multi-outcome internal arbs
- **Cross-platform** — Price discrepancies between any platform pair
- **PredictIt** — Binary market arbs and cross-platform matching
- **Betfair Exchange** — Exchange-based arbs with commission handling
- **Manifold Markets** — Play-money/sweepstakes market arbs

Supports one-shot (default) and continuous mode with optional automated trade execution.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# One-shot scan (all arb types)
python scanner.py

# Continuous mode with trade execution
python scanner.py --continuous --interval 60

# Cross-all: match across all platforms
python scanner.py --mode cross-all

# Kalshi-only
python scanner.py --mode kalshi

# Adjust thresholds
python scanner.py --min-profit 0.01 --min-depth 50

# Dry run (default) vs live execution
python scanner.py --dry-run                         # log only
python scanner.py --exec-mode full-auto --max-trade 10  # live

# Run tests
pytest tests/ -v
```

## Architecture

| Module | Purpose |
|--------|---------|
| `scanner.py` | Main entry point, CLI args, orchestrates scan loop, displays results |
| `polymarket_api.py` | Polymarket REST + CLOB API — markets, events, order books, trading via `PolymarketTrader` |
| `kalshi_api.py` | Kalshi REST API — `KalshiClient` with RSA-PSS auth, market/event fetching, orders |
| `predictit_api.py` | PredictIt REST API — `PredictItClient` with session auth, $850 position limits |
| `betfair_api.py` | Betfair Exchange API — `BetfairClient` with SSO auth, back/lay odds, commission |
| `manifold_api.py` | Manifold Markets API — `ManifoldClient` with API key auth, mana/sweepstakes |
| `matcher.py` | Fuzzy title matching — platform-specific (`match_markets_to_events`) and generic (`match_cross_platform`) |
| `fees.py` | Net profit calculators for all platform pairs (accounts for platform-specific fees) |
| `risk_manager.py` | `RiskManager` — position limits, daily loss limits, balance checks, depth checks |
| `executor.py` | `ArbitrageExecutor` — multi-leg execution with revalidation, fill confirmation, position tracking |
| `db.py` | `TradeDB` — SQLite with opportunities, trades, and positions tables |
| `ws_feeds.py` | `FeedManager` — WebSocket price feeds for Polymarket + Kalshi |
| `config.py` | Centralized env-var-backed configuration constants |
| `tests/` | pytest test suite — fees, risk, db, executor, matcher |

## Key Patterns

- Two-stage detection: mid prices (fast) → CLOB ask prices (accurate)
- Token ID resolution: CLOB token IDs attached during scan, used in execution
- Price revalidation: re-fetches prices before execution, rejects if profit drops >10%
- Fill confirmation: polls order status every 100ms for up to 2s after placement
- Position lifecycle: open → settled/expired (tracks realized P&L vs expected)
- Retry with backoff: `tenacity` for API calls (3 attempts, exponential backoff, 429-aware)
- Proxy support: per-platform proxy URLs via env vars
- WebSocket cache: executor uses WS price cache for revalidation (5s freshness window)
- Parallel fetching: `ThreadPoolExecutor` for Kalshi markets and cross-platform matching
- Platform-agnostic matching: `match_cross_platform()` compares any two market lists

## Environment Variables

See `.env.example` for all supported variables. Key ones:
- `POLYMARKET_PRIVATE_KEY` — Ethereum key for CLOB trading
- `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH` — Kalshi RSA-PSS auth
- `PREDICTIT_EMAIL`, `PREDICTIT_PASSWORD` — PredictIt session auth
- `BETFAIR_USERNAME`, `BETFAIR_PASSWORD`, `BETFAIR_API_KEY` — Betfair SSO
- `MANIFOLD_API_KEY` — Manifold API key
- `POLYMARKET_PROXY_URL`, `KALSHI_PROXY_URL` — proxy configuration
- `DRY_RUN`, `EXECUTION_MODE`, `MAX_TRADE_SIZE` — execution controls

## Dependencies

`requests`, `python-dotenv`, `tabulate`, `thefuzz[speedup]`, `cryptography`, `py-clob-client`, `websockets`, `tenacity`, `python-socks[asyncio]`, `pytest`

## Agent Team Notes

When splitting work across teammates:
- **API layer** (`polymarket_api.py`, `kalshi_api.py`, `predictit_api.py`, `betfair_api.py`, `manifold_api.py`, `ws_feeds.py`) — platform integration, auth, data fetching
- **Analysis layer** (`scanner.py`, `matcher.py`, `fees.py`, `config.py`) — detection logic, matching, profit calculation
- **Execution layer** (`executor.py`, `risk_manager.py`, `db.py`) — trade execution, risk management, persistence

Avoid two teammates editing the same module simultaneously. The scanner.py file is large — coordinate carefully if multiple people need to change it.

## Position Lifecycle

```
Trade executed → Position created (status='open', expected_pnl set)
  ↓
Continuous mode polls for settlement:
  - Kalshi: checks market 'result' field
  - Polymarket: checks market closed/resolved status
  ↓
Position settled (status='settled', realized_pnl calculated)
  or
Position expired (status='expired')
```
