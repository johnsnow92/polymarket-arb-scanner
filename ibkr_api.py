"""IBKR ForecastEx API client via ib_insync (TWS API).

Connects to IB Gateway or TWS via socket. Uses ib_insync for async-friendly
contract discovery, market data, and order management.

ForecastEx contract model:
  - secType="OPT", exchange="FORECASTX"
  - right="C" (Call) = YES contract, right="P" (Put) = NO contract
  - Limit orders only (LMT), time-in-force: DAY/GTC/IOC
  - BUY-only: no SELL orders. Close = buy opposing contract (auto-nets).
  - Prices in dollars (0.01-0.99), NOT cents.
  - $0.00 commission.
"""

import logging
import os
import threading
import time

from config import IBKR_ORDER_RATE_LIMIT

logger = logging.getLogger(__name__)

# Rate limiting for orders (thread-safe)
_last_order_time = 0
_order_lock = threading.Lock()


def _order_rate_limit():
    """Enforce stricter rate limit for order placement (5s interval)."""
    global _last_order_time
    with _order_lock:
        now = time.time()
        elapsed = now - _last_order_time
        if elapsed < IBKR_ORDER_RATE_LIMIT:
            time.sleep(IBKR_ORDER_RATE_LIMIT - elapsed)
        _last_order_time = time.time()


