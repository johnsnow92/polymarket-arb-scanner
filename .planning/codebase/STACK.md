# Technology Stack

**Analysis Date:** 2026-03-17

## Languages

**Primary:**
- Python 3.12 - All application code, CLI, continuous mode, API clients, scanning, execution

**Secondary:**
- JavaScript/Node.js - OpenCode plugin system (`.opencode/`)

## Runtime

**Environment:**
- Python 3.12.x (specified in Dockerfile, GitHub Actions)
- pip for package management
- Lockfile: No lockfile present (requirements.txt only, pinned versions)

## Frameworks

**Core:**
- py-clob-client 0.34.5 - Polymarket CLOB API integration
- ib_insync >=0.9.70 - IBKR ForecastEx TWS API client
- requests 2.31.0 - HTTP client for all REST API integrations
- websockets 16.0 - WebSocket connections for real-time price feeds
- tenacity 9.1.4 - Retry logic with exponential backoff for API calls

**Data & ML:**
- fastembed >=0.4.0 - Text embeddings for event matching (ONNX Runtime required)
- numpy >=1.24.0 - Numerical arrays for calculations

**Utilities:**
- python-dotenv 1.2.1 - Environment variable loading from .env
- cryptography 46.0.4 - RSA-PSS signing (Kalshi API), HMAC (Gemini API)
- thefuzz[speedup] 0.22.1 - Fuzzy string matching for cross-platform market pairing
- tabulate 0.9.0 - CLI table formatting
- python-socks[asyncio] 2.8.0 - SOCKS proxy support for WebSocket connections

**Testing:**
- pytest 9.0.2 - Test framework (dev-only)

## Build & Deployment

**Container:**
- Docker (Dockerfile at root)
- Python 3.12-slim base image
- System dependency: libgomp1 (ONNX Runtime requirement)
- Build stages: system deps → pip install → fastembed model pre-download → code copy

**CI/CD:**
- GitHub Actions (`.github/workflows/test.yml`)
- Runs on `ubuntu-latest`, Python 3.12
- Triggers: push to master, PRs to master, manual workflow_dispatch
- Pipeline: checkout → setup Python (with pip cache) → install deps (both requirements.txt and requirements-dev.txt) → run pytest

**Deployment:**
- Railway (auto-deploys on push to master via GitHub integration)
- Entrypoint: `python scanner.py --continuous`
- Health check: HTTP GET `/healthz` on port 8080 (via DASHBOARD_PORT env var)
- Data persistence: `/data` directory (mounted EFS in Railway)

## Key Dependencies

**Critical (Trading):**
- py-clob-client 0.34.5 - Polymarket CLOB order placement and market data
- ib_insync >=0.9.70 - IBKR ForecastEx trading via TWS API socket (no REST API exists)
- requests 2.31.0 - All trading platform REST APIs (Kalshi, Betfair, Smarkets, SX Bet, Matchbook, Gemini)

**Critical (Real-time):**
- websockets 16.0 - Polymarket and Kalshi WebSocket feeds for price updates
- tenacity 9.1.4 - Automatic retries on API failures and rate limits

**Infrastructure:**
- cryptography 46.0.4 - API authentication (RSA-PSS for Kalshi, HMAC-SHA384 for Gemini)
- python-socks[asyncio] 2.8.0 - Optional proxy routing for all REST/WebSocket connections

## Configuration

**Environment:**
- `.env` file loading via python-dotenv (checked into repo, contains non-secret vars)
- Fallback to `~/.claude/.env` for development overrides
- All runtime config from `config.py` — no separate config files
- Config precedence: CLI args > environment variables > defaults in `config.py`

**Key Configuration Areas:**
- Platform credentials (env vars): `POLYMARKET_PRIVATE_KEY`, `KALSHI_API_KEY_ID`, `BETFAIR_USERNAME`, etc.
- API rate limits: `PM_RATE_LIMIT`, `KALSHI_RATE_LIMIT`, `GEMINI_RATE_LIMIT`, etc.
- Risk management: `DAILY_LOSS_LIMIT`, `MAX_OPEN_POSITIONS`, `MIN_LIQUIDITY`, `MAX_TRADE_SIZE`
- Execution mode: `DRY_RUN` (default: true), `EXECUTION_MODE` (semi-auto/full-auto), `ENABLED_EXECUTION_PLATFORMS`
- WebSocket tuning: `WS_SUBSCRIPTION_LIMIT`, `WS_TRIGGER_THRESHOLD`, `WS_TRIGGER_ENABLED`
- Advanced features: `EVENT_MONITOR_ENABLED`, `DYNAMIC_FEE_ENABLED`, `MM_ENABLED`

**Build:**
- `Dockerfile` - production container definition
- `.dockerignore` - exclude .git, venv, __pycache__, tests
- `railway.toml` - Railway deployment config (minimal)

## Platform Requirements

**Development:**
- Python 3.12+ (local or via Docker)
- pip with `requirements.txt` and `requirements-dev.txt`
- ONNX Runtime (libgomp1) for fastembed
- Optional: Kalshi RSA private key file or base64-encoded key
- Optional: IB Gateway or TWS running at 127.0.0.1:4001 for IBKR trading

**Production:**
- Railway (auto-deploys from GitHub master branch)
- Docker runtime (Python 3.12-slim base)
- Environment variables for platform credentials (injected by Railway)
- Mount point at `/data` for trades.db persistence (Railway EFS)
- Outbound HTTPS/WebSocket access to all 8 trading platforms

## Notable Design Patterns

**Singleton Modules:**
- `config.py` - Centralized constants with env var backing, validation at import time
- `db.py` - TradeDB with thread-safe WAL mode SQLite
- `dashboard.py` - Shared _DashboardState singleton, HTTP server in separate thread
- `metrics.py` - Stdlib-only metrics (no prometheus dependency)

**Rate Limiting:**
- Custom per-module: `polymarket_api.py`, `kalshi_api.py`, `gemini_api.py`, `betfair_api.py`, `smarkets_api.py`, `sxbet_api.py`, `matchbook_api.py`
- Thread-safe with global `_last_request_time` + `_rate_lock`
- Enforced via decorator or manual calls in retry loops

**Async/Threading:**
- `continuous.py` - asyncio event loop for WebSocket feeds
- FeedManager - manages multiple WebSocket connections asynchronously
- ThreadPoolExecutor - parallel API fetching and scanning
- Per-market locks - prevent double execution in continuous mode

---

*Stack analysis: 2026-03-17*
