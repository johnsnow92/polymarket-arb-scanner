"""Arbitrage trade execution engine."""

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    FILL_POLL_INTERVAL, FILL_POLL_TIMEOUT, HEDGE_ENABLED,
    CONCURRENT_EXECUTION, BALANCE_CACHE_TTL,
    ENABLED_EXECUTION_PLATFORMS, PLATFORM_MIN_ORDER_SIZE,
    ORDER_TIME_IN_FORCE, GTC_ORDER_TIMEOUT,
    FAILED_TRADE_COOLDOWN, WS_CACHE_MAX_AGE_REVALIDATION,
)

# Conditional metrics import — never breaks if metrics.py is missing
try:
    from config import METRICS_ENABLED as _METRICS_ENABLED
    if _METRICS_ENABLED:
        from metrics import metrics as _metrics
    else:
        _metrics = None
except Exception:
    _metrics = None
from db import TradeDB
from risk_manager import RiskManager
from polymarket_api import get_clob_prices, fetch_order_book, get_best_bid_ask, PolymarketTrader
from kalshi_api import KalshiClient
from betfair_api import BetfairClient
from smarkets_api import SmarketsClient
from sxbet_api import SXBetClient
from matchbook_api import MatchbookClient
from gemini_api import GeminiClient
from ibkr_api import IBKRClient
from fees import (
    net_profit_binary_internal,
    net_profit_negrisk_internal,
    net_profit_cross_platform,
    net_profit_kalshi_binary,
    net_profit_kalshi_multi,
    net_profit_triangular,
    net_profit_gemini_binary,
    net_profit_ibkr_binary,
    net_profit_multi_cross,
    find_lowest_fee_path,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HARDEN-05: Idempotency key generation
# ---------------------------------------------------------------------------

def _make_idempotency_key(market_id: str, side: str, price: float, extra: str = "") -> str:
    """Return a 16-char hex key stable within a 60-second window (HARDEN-05).

    The key is derived from: market_id, side, price (4 decimal places),
    the current minute bucket (Unix time // 60), and an optional extra
    discriminator. This ensures the same logical order attempt within a
    single minute maps to the same key, allowing platforms and our own DB
    to detect and reject duplicate submissions.
    """
    minute_bucket = int(time.time()) // 60
    raw = f"{market_id}:{side}:{price:.4f}:{minute_bucket}:{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ArbitrageExecutor:
    """Executes arbitrage trades with risk controls, dry-run, and semi/full-auto modes."""

    def __init__(
        self,
        pm_trader: PolymarketTrader | None,
        kalshi_client: KalshiClient | None,
        db: TradeDB,
        risk_manager: RiskManager,
        dry_run: bool = True,
        exec_mode: str = "semi-auto",
        max_trade_size: float = 5.0,
        price_cache: dict | None = None,
        betfair_client: BetfairClient | None = None,
        smarkets_client: SmarketsClient | None = None,
        sxbet_client: SXBetClient | None = None,
        matchbook_client: MatchbookClient | None = None,
        gemini_client: GeminiClient | None = None,
        ibkr_client: IBKRClient | None = None,
        gas_monitor=None,
        revalidation_adaptive: bool = True,
        revalidation_min_floor: float = 0.003,
        dynamic_sizing: bool = False,
        sizing_aggressiveness: float = 0.5,
        concurrent_execution: bool = False,
        notifier=None,
        position_sizer=None,
    ):
        self.pm_trader = pm_trader
        self.kalshi_client = kalshi_client
        self.betfair_client = betfair_client
        self.smarkets_client = smarkets_client
        self.sxbet_client = sxbet_client
        self.matchbook_client = matchbook_client
        self.gemini_client = gemini_client
        self.ibkr_client = ibkr_client
        self.gas_monitor = gas_monitor
        self.notifier = notifier
        self.db = db
        self.risk = risk_manager
        self.dry_run = dry_run
        self.exec_mode = exec_mode
        self.max_trade_size = max_trade_size
        self.price_cache = price_cache
        self.revalidation_adaptive = revalidation_adaptive
        self.revalidation_min_floor = revalidation_min_floor
        self.dynamic_sizing = dynamic_sizing
        self.sizing_aggressiveness = sizing_aggressiveness
        self.concurrent_execution = concurrent_execution
        self.position_sizer = position_sizer
        # Balance cache: avoids redundant API calls within a scan cycle
        self._balance_cache: dict = {}
        self._balance_cache_ts: float = 0.0
        self._balance_cache_type: str = ""
        # Failed-trade cooldown: ticker/market -> earliest retry time.
        # Prevents catastrophic loops when the same bogus opportunity keeps
        # failing (e.g. insufficient liquidity) and is re-presented.
        self._failed_cooldowns: dict[str, float] = {}
        self._FAILED_COOLDOWN_SECS = FAILED_TRADE_COOLDOWN
        # HARDEN-03: structured decision log (JSONL)
        _data_dir = os.getenv("DATA_DIR", ".")
        self._decision_log_path = os.path.join(_data_dir, "decisions.jsonl")
        self._decision_log_lock = threading.Lock()
        self._decision_fh = open(self._decision_log_path, "a", encoding="utf-8", buffering=1)

    def _get_cached_balances(self, opp_type: str) -> dict | None:
        """Return cached balances if fresh, otherwise fetch and cache.

        Caches balance results for BALANCE_CACHE_TTL seconds to avoid
        redundant API calls between the risk gate and preflight check,
        and across rapid WS-triggered executions.

        Args:
            opp_type: Opportunity type string for platform determination.

        Returns:
            Dict of {platform: balance} or None if no balances available.
        """
        now = time.time()
        if (now - self._balance_cache_ts < BALANCE_CACHE_TTL
                and self._balance_cache_type == opp_type
                and self._balance_cache):
            return self._balance_cache
        balances = self._fetch_balances(opp_type)
        if balances:
            self._balance_cache = balances
            self._balance_cache_ts = now
            self._balance_cache_type = opp_type
        return balances

    def invalidate_balance_cache(self):
        """Clear the balance cache after a trade fills or position changes."""
        self._balance_cache = {}
        self._balance_cache_ts = 0.0
        self._balance_cache_type = ""

    def execute(self, opportunity: dict, market_data: dict | None = None) -> bool:
        """Execute an arbitrage opportunity through the full pipeline.

        Args:
            opportunity: Opportunity dict from scanner
            market_data: Optional pre-fetched market data for re-validation

        Returns:
            True if trade was executed (or logged as dry_run), False if skipped.
        """
        opp_type = opportunity.get("type", "")
        market = opportunity.get("market", "Unknown")

        # 0. Kill switch — abort all execution when dashboard pause is engaged
        try:
            from dashboard import is_paused
            if is_paused():
                logger.info(f"[PAUSED] Kill switch active — skipping: {market} ({opp_type})")
                self._log_skipped(opportunity, "kill_switch")
                return False
        except ImportError:
            pass  # Dashboard not available (e.g. tests)

        # 0b. Failed-trade cooldown — prevent re-execution loops
        cooldown_key = opportunity.get("_kalshi_ticker") or market
        cooldown_until = self._failed_cooldowns.get(cooldown_key, 0)
        if time.time() < cooldown_until:
            remaining = int(cooldown_until - time.time())
            logger.debug(f"Cooldown active for {cooldown_key} ({remaining}s remaining). Skipping.")
            self._log_skipped(opportunity, "failed_cooldown")
            return False

        # 0c. HARDEN-05: Idempotency — reject duplicate within 60s window
        if self.db.has_recent_trade(market, window_secs=60.0):
            logger.debug(f"Duplicate trade for {market} within 60s window. Skipping.")
            self._log_skipped(opportunity, "duplicate_trade")
            return False

        prefix = "[DRY RUN] " if self.dry_run else ""

        logger.info(f"{prefix}--- Evaluating: {market} ({opp_type}) ---")

        _exec_start = time.time()

        # 1. Re-validate prices
        if not self.dry_run and not self._revalidate(opportunity, self.price_cache):
            self._log_skipped(opportunity, "stale_prices")
            if _metrics:
                _metrics.inc("revalidation_failures", {"type": opp_type})
            return False

        # 2. Risk gate (uses cached balances to avoid redundant API calls)
        balances = self._get_cached_balances(opp_type)
        allowed, reason = self.risk.check(opportunity, self.db, balances)
        if not allowed:
            logger.info(f"{prefix}Risk blocked: {reason}")
            self._log_skipped(opportunity, f"risk:{reason}")
            if _metrics:
                _metrics.inc("risk_rejections", {"strategy": opp_type, "reason": reason[:50]})
            return False

        # 2b. Dynamic fee check (GasMonitor)
        if self.gas_monitor and not self.gas_monitor.should_execute(opportunity):
            logger.info(f"{prefix}Dynamic fee check: profit below gas-aware threshold")
            self._log_skipped(opportunity, "gas_threshold")
            return False

        # 3. Size calculation
        depth = opportunity.get("_clob_depth", 0)
        per_leg_budget = self._per_leg_budget(opp_type, opportunity, balances)
        if self.position_sizer:
            desired_size = self.position_sizer.size_for_opportunity(opportunity)
        elif self.dynamic_sizing:
            desired_size = self.risk.calculate_dynamic_size(opportunity, self.sizing_aggressiveness)
        else:
            desired_size = self.max_trade_size
        size = self.risk.clamp_size(desired_size, depth, per_leg_budget)
        if size <= 0:
            logger.info(f"{prefix}Size 0 after constraints. Skipping.")
            self._log_skipped(opportunity, "zero_size")
            return False

        # 4. Build execution plan
        legs = self._build_legs(opportunity, size)
        if not legs:
            logger.info(f"{prefix}Could not build execution legs. Skipping.")
            self._log_skipped(opportunity, "no_legs")
            return False

        # 5. Display plan
        self._print_plan(opportunity, legs, size, prefix)

        # 6. Approval gate (semi-auto)
        if not self.dry_run and self.exec_mode == "semi-auto":
            try:
                answer = input("  Execute? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "y":
                logger.info("Skipped by user.")
                self._log_skipped(opportunity, "user_declined")
                return False

        # 7. Execute or dry-run log
        if self.dry_run:
            result = self._dry_run_log(opportunity, legs, size)
            if _metrics and result:
                _metrics.inc("trades_executed", {"strategy": opp_type, "status": "dry_run"})
            return result
        else:
            # Use concurrent execution when enabled and all legs support cancellation
            if self.concurrent_execution and self._supports_concurrent(legs):
                result = self._execute_legs_concurrent(opportunity, legs, size)
            else:
                result = self._execute_legs(opportunity, legs, size)
            if _metrics:
                latency = time.time() - _exec_start
                _metrics.observe("execution_latency_seconds", {"strategy": opp_type}, latency)
                if result:
                    _metrics.inc("trades_executed", {"strategy": opp_type, "status": "filled"})
                else:
                    _metrics.inc("trades_failed", {"strategy": opp_type, "reason": "execution"})
            # HARDEN-03: log live execution decision
            if result:
                self._write_decision(opportunity, "execute", "filled")
            else:
                self._write_decision(opportunity, "reject", "execution_failed")
            # On failure, set a cooldown so the same opportunity is not
            # re-attempted immediately (prevents catastrophic retry loops).
            if not result:
                self._failed_cooldowns[cooldown_key] = time.time() + self._FAILED_COOLDOWN_SECS
                logger.info(f"Cooldown set for {cooldown_key} ({self._FAILED_COOLDOWN_SECS}s)")
            return result

    def _revalidate(self, opportunity: dict, price_cache: dict | None = None) -> bool:
        """Re-fetch current prices and verify the opportunity still exists.

        Returns True if the opportunity is still profitable (>= 90% of original),
        False if stale, degraded, or any API call fails.
        """
        opp_type = opportunity.get("type", "")
        original_profit = opportunity.get("net_profit", 0)
        if original_profit <= 0:
            return False

        try:
            if opp_type == "Binary":
                return self._revalidate_binary(opportunity, original_profit, price_cache)
            elif opp_type.startswith("NegRisk"):
                return self._revalidate_negrisk(opportunity, original_profit, price_cache)
            elif opp_type.startswith("Cross"):
                return self._revalidate_cross(opportunity, original_profit, price_cache)
            elif opp_type == "KalshiBinary":
                return self._revalidate_kalshi_binary(opportunity, original_profit)
            elif opp_type.startswith("KalshiMulti"):
                return self._revalidate_kalshi_multi(opportunity, original_profit)
            elif opp_type.startswith("Spread"):
                return True  # Spread prices are live order book — no mid-price staleness
            elif opp_type in ("BetfairBackAll", "BetfairBackLay"):
                return True  # Betfair prices are live order book
            elif opp_type in ("SmarketsBackAll", "SmarketsBackLay"):
                return True  # Smarkets prices are live order book
            elif opp_type in ("SXBetBackAll", "SXBetBackLay"):
                return True  # SX Bet prices are live order book
            elif opp_type in ("MatchbookBackAll", "MatchbookBackLay"):
                return True  # Matchbook prices are live order book
            elif opp_type in ("GeminiBinary", "GeminiMulti"):
                return True  # Gemini prices are from order book
            elif opp_type == "IBKRBinary":
                return True  # IBKR prices are from snapshot
            elif opp_type.startswith("MultiCross"):
                return self._revalidate_multi_cross(opportunity, original_profit, price_cache)
            elif opp_type == "TriangularCross":
                return self._revalidate_triangular(opportunity, original_profit, price_cache)
            elif opp_type == "EventDivergence":
                return True  # Signal-based — no stale mid-price to revalidate
            elif opp_type in ("StalePriceOpp", "ResolutionSnipeOpp", "ConvergenceOpp"):
                return True  # Signal/time-based — directional, no mid-price revalidation
            elif opp_type == "MarketMake":
                return True  # MM quotes are continuously refreshed by the MM engine
            # Unknown type — proceed cautiously
            return True
        except Exception as e:
            logger.warning(f"Revalidation failed: {e}")
            return False

    def _check_ws_cache(self, price_cache: dict | None, platform: str, key: str) -> dict | None:
        """Check WebSocket price cache for fresh data (< 5s old)."""
        if not price_cache:
            return None
        entry = price_cache.get((platform, key))
        if entry and time.time() - entry.get("_ts", 0) < 5:
            return entry
        return None

    def _get_revalidation_threshold(self, original_profit: float, opp: dict) -> float:
        """Calculate the minimum acceptable profit during revalidation.

        When adaptive revalidation is enabled:
        - ROI >= 5%: strict 90% of original profit
        - ROI 2-5%: moderate 80% of original profit
        - ROI < 2%: lenient — accept if profit > absolute floor
        Partial-CLOB opportunities use 80% max instead of 90%.

        Multi-outcome opportunities (3+ legs) get a per-leg tolerance
        bonus because each leg's re-fetch introduces independent noise.
        When disabled: always use strict 90% threshold.
        """
        is_partial = opp.get("_partial_clob", False)
        opp_type = opp.get("type", "")

        # Multi-outcome leg count scaling — each leg adds re-fetch noise
        leg_count = 1
        if "(" in opp_type:
            try:
                leg_count = int(opp_type.split("(")[1].rstrip(")"))
            except (ValueError, IndexError):
                leg_count = 1
        # Each additional leg beyond 2 loosens threshold by 5% (capped at 30% bonus)
        multi_bonus = min(0.30, max(0, (leg_count - 2)) * 0.05)

        if not self.revalidation_adaptive:
            base = 0.8 if is_partial else 0.9
            return original_profit * max(0.5, base - multi_bonus)

        total_cost_str = opp.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        roi = original_profit / total_cost if total_cost > 0 else 0

        if roi >= 0.05:
            base = 0.8 if is_partial else 0.9
            return original_profit * max(0.5, base - multi_bonus)
        elif roi >= 0.02:
            base = 0.7 if is_partial else 0.8
            return original_profit * max(0.4, base - multi_bonus)
        else:
            return self.revalidation_min_floor

    def _revalidate_binary(self, opp: dict, original_profit: float, price_cache: dict | None) -> bool:
        """Revalidate a Polymarket binary opportunity."""
        token_ids = opp.get("_token_ids", [])
        if len(token_ids) < 2:
            logger.warning("Revalidation: missing token IDs for binary")
            return False

        # Try WS cache first, then API
        yes_ask = no_ask = None
        cached_yes = self._check_ws_cache(price_cache, "polymarket", token_ids[0])
        cached_no = self._check_ws_cache(price_cache, "polymarket", token_ids[1])
        if cached_yes and cached_no:
            yes_ask = cached_yes.get("price")
            no_ask = cached_no.get("price")

        if yes_ask is None or no_ask is None:
            yes_book = fetch_order_book(token_ids[0])
            no_book = fetch_order_book(token_ids[1])
            if not yes_book or not no_book:
                return False
            yes_data = get_best_bid_ask(yes_book)
            no_data = get_best_bid_ask(no_book)
            yes_ask = yes_data["ask"]
            no_ask = no_data["ask"]

        if yes_ask is None or no_ask is None:
            return False

        result = net_profit_binary_internal(yes_ask, no_ask)
        threshold = self._get_revalidation_threshold(original_profit, opp)
        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False
        # Update opportunity with fresh prices
        opp["prices"] = f"Y={yes_ask:.3f} N={no_ask:.3f}"
        opp["net_profit"] = result["net_profit"]
        return True

    def _revalidate_negrisk(self, opp: dict, original_profit: float, price_cache: dict | None) -> bool:
        """Revalidate a Polymarket NegRisk opportunity."""
        token_ids = opp.get("_token_ids", [])
        if not token_ids:
            return False

        yes_asks = []
        for tid in token_ids:
            if not tid:
                return False
            cached = self._check_ws_cache(price_cache, "polymarket", tid)
            if cached and cached.get("price") is not None:
                yes_asks.append(cached["price"])
            else:
                book = fetch_order_book(tid)
                if not book:
                    return False
                data = get_best_bid_ask(book)
                if data["ask"] is None:
                    return False
                yes_asks.append(data["ask"])

        result = net_profit_negrisk_internal(yes_asks)
        threshold = self._get_revalidation_threshold(original_profit, opp)
        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False
        opp["net_profit"] = result["net_profit"]
        return True

    def _revalidate_cross(self, opp: dict, original_profit: float, price_cache: dict | None) -> bool:
        """Revalidate a cross-platform opportunity."""
        token_ids = opp.get("_token_ids", [])
        kalshi_ticker = opp.get("_kalshi_ticker", "")

        # Re-fetch PM prices
        pm_yes = pm_no = None
        if len(token_ids) >= 2:
            for i, tid in enumerate(token_ids[:2]):
                cached = self._check_ws_cache(price_cache, "polymarket", tid)
                if cached and cached.get("price") is not None:
                    if i == 0:
                        pm_yes = cached["price"]
                    else:
                        pm_no = cached["price"]
            if pm_yes is None or pm_no is None:
                yes_book = fetch_order_book(token_ids[0])
                no_book = fetch_order_book(token_ids[1])
                if not yes_book or not no_book:
                    return False
                yes_data = get_best_bid_ask(yes_book)
                no_data = get_best_bid_ask(no_book)
                pm_yes = yes_data["ask"]
                pm_no = no_data["ask"]

        # Re-fetch Kalshi prices
        k_yes = k_no = None
        if kalshi_ticker and self.kalshi_client:
            cached_k = self._check_ws_cache(price_cache, "kalshi", kalshi_ticker)
            if cached_k:
                k_yes = cached_k.get("yes_price")
                k_no = cached_k.get("no_price")
            if k_yes is None or k_no is None:
                book = self.kalshi_client.fetch_order_book(kalshi_ticker)
                if not book:
                    return False
                # Parse Kalshi order book for best prices
                orderbook = book.get("orderbook", book)
                yes_entries = orderbook.get("yes", [])
                no_entries = orderbook.get("no", [])
                if yes_entries:
                    entry = yes_entries[0]
                    k_yes = float(entry[0]) / 100 if isinstance(entry, list) else float(entry.get("price", 0)) / 100
                if no_entries:
                    entry = no_entries[0]
                    k_no = float(entry[0]) / 100 if isinstance(entry, list) else float(entry.get("price", 0)) / 100

        if pm_yes is None or pm_no is None or k_yes is None or k_no is None:
            return False

        result1 = net_profit_cross_platform(pm_yes, k_no, "yes", "no")
        result2 = net_profit_cross_platform(pm_no, k_yes, "no", "yes")

        if result1["net_profit"] >= result2["net_profit"]:
            best = result1["net_profit"]
            opp["prices"] = f"PM_Y={pm_yes:.3f} K_N={k_no:.3f}"
        else:
            best = result2["net_profit"]
            opp["prices"] = f"PM_N={pm_no:.3f} K_Y={k_yes:.3f}"

        threshold = self._get_revalidation_threshold(original_profit, opp)
        if best < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {best:.4f}")
            return False
        opp["net_profit"] = best
        return True

    def _revalidate_kalshi_binary(self, opp: dict, original_profit: float) -> bool:
        """Revalidate a Kalshi binary opportunity."""
        ticker = opp.get("_kalshi_ticker", "")
        if not ticker or not self.kalshi_client:
            return False
        book = self.kalshi_client.fetch_order_book(ticker)
        if not book:
            return False
        orderbook = book.get("orderbook", book)
        yes_entries = orderbook.get("yes", [])
        no_entries = orderbook.get("no", [])
        if not yes_entries or not no_entries:
            return False
        y_entry = yes_entries[0]
        n_entry = no_entries[0]
        k_yes = float(y_entry[0]) / 100 if isinstance(y_entry, list) else float(y_entry.get("price", 0)) / 100
        k_no = float(n_entry[0]) / 100 if isinstance(n_entry, list) else float(n_entry.get("price", 0)) / 100

        result = net_profit_kalshi_binary(k_yes, k_no)
        threshold = self._get_revalidation_threshold(original_profit, opp)
        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False
        opp["net_profit"] = result["net_profit"]
        return True

    def _revalidate_kalshi_multi(self, opp: dict, original_profit: float) -> bool:
        """Revalidate a Kalshi multi-outcome opportunity."""
        tickers = opp.get("_kalshi_tickers", [])
        if not tickers or not self.kalshi_client:
            logger.info("Revalidation: no tickers or no Kalshi client")
            return False
        yes_prices = []
        for ticker in tickers:
            book = self.kalshi_client.fetch_order_book(ticker)
            if not book:
                logger.info("Revalidation: no order book for %s", ticker)
                return False
            orderbook = book.get("orderbook", book)
            yes_entries = orderbook.get("yes", [])
            if not yes_entries:
                logger.info("Revalidation: no yes entries for %s", ticker)
                return False
            entry = yes_entries[0]
            price = float(entry[0]) / 100 if isinstance(entry, list) else float(entry.get("price", 0)) / 100
            yes_prices.append(price)
        result = net_profit_kalshi_multi(yes_prices)
        threshold = self._get_revalidation_threshold(original_profit, opp)
        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False
        opp["net_profit"] = result["net_profit"]
        return True

    def _revalidate_triangular(self, opp: dict, original_profit: float, price_cache: dict | None) -> bool:
        """Revalidate a TriangularCross opportunity by re-fetching prices from both platforms."""
        platform_a = opp.get("_platform_a", "")
        platform_b = opp.get("_platform_b", "")
        prices_str = opp.get("prices", "")

        if not platform_a or not platform_b:
            return False

        # Parse current prices from prices string: "{platform}_Y={price} {platform}_N={price}"
        parts = prices_str.split()
        if len(parts) != 2:
            return False

        yes_price = no_price = None
        for part in parts:
            if "=" not in part:
                return False
            label, val = part.split("=", 1)
            try:
                price = float(val)
            except ValueError:
                return False
            if label.endswith("_Y"):
                yes_price = price
            elif label.endswith("_N"):
                no_price = price

        if yes_price is None or no_price is None:
            return False

        # Re-fetch YES-side price
        fresh_yes = self._refetch_platform_price(platform_a, opp, price_cache, "yes")
        # Re-fetch NO-side price
        fresh_no = self._refetch_platform_price(platform_b, opp, price_cache, "no")

        # If we couldn't refetch, use the original prices
        if fresh_yes is not None:
            yes_price = fresh_yes
        if fresh_no is not None:
            no_price = fresh_no

        # Recalculate profit with fresh prices
        result = net_profit_triangular(yes_price, no_price, platform_a, platform_b)
        threshold = self._get_revalidation_threshold(original_profit, opp)

        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: triangular profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False

        # Update opportunity with fresh prices
        opp["prices"] = f"{platform_a}_Y={yes_price:.3f} {platform_b}_N={no_price:.3f}"
        opp["net_profit"] = result["net_profit"]
        return True

    def _revalidate_multi_cross(self, opp: dict, original_profit: float, price_cache: dict | None) -> bool:
        """Revalidate a MultiCross opportunity by re-checking each leg's price."""
        outcome_legs = opp.get("_outcome_legs", [])
        if not outcome_legs:
            return False

        prices = []
        platforms = []
        for leg in outcome_legs:
            platform = leg.get("platform", "")
            price = leg.get("price", 0)

            # Try to get a fresh price from WS cache
            cache_key = leg.get("_token_id") or leg.get("_kalshi_ticker", "")
            cached = self._check_ws_cache(price_cache, platform, cache_key) if cache_key else None
            if cached:
                fresh_price = cached.get("yes_ask") or cached.get("yes", price)
                prices.append(fresh_price)
            else:
                prices.append(price)
            platforms.append(platform)

        result = net_profit_multi_cross(prices, platforms)
        threshold = self._get_revalidation_threshold(original_profit, opp)

        if result["net_profit"] < threshold:
            logger.info(f"Revalidation: multi-cross profit degraded {original_profit:.4f} -> {result['net_profit']:.4f}")
            return False

        opp["net_profit"] = result["net_profit"]
        return True

    def _refetch_platform_price(
        self, platform: str, opp: dict, price_cache: dict | None, side: str,
    ) -> float | None:
        """Re-fetch a single price from a platform for revalidation.

        Returns the fresh ask price, or None if unable to fetch.
        """
        if platform == "polymarket":
            token_ids = opp.get("_token_ids", [])
            idx = 0 if side == "yes" else (1 if len(token_ids) > 1 else 0)
            if idx < len(token_ids):
                tid = token_ids[idx]
                cached = self._check_ws_cache(price_cache, "polymarket", tid)
                if cached and cached.get("price") is not None:
                    return cached["price"]
                book = fetch_order_book(tid)
                if book:
                    data = get_best_bid_ask(book)
                    return data.get("ask")
        elif platform == "kalshi":
            ticker = opp.get("_kalshi_ticker", "")
            if ticker and self.kalshi_client:
                cached = self._check_ws_cache(price_cache, "kalshi", ticker)
                if cached and cached.get(f"{side}_price") is not None:
                    return cached[f"{side}_price"]
                book = self.kalshi_client.fetch_order_book(ticker)
                if book:
                    orderbook = book.get("orderbook", book)
                    entries = orderbook.get(side, [])
                    if entries:
                        entry = entries[0]
                        return float(entry[0]) / 100 if isinstance(entry, list) else float(entry.get("price", 0)) / 100
        elif platform == "gemini":
            event_id = opp.get("_gm_event_id", "")
            if event_id and self.gemini_client:
                symbol = opp.get("_gm_yes_symbol" if side == "yes" else "_gm_no_symbol", "")
                if symbol:
                    book = self.gemini_client.get_order_book(symbol, limit=1)
                    if book and book.get("asks"):
                        return book["asks"][0].get("price")
        elif platform == "ibkr":
            event_id = opp.get("_ibkr_event_id", "")
            if event_id and self.ibkr_client:
                # Re-fetch via get_market_price with a minimal market dict
                conid_key = "_ibkr_yes_conid" if side == "yes" else "_ibkr_no_conid"
                conid = opp.get(conid_key, "")
                if conid:
                    mini_market = {
                        "contracts": [
                            {"conid": opp.get("_ibkr_yes_conid", ""), "side": "YES"},
                            {"conid": opp.get("_ibkr_no_conid", ""), "side": "NO"},
                        ]
                    }
                    y, n = self.ibkr_client.get_market_price(mini_market)
                    return y if side == "yes" else n
        # For betfair/smarkets/sxbet/matchbook: live order book, skip refetch
        return None

    def _per_leg_budget(self, opp_type: str, opportunity: dict, balances: dict | None) -> float | None:
        """Calculate per-leg budget based on platform balance and number of legs.

        For multi-leg arbs on the same platform, divides the available balance
        by the number of legs so the total trade fits within the balance.
        """
        if not balances:
            return None

        # Determine relevant balance
        if "Kalshi" in opp_type and "Cross" not in opp_type:
            balance = balances.get("kalshi")
        elif "Cross" in opp_type or "Triangular" in opp_type:
            # Cross/Triangular arbs have 1 leg per platform — use the smaller balance
            bal_values = [v for v in balances.values() if v is not None and v > 0]
            balance = min(bal_values) if bal_values else None
        elif opp_type.startswith("Betfair"):
            balance = balances.get("betfair")
        elif opp_type.startswith("Smarkets"):
            balance = balances.get("smarkets")
        elif opp_type.startswith("SXBet"):
            balance = balances.get("sxbet")
        elif opp_type.startswith("Matchbook"):
            balance = balances.get("matchbook")
        elif opp_type.startswith("Gemini"):
            balance = balances.get("gemini")
        elif opp_type.startswith("IBKR"):
            balance = balances.get("ibkr")
        else:
            balance = balances.get("polymarket")

        if balance is None or balance <= 0:
            return 0.0

        # Estimate number of legs on the same platform
        if opp_type == "Binary" or opp_type == "KalshiBinary":
            num_legs = 2
        elif opp_type.startswith("KalshiMulti") or opp_type.startswith("NegRisk"):
            # Parse count from type like "KalshiMulti(3)" or "NegRisk(5)"
            import re
            m = re.search(r'\((\d+)\)', opp_type)
            num_legs = int(m.group(1)) if m else len(opportunity.get("_token_ids", [])) or 2
        elif "Cross" in opp_type or "Triangular" in opp_type:
            num_legs = 1  # 1 leg per platform
        elif opp_type.endswith("BackAll"):
            # Multi-leg: count from selection/runner IDs
            ids_keys = ("_bf_selection_ids", "_sm_contract_ids", "_sx_outcome_ids", "_mb_runner_ids")
            for key in ids_keys:
                ids = opportunity.get(key, [])
                if ids:
                    num_legs = len(ids)
                    break
            else:
                num_legs = 2
        elif opp_type.endswith("BackLay"):
            num_legs = 2
        else:
            num_legs = 2

        return balance / num_legs

    def _fetch_balances(self, opp_type: str) -> dict | None:
        """Fetch balances from relevant platforms concurrently.

        Builds a list of (platform_name, callable) pairs based on the
        opportunity type, then submits all balance fetches in parallel
        via ThreadPoolExecutor. This reduces cross-platform balance
        fetching from sequential (200ms-2s) to parallel (~200ms).

        Args:
            opp_type: Opportunity type string (e.g. "Cross(PM_YES + K_NO)").

        Returns:
            Dict of {platform: balance} or None if no balances fetched.
        """
        needs_kalshi = "Kalshi" in opp_type or "Cross" in opp_type or "Triangular" in opp_type
        needs_polymarket = "Kalshi" not in opp_type or "Cross" in opp_type or "Triangular" in opp_type
        needs_exchange = (
            "Cross" in opp_type or "Triangular" in opp_type
            or opp_type.startswith("Betfair") or opp_type.startswith("Smarkets")
            or opp_type.startswith("SXBet") or opp_type.startswith("Matchbook")
            or opp_type.startswith("Gemini") or opp_type.startswith("IBKR")
            or opp_type == "EventDivergence"
        )

        # Build list of (platform_name, fetch_callable) to run in parallel
        fetch_tasks: list[tuple[str, callable]] = []
        if needs_kalshi and self.kalshi_client:
            fetch_tasks.append(("kalshi", self.kalshi_client.get_balance))
        if needs_polymarket and self.pm_trader:
            fetch_tasks.append(("polymarket", self.pm_trader.get_balance))
        if needs_exchange:
            exchange_clients = [
                ("betfair", self.betfair_client),
                ("smarkets", self.smarkets_client),
                ("sxbet", self.sxbet_client),
                ("matchbook", self.matchbook_client),
                ("gemini", self.gemini_client),
                ("ibkr", self.ibkr_client),
            ]
            for name, client in exchange_clients:
                if client and hasattr(client, "get_balance"):
                    fetch_tasks.append((name, client.get_balance))

        if not fetch_tasks:
            return None

        balances = {}
        with ThreadPoolExecutor(max_workers=len(fetch_tasks)) as pool:
            futures = {
                pool.submit(fn): name for name, fn in fetch_tasks
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    bal = future.result()
                    if bal is not None:
                        balances[name] = bal
                except Exception as e:
                    logger.debug("Failed to fetch %s balance: %s", name, e)
        return balances if balances else None

    def _build_legs(self, opportunity: dict, size: float) -> list[dict]:
        """Build execution legs based on opportunity type."""
        opp_type = opportunity.get("type", "")
        token_ids = opportunity.get("_token_ids", [])
        legs = []

        if opp_type == "KalshiBinary":
            # Buy YES + NO on same Kalshi market
            legs = [
                {"platform": "kalshi", "side": "yes", "action": "buy",
                 "price": opportunity["_kalshi_yes"],
                 "_ticker": opportunity["_kalshi_ticker"]},
                {"platform": "kalshi", "side": "no", "action": "buy",
                 "price": opportunity["_kalshi_no"],
                 "_ticker": opportunity["_kalshi_ticker"]},
            ]
        elif opp_type.startswith("KalshiMulti"):
            # Buy YES on every outcome in a Kalshi event
            legs = [
                {"platform": "kalshi", "side": "yes", "action": "buy",
                 "price": price, "_ticker": ticker}
                for ticker, price in zip(
                    opportunity["_kalshi_tickers"],
                    opportunity["_kalshi_prices"],
                )
            ]
        elif opp_type == "Binary":
            # Buy YES + NO on Polymarket
            yes_token = token_ids[0] if len(token_ids) > 0 else ""
            no_token = token_ids[1] if len(token_ids) > 1 else ""
            yes_price = self._parse_price(opportunity, "Y=")
            no_price = self._parse_price(opportunity, "N=")
            if yes_price is None or no_price is None:
                return []
            legs = [
                {"platform": "polymarket", "side": "BUY", "token": "yes",
                 "price": yes_price, "_token_id": yes_token},
                {"platform": "polymarket", "side": "BUY", "token": "no",
                 "price": no_price, "_token_id": no_token},
            ]
        elif opp_type.startswith("NegRisk"):
            # Buy YES on each outcome — prices are comma-separated in the opp
            prices_str = opportunity.get("prices", "")
            prices = []
            for p in prices_str.split(","):
                p = p.strip()
                # Skip trailing summary like "... (N total)"
                if not p or p.startswith("(") or p == "...":
                    continue
                try:
                    prices.append(float(p))
                except ValueError:
                    continue
            # Validate price count matches token ID count
            if token_ids and len(prices) != len(token_ids):
                logger.warning(f"NegRisk price count ({len(prices)}) != token ID count ({len(token_ids)}). Skipping.")
                return []
            for i, price in enumerate(prices):
                legs.append({
                    "platform": "polymarket",
                    "side": "BUY",
                    "token": f"yes_{i}",
                    "price": price,
                    "_token_id": token_ids[i] if i < len(token_ids) else "",
                })
        elif opp_type.startswith("Spread"):
            # Spread capture: BUY then SELL on same token
            token_id = opportunity.get("_token_id", "")
            buy_price = opportunity.get("_ask_price", 0)
            sell_price = opportunity.get("_bid_price", 0)
            spread_platform = opportunity.get("_spread_platform", "polymarket")
            if spread_platform == "polymarket":
                legs = [
                    {"platform": "polymarket", "side": "BUY", "token": "yes",
                     "price": buy_price, "_token_id": token_id},
                    {"platform": "polymarket", "side": "SELL", "token": "yes",
                     "price": sell_price, "_token_id": token_id},
                ]
            elif spread_platform == "kalshi":
                ticker = opportunity.get("_kalshi_ticker", "")
                side = opportunity.get("_spread_side", "yes")
                legs = [
                    {"platform": "kalshi", "side": side, "action": "buy",
                     "price": buy_price, "_ticker": ticker},
                    {"platform": "kalshi", "side": side, "action": "sell",
                     "price": sell_price, "_ticker": ticker},
                ]
        elif opp_type == "BetfairBackAll":
            legs = [
                {"platform": "betfair", "side": "BACK",
                 "price": price, "_market_id": opportunity.get("_bf_market_id", ""),
                 "_selection_id": sel_id}
                for sel_id, price in zip(
                    opportunity.get("_bf_selection_ids", []),
                    opportunity.get("_bf_prices", []),
                )
            ]
        elif opp_type == "BetfairBackLay":
            market_id = opportunity.get("_bf_market_id", "")
            sel_id = opportunity.get("_bf_selection_id")
            legs = [
                {"platform": "betfair", "side": "BACK",
                 "price": opportunity.get("_bf_back_price", 0),
                 "_market_id": market_id, "_selection_id": sel_id},
                {"platform": "betfair", "side": "LAY",
                 "price": opportunity.get("_bf_lay_price", 0),
                 "_market_id": market_id, "_selection_id": sel_id},
            ]
        elif opp_type == "SmarketsBackAll":
            legs = [
                {"platform": "smarkets", "side": "BACK",
                 "price": price, "_market_id": opportunity.get("_sm_market_id", ""),
                 "_contract_id": cid}
                for cid, price in zip(
                    opportunity.get("_sm_contract_ids", []),
                    opportunity.get("_sm_prices", []),
                )
            ]
        elif opp_type == "SmarketsBackLay":
            legs = [
                {"platform": "smarkets", "side": "BACK",
                 "price": opportunity.get("_sm_back_price", 0),
                 "_market_id": opportunity.get("_sm_market_id", ""),
                 "_contract_id": opportunity.get("_sm_contract_id")},
                {"platform": "smarkets", "side": "LAY",
                 "price": opportunity.get("_sm_lay_price", 0),
                 "_market_id": opportunity.get("_sm_market_id", ""),
                 "_contract_id": opportunity.get("_sm_contract_id")},
            ]
        elif opp_type == "SXBetBackAll":
            legs = [
                {"platform": "sxbet", "side": "BACK",
                 "price": price, "_market_hash": opportunity.get("_sx_market_hash", ""),
                 "_outcome_id": oid}
                for oid, price in zip(
                    opportunity.get("_sx_outcome_ids", []),
                    opportunity.get("_sx_prices", []),
                )
            ]
        elif opp_type == "SXBetBackLay":
            legs = [
                {"platform": "sxbet", "side": "BACK",
                 "price": opportunity.get("_sx_back_price", 0),
                 "_market_hash": opportunity.get("_sx_market_hash", ""),
                 "_outcome_id": opportunity.get("_sx_outcome_id")},
                {"platform": "sxbet", "side": "LAY",
                 "price": opportunity.get("_sx_lay_price", 0),
                 "_market_hash": opportunity.get("_sx_market_hash", ""),
                 "_outcome_id": opportunity.get("_sx_outcome_id")},
            ]
        elif opp_type == "MatchbookBackAll":
            legs = [
                {"platform": "matchbook", "side": "BACK",
                 "price": price, "_market_id": opportunity.get("_mb_market_id", ""),
                 "_runner_id": rid}
                for rid, price in zip(
                    opportunity.get("_mb_runner_ids", []),
                    opportunity.get("_mb_prices", []),
                )
            ]
        elif opp_type == "MatchbookBackLay":
            market_id = opportunity.get("_mb_market_id", "")
            runner_id = opportunity.get("_mb_runner_id")
            legs = [
                {"platform": "matchbook", "side": "BACK",
                 "price": opportunity.get("_mb_back_price", 0),
                 "_market_id": market_id, "_runner_id": runner_id},
                {"platform": "matchbook", "side": "LAY",
                 "price": opportunity.get("_mb_lay_price", 0),
                 "_market_id": market_id, "_runner_id": runner_id},
            ]
        elif opp_type == "GeminiBinary":
            if "gemini" not in ENABLED_EXECUTION_PLATFORMS:
                logger.debug("Skipping GeminiBinary: gemini not in ENABLED_EXECUTION_PLATFORMS")
                return []
            yes_price = opportunity.get("_gm_yes_price", self._parse_price(opportunity, "Y="))
            no_price = opportunity.get("_gm_no_price", self._parse_price(opportunity, "N="))
            if yes_price is None or no_price is None:
                return []
            legs = [
                {"platform": "gemini", "symbol": opportunity.get("_gm_yes_symbol", ""),
                 "side": "buy", "outcome": "yes", "price": yes_price},
                {"platform": "gemini", "symbol": opportunity.get("_gm_no_symbol", ""),
                 "side": "buy", "outcome": "no", "price": no_price},
            ]
        elif opp_type == "GeminiMulti":
            if "gemini" not in ENABLED_EXECUTION_PLATFORMS:
                logger.debug("Skipping GeminiMulti: gemini not in ENABLED_EXECUTION_PLATFORMS")
                return []
            symbols = opportunity.get("_gm_symbols", [])
            prices = opportunity.get("_gm_prices", [])
            for symbol, price in zip(symbols, prices):
                legs.append({
                    "platform": "gemini", "symbol": symbol,
                    "side": "buy", "outcome": "yes", "price": price,
                })
        elif opp_type == "IBKRBinary":
            if "ibkr" not in ENABLED_EXECUTION_PLATFORMS:
                logger.debug("Skipping IBKRBinary: ibkr not in ENABLED_EXECUTION_PLATFORMS")
                return []
            yes_price = opportunity.get("_ibkr_yes_price", self._parse_price(opportunity, "Y="))
            no_price = opportunity.get("_ibkr_no_price", self._parse_price(opportunity, "N="))
            if yes_price is None or no_price is None:
                return []
            legs = [
                {"platform": "ibkr", "conid": opportunity.get("_ibkr_yes_conid", ""),
                 "side": "buy", "price": yes_price},
                {"platform": "ibkr", "conid": opportunity.get("_ibkr_no_conid", ""),
                 "side": "buy", "price": no_price},
            ]
        elif opp_type.startswith("MultiCross"):
            # Multi-outcome cross-platform: buy cheapest YES per outcome across platforms
            outcome_legs = opportunity.get("_outcome_legs", [])
            for leg in outcome_legs:
                plat = leg.get("platform", "")
                if plat and plat not in ENABLED_EXECUTION_PLATFORMS:
                    logger.info(
                        "MultiCross leg on '%s' blocked — not in ENABLED_EXECUTION_PLATFORMS",
                        plat,
                    )
                    return []
                if plat == "polymarket":
                    legs.append({
                        "platform": "polymarket", "side": "BUY", "token": "yes",
                        "price": leg["price"],
                        "_token_id": leg.get("_token_id", ""),
                    })
                elif plat == "kalshi":
                    legs.append({
                        "platform": "kalshi", "side": "yes", "action": "buy",
                        "price": leg["price"],
                        "_ticker": leg.get("_kalshi_ticker", ""),
                    })
        elif opp_type == "TriangularCross":
            # 3-way cross-platform: cheapest YES + cheapest NO across 3+ platforms
            legs = self._build_cross_all_legs(opportunity, size)
        elif opp_type == "EventDivergence":
            # Single-leg directional trade based on Metaculus divergence signal
            legs = self._build_event_divergence_legs(opportunity, size)
        elif opp_type in ("StalePriceOpp", "ResolutionSnipeOpp", "ConvergenceOpp"):
            # Layer 2-4: single-leg directional trades
            legs = self._build_directional_legs(opportunity, size)
        elif opp_type == "MarketMake":
            # Layer 3: market making — bid+ask pair
            legs = self._build_mm_legs(opportunity, size)
        elif opp_type.startswith("Cross"):
            # Re-validate fee path if scan provided a hint (per user decision: confirm or override)
            fee_path = opportunity.get("_fee_path")
            if fee_path:
                fresh = find_lowest_fee_path(
                    [fee_path["best_yes_platform"], fee_path["best_no_platform"]],
                    {fee_path["best_yes_platform"]: fee_path["yes_price"]},
                    {fee_path["best_no_platform"]: fee_path["no_price"]},
                )
                if fresh and fresh["net_profit"] > 0:
                    logger.info(
                        f"Fee path re-validated: {fresh['best_yes_platform']} YES + "
                        f"{fresh['best_no_platform']} NO, net=${fresh['net_profit']:.4f}"
                    )
                    yes_plat = fresh["best_yes_platform"]
                    no_plat = fresh["best_no_platform"]
                    yes_price = fresh["yes_price"]
                    no_price = fresh["no_price"]

                    # Build YES leg using fee-path-optimal platform
                    yes_leg: dict = {"platform": yes_plat, "side": "BUY", "token": "yes",
                                     "price": yes_price}
                    if yes_plat == "polymarket":
                        yes_leg["_token_id"] = token_ids[0] if len(token_ids) > 0 else ""
                    elif yes_plat == "kalshi":
                        yes_leg["side"] = "yes"
                        yes_leg["action"] = "buy"
                        yes_leg["_ticker"] = opportunity.get("_kalshi_ticker", "")

                    # Build NO leg using fee-path-optimal platform
                    no_leg: dict = {"platform": no_plat, "side": "BUY", "token": "no",
                                    "price": no_price}
                    if no_plat == "polymarket":
                        no_leg["_token_id"] = token_ids[1] if len(token_ids) > 1 else ""
                    elif no_plat == "kalshi":
                        no_leg["side"] = "no"
                        no_leg["action"] = "buy"
                        no_leg["_ticker"] = opportunity.get("_kalshi_ticker", "")

                    legs = [yes_leg, no_leg]
                    return legs
                else:
                    logger.info("Fee path stale (no longer profitable), falling back to default routing")
                    # Fall through to default prices_str routing below

            # Default routing: parse prices_str (backward compatible)
            prices_str = opportunity.get("prices", "")
            kalshi_ticker = opportunity.get("_kalshi_ticker", "")
            if "PM_Y=" in prices_str and "K_N=" in prices_str:
                yes_token = token_ids[0] if len(token_ids) > 0 else ""
                pm_price = self._parse_price(opportunity, "PM_Y=")
                k_price = self._parse_price(opportunity, "K_N=")
                if pm_price is None or k_price is None:
                    return []
                legs = [
                    {"platform": "polymarket", "side": "BUY", "token": "yes",
                     "price": pm_price, "_token_id": yes_token},
                    {"platform": "kalshi", "side": "no", "action": "buy",
                     "price": k_price, "_ticker": kalshi_ticker},
                ]
            elif "PM_N=" in prices_str and "K_Y=" in prices_str:
                no_token = token_ids[1] if len(token_ids) > 1 else ""
                pm_price = self._parse_price(opportunity, "PM_N=")
                k_price = self._parse_price(opportunity, "K_Y=")
                if pm_price is None or k_price is None:
                    return []
                legs = [
                    {"platform": "polymarket", "side": "BUY", "token": "no",
                     "price": pm_price, "_token_id": no_token},
                    {"platform": "kalshi", "side": "yes", "action": "buy",
                     "price": k_price, "_ticker": kalshi_ticker},
                ]
            elif "_platform_a" in opportunity:
                # Generic cross-platform handler for cross-all opportunities
                legs = self._build_cross_all_legs(opportunity, size)

        # HARDEN-05: Attach a per-leg idempotency key before returning.
        # Keys are stable within a 60-second minute bucket so that retries
        # within the same minute are identifiable as duplicates by the platform.
        market_id = opportunity.get("market", "")
        for leg in legs:
            side = leg.get("side", leg.get("token", leg.get("outcome", "")))
            leg["_idempotency_key"] = _make_idempotency_key(
                market_id=market_id,
                side=str(side),
                price=float(leg.get("price", 0)),
            )

        return legs

    def _build_cross_all_legs(self, opportunity: dict, size: float) -> list[dict]:
        """Build execution legs for generic cross-platform opportunities."""
        prices_str = opportunity.get("prices", "")
        platform_a = opportunity.get("_platform_a", "")
        platform_b = opportunity.get("_platform_b", "")

        # Reject early if either platform is not whitelisted
        for plat in (platform_a, platform_b):
            if plat and plat not in ENABLED_EXECUTION_PLATFORMS:
                logger.info(
                    f"Cross-platform leg on '{plat}' blocked — "
                    f"not in ENABLED_EXECUTION_PLATFORMS"
                )
                return []
            min_size = PLATFORM_MIN_ORDER_SIZE.get(plat, 0)
            if plat and size > 0 and size / 2 < min_size:
                logger.info(
                    f"Cross-platform leg on '{plat}' blocked — "
                    f"per-leg size ${size / 2:.2f} below minimum ${min_size:.2f}"
                )
                return []
        token_ids = opportunity.get("_token_ids", [])

        # Parse prices: format is "{platform}_Y={price} {platform}_N={price}"
        parts = prices_str.split()
        if len(parts) != 2:
            return []

        leg_a = self._build_single_cross_leg(
            parts[0], platform_a, platform_b, opportunity, token_ids, is_first=True)
        leg_b = self._build_single_cross_leg(
            parts[1], platform_b, platform_a, opportunity, token_ids, is_first=False)

        if leg_a is None or leg_b is None:
            return []
        return [leg_a, leg_b]

    def _build_single_cross_leg(
        self, price_part: str, expected_platform: str, other_platform: str,
        opportunity: dict, token_ids: list, is_first: bool,
    ) -> dict | None:
        """Parse a single price part and build a leg dict."""
        # price_part format: "{platform}_Y={price}" or "{platform}_N={price}"
        if "=" not in price_part:
            return None

        label, val = price_part.split("=", 1)
        try:
            price = float(val)
        except ValueError:
            return None
        if price <= 0 or price >= 1:
            return None

        # Determine platform and side from label
        if label.endswith("_Y"):
            platform_name = label[:-2]
            side = "yes"
        elif label.endswith("_N"):
            platform_name = label[:-2]
            side = "no"
        else:
            return None

        leg = {"price": price}

        if platform_name == "polymarket":
            token_idx = 0 if side == "yes" else 1
            leg["platform"] = "polymarket"
            leg["side"] = "BUY"
            leg["token"] = side
            leg["_token_id"] = token_ids[token_idx] if token_idx < len(token_ids) else ""
        elif platform_name == "kalshi":
            leg["platform"] = "kalshi"
            leg["side"] = side
            leg["action"] = "buy"
            leg["_ticker"] = opportunity.get("_kalshi_ticker", "")
        elif platform_name == "betfair":
            leg["platform"] = "betfair"
            leg["side"] = side
            leg["_market_id"] = opportunity.get("_market_id", "")
            leg["_selection_id"] = opportunity.get("_selection_id")
        elif platform_name == "smarkets":
            leg["platform"] = "smarkets"
            leg["side"] = side
            leg["_market_id"] = opportunity.get("_sm_market_id", "")
        elif platform_name == "sxbet":
            leg["platform"] = "sxbet"
            leg["side"] = side
            leg["_market_hash"] = opportunity.get("_sx_market_hash", "")
        elif platform_name == "matchbook":
            leg["platform"] = "matchbook"
            leg["side"] = side
            leg["_market_id"] = opportunity.get("_mb_market_id", "")
            leg["_runner_id"] = opportunity.get("_mb_runner_id")
        elif platform_name == "gemini":
            leg["platform"] = "gemini"
            leg["side"] = "buy"
            leg["outcome"] = side
            leg["symbol"] = opportunity.get(
                "_gm_yes_symbol" if side == "yes" else "_gm_no_symbol", "")
        elif platform_name == "ibkr":
            leg["platform"] = "ibkr"
            leg["side"] = "buy"
            leg["conid"] = opportunity.get(
                "_ibkr_yes_conid" if side == "yes" else "_ibkr_no_conid", "")
        else:
            return None

        return leg

    def _build_event_divergence_legs(self, opportunity: dict, size: float) -> list[dict]:
        """Build execution legs for an EventDivergence opportunity.

        EventDivergence is a single-leg directional trade: buy YES or NO on
        one platform based on Metaculus divergence signal.
        """
        platform = opportunity.get("_platform", "")
        direction = opportunity.get("_direction", "")
        prices_str = opportunity.get("prices", "")

        # Extract platform price from "platform={price} metaculus={price}"
        platform_price = None
        for part in prices_str.split():
            if part.startswith("platform="):
                try:
                    platform_price = float(part.split("=", 1)[1])
                except ValueError as e:
                    logger.debug("Ignoring unparseable price part: %s", e)
                break

        if not platform or not direction or platform_price is None:
            return []

        if direction == "BUY_YES":
            side = "yes"
            price = platform_price
        elif direction == "BUY_NO":
            side = "no"
            price = 1.0 - platform_price  # NO price is complement
        else:
            return []

        leg = {"price": price, "side": side}

        if platform == "polymarket":
            token_ids = opportunity.get("_token_ids", [])
            token_idx = 0 if side == "yes" else 1
            leg["platform"] = "polymarket"
            leg["side"] = "BUY"
            leg["token"] = side
            leg["_token_id"] = token_ids[token_idx] if token_idx < len(token_ids) else ""
        elif platform == "kalshi":
            leg["platform"] = "kalshi"
            leg["action"] = "buy"
            leg["_ticker"] = opportunity.get("_kalshi_ticker", "")
        elif platform == "betfair":
            leg["platform"] = "betfair"
            leg["side"] = "BACK" if direction == "BUY_YES" else "LAY"
            leg["_market_id"] = opportunity.get("_market_id", "")
            leg["_selection_id"] = opportunity.get("_selection_id")
        elif platform == "smarkets":
            leg["platform"] = "smarkets"
            leg["side"] = "BACK" if direction == "BUY_YES" else "LAY"
            leg["_market_id"] = opportunity.get("_sm_market_id", "")
            leg["_contract_id"] = opportunity.get("_sm_contract_id", "")
        elif platform == "sxbet":
            leg["platform"] = "sxbet"
            leg["side"] = "BACK" if direction == "BUY_YES" else "LAY"
            leg["_market_hash"] = opportunity.get("_sx_market_hash", "")
            leg["_outcome_id"] = opportunity.get("_sx_outcome_id", "")
        elif platform == "matchbook":
            leg["platform"] = "matchbook"
            leg["side"] = "back" if direction == "BUY_YES" else "lay"
            leg["_market_id"] = opportunity.get("_mb_market_id", "")
            leg["_runner_id"] = opportunity.get("_mb_runner_id", "")
        elif platform == "gemini":
            leg["platform"] = "gemini"
            leg["side"] = "buy"
            leg["outcome"] = "yes" if direction == "BUY_YES" else "no"
            leg["symbol"] = opportunity.get(
                "_gm_yes_symbol" if direction == "BUY_YES" else "_gm_no_symbol", "")
        elif platform == "ibkr":
            # IBKR: BUY_YES = buy YES conid, BUY_NO = buy NO conid (both are BUY)
            leg["platform"] = "ibkr"
            leg["side"] = "buy"
            leg["conid"] = opportunity.get(
                "_ibkr_yes_conid" if direction == "BUY_YES" else "_ibkr_no_conid", "")
        else:
            return []

        return [leg]

    def _build_directional_legs(self, opportunity: dict, size: float) -> list[dict]:
        """Build execution legs for directional opportunities (Stale, Resolution, Convergence).

        These are single-leg trades similar to EventDivergence — buy YES or NO
        on a single platform based on signal.
        """
        platform = opportunity.get("_platform", "")
        direction = opportunity.get("_direction", "")
        trade_price = opportunity.get("_trade_price") or opportunity.get("_price", 0)

        if not platform or not direction or not trade_price:
            return []

        if platform not in ENABLED_EXECUTION_PLATFORMS:
            logger.info("Directional leg on '%s' blocked — not in ENABLED_EXECUTION_PLATFORMS", platform)
            return []

        side = "yes" if direction == "BUY_YES" else "no"
        leg = {"price": trade_price, "side": side}

        if platform == "polymarket":
            token_ids = opportunity.get("_token_ids", [])
            token_idx = 0 if side == "yes" else 1
            leg["platform"] = "polymarket"
            leg["side"] = "BUY"
            leg["token"] = side
            leg["_token_id"] = token_ids[token_idx] if token_idx < len(token_ids) else ""
        elif platform == "kalshi":
            leg["platform"] = "kalshi"
            leg["action"] = "buy"
            leg["_ticker"] = opportunity.get("_kalshi_ticker", "")
        elif platform == "gemini":
            leg["platform"] = "gemini"
            leg["side"] = "buy"
            leg["outcome"] = side
            leg["symbol"] = opportunity.get(
                "_gm_yes_symbol" if side == "yes" else "_gm_no_symbol", "")
        elif platform == "ibkr":
            leg["platform"] = "ibkr"
            leg["side"] = "buy"
            leg["conid"] = opportunity.get(
                "_ibkr_yes_conid" if side == "yes" else "_ibkr_no_conid", "")
        elif platform == "betfair":
            leg["platform"] = "betfair"
            leg["side"] = "BACK" if direction == "BUY_YES" else "LAY"
            leg["_market_id"] = opportunity.get("_market_id", "")
            leg["_selection_id"] = opportunity.get("_selection_id")
        elif platform == "smarkets":
            leg["platform"] = "smarkets"
            leg["side"] = "BACK" if direction == "BUY_YES" else "LAY"
            leg["_market_id"] = opportunity.get("_sm_market_id", "")
            leg["_contract_id"] = opportunity.get("_sm_contract_id", "")
        elif platform == "sxbet":
            leg["platform"] = "sxbet"
            leg["side"] = "BACK" if direction == "BUY_YES" else "LAY"
            leg["_market_hash"] = opportunity.get("_sx_market_hash", "")
            leg["_outcome_id"] = opportunity.get("_sx_outcome_id", "")
        elif platform == "matchbook":
            leg["platform"] = "matchbook"
            leg["side"] = "back" if direction == "BUY_YES" else "lay"
            leg["_market_id"] = opportunity.get("_mb_market_id", "")
            leg["_runner_id"] = opportunity.get("_mb_runner_id", "")
        else:
            return []

        return [leg]

    def _build_mm_legs(self, opportunity: dict, size: float) -> list[dict]:
        """Build execution legs for a market making opportunity.

        Market making places both a bid and an ask as resting limit orders.
        """
        platform = opportunity.get("_platform", "")
        bid_price = opportunity.get("_bid_price", 0)
        ask_price = opportunity.get("_ask_price", 0)
        market_key = opportunity.get("_market_key", "")

        if not platform or not bid_price or not ask_price:
            return []

        if platform not in ENABLED_EXECUTION_PLATFORMS:
            logger.info("MM leg on '%s' blocked — not in ENABLED_EXECUTION_PLATFORMS", platform)
            return []

        legs = [
            {"platform": platform, "side": "BUY", "price": bid_price,
             "token": "yes", "_market_key": market_key, "_mm_side": "bid"},
            {"platform": platform, "side": "SELL", "price": ask_price,
             "token": "yes", "_market_key": market_key, "_mm_side": "ask"},
        ]
        return legs

    def _parse_price(self, opportunity: dict, prefix: str) -> float | None:
        """Extract a price from the opportunity's prices string. Returns None on failure."""
        prices_str = opportunity.get("prices", "")
        for part in prices_str.split():
            if part.startswith(prefix):
                try:
                    value = float(part[len(prefix):])
                    if value <= 0 or value >= 1:
                        logger.warning(f"Price out of range for {prefix}: {value}")
                        return None
                    return value
                except ValueError:
                    logger.warning(f"Failed to parse price for {prefix}: {part}")
                    return None
        logger.warning(f"Price prefix '{prefix}' not found in: {prices_str}")
        return None

    def _print_plan(self, opportunity: dict, legs: list[dict], size: float, prefix: str):
        """Print the execution plan."""
        logger.info(f"{prefix}Trade size: ${size:.2f}")
        logger.info(f"{prefix}Net profit: ${opportunity.get('net_profit', 0):.4f}")
        logger.info(f"{prefix}ROI: {opportunity.get('net_roi', 'N/A')}")
        for i, leg in enumerate(legs):
            platform = leg["platform"]
            side = leg.get("side", "")
            price = leg.get("price", 0)
            token = leg.get("token", "")
            logger.info(f"{prefix}Leg {i+1}: {platform} {side} {token} @ ${price:.3f}")

    def _dry_run_log(self, opportunity: dict, legs: list[dict], size: float) -> bool:
        """Log a dry-run execution."""
        total_cost_str = opportunity.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        roi_str = opportunity.get("net_roi", "0%")
        roi = float(roi_str.replace("%", "")) / 100 if isinstance(roi_str, str) else 0

        opp_id = self.db.log_opportunity(
            opp_type=opportunity.get("type", ""),
            market=opportunity.get("market", ""),
            prices=opportunity.get("prices", ""),
            total_cost=total_cost,
            net_profit=opportunity.get("net_profit", 0),
            net_roi=roi,
            depth=opportunity.get("_clob_depth", 0),
            action="dry_run",
        )

        for leg in legs:
            self.db.log_trade(
                opportunity_id=opp_id,
                platform=leg["platform"],
                side=leg.get("side", leg.get("token", "")),
                price=leg.get("price", 0),
                size=size,
                status="dry_run",
            )

        logger.info(f"[DRY RUN] Logged opportunity #{opp_id} with {len(legs)} legs.")
        self._write_decision(opportunity, "execute", "dry_run")
        return True

    def _execute_legs(self, opportunity: dict, legs: list[dict], size: float) -> bool:
        """Execute trade legs concurrently on both platforms."""
        total_cost_str = opportunity.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        roi_str = opportunity.get("net_roi", "0%")
        roi = float(roi_str.replace("%", "")) / 100 if isinstance(roi_str, str) else 0

        opp_id = self.db.log_opportunity(
            opp_type=opportunity.get("type", ""),
            market=opportunity.get("market", ""),
            prices=opportunity.get("prices", ""),
            total_cost=total_cost,
            net_profit=opportunity.get("net_profit", 0),
            net_roi=roi,
            depth=opportunity.get("_clob_depth", 0),
            action="traded",
        )

        # Determine if legs span multiple platforms (cross-platform arbs)
        platforms = set(leg["platform"] for leg in legs)
        cross_platform = len(platforms) > 1

        # Log all trades as pending
        for i, leg in enumerate(legs):
            trade_id = self.db.log_trade(
                opportunity_id=opp_id,
                platform=leg["platform"],
                side=leg.get("side", leg.get("token", "")),
                price=leg.get("price", 0),
                size=size,
                status="pending",
            )
            leg["_trade_id"] = trade_id

        # Pre-flight: verify cost per platform fits within cached balances.
        # Groups legs by platform and checks each platform's balance separately,
        # fixing the previous bug where only the first leg's platform was checked.
        opp_type = opportunity.get("type", "")
        cached_balances = self._get_cached_balances(opp_type) or {}
        cost_per_platform: dict[str, float] = {}
        for leg in legs:
            price = leg.get("price", 0)
            if price > 0:
                count = int(size / price)
                plat = leg["platform"]
                cost_per_platform[plat] = cost_per_platform.get(plat, 0) + count * price
        for plat, cost in cost_per_platform.items():
            bal = cached_balances.get(plat)
            if bal is not None and isinstance(bal, (int, float)) and cost > bal * 0.95:
                logger.warning(
                    "Pre-flight: %s cost $%.2f exceeds 95%% of balance $%.2f. Skipping.",
                    plat, cost, bal,
                )
                for leg in legs:
                    self.db.update_trade_status(leg["_trade_id"], "aborted")
                return False

        results = {}
        if cross_platform:
            # Cross-platform: execute legs concurrently (different exchanges)
            with ThreadPoolExecutor(max_workers=len(legs)) as pool:
                futures = {}
                for i, leg in enumerate(legs):
                    future = pool.submit(self._execute_single_leg, leg, size, opportunity)
                    futures[future] = (i, leg)
                for future in as_completed(futures):
                    idx, leg = futures[future]
                    try:
                        success, order_id, fill_price = future.result()
                        trade_id = leg["_trade_id"]
                        if success:
                            slippage = fill_price - leg.get("price", 0) if fill_price else 0
                            self.db.update_trade_status(trade_id, "filled", fill_price,
                                                        slippage=slippage)
                            results[idx] = True
                            logger.info(f"Leg {idx+1} FILLED: {leg['platform']} order={order_id}")
                        else:
                            self.db.update_trade_status(trade_id, "failed")
                            results[idx] = False
                            logger.error(f"Leg {idx+1} FAILED: {leg['platform']}")
                    except Exception as e:
                        trade_id = leg["_trade_id"]
                        self.db.update_trade_status(trade_id, "failed")
                        results[idx] = False
                        logger.error(f"Leg {idx+1} ERROR: {e}")
        else:
            # Same-platform: execute legs sequentially, abort on first failure
            for i, leg in enumerate(legs):
                try:
                    success, order_id, fill_price = self._execute_single_leg(leg, size, opportunity)
                    trade_id = leg["_trade_id"]
                    if success:
                        slippage = fill_price - leg.get("price", 0) if fill_price else 0
                        self.db.update_trade_status(trade_id, "filled", fill_price,
                                                    slippage=slippage)
                        results[i] = True
                        logger.info(f"Leg {i+1} FILLED: {leg['platform']} order={order_id}")
                    else:
                        self.db.update_trade_status(trade_id, "failed")
                        results[i] = False
                        logger.error(f"Leg {i+1} FAILED: {leg['platform']}")
                        # Abort remaining legs — no point continuing
                        for j in range(i + 1, len(legs)):
                            self.db.update_trade_status(legs[j]["_trade_id"], "aborted")
                        logger.warning("Aborting remaining legs after leg %d failure.", i + 1)
                        break
                except Exception as e:
                    trade_id = leg["_trade_id"]
                    self.db.update_trade_status(trade_id, "failed")
                    results[i] = False
                    logger.error(f"Leg {i+1} ERROR: {e}")
                    for j in range(i + 1, len(legs)):
                        self.db.update_trade_status(legs[j]["_trade_id"], "aborted")
                    break

        # Check if all legs succeeded
        all_filled = len(results) == len(legs) and all(results.values())
        if all_filled:
            # Create position in DB for lifecycle tracking
            market = opportunity.get("market", "Unknown")
            opp_type = opportunity.get("type", "")
            platform = "polymarket"
            if "Kalshi" in opp_type:
                platform = "kalshi"
            elif "Cross" in opp_type:
                platform = "cross"
            self.db.create_position(
                opportunity_id=opp_id,
                market_identifier=market,
                platform=platform,
                expected_pnl=opportunity.get("net_profit", 0),
            )
            # Invalidate balance cache after a successful trade
            self.invalidate_balance_cache()
            # Notify on successful trade
            self._notify_trade(opportunity, legs, size, success=True)
        else:
            # Partial fill detected — attempt hedging on filled legs
            logger.warning("Partial fill detected. Attempting hedge...")
            if HEDGE_ENABLED:
                from hedger import PartialFillHedger
                hedger = PartialFillHedger(
                    pm_trader=self.pm_trader,
                    kalshi_client=self.kalshi_client,
                    betfair_client=self.betfair_client,
                    smarkets_client=self.smarkets_client,
                    sxbet_client=self.sxbet_client,
                    matchbook_client=self.matchbook_client,
                    gemini_client=self.gemini_client,
                    db=self.db,
                )
                for i, leg in enumerate(legs):
                    if results.get(i):
                        fill_price = leg.get("price", 0)
                        hedger.queue_hedge(
                            trade_id=leg.get("_trade_id"),
                            platform=leg["platform"],
                            token_id=leg.get("_token_id", leg.get("_ticker", "")),
                            side=leg.get("side", ""),
                            fill_price=fill_price,
                            size=size,
                            opportunity_id=opp_id,
                        )
                        # Attempt immediate hedge
                        hedger.process_pending_hedges()
            else:
                    # Legacy fallback: cancel + orphan
                for i, leg in enumerate(legs):
                    if results.get(i) and leg.get("_order_id"):
                        cancel_ok = self._cancel_leg(leg)
                        if not cancel_ok:
                            trade_id = leg.get("_trade_id")
                            if trade_id:
                                self.db.update_trade_status(trade_id, "orphaned")
                            logger.warning(f"Leg {i+1} cancel failed -- marked as orphaned.")
            # Notify on failed trade
            self._notify_trade(opportunity, legs, size, success=False)

        return all_filled

    # Platforms that cannot sell/cancel — concurrent execution is not safe
    _NO_CANCEL_PLATFORMS = frozenset({"ibkr"})

    def _supports_concurrent(self, legs: list[dict]) -> bool:
        """Check whether all legs are on platforms that support cancellation.

        Concurrent execution requires the ability to hedge (sell) a filled
        leg if the other leg fails.  IBKR is BUY-only and cannot be hedged,
        so any opportunity involving IBKR must fall back to sequential mode.
        """
        for leg in legs:
            if leg.get("platform", "") in self._NO_CANCEL_PLATFORMS:
                return False
        return len(legs) >= 2

    def _execute_legs_concurrent(
        self, opportunity: dict, legs: list[dict], size: float,
    ) -> bool:
        """Submit all legs simultaneously and hedge on partial failure.

        All legs are submitted via ThreadPoolExecutor at once.  After all
        futures complete:
        - All succeed → create position (normal flow).
        - Some fail → hedge filled legs using PartialFillHedger.
        """
        total_cost_str = opportunity.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        roi_str = opportunity.get("net_roi", "0%")
        roi = float(roi_str.replace("%", "")) / 100 if isinstance(roi_str, str) else 0

        opp_id = self.db.log_opportunity(
            opp_type=opportunity.get("type", ""),
            market=opportunity.get("market", ""),
            prices=opportunity.get("prices", ""),
            total_cost=total_cost,
            net_profit=opportunity.get("net_profit", 0),
            net_roi=roi,
            depth=opportunity.get("_clob_depth", 0),
            action="traded_concurrent",
        )

        # Log all trades as pending
        for leg in legs:
            trade_id = self.db.log_trade(
                opportunity_id=opp_id,
                platform=leg["platform"],
                side=leg.get("side", leg.get("token", "")),
                price=leg.get("price", 0),
                size=size,
                status="pending",
            )
            leg["_trade_id"] = trade_id

        results: dict[int, bool] = {}

        # Submit all legs concurrently
        with ThreadPoolExecutor(max_workers=len(legs)) as pool:
            futures = {}
            for i, leg in enumerate(legs):
                future = pool.submit(self._execute_single_leg, leg, size, opportunity)
                futures[future] = (i, leg)
            for future in as_completed(futures):
                idx, leg = futures[future]
                try:
                    success, order_id, fill_price = future.result()
                    trade_id = leg["_trade_id"]
                    if success:
                        slippage = fill_price - leg.get("price", 0) if fill_price else 0
                        self.db.update_trade_status(
                            trade_id, "filled", fill_price, slippage=slippage)
                        results[idx] = True
                        logger.info(
                            "Concurrent leg %d FILLED: %s order=%s",
                            idx + 1, leg["platform"], order_id,
                        )
                    else:
                        self.db.update_trade_status(trade_id, "failed")
                        results[idx] = False
                        logger.error(
                            "Concurrent leg %d FAILED: %s", idx + 1, leg["platform"])
                except Exception as e:
                    trade_id = leg["_trade_id"]
                    self.db.update_trade_status(trade_id, "failed")
                    results[idx] = False
                    logger.error("Concurrent leg %d ERROR: %s", idx + 1, e)

        all_filled = len(results) == len(legs) and all(results.values())
        if all_filled:
            market = opportunity.get("market", "Unknown")
            opp_type = opportunity.get("type", "")
            platform = "polymarket"
            if "Kalshi" in opp_type:
                platform = "kalshi"
            elif "Cross" in opp_type:
                platform = "cross"
            self.db.create_position(
                opportunity_id=opp_id,
                market_identifier=market,
                platform=platform,
                expected_pnl=opportunity.get("net_profit", 0),
            )
            self._notify_trade(opportunity, legs, size, success=True)
        else:
            # Hedge any filled legs
            logger.warning("Concurrent execution: partial fill detected. Attempting hedge...")
            if HEDGE_ENABLED:
                from hedger import PartialFillHedger
                hedger = PartialFillHedger(
                    pm_trader=self.pm_trader,
                    kalshi_client=self.kalshi_client,
                    betfair_client=self.betfair_client,
                    smarkets_client=self.smarkets_client,
                    sxbet_client=self.sxbet_client,
                    matchbook_client=self.matchbook_client,
                    gemini_client=self.gemini_client,
                    db=self.db,
                )
                for i, leg in enumerate(legs):
                    if results.get(i):
                        fill_price = leg.get("price", 0)
                        hedger.queue_hedge(
                            trade_id=leg.get("_trade_id"),
                            platform=leg["platform"],
                            token_id=leg.get("_token_id", leg.get("_ticker", "")),
                            side=leg.get("side", ""),
                            fill_price=fill_price,
                            size=size,
                            opportunity_id=opp_id,
                        )
                hedger.process_pending_hedges()
            self._notify_trade(opportunity, legs, size, success=False)

        return all_filled

    def _execute_single_leg(
        self, leg: dict, size: float, opportunity: dict
    ) -> tuple[bool, str | None, float | None]:
        """Execute a single trade leg. Returns (success, order_id, fill_price)."""
        platform = leg["platform"]
        price = leg.get("price", 0)

        # --- Platform whitelist guard ---
        if platform not in ENABLED_EXECUTION_PLATFORMS:
            logger.warning(
                f"Platform '{platform}' not in ENABLED_EXECUTION_PLATFORMS "
                f"({', '.join(sorted(ENABLED_EXECUTION_PLATFORMS))}). "
                f"Skipping leg."
            )
            return False, None, None

        # --- Minimum order size guard ---
        min_size = PLATFORM_MIN_ORDER_SIZE.get(platform, 0)
        if size < min_size:
            logger.warning(
                f"Order size ${size:.2f} below {platform} minimum "
                f"${min_size:.2f}. Skipping leg."
            )
            return False, None, None

        if platform == "polymarket":
            if not self.pm_trader:
                return False, None, None
            token_id = leg.get("_token_id", "")
            if not token_id:
                logger.error("Missing token_id for Polymarket leg")
                return False, None, None
            side = leg.get("side", "BUY")
            neg_risk = "NegRisk" in opportunity.get("type", "")
            resp = self.pm_trader.place_order(
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                neg_risk=neg_risk,
            )
            if resp and resp.get("success"):
                order_id = resp.get("orderID", resp.get("order_id", ""))
                leg["_order_id"] = order_id
                # Poll for fill confirmation
                fill_price = self._confirm_fill_pm(order_id, price)
                return True, order_id, fill_price
            return False, None, None

        elif platform == "kalshi":
            if not self.kalshi_client:
                return False, None, None
            ticker = leg.get("_ticker", "")
            side = leg.get("side", "yes")
            action = leg.get("action", "buy")
            # Convert dollar size to contracts (1 contract = $1 payout)
            count = max(1, int(size / price)) if price > 0 else 1

            # Determine time-in-force based on config and leg position
            leg_index = leg.get("_leg_index", 0)
            if ORDER_TIME_IN_FORCE == "gtc":
                tif = "gtc"
            elif ORDER_TIME_IN_FORCE == "gtc_first_leg" and leg_index == 0:
                tif = "gtc"
            else:
                tif = "fill_or_kill"

            resp = self.kalshi_client.place_order(
                ticker=ticker,
                side=side,
                action=action,
                count=count,
                price_dollars=price,
                time_in_force=tif,
            )
            if resp:
                order = resp.get("order", resp)
                order_id = order.get("order_id", "")
                leg["_order_id"] = order_id
                status = order.get("status", "")
                if status == "executed":
                    # FOK filled instantly — extract avg_price directly
                    avg_price = order.get("avg_price")
                    if avg_price is not None:
                        fill_price = float(avg_price) / 100.0
                    else:
                        fill_price = price
                    return True, order_id, fill_price
                elif status == "resting":
                    # GTC order resting — wait up to GTC_ORDER_TIMEOUT then cancel
                    timeout = GTC_ORDER_TIMEOUT if tif == "gtc" else FILL_POLL_TIMEOUT
                    fill_price = self._confirm_fill_kalshi(order_id, price)
                    if fill_price is None and tif == "gtc":
                        # Cancel unfilled GTC order
                        logger.warning("Kalshi GTC order timed out (%.0fs), cancelling: %s",
                                       timeout, order_id)
                        try:
                            self.kalshi_client.cancel_order(order_id)
                        except Exception as e:
                            logger.warning("Failed to cancel Kalshi GTC order %s: %s",
                                           order_id, e)
                        return False, order_id, None
                    return True, order_id, fill_price
                logger.warning("Kalshi order not filled: status=%s ticker=%s resp=%s",
                               status, ticker, str(resp)[:300])
            else:
                logger.warning("Kalshi place_order returned None for %s %s @ $%.3f (count=%d)",
                               side, ticker, price, count)
            return False, None, None

        elif platform == "betfair":
            if not self.betfair_client or not self.betfair_client.authenticated:
                return False, None, None
            market_id = leg.get("_market_id", "")
            selection_id = leg.get("_selection_id")
            if not market_id or not selection_id:
                return False, None, None
            side = "BACK" if leg.get("side", "").lower() in ("yes", "back") else "LAY"
            # Convert probability price to decimal odds
            decimal_odds = round(1.0 / price, 2) if price > 0 else 2.0
            instructions = [{
                "selectionId": selection_id,
                "side": side,
                "orderType": "LIMIT",
                "limitOrder": {
                    "size": round(size, 2),
                    "price": decimal_odds,
                    "persistenceType": "LAPSE",
                },
            }]
            resp = self.betfair_client.place_orders(market_id, instructions)
            if resp and resp.get("status") == "SUCCESS":
                results = resp.get("instructionReports", [])
                if results:
                    bet_id = results[0].get("betId", "")
                    leg["_order_id"] = bet_id
                    fill_price = self._confirm_fill_betfair(bet_id, price)
                    return True, bet_id, fill_price
            return False, None, None

        elif platform == "smarkets":
            if not self.smarkets_client or not self.smarkets_client.authenticated:
                return False, None, None
            market_id = leg.get("_market_id", "")
            contract_id = leg.get("_contract_id", "")
            if not market_id:
                return False, None, None
            side = leg.get("side", "BACK")
            quantity = max(1, int(size / price)) if price > 0 else 1
            resp = self.smarkets_client.place_order(
                market_id=market_id, contract_id=contract_id,
                side=side, price=price, quantity=quantity,
            )
            if resp:
                order_id = str(resp.get("id", resp.get("order_id", "")))
                leg["_order_id"] = order_id
                fill_price = self._confirm_fill_smarkets(order_id, price)
                return True, order_id, fill_price
            return False, None, None

        elif platform == "sxbet":
            if not self.sxbet_client or not self.sxbet_client.authenticated:
                return False, None, None
            market_hash = leg.get("_market_hash", "")
            outcome_id = leg.get("_outcome_id", "")
            if not market_hash:
                return False, None, None
            side = leg.get("side", "BACK")
            quantity = max(1, int(size / price)) if price > 0 else 1
            resp = self.sxbet_client.place_order(
                market_hash=market_hash, outcome_id=outcome_id,
                side=side, price=price, quantity=quantity,
            )
            if resp:
                order_id = str(resp.get("orderHash", resp.get("id", "")))
                leg["_order_id"] = order_id
                fill_price = self._confirm_fill_sxbet(order_id, price)
                return True, order_id, fill_price
            return False, None, None

        elif platform == "matchbook":
            if not self.matchbook_client or not self.matchbook_client.authenticated:
                return False, None, None
            market_id = leg.get("_market_id", "")
            runner_id = leg.get("_runner_id", "")
            if not market_id or not runner_id:
                return False, None, None
            side = leg.get("side", "BACK").lower()
            # Convert probability price to decimal odds
            decimal_odds = round(1.0 / price, 2) if price > 0 else 2.0
            resp = self.matchbook_client.place_order(
                market_id=market_id, runner_id=runner_id,
                side=side, odds=decimal_odds, stake=round(size, 2),
            )
            if resp:
                order_id = str(resp.get("id", resp.get("offer-id", "")))
                leg["_order_id"] = order_id
                fill_price = self._confirm_fill_matchbook(order_id, price)
                return True, order_id, fill_price
            return False, None, None

        elif platform == "gemini":
            if not self.gemini_client or not self.gemini_client.authenticated:
                return False, None, None
            symbol = leg.get("symbol", "")
            if not symbol:
                return False, None, None
            outcome = leg.get("outcome", "yes")
            quantity = max(1, int(size / price)) if price > 0 else 1
            from config import GEMINI_ORDER_TYPE
            tif = "good-til-cancelled" if GEMINI_ORDER_TYPE == "gtc" else "immediate-or-cancel"
            resp = self.gemini_client.place_order(
                symbol=symbol, side="buy", outcome=outcome,
                quantity=quantity, price=price, time_in_force=tif,
            )
            if resp:
                order_id = str(resp.get("orderId", resp.get("order_id", "")))
                leg["_order_id"] = order_id
                fill_price = self._confirm_fill_gemini(order_id, price)
                return True, order_id, fill_price
            return False, None, None

        elif platform == "ibkr":
            if not self.ibkr_client or not self.ibkr_client.authenticated:
                return False, None, None
            conid = leg.get("conid", "")
            if not conid:
                return False, None, None
            quantity = max(1, int(size / price)) if price > 0 else 1
            resp = self.ibkr_client.place_order(
                conid=conid, quantity=quantity, price=price,
            )
            if resp:
                order_id = str(resp.get("orderId", resp.get("order_id", "")))
                leg["_order_id"] = order_id
                fill_price = self._confirm_fill_ibkr(order_id, price)
                return True, order_id, fill_price
            return False, None, None

        return False, None, None

    def _confirm_fill_gemini(self, order_id: str, expected_price: float) -> float | None:
        """Poll Gemini for fill confirmation. Returns actual fill price or None on timeout."""
        if not self.gemini_client or not order_id:
            return expected_price
        max_polls = int(FILL_POLL_TIMEOUT / FILL_POLL_INTERVAL)
        for _ in range(max_polls):
            status = self.gemini_client.get_order_status(order_id)
            if status:
                order_status = (status.get("status") or status.get("orderStatus") or "").lower()
                if order_status in ("filled", "closed"):
                    avg_price = status.get("avgExecutionPrice") or status.get("price")
                    if avg_price is not None:
                        return float(avg_price)
                    return expected_price
                elif order_status in ("cancelled", "canceled", "expired"):
                    return expected_price
            time.sleep(FILL_POLL_INTERVAL)
        logger.warning("Fill poll timeout for Gemini order %s after %.1fs — status uncertain",
                        order_id, FILL_POLL_TIMEOUT)
        return None

    def _confirm_fill_ibkr(self, order_id: str, expected_price: float) -> float | None:
        """Poll IBKR for fill confirmation. Returns actual fill price or None on timeout."""
        if not self.ibkr_client or not order_id:
            return expected_price
        max_polls = int(FILL_POLL_TIMEOUT / FILL_POLL_INTERVAL)
        for _ in range(max_polls):
            status = self.ibkr_client.get_order_status(order_id)
            if status:
                order_status = (status.get("status") or "").lower()
                if order_status == "filled":
                    avg_price = status.get("avgFillPrice")
                    if avg_price is not None and float(avg_price) > 0:
                        return float(avg_price)
                    return expected_price
                elif order_status in ("cancelled", "expired", "inactive"):
                    return expected_price
            time.sleep(FILL_POLL_INTERVAL)
        logger.warning("Fill poll timeout for IBKR order %s after %.1fs — status uncertain",
                        order_id, FILL_POLL_TIMEOUT)
        return None

    def _confirm_fill_pm(self, order_id: str, expected_price: float) -> float | None:
        """Poll Polymarket for fill confirmation. Returns actual fill price or None on timeout."""
        if not self.pm_trader or not order_id:
            return expected_price
        max_polls = int(FILL_POLL_TIMEOUT / FILL_POLL_INTERVAL)
        for _ in range(max_polls):
            status = self.pm_trader.get_order_status(order_id)
            if status:
                order_status = status.get("status", "")
                if order_status == "matched":
                    return float(status.get("price", expected_price))
                elif order_status in ("canceled", "expired"):
                    return expected_price
            time.sleep(FILL_POLL_INTERVAL)
        logger.warning("Fill poll timeout for Polymarket order %s after %.1fs — status uncertain",
                        order_id, FILL_POLL_TIMEOUT)
        return None

    def _confirm_fill_kalshi(self, order_id: str, expected_price: float) -> float | None:
        """Poll Kalshi for fill confirmation. Returns actual fill price or None on timeout."""
        if not self.kalshi_client or not order_id:
            return expected_price
        max_polls = int(FILL_POLL_TIMEOUT / FILL_POLL_INTERVAL)
        for _ in range(max_polls):
            status = self.kalshi_client.get_order_status(order_id)
            if status:
                order_status = status.get("status", "")
                if order_status == "executed":
                    avg_price = status.get("avg_price")
                    if avg_price is not None:
                        return float(avg_price) / 100.0
                    return expected_price
                elif order_status in ("canceled", "expired"):
                    return expected_price
            time.sleep(FILL_POLL_INTERVAL)
        logger.warning("Fill poll timeout for Kalshi order %s after %.1fs — status uncertain",
                        order_id, FILL_POLL_TIMEOUT)
        return None

    def _confirm_fill_betfair(self, bet_id: str, expected_price: float) -> float | None:
        """Poll Betfair for fill confirmation. Returns actual fill price or None on timeout."""
        if not self.betfair_client or not bet_id:
            return expected_price
        max_polls = int(FILL_POLL_TIMEOUT / FILL_POLL_INTERVAL)
        for _ in range(max_polls):
            status = self.betfair_client.get_order_status(bet_id)
            if status:
                order_status = status.get("status", "")
                if order_status == "EXECUTION_COMPLETE":
                    avg_price = status.get("averagePriceMatched") or status.get("priceMatched")
                    if avg_price is not None and float(avg_price) > 1.0:
                        return 1.0 / float(avg_price)  # decimal odds -> probability
                    return expected_price
                elif order_status in ("CANCELLED", "EXPIRED", "LAPSED"):
                    return expected_price
            time.sleep(FILL_POLL_INTERVAL)
        logger.warning("Fill poll timeout for Betfair bet %s after %.1fs — status uncertain",
                        bet_id, FILL_POLL_TIMEOUT)
        return None

    def _confirm_fill_smarkets(self, order_id: str, expected_price: float) -> float | None:
        """Poll Smarkets for fill confirmation. Returns actual fill price or None on timeout."""
        if not self.smarkets_client or not order_id:
            return expected_price
        max_polls = int(FILL_POLL_TIMEOUT / FILL_POLL_INTERVAL)
        for _ in range(max_polls):
            status = self.smarkets_client.get_order_status(order_id)
            if status:
                order_status = status.get("state", status.get("status", ""))
                if order_status in ("matched", "filled", "executed"):
                    avg_price = status.get("avg_price") or status.get("price")
                    if avg_price is not None:
                        return float(avg_price) / 10000.0  # basis points -> probability
                    return expected_price
                elif order_status in ("cancelled", "expired"):
                    return expected_price
            time.sleep(FILL_POLL_INTERVAL)
        logger.warning("Fill poll timeout for Smarkets order %s after %.1fs — status uncertain",
                        order_id, FILL_POLL_TIMEOUT)
        return None

    def _confirm_fill_sxbet(self, order_id: str, expected_price: float) -> float | None:
        """Poll SX Bet for fill confirmation. Returns actual fill price or None on timeout."""
        if not self.sxbet_client or not order_id:
            return expected_price
        max_polls = int(FILL_POLL_TIMEOUT / FILL_POLL_INTERVAL)
        for _ in range(max_polls):
            status = self.sxbet_client.get_order_status(order_id)
            if status:
                order_status = status.get("status", "")
                if order_status in ("FILLED", "MATCHED"):
                    avg_price = status.get("avgPrice") or status.get("price")
                    if avg_price is not None:
                        return float(avg_price)
                    return expected_price
                elif order_status in ("CANCELLED", "EXPIRED"):
                    return expected_price
            time.sleep(FILL_POLL_INTERVAL)
        logger.warning("Fill poll timeout for SX Bet order %s after %.1fs — status uncertain",
                        order_id, FILL_POLL_TIMEOUT)
        return None

    def _confirm_fill_matchbook(self, order_id: str, expected_price: float) -> float | None:
        """Poll Matchbook for fill confirmation. Returns actual fill price or None on timeout."""
        if not self.matchbook_client or not order_id:
            return expected_price
        max_polls = int(FILL_POLL_TIMEOUT / FILL_POLL_INTERVAL)
        for _ in range(max_polls):
            status = self.matchbook_client.get_order_status(order_id)
            if status:
                order_status = status.get("status", "")
                if order_status in ("matched", "filled"):
                    avg_odds = status.get("matched-odds") or status.get("odds")
                    if avg_odds is not None and float(avg_odds) > 1.0:
                        return 1.0 / float(avg_odds)  # decimal odds -> probability
                    return expected_price
                elif order_status in ("cancelled", "expired"):
                    return expected_price
            time.sleep(FILL_POLL_INTERVAL)
        logger.warning("Fill poll timeout for Matchbook order %s after %.1fs — status uncertain",
                        order_id, FILL_POLL_TIMEOUT)
        return None

    def _cancel_leg(self, leg: dict) -> bool:
        """Attempt to cancel a filled/resting order for cleanup. Returns True if successful."""
        platform = leg["platform"]
        order_id = leg.get("_order_id", "")
        if not order_id:
            return False
        if platform == "polymarket" and self.pm_trader:
            return self.pm_trader.cancel_order(order_id)
        elif platform == "kalshi" and self.kalshi_client:
            return self.kalshi_client.cancel_order(order_id)
        elif platform == "betfair" and self.betfair_client:
            return self.betfair_client.cancel_orders(leg.get("_market_id", ""), [order_id])
        elif platform == "smarkets" and self.smarkets_client:
            return self.smarkets_client.cancel_order(order_id)
        elif platform == "sxbet" and self.sxbet_client:
            return self.sxbet_client.cancel_order(order_id)
        elif platform == "matchbook" and self.matchbook_client:
            return self.matchbook_client.cancel_order(order_id)
        elif platform == "gemini" and self.gemini_client:
            return self.gemini_client.cancel_order(order_id)
        elif platform == "ibkr" and self.ibkr_client:
            return self.ibkr_client.cancel_order(order_id)
        return False

    def _notify_trade(self, opportunity: dict, legs: list[dict], size: float,
                      success: bool):
        """Send a webhook notification about a trade execution result.

        Args:
            opportunity: The opportunity dict that was executed.
            legs: List of execution leg dicts.
            size: Trade size in dollars.
            success: True if all legs filled, False if partial/failed.
        """
        if not self.notifier or not hasattr(self.notifier, "url") or not self.notifier.url:
            return

        import threading as _threading

        market = opportunity.get("market", "Unknown")
        opp_type = opportunity.get("type", "")
        profit = opportunity.get("net_profit", 0)
        platforms = ", ".join(set(leg["platform"] for leg in legs))

        if success:
            status = "FILLED"
            msg = (f"TRADE FILLED: {opp_type} | {market[:60]} | "
                   f"${size:.2f} on {platforms} | expected profit ${profit:.4f}")
        else:
            status = "FAILED"
            filled = sum(1 for leg in legs if leg.get("_trade_id"))
            msg = (f"TRADE FAILED: {opp_type} | {market[:60]} | "
                   f"${size:.2f} on {platforms} | {filled}/{len(legs)} legs filled")

        url = self.notifier.url
        if getattr(self.notifier, "_is_telegram", False):
            emoji = "\u2705" if success else "\u274c"
            payload = {"text": f"{emoji} {msg}"}
        elif getattr(self.notifier, "_is_callmebot", False):
            payload = {"text": msg}
        elif "hooks.slack.com" in url:
            emoji = ":white_check_mark:" if success else ":x:"
            payload = {"text": f"{emoji} {msg}"}
        elif "discord.com/api/webhooks" in url:
            emoji = "+" if success else "x"
            payload = {"content": msg}
        else:
            payload = {
                "event": "trade_execution",
                "status": status,
                "type": opp_type,
                "market": market,
                "size": size,
                "profit": profit,
                "platforms": platforms,
                "legs": len(legs),
            }

        thread = _threading.Thread(
            target=self.notifier._send_raw, args=(payload,), daemon=True)
        thread.start()

    def _log_skipped(self, opportunity: dict, reason: str):
        """Log a skipped opportunity."""
        total_cost_str = opportunity.get("total_cost", "$0")
        total_cost = float(total_cost_str.replace("$", "")) if isinstance(total_cost_str, str) else float(total_cost_str)
        roi_str = opportunity.get("net_roi", "0%")
        roi = float(roi_str.replace("%", "")) / 100 if isinstance(roi_str, str) else 0

        self.db.log_opportunity(
            opp_type=opportunity.get("type", ""),
            market=opportunity.get("market", ""),
            prices=opportunity.get("prices", ""),
            total_cost=total_cost,
            net_profit=opportunity.get("net_profit", 0),
            net_roi=roi,
            depth=opportunity.get("_clob_depth", 0),
            action=f"skipped:{reason}",
        )
        self._write_decision(opportunity, "skip", reason)

    def _write_decision(self, opp: dict, decision: str, reason: str, risk_check: str | None = None):
        """Append one JSON line to decisions.jsonl (HARDEN-03).

        Args:
            opp: Opportunity dict from scanner.
            decision: One of "skip", "execute", "reject".
            reason: Human-readable reason string (e.g. "dry_run", "stale_prices").
            risk_check: Optional risk check result description.
        """
        entry = {
            "ts": time.time(),
            "strategy": opp.get("type", ""),
            "market": opp.get("market", ""),
            "decision": decision,
            "reason": reason,
            "prices": opp.get("prices", ""),
            "expected_profit": opp.get("net_profit", 0),
            "expected_roi": opp.get("net_roi", ""),
            "risk_check": risk_check,
        }
        line = json.dumps(entry) + "\n"
        with self._decision_log_lock:
            self._decision_fh.write(line)

    def close(self):
        """Release the JSONL decision log file handle."""
        if hasattr(self, "_decision_fh") and self._decision_fh and not self._decision_fh.closed:
            self._decision_fh.close()