class IBKRClient:
    """IBKR ForecastEx client via ib_insync (TWS API).

    BUY-only: IBKR ForecastEx only supports BUY orders. Closing a position
    is done by buying the opposing contract (IBKR auto-nets same-event
    opposing contracts).

    Requires IB Gateway or TWS running locally (default 127.0.0.1:4001).
    """

    def __init__(self):
        self.ib = None
        self.authenticated = False
        self._contracts_cache = {}  # conid -> Contract

    def login(self, host: str = None, port: int = None, client_id: int = None) -> bool:
        """Connect to IB Gateway or TWS.

        Args:
            host: Gateway host (falls back to IBKR_HOST env var, default 127.0.0.1).
            port: Gateway port (falls back to IBKR_PORT env var, default 4001).
            client_id: TWS API client ID (falls back to IBKR_CLIENT_ID env var, default 1).

        Returns:
            True if connection succeeded.
        """
        host = host or os.getenv("IBKR_HOST", "127.0.0.1")
        port = port or int(os.getenv("IBKR_PORT", "4001"))
        client_id = client_id or int(os.getenv("IBKR_CLIENT_ID", "1"))

        try:
            from ib_insync import IB
            self.ib = IB()
            self.ib.connect(host, port, clientId=client_id, readonly=False)
            self.authenticated = self.ib.isConnected()
            if self.authenticated:
                logger.info("IBKR connected to %s:%d (clientId=%d)", host, port, client_id)
            else:
                logger.error("IBKR connection failed — not connected")
            return self.authenticated
        except ImportError:
            logger.error("ib_insync not installed — run: pip install ib_insync")
            return False
        except Exception as e:
            logger.error("IBKR connection failed: %s", e)
            return False

    def disconnect(self):
        """Disconnect from IB Gateway."""
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
            self.authenticated = False
            logger.info("IBKR disconnected.")

    def fetch_all_markets(self) -> list[dict]:
        """Fetch all ForecastEx contracts via contract search.

        Returns:
            Normalized list of event dicts with YES/NO contract pairs.
        """
        if not self.authenticated or not self.ib:
            return []

        try:
            from ib_insync import Contract
            # ForecastEx models event contracts as OPT on the FORECASTX exchange
            fx_contract = Contract(exchange="FORECASTX", secType="OPT")
            details_list = self.ib.reqContractDetails(fx_contract)

            if not details_list:
                logger.warning("No ForecastEx contracts found.")
                return []

            # Group contracts by underlying event
            events_map = {}
            for detail in details_list:
                contract = detail.contract
                conid = str(contract.conId)
                # Cache contract objects for order placement
                self._contracts_cache[conid] = contract

                # Group by underlying conid (event)
                event_id = str(contract.underConId or contract.conId)
                if event_id not in events_map:
                    events_map[event_id] = {
                        "id": event_id,
                        "title": detail.longName or contract.localSymbol or "",
                        "contracts": [],
                        "status": "active",
                    }

                # Determine side from contract.right: C (Call) = YES, P (Put) = NO
                symbol = contract.localSymbol or contract.symbol or ""
                right = getattr(contract, "right", "")
                if right == "C":
                    side = "YES"
                elif right == "P":
                    side = "NO"
                else:
                    side = "YES" if "YES" in symbol.upper() else "NO" if "NO" in symbol.upper() else ""

                # Get last price via market data snapshot
                price = None
                try:
                    self.ib.reqMktData(contract, "", True, False)
                    self.ib.sleep(0.5)
                    ticker = self.ib.ticker(contract)
                    if ticker and ticker.last and ticker.last > 0:
                        price = ticker.last
                    elif ticker and ticker.close and ticker.close > 0:
                        price = ticker.close
                except Exception:
                    pass

                events_map[event_id]["contracts"].append({
                    "conid": conid,
                    "label": symbol,
                    "side": side,
                    "price": price,
                })

            events = list(events_map.values())
            logger.info("Fetched %d IBKR ForecastEx events.", len(events))
            return events

        except Exception as e:
            logger.error("IBKR fetch_all_markets failed: %s", e)
            return []

    def get_market_price(self, market: dict) -> tuple[float | None, float | None]:
        """Get YES/NO prices for a ForecastEx event.

        Prices are already in 0-1 dollar range (IBKR ForecastEx uses 0.01-0.99).

        Args:
            market: Event dict from ``fetch_all_markets()``.

        Returns:
            (yes_price, no_price) in 0-1 range, or (None, None).
        """
        contracts = market.get("contracts", [])
        if len(contracts) < 2:
            return None, None

        yes_price = None
        no_price = None

        for c in contracts:
            side = (c.get("side") or c.get("label") or "").upper()
            price = c.get("price")
            if price is not None:
                price = float(price)
                if "YES" in side:
                    yes_price = price
                elif "NO" in side:
                    no_price = price

        if yes_price is not None and no_price is not None:
            return yes_price, no_price

        # Try fetching live prices for contracts without cached data
        if not self.authenticated or not self.ib:
            return yes_price, no_price

        for c in contracts:
            conid = c.get("conid", "")
            side = (c.get("side") or c.get("label") or "").upper()
            contract_obj = self._contracts_cache.get(conid)
            if not contract_obj:
                continue

            try:
                self.ib.reqMktData(contract_obj, "", True, False)
                self.ib.sleep(0.5)
                ticker = self.ib.ticker(contract_obj)
                if ticker:
                    ask = ticker.ask if ticker.ask and ticker.ask > 0 else None
                    last = ticker.last if ticker.last and ticker.last > 0 else None
                    price = ask or last
                    if price:
                        if "YES" in side:
                            yes_price = price
                        elif "NO" in side:
                            no_price = price
            except Exception:
                pass

        return yes_price, no_price

    def place_order(self, conid: str, quantity: int, price: float) -> dict | None:
        """Place a BUY limit order on ForecastEx.

        IBKR ForecastEx only supports BUY orders.
        Price is in dollars (0.01-0.99) — passed directly, no conversion needed.

        Args:
            conid: Contract ID.
            quantity: Number of contracts.
            price: Limit price in dollars (0.01-0.99).

        Returns:
            Dict with orderId and status, or None on failure.
        """
        if not self.authenticated or not self.ib:
            logger.error("IBKR: must connect before placing orders")
            return None

        _order_rate_limit()

        contract = self._contracts_cache.get(conid)
        if not contract:
            logger.error("IBKR: unknown conid %s — not in cache", conid)
            return None

        try:
            from ib_insync import LimitOrder
            order = LimitOrder("BUY", quantity, price)
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(0.1)  # Allow time for order acknowledgment

            return {
                "orderId": str(trade.order.orderId),
                "status": trade.orderStatus.status,
                "filled": trade.orderStatus.filled,
                "remaining": trade.orderStatus.remaining,
            }
        except Exception as e:
            logger.error("IBKR place_order failed for conid %s: %s", conid, e)
            return None

    def get_order_status(self, order_id: str) -> dict | None:
        """Get order status.

        Args:
            order_id: IBKR order ID.

        Returns:
            Dict with status info, or None.
        """
        if not self.authenticated or not self.ib:
            return None

        try:
            for trade in self.ib.trades():
                if str(trade.order.orderId) == str(order_id):
                    return {
                        "orderId": str(trade.order.orderId),
                        "status": trade.orderStatus.status,
                        "filled": trade.orderStatus.filled,
                        "remaining": trade.orderStatus.remaining,
                        "avgFillPrice": trade.orderStatus.avgFillPrice,
                    }
            return None
        except Exception as e:
            logger.error("IBKR get_order_status failed: %s", e)
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order.

        Args:
            order_id: IBKR order ID.

        Returns:
            True if cancellation was submitted.
        """
        if not self.authenticated or not self.ib:
            return False

        try:
            for trade in self.ib.openTrades():
                if str(trade.order.orderId) == str(order_id):
                    self.ib.cancelOrder(trade.order)
                    self.ib.sleep(0.1)
                    return True
            return False
        except Exception as e:
            logger.error("IBKR cancel_order failed: %s", e)
            return False

    def get_balance(self) -> float | None:
        """Get available account funds.

        Returns:
            Available funds as float, or None on failure.
        """
        if not self.authenticated or not self.ib:
            return None

        try:
            accounts = self.ib.managedAccounts()
            if not accounts:
                return None

            account_values = self.ib.accountValues(accounts[0])
            for av in account_values:
                if av.tag == "AvailableFunds" and av.currency == "USD":
                    return float(av.value)

            # Fallback: try BuyingPower or CashBalance
            for av in account_values:
                if av.tag in ("BuyingPower", "CashBalance") and av.currency == "USD":
                    return float(av.value)

            return None
        except Exception as e:
            logger.error("IBKR get_balance failed: %s", e)
            return None

    def get_market_status(self, contract_id: str) -> dict | None:
        """Get contract status for settlement detection.

        Args:
            contract_id: ForecastEx contract/event ID.

        Returns:
            Dict with status info, or None.
        """
        contract = self._contracts_cache.get(contract_id)
        if not contract or not self.ib:
            return None

        try:
            details = self.ib.reqContractDetails(contract)
            if details:
                d = details[0]
                return {
                    "conid": contract_id,
                    "status": d.contractMonth or "active",
                    "lastTradeDate": d.contract.lastTradeDateOrContractMonth,
                }
            return None
        except Exception as e:
            logger.error("IBKR get_market_status failed: %s", e)
            return None
