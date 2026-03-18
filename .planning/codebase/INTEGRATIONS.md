# External Integrations

**Analysis Date:** 2026-03-17

## Trading Platforms (8 Total)

**Polymarket (Gamma + CLOB):**
- REST API: Gamma API (markets, events) + CLOB API (order placement)
- SDK/Client: `py_clob_client` 0.34.5
- Auth: Ethereum private key (EIP-712 signing)
- Env vars: `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_CHAIN_ID` (default: 137 Polygon), `POLYMARKET_FUNDER_ADDRESS`, `POLYMARKET_SIGNATURE_TYPE`
- Implementation: `polymarket_api.py`
- Rate limit: `PM_RATE_LIMIT` (default: 0.01s per request)
- WebSocket: `wss://ws-subscriptions-clob.polymarket.com/ws/market` for real-time order book
- Proxy support: `POLYMARKET_PROXY_URL` env var
- Fees: Taker/maker depend on volume (not directly configurable)

**Kalshi (Elections Betting):**
- REST API: `https://api.elections.kalshi.com/trade-api/v2`
- SDK/Client: Custom via requests + RSA-PSS signing
- Auth: RSA-PSS signed headers (SHA-256, salt=DIGEST_LENGTH)
- Env vars: `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH` or `KALSHI_PRIVATE_KEY_BASE64`
- Implementation: `kalshi_api.py`
- Rate limit: `KALSHI_RATE_LIMIT` (default: 0.05s per request)
- WebSocket: `wss://api.elections.kalshi.com/trade-api/ws/v2` for authenticated feeds
- Proxy support: `KALSHI_PROXY_URL` env var
- Fees: ~$0.02 taker per contract, capped at `KALSHI_FEE_CAP_CENTS` (default: 175 cents)

**Betfair (Betting Exchange):**
- REST API: `https://api.betfair.com/exchange/` (betting and account)
- SDK/Client: Custom via requests
- Auth: SSO login (username/password) → session token + API key
- Env vars: `BETFAIR_USERNAME`, `BETFAIR_PASSWORD`, `BETFAIR_API_KEY`
- Implementation: `betfair_api.py`
- Rate limit: 200ms between requests
- Streaming: TLS TCP socket to `stream-api.betfair.com:443` (configurable via `BETFAIR_STREAM_HOST`, `BETFAIR_STREAM_PORT`)
- Proxy support: `BETFAIR_PROXY_URL` env var
- Fees: `BETFAIR_COMMISSION_RATE` (default: 0.03 / 3% for moderate-volume users)

**Smarkets (Betting Exchange):**
- REST API: Custom protocol over HTTP
- SDK/Client: Custom via requests
- Auth: API key session
- Env vars: `SMARKETS_API_KEY`
- Implementation: `smarkets_api.py`
- Rate limit: Custom per API endpoint
- Proxy support: `SMARKETS_PROXY_URL` env var
- Fees: `SMARKETS_COMMISSION_RATE` (default: 0.02 / 2% commission)

**SX Bet (Decentralized Exchange):**
- REST API: `https://api.sx.bet`
- SDK/Client: Custom via requests
- Auth: Unauthenticated for read, Ethereum signatures for trading
- Env vars: `SXBET_API_KEY` (wallet address), `SXBET_PRIVATE_KEY` (optional, for signing)
- Implementation: `sxbet_api.py`
- Rate limit: 100ms between requests
- Proxy support: `SXBET_PROXY_URL` env var
- Fees: 0% commission on API trades

**Matchbook (Betting Exchange):**
- REST API: `https://api.matchbook.com/edge/rest`
- SDK/Client: Custom via requests
- Auth: Session-based (username/password login)
- Env vars: `MATCHBOOK_USERNAME`, `MATCHBOOK_PASSWORD`
- Implementation: `matchbook_api.py`
- Rate limit: 200ms between requests
- Proxy support: `MATCHBOOK_PROXY_URL` env var
- Fees: 0% commission on predictions

**Gemini Predictions:**
- REST API: `https://api.gemini.com` (configurable via `GEMINI_BASE_URL`)
- SDK/Client: Custom via requests + HMAC-SHA384 signing
- Auth: HMAC-SHA384 signed headers (X-GEMINI-APIKEY, X-GEMINI-PAYLOAD, X-GEMINI-SIGNATURE)
- Env vars: `GEMINI_API_KEY`, `GEMINI_API_SECRET`, `GEMINI_BASE_URL` (default: https://api.gemini.com)
- Implementation: `gemini_api.py`
- Rate limit: `GEMINI_RATE_LIMIT` (default: 0.1s per request)
- Proxy support: `GEMINI_PROXY_URL` env var
- Fees: `GEMINI_FEE_RATE` (default: 0.05 / 5% taker, 1% maker)
- Order types: IOC or GTC (configurable via `GEMINI_ORDER_TYPE`)
- Nonce: seconds-based (not milliseconds), must be within 30s of server time
- Master API keys: require `"account": "primary"` in payload

**IBKR ForecastEx (Interactive Brokers):**
- Socket API: No REST API — uses TWS API via `ib_insync`
- SDK/Client: `ib_insync` >=0.9.70 (connects to IB Gateway or TWS)
- Auth: Client ID authentication to local gateway socket
- Env vars: `IBKR_HOST` (default: 127.0.0.1), `IBKR_PORT` (default: 4001), `IBKR_CLIENT_ID` (default: 1)
- Implementation: `ibkr_api.py`
- Rate limit: `IBKR_ORDER_RATE_LIMIT` (default: 5.0s between orders)
- Contract model: `secType="OPT"`, `exchange="FORECASTX"`, YES=Call, NO=Put
- Prices in dollars (0.01-0.99), NOT cents
- Trading: BUY-only, LMT-only (limit orders), $0.00 commission
- Requires: IB Gateway or TWS running at configurable host/port

## Data & Signal Sources (Read-Only)

**Metaculus (Community Predictions):**
- API: `https://www.metaculus.com/api2` (public REST API)
- Auth: Optional API key for higher rate limits
- Env vars: `METACULUS_API_KEY` (optional)
- Implementation: `metaculus_api.py`
- Rate limit: 1s between requests (higher with API key)
- Cache: `METACULUS_CACHE_TTL` (default: 300s)
- Purpose: Divergence detection — compare platform prices to community consensus
- Feature gate: `EVENT_MONITOR_ENABLED` (default: false)

**Manifold Markets (Prediction Probabilities):**
- API: `https://api.manifold.markets/v0` (public REST API)
- Auth: Optional API key for authenticated endpoints
- Implementation: `manifold_api.py`
- Rate limit: 100ms between requests
- Purpose: Multi-source probability aggregation (signal_aggregator.py)
- Read-only

## Gas Price & Polygon RPC

**Polygon RPC (Dynamic Fee Monitoring):**
- RPC endpoint: `POLYGON_RPC_URL` (default: https://polygon-rpc.com)
- Purpose: Real-time gas price monitoring for dynamic fee-aware execution thresholds
- Implementation: `gas_monitor.py`
- Feature gate: `DYNAMIC_FEE_ENABLED` (default: false)
- Cache: `GAS_PRICE_CACHE_TTL` (default: 15s)

## Data Storage

**Database:**
- SQLite 3 (local file)
- Location: `trades.db` in `DATA_DIR` env var (default: current directory)
- Client: stdlib `sqlite3` with WAL mode for concurrent reads
- Thread-safe: Custom `TradeDB` class with threading lock
- Tables: opportunities, trades, positions, partial_fills
- Implementation: `db.py`

**File Storage:**
- Local filesystem only
- Snapshots: `snapshot.py` (historical price data for backtesting, stored in SQLite)

**Caching:**
- In-memory price cache (dict updated from WebSocket threads, 60s TTL)
- Metaculus response cache (300s TTL configurable)

## Monitoring & Observability

**Logging:**
- Python stdlib logging to stdout (configurable level via `LOG_LEVEL` env var)
- Optional file logging via `LOG_FILE` env var
- Log format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- Implementation: `config.py:setup_logging()`

**Metrics:**
- Custom stdlib-only metrics (counters, gauges, histograms)
- No external dependencies (Prometheus-style exposition available)
- Implementation: `metrics.py`
- Conditional import: gracefully disabled if metrics not enabled

**Dashboards & Status:**
- HTTP dashboard server (lightweight, no framework)
- Endpoint: GET `/` (HTML single-page app)
- API endpoints: `/status` (JSON), `/positions`, `/trades`, `/opportunities`, `/pnl`, `/healthz`
- Port: `DASHBOARD_PORT` (default: 8080)
- Auth: Optional HTTP Basic Auth if `DASHBOARD_PASS` env var set
- Implementation: `dashboard.py`, `dashboard_ui.py`, `run_dashboard.py`

**Alerting:**
- Webhook notifier: `notifier.py`
- Supported: Telegram, Slack, Discord, CallMeBot WhatsApp, generic webhooks
- Env vars: `WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CALLMEBOT_PHONE`, `CALLMEBOT_APIKEY`
- Non-blocking: sends alerts asynchronously in separate thread
- Feature gate: `WEBHOOK_URL` must be configured

## CI/CD & Deployment

**Repository:**
- GitHub (polymarket-arb-scanner)
- Workflows: `.github/workflows/test.yml` (pytest on push to master)

**Hosting:**
- Railway (primary)
- Auto-deploy: Triggered on push to `master` branch via GitHub integration
- Build: Docker image (Dockerfile at root)
- Health check: GET `http://localhost:{DASHBOARD_PORT}/healthz` (30s interval, 5s timeout, 3 retries)
- Startup grace period: 120s

**Backup/Archival:**
- Not integrated (manual backup of trades.db to EFS)

## Webhooks & Callbacks

**Incoming:**
- `/healthz` — health check endpoint (no auth required)
- `/` — dashboard HTML (optional HTTP Basic Auth)
- `/status`, `/positions`, `/trades`, `/opportunities`, `/pnl` — REST API endpoints
- `WEBHOOK_URL` env var — optional external webhook receiver (Telegram, Slack, Discord, etc.)

**Outgoing:**
- Notifications to external webhooks (Telegram, Slack, Discord, CallMeBot)
- No callbacks expected from trading platforms (stateless REST/WebSocket)

## Environment Configuration

**Required for Trading:**
- `POLYMARKET_PRIVATE_KEY` - Ethereum private key for Polymarket (hex format)
- `KALSHI_API_KEY_ID` - Kalshi API key ID
- `KALSHI_PRIVATE_KEY_PATH` or `KALSHI_PRIVATE_KEY_BASE64` - Kalshi RSA private key
- `BETFAIR_USERNAME`, `BETFAIR_PASSWORD`, `BETFAIR_API_KEY` - Betfair SSO + exchange auth
- `SMARKETS_API_KEY` - Smarkets session API key
- `SXBET_API_KEY`, `SXBET_PRIVATE_KEY` - SX Bet wallet + signing key
- `MATCHBOOK_USERNAME`, `MATCHBOOK_PASSWORD` - Matchbook login
- `GEMINI_API_KEY`, `GEMINI_API_SECRET` - Gemini Predictions auth
- IBKR: IB Gateway/TWS running at `IBKR_HOST:IBKR_PORT` (no env var secrets)

**Optional:**
- `METACULUS_API_KEY` - Metaculus (read-only signal source)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` - Telegram notifications
- `CALLMEBOT_PHONE`, `CALLMEBOT_APIKEY` - WhatsApp via CallMeBot
- `WEBHOOK_URL` - Generic webhook receiver
- Proxy URLs: `POLYMARKET_PROXY_URL`, `KALSHI_PROXY_URL`, `BETFAIR_PROXY_URL`, etc.
- `POLYGON_RPC_URL` - Custom Polygon RPC for gas monitoring

**Secrets Location:**
- `.env` file (development, checked into repo for non-secrets)
- Environment variables injected by Railway (production)
- Kalshi private key: File path (`KALSHI_PRIVATE_KEY_PATH`) or base64 string (`KALSHI_PRIVATE_KEY_BASE64`)
- IBKR: No secrets (client ID is not sensitive)

## Integration Patterns

**Rate Limiting:**
- Custom per-platform with thread-safe global state
- All platforms: `_last_request_time` + `_rate_lock` (threading.Lock)
- Retries: `tenacity` with exponential backoff (3 attempts, 1-10s delays)

**Error Handling:**
- Custom exceptions: `_RateLimitError(Exception)` in each API module
- Retry decorator: `@retry(stop=stop_after_attempt(3), wait=wait_exponential(...))`
- All HTTP errors: `resp.raise_for_status()`

**Authentication:**
- Ethereum signatures: py-clob-client (Polymarket), custom (SX Bet)
- RSA-PSS: Kalshi
- HMAC-SHA384: Gemini
- Session tokens: Betfair, Smarkets, Matchbook
- API keys: Kalshi (secondary), Gemini (secondary), Smarkets, SX Bet, optional (Metaculus)
- Credentials never logged (stripped in error messages)

**WebSocket Feeds:**
- Polymarket: CLOB market data via wss://ws-subscriptions-clob.polymarket.com
- Kalshi: Authenticated feed via RSA-PSS signature
- Betfair: Native streaming API (TLS socket to stream-api.betfair.com)
- FeedManager: `ws_feeds.py` manages multiple asyncio WebSocket connections
- Reconnect strategy: Exponential backoff (5s initial, 60s max)
- Proxy support: Optional SOCKS5 proxy via `python-socks[asyncio]`

---

*Integration audit: 2026-03-17*
