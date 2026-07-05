"""Kalshi reward-MM pilot safety layer (plan 10 — docs/plans/10-mm-pilot-prep.md).

Everything needed to *quote* already existed (QuoteEngine, trackers, hedger);
this module is everything needed to *survive being filled*:

- ``FillPoller``       — REST polling of ``KalshiClient.get_fills`` (section 3)
- ``HedgeController``  — auto-hedge on real fills, fail closed (section 4)
- ``authorize_order``  — the single choke point in front of ``place_order``
                         enforcing hard inventory caps (section 5)
- gate chain G1-G12    — deterministic pre-quote gates in the hot path (section 6)
- ``ControlsPoller``   — Supabase ``bot_controls.mm_pilot_enabled`` kill switch,
                         fail closed on control-plane loss (section 7)
- canary state machine — tiny-size start, auto-halt on any deviation (section 8)

Deterministic code only — no LLM anywhere, hot path or otherwise. Venue is
Kalshi ONLY; the platform literal is hard-checked at the choke point on top of
the ``ENABLED_EXECUTION_PLATFORMS`` allowlist. Every gate FAILS CLOSED: missing
or stale data, unreachable control plane, unknown orders, or hedge failure stop
quoting and pull resting orders — never proceed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PLATFORM = "kalshi"  # hardcoded venue — the pilot never routes anywhere else
KALSHI_TICK = 0.01
DECISIONS_LOG_PATH = "decisions.jsonl"


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FillEvent:
    """One accepted pilot fill (spec section 3)."""

    fill_id: str
    order_id: str
    ticker: str
    side: str            # "yes" | "no"
    action: str          # "buy" | "sell"
    count: int           # contracts
    price: float         # yes-price in dollars 0.01-0.99
    is_taker: bool       # True should never happen for resting quotes
    created_ts: float
    mid_at_detect: float  # book mid when we detected the fill (for toxicity)


@dataclass(frozen=True)
class GateResult:
    """Verdict from ``authorize_order`` / a pre-quote gate."""

    allowed: bool
    reason: str


def _parse_created_ts(fill: dict, fallback: float) -> float:
    """Parse a Kalshi fill's created_time into Unix seconds (fail-soft)."""
    raw = fill.get("created_time")
    if raw is None:
        return fallback
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return fallback


# ---------------------------------------------------------------------------
# PilotInventory — signed YES-equivalent exposure with avg-cost accounting
# ---------------------------------------------------------------------------

class PilotInventory:
    """Per-market net inventory in YES-equivalent terms. Thread-safe.

    Long YES exposure (buy yes / sell no) is positive; long NO exposure is
    negative. Dollars-at-cost uses the direction's contract price (yes price
    when long YES, ``1 - yes_price`` when long NO). Realized P&L accrues when
    a fill reduces the position.
    """

    def __init__(self):
        self._net: dict[str, int] = {}       # ticker -> signed contracts
        self._avg: dict[str, float] = {}     # ticker -> avg cost/contract (direction terms)
        self._realized: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def signed_contracts(side: str, action: str, count: int) -> int:
        """YES-equivalent signed contract delta for a fill."""
        positive = (side == "yes") == (action == "buy")
        return count if positive else -count

    def apply_fill(self, ticker: str, side: str, action: str, count: int,
                   yes_price: float) -> float:
        """Apply a fill; returns realized P&L delta (0.0 when accumulating)."""
        delta = self.signed_contracts(side, action, count)
        with self._lock:
            net = self._net.get(ticker, 0)
            avg = self._avg.get(ticker, 0.0)
            realized = 0.0
            remaining = delta
            if net != 0 and (net > 0) != (remaining > 0):
                # Reducing (possibly through zero)
                reduce_ct = min(abs(remaining), abs(net))
                exit_price = yes_price if net > 0 else (1.0 - yes_price)
                realized = (exit_price - avg) * reduce_ct
                net += reduce_ct if remaining > 0 else -reduce_ct
                remaining += reduce_ct if remaining < 0 else -reduce_ct
                if net == 0:
                    avg = 0.0
            if remaining != 0:
                # Accumulating in the direction of `remaining`
                dir_price = yes_price if remaining > 0 else (1.0 - yes_price)
                total_ct = abs(net) + abs(remaining)
                avg = ((abs(net) * avg + abs(remaining) * dir_price) / total_ct
                       if total_ct else 0.0)
                net += remaining
            self._net[ticker] = net
            self._avg[ticker] = avg
            self._realized[ticker] = self._realized.get(ticker, 0.0) + realized
            return realized

    def net_contracts(self, ticker: str) -> int:
        with self._lock:
            return self._net.get(ticker, 0)

    def net_usd(self, ticker: str) -> float:
        """Signed net inventory in dollars at cost."""
        with self._lock:
            return self._net.get(ticker, 0) * self._avg.get(ticker, 0.0)

    def avg_cost(self, ticker: str) -> float:
        with self._lock:
            return self._avg.get(ticker, 0.0)

    def total_net_usd(self) -> float:
        """Sum of absolute net inventory (dollars at cost) across markets."""
        with self._lock:
            return sum(abs(n) * self._avg.get(t, 0.0)
                       for t, n in self._net.items())

    def realized_pnl_total(self) -> float:
        with self._lock:
            return sum(self._realized.values())

    def tickers_with_inventory(self) -> list[str]:
        with self._lock:
            return [t for t, n in self._net.items() if n != 0]


# ---------------------------------------------------------------------------
# ControlsPoller — Supabase bot_controls kill switch (spec section 7)
# ---------------------------------------------------------------------------

class ControlsPoller:
    """Timestamped local cache of ``bot_controls.mm_pilot_enabled``.

    Fail closed: no cache, a stale cache (control plane unreachable), or an
    explicit false all read as OFF. Unknown operator intent = off.
    """

    CONTROL_KEY = "mm_pilot_enabled"

    def __init__(self, supabase_client=None, time_fn=time.time):
        self._client = supabase_client
        self._time_fn = time_fn
        self._value: bool | None = None
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()

    def poll(self) -> None:
        """Refresh the cached value. Errors leave the cache stale (fail closed)."""
        if self._client is None:
            return
        try:
            resp = (self._client.table("bot_controls")
                    .select("value").eq("key", self.CONTROL_KEY)
                    .limit(1).execute())
            rows = getattr(resp, "data", None) or []
            if not rows:
                logger.warning("bot_controls has no %s row — treating as OFF",
                               self.CONTROL_KEY)
                with self._lock:
                    self._value = False
                    self._fetched_at = self._time_fn()
                return
            with self._lock:
                self._value = bool(rows[0].get("value"))
                self._fetched_at = self._time_fn()
        except Exception as exc:
            logger.warning("ControlsPoller poll failed (%s) — cache goes stale, "
                           "pilot fails closed after %.0fs", exc,
                           self._max_stale())

    @staticmethod
    def _max_stale() -> float:
        from config import MM_CONTROLS_MAX_STALE_SECONDS
        return MM_CONTROLS_MAX_STALE_SECONDS

    def set_cached(self, value: bool, fetched_at: float | None = None) -> None:
        """Directly seed the cache (tests / local override)."""
        with self._lock:
            self._value = value
            self._fetched_at = (self._time_fn() if fetched_at is None
                                else fetched_at)

    def is_enabled(self) -> bool:
        """True only if the cached value is fresh AND true."""
        with self._lock:
            value, fetched_at = self._value, self._fetched_at
        if value is not True:
            return False
        return (self._time_fn() - fetched_at) <= self._max_stale()


# ---------------------------------------------------------------------------
# Kalshi client proxy — routes hedge placements through the choke point
# ---------------------------------------------------------------------------

class _PilotKalshiProxy:
    """Client facade handed to the pilot-owned ``PartialFillHedger``.

    ``PartialFillHedger._hedge_kalshi`` stays the hedge placement primitive
    (touch-price + max-loss logic unmodified), but its ``place_order`` call is
    routed back through the pilot's ``authorize_order`` choke point so there
    is provably no second code path to ``kalshi_client.place_order`` and the
    hedge order id lands in the pilot's own registry.
    """

    def __init__(self, pilot: "KalshiMMPilot"):
        self._pilot = pilot

    def fetch_order_book(self, ticker: str) -> dict | None:
        return self._pilot.get_raw_book(ticker)

    def place_order(self, ticker: str, side: str, action: str, count: int,
                    price_dollars: float, time_in_force: str = "ioc") -> dict | None:
        order_id = self._pilot.place_pilot_order(
            ticker=ticker, side=side, action=action, count=count,
            price=price_dollars, purpose="hedge", reducing=True,
        )
        if order_id is None:
            return None
        return {"order": {"order_id": order_id}}


# ---------------------------------------------------------------------------
# KalshiMMPilot — orchestrator
# ---------------------------------------------------------------------------

class KalshiMMPilot:
    """Orchestrates the Kalshi reward-MM pilot inside continuous mode.

    All external effects (orders, cancels, alerts, audit rows) flow through
    injectable collaborators so every safety property is unit-testable with a
    scripted fake client. ``DRY_RUN`` short-circuits before any client call:
    dry-run sessions place zero real orders and synthesize fills from book
    crosses (rollout phase D0).
    """

    def __init__(
        self,
        kalshi_client=None,
        db=None,
        alert_manager=None,
        controls: ControlsPoller | None = None,
        quote_engine=None,
        toxic_detector=None,
        volatility_tracker=None,
        hedger_factory=None,
        decision_writer=None,
        dry_run: bool | None = None,
        time_fn=time.time,
    ):
        import config
        from market_maker import (QuoteEngine, get_toxic_flow_detector,
                                  get_volatility_tracker)

        self._client = kalshi_client
        self._db = db
        self._alerts = alert_manager
        self._controls = controls or ControlsPoller(time_fn=time_fn)
        self._quote_engine = quote_engine or QuoteEngine(
            min_spread=config.MM_MIN_SPREAD)
        self._toxic = toxic_detector or get_toxic_flow_detector()
        self._vol = volatility_tracker or get_volatility_tracker()
        self._time_fn = time_fn
        self.dry_run = config.DRY_RUN if dry_run is None else dry_run

        # Pilot-owned hedger over the choke-point proxy (spec section 4).
        from hedger import PartialFillHedger
        factory = hedger_factory or (
            lambda proxy: PartialFillHedger(kalshi_client=proxy, db=db))
        self._hedger = factory(_PilotKalshiProxy(self))

        self.inventory = PilotInventory()
        self._lock = threading.RLock()

        # Resting-order registry: order_id -> order info. The ONLY source of
        # truth for what the pilot has on the book.
        self._orders: dict[str, dict] = {}
        self._order_seq = 0
        self.place_order_calls = 0  # test/runtime assertion counter

        # Book cache per ticker: parsed levels + raw + freshness.
        self._books: dict[str, dict] = {}

        # Selection snapshot (PR #43's select_lip_markets output). None until
        # the first snapshot arrives — G4 fails closed without one.
        self._selected: set[str] | None = None

        # Halt state
        self.halted = False           # whole-pilot halt (manual restart)
        self.halt_reason = ""
        self._market_halted: dict[str, str] = {}     # ticker -> reason
        self._market_halt_times: dict[str, list[float]] = {}
        self._hedge_failures: dict[str, int] = {}    # consecutive, per ticker

        # Fill dedupe (bounded to last 1,000 fill ids)
        self._seen_fill_ids: dict[str, None] = {}
        self._last_fill_ts: float = self._time_fn()

        # Canary state (spec section 8)
        self.canary_graduated = False
        self.canary_clean_fills = 0
        self._live_started_ts = self._time_fn()

        self._decision_writer = decision_writer
        self._decision_fh = None
        self._decision_lock = threading.Lock()
        self._loop_error_streak = 0

    # -- audit -------------------------------------------------------------

    def _write_decision(self, gate: str, ticker: str, allowed: bool,
                        reason: str, **extra) -> None:
        """Append one gate/order decision to the decisions audit trail."""
        entry = {
            "ts": self._time_fn(),
            "strategy": "KalshiMMPilot",
            "tax_bucket": "ordinary",
            "gate": gate,
            "market": ticker,
            "decision": "pass" if allowed else "fail",
            "reason": reason,
        }
        entry.update(extra)
        try:
            if self._decision_writer is not None:
                self._decision_writer(entry)
                return
            with self._decision_lock:
                if self._decision_fh is None:
                    self._decision_fh = open(DECISIONS_LOG_PATH, "a",
                                             encoding="utf-8")
                self._decision_fh.write(json.dumps(entry) + "\n")
                self._decision_fh.flush()
        except Exception:
            logger.exception("MM pilot decision audit write failed")

    def _alert(self, alert_type: str, severity: str, message: str,
               details: dict | None = None) -> None:
        logger.log(logging.CRITICAL if severity == "CRITICAL" else logging.WARNING,
                   "MM pilot %s: %s", alert_type, message)
        if self._alerts is not None:
            try:
                self._alerts.alert(alert_type, severity, message, details)
            except Exception:
                logger.exception("MM pilot alert dispatch failed")

    # -- selection / book state --------------------------------------------

    def update_selection(self, tickers: list[str]) -> None:
        """Install the latest ``select_lip_markets`` output (gate G4 input)."""
        with self._lock:
            self._selected = {t for t in tickers if t}

    def pilot_tickers(self) -> list[str]:
        """Markets the pilot currently owns: selected + carrying state."""
        with self._lock:
            tickers = set(self._selected or ())
            tickers.update(info["ticker"] for info in self._orders.values())
        tickers.update(self.inventory.tickers_with_inventory())
        return sorted(tickers)

    def update_book(self, ticker: str, raw_book: dict | None) -> None:
        """Cache a fresh order book snapshot for a pilot market."""
        from kalshi_api import (parse_orderbook, best_yes_bid, best_no_bid,
                                best_yes_ask)
        if not raw_book:
            return
        parsed = parse_orderbook(raw_book)
        yes_bid = best_yes_bid(parsed)
        no_bid = best_no_bid(parsed)
        yes_ask = best_yes_ask(parsed)  # derived from best NO bid
        mid = None
        if yes_bid and yes_ask:
            mid = (yes_bid[0] + yes_ask[0]) / 2.0
        with self._lock:
            self._books[ticker] = {
                "raw": raw_book,
                "mid": mid,
                "yes_bid": yes_bid,      # (price, qty) | None
                "no_bid": no_bid,        # (price, qty) | None
                "yes_ask": yes_ask,      # (price, qty) | None
                "updated_at": self._time_fn(),
            }
        if mid is not None:
            try:
                self._vol.record_price(ticker, mid)
            except Exception:
                pass

    def on_ws_price(self, ticker: str, yes_price: float) -> None:
        """Mid update from the orderbook_delta WS channel (freshness only)."""
        with self._lock:
            book = self._books.get(ticker)
            if book is not None:
                book["mid"] = float(yes_price)
                book["updated_at"] = self._time_fn()
        try:
            self._vol.record_price(ticker, float(yes_price))
        except Exception:
            pass

    def get_raw_book(self, ticker: str) -> dict | None:
        with self._lock:
            book = self._books.get(ticker)
            return book["raw"] if book else None

    def _book(self, ticker: str) -> dict | None:
        with self._lock:
            return self._books.get(ticker)

    # -- registry helpers ----------------------------------------------------

    def resting_orders(self, ticker: str = "") -> list[dict]:
        with self._lock:
            return [
                {"order_id": oid, **info}
                for oid, info in self._orders.items()
                if not ticker or info["ticker"] == ticker
            ]

    def _resting_notional(self, ticker: str) -> float:
        with self._lock:
            return sum(info["count"] * info["price"]
                       for info in self._orders.values()
                       if info["ticker"] == ticker)

    # -- choke point (spec section 5) ----------------------------------------

    def authorize_order(self, ticker: str, side: str, action: str,
                        count: int, price: float,
                        reducing: bool = False) -> GateResult:
        """The ONLY gate to ``place_order`` for the pilot. Deterministic,
        thread-safe.

        Checks, in order: kill-switch state, platform allowlist ("kalshi" in
        ENABLED_EXECUTION_PLATFORMS and platform hardcoded "kalshi"), halted
        flags, per-market caps (USD and contracts), total cap, gross cap, and
        per-order size. Reducing orders (orders that strictly decrease
        ``|net_inventory|``) bypass the inventory checks only — caps must
        never block the exit — but still pass kill-switch, platform, and size
        checks. Every rejection is logged and written to the decisions audit
        trail.
        """
        import config

        result = self._authorize(ticker, side, action, count, price, reducing,
                                 config)
        if not result.allowed:
            logger.warning("MM pilot order REJECTED (%s): %s %s %s x%d @ %.2f",
                           result.reason, ticker, action, side, count, price)
        self._write_decision("authorize_order", ticker, result.allowed,
                             result.reason, side=side, action=action,
                             count=count, price=price, reducing=reducing)
        return result

    def _authorize(self, ticker: str, side: str, action: str, count: int,
                   price: float, reducing: bool, config) -> GateResult:
        # 1. Kill switch (env flag + fresh control-plane cache)
        if not config.MM_KALSHI_PILOT_ENABLED:
            return GateResult(False, "kill_switch_env")
        if not self._controls.is_enabled():
            return GateResult(False, "kill_switch")
        # 2. Platform allowlist + hardcoded venue
        if PLATFORM not in config.ENABLED_EXECUTION_PLATFORMS:
            return GateResult(False, "platform_not_allowlisted")
        # 3. Halted flags
        if self.halted:
            return GateResult(False, "pilot_halted")
        if ticker in self._market_halted and not reducing:
            return GateResult(False, "market_halted")
        # 4. Per-order size
        if count < 1:
            return GateResult(False, "order_size")
        if price <= 0 or price >= 1:
            return GateResult(False, "order_price")
        notional = count * price
        if notional > config.MM_MAX_GROSS_PER_MARKET_USD:
            return GateResult(False, "order_size")
        if reducing:
            # Inventory checks are bypassed for reducing orders only —
            # caps must never block the exit.
            return GateResult(True, "ok_reducing")
        # 5. Per-market inventory caps — both units, most restrictive wins.
        # An order whose fill moves |net| toward zero is reducing-direction
        # (the one-sided quote the section-5 table keeps alive at cap): it
        # passes the inventory checks; accumulating orders are capped.
        signed = PilotInventory.signed_contracts(side, action, count)
        net_ct = self.inventory.net_contracts(ticker)
        net_usd = abs(self.inventory.net_usd(ticker))
        projected_ct = net_ct + signed
        accumulating = abs(projected_ct) > abs(net_ct)
        if accumulating:
            if abs(projected_ct) > config.MM_MAX_INVENTORY_CONTRACTS:
                return GateResult(False, "per_market_contract_cap")
            if net_usd + notional > config.MM_MAX_INVENTORY_USD:
                return GateResult(False, "per_market_inventory_cap")
            # 6. Total cap
            if (self.inventory.total_net_usd() + notional
                    > config.MM_MAX_TOTAL_INVENTORY_USD):
                return GateResult(False, "total_inventory_cap")
        # 7. Gross cap: inventory at cost + resting quote notional + this order
        gross = net_usd + self._resting_notional(ticker) + notional
        if gross > config.MM_MAX_GROSS_PER_MARKET_USD:
            return GateResult(False, "gross_cap")
        return GateResult(True, "ok")

    def place_pilot_order(self, ticker: str, side: str, action: str,
                          count: int, price: float, purpose: str,
                          reducing: bool = False) -> str | None:
        """Place one pilot order through the choke point.

        Returns the order id (synthetic in dry-run), or None on rejection or
        placement failure. This method contains the pilot's single reference
        to ``kalshi_client.place_order``.
        """
        with self._lock:
            verdict = self.authorize_order(ticker, side, action, count, price,
                                           reducing=reducing)
            if not verdict.allowed:
                return None
            if self.dry_run:
                self._order_seq += 1
                order_id = f"dry_mmpilot_{ticker}_{purpose}_{self._order_seq}"
            elif self._client is None:
                # Fail closed: live mode without a client places nothing.
                logger.error("MM pilot live placement with no Kalshi client — "
                             "rejected (fail closed)")
                self._write_decision("place_order", ticker, False, "no_client")
                return None
            else:
                self.place_order_calls += 1
                # Quotes rest GTC; hedges are IOC-style fill_or_kill at touch
                # (unfilled hedges are caught by _check_pending_hedges).
                tif = "fill_or_kill" if purpose == "hedge" else "gtc"
                resp = self._client.place_order(
                    ticker=ticker, side=side, action=action, count=count,
                    price_dollars=price, time_in_force=tif,
                )
                if resp is None:
                    logger.warning("MM pilot place_order failed on %s", ticker)
                    return None
                order = resp.get("order", resp) if isinstance(resp, dict) else {}
                order_id = order.get("order_id") or order.get("id")
                if not order_id:
                    logger.warning("MM pilot place_order returned no id on %s",
                                   ticker)
                    return None
            self._orders[order_id] = {
                "ticker": ticker,
                "side": side,
                "action": action,
                "count": count,
                "price": price,
                "purpose": purpose,
                "placed_at": self._time_fn(),
            }
            return order_id

    def _cancel_order(self, order_id: str) -> None:
        """Cancel one resting pilot order (remote best-effort, retry once)."""
        with self._lock:
            info = self._orders.pop(order_id, None)
        if info is None:
            return
        if self.dry_run or self._client is None or order_id.startswith("dry_"):
            return
        try:
            if not self._client.cancel_order(order_id):
                self._client.cancel_order(order_id)
        except Exception:
            logger.exception("MM pilot cancel_order raised for %s", order_id)

    def pull_market(self, ticker: str, reason: str) -> int:
        """Cancel every resting pilot order in a market (fail closed)."""
        order_ids = [o["order_id"] for o in self.resting_orders(ticker)]
        for oid in order_ids:
            self._cancel_order(oid)
        if order_ids:
            logger.info("MM pilot pulled %d quotes on %s (%s)",
                        len(order_ids), ticker, reason)
        return len(order_ids)

    def pull_all(self, reason: str) -> int:
        order_ids = [o["order_id"] for o in self.resting_orders()]
        for oid in order_ids:
            self._cancel_order(oid)
        if order_ids:
            logger.warning("MM pilot pulled ALL %d resting orders (%s)",
                           len(order_ids), reason)
        return len(order_ids)

    # -- halt machinery ------------------------------------------------------

    def halt_market(self, ticker: str, reason: str) -> None:
        """Halt one market: pull quotes, refuse to re-quote until manual reset."""
        import config
        self.pull_market(ticker, reason)
        with self._lock:
            self._market_halted[ticker] = reason
            times = self._market_halt_times.setdefault(ticker, [])
            now = self._time_fn()
            times.append(now)
            window = config.MM_HALT_WINDOW_SECONDS
            recent = [t for t in times if now - t <= window]
            self._market_halt_times[ticker] = recent
        self._alert("MM_PILOT_MARKET_HALT", "WARNING",
                    f"MM pilot market {ticker} halted: {reason}",
                    {"ticker": ticker, "reason": reason})
        self._write_decision("halt_market", ticker, False, reason)
        if len(recent) >= 2:
            self.halt_all(f"market {ticker} halted twice within "
                          f"{int(window)}s ({reason})")

    def halt_all(self, reason: str) -> None:
        """HALT the whole pilot: cancel everything; manual restart required."""
        with self._lock:
            if self.halted:
                return
            self.halted = True
            self.halt_reason = reason
        self.pull_all(reason)
        self._alert("MM_PILOT_HALT", "CRITICAL",
                    f"MM pilot HALTED: {reason} — manual restart required",
                    {"reason": reason})
        self._write_decision("halt_all", "*", False, reason)

    # -- gate chain (spec section 6) ------------------------------------------

    def _evaluate_gates(self, ticker: str) -> dict:
        """Run gates G1-G12 (pre-placement subset) in fixed order.

        Returns ``{"action": "quote"|"skip"|"pull"|"halted", "reason": str,
        "one_side": None|"bid_only"|"ask_only", "no_new_quotes": bool}``.
        First failure short-circuits. No network calls in here — inputs are
        pre-fetched state.
        """
        import config

        def gate(name: str, ok: bool, reason: str) -> bool:
            self._write_decision(name, ticker, ok, reason)
            return ok

        # G1 kill switch
        g1 = config.MM_KALSHI_PILOT_ENABLED and self._controls.is_enabled()
        if not gate("G1_kill_switch", g1, "ok" if g1 else "kill_switch"):
            self.halt_all("kill switch off or control plane stale")
            return {"action": "halted", "reason": "kill_switch"}
        # G2 venue allowlist
        g2 = PLATFORM in config.ENABLED_EXECUTION_PLATFORMS
        if not gate("G2_venue_allowlist", g2,
                    "ok" if g2 else "platform_not_allowlisted"):
            self.halt_all("kalshi missing from ENABLED_EXECUTION_PLATFORMS "
                          "(config error)")
            return {"action": "halted", "reason": "platform_not_allowlisted"}
        # G3 halted flags
        g3 = not self.halted and ticker not in self._market_halted
        if not gate("G3_market_halted", g3, "ok" if g3 else "market_halted"):
            return {"action": "skip", "reason": "market_halted"}
        # G4 still selected (fail closed when no snapshot has ever arrived)
        with self._lock:
            selected = self._selected
        g4 = selected is not None and ticker in selected
        if not gate("G4_still_selected", g4, "ok" if g4 else "deselected"):
            return {"action": "pull", "reason": "deselected"}
        # G5 price band + G6 book staleness
        book = self._book(ticker)
        mid = book.get("mid") if book else None
        if book is None or mid is None:
            gate("G6_book_staleness", False, "book_missing")
            return {"action": "pull", "reason": "book_missing"}
        g5 = config.LIP_PRICE_BAND_LOW <= mid <= config.LIP_PRICE_BAND_HIGH
        if not gate("G5_price_band", g5, "ok" if g5 else "price_band"):
            return {"action": "pull", "reason": "price_band"}
        age = self._time_fn() - book["updated_at"]
        g6 = age <= config.MM_BOOK_MAX_STALE_SECONDS
        if not gate("G6_book_staleness", g6, "ok" if g6 else "book_stale"):
            return {"action": "pull", "reason": "book_stale"}
        # G7 toxic-flow pause
        g7 = not self._toxic.should_pause(ticker)
        if not gate("G7_toxic_flow", g7, "ok" if g7 else "toxic_flow_pause"):
            return {"action": "pull", "reason": "toxic_flow_pause"}
        # G8 volatility ceiling (G9 widening is applied inside QuoteEngine)
        multiplier = self._vol.get_spread_multiplier(ticker)
        g8 = multiplier < config.MM_VOL_PULL_MULTIPLIER
        if not gate("G8_volatility_ceiling", g8,
                    "ok" if g8 else "volatility_ceiling"):
            return {"action": "pull", "reason": "volatility_ceiling"}
        gate("G9_volatility_widening", True,
             f"multiplier={multiplier:.2f}")
        # G10 inventory caps — stop / one-side, not clamp-and-continue
        one_side = None
        net_ct = self.inventory.net_contracts(ticker)
        net_usd = self.inventory.net_usd(ticker)
        over_per_market = (abs(net_ct) >= config.MM_MAX_INVENTORY_CONTRACTS or
                           abs(net_usd) >= config.MM_MAX_INVENTORY_USD)
        if over_per_market:
            one_side = "ask_only" if net_ct > 0 else "bid_only"
        total = self.inventory.total_net_usd()
        if total >= config.MM_MAX_TOTAL_INVENTORY_USD:
            # Stop the accumulating side in EVERY market; a flat market has
            # no inventory-reducing quote, so it places nothing.
            if net_ct > 0:
                one_side = "ask_only"
            elif net_ct < 0:
                one_side = "bid_only"
            else:
                gate("G10_inventory_caps", False, "total_inventory_cap")
                return {"action": "pull", "reason": "total_inventory_cap"}
        gross = abs(net_usd) + self._resting_notional(ticker)
        no_new_quotes = False
        if gross > config.MM_MAX_GROSS_PER_MARKET_USD:
            # Cancel newest resting orders until gross < cap; no new quotes.
            orders = sorted(self.resting_orders(ticker),
                            key=lambda o: o["placed_at"], reverse=True)
            for order in orders:
                if gross <= config.MM_MAX_GROSS_PER_MARKET_USD:
                    break
                self._cancel_order(order["order_id"])
                gross -= order["count"] * order["price"]
            no_new_quotes = True
            gate("G10_inventory_caps", False, "gross_cap")
        else:
            gate("G10_inventory_caps", True,
                 f"one_side={one_side or 'none'}")
        if no_new_quotes:
            return {"action": "skip", "reason": "gross_cap"}
        return {"action": "quote", "reason": "ok", "one_side": one_side}

    # -- quoting ---------------------------------------------------------------

    def _quote_size_usd(self) -> float:
        """Canary size until graduation (spec section 8); pilot size after."""
        import config
        if self.canary_graduated:
            return config.MM_QUOTE_SIZE_USD
        return config.MM_CANARY_QUOTE_SIZE_USD

    @staticmethod
    def _round_tick(price: float) -> float:
        return round(round(price / KALSHI_TICK) * KALSHI_TICK, 2)

    def refresh_market(self, ticker: str) -> list[str]:
        """Gate chain -> quotes -> choke point -> cancel/replace GTC orders.

        Returns the list of order ids placed this cycle (possibly empty).
        """
        import config

        if self._client is not None:
            try:
                self.update_book(ticker, self._client.fetch_order_book(ticker))
            except Exception:
                logger.exception("MM pilot book fetch failed for %s", ticker)

        plan = self._evaluate_gates(ticker)
        if plan["action"] in ("pull", "halted"):
            if plan["action"] == "pull":
                self.pull_market(ticker, plan["reason"])
            return []
        if plan["action"] == "skip":
            return []

        book = self._book(ticker)
        mid = book["mid"]
        quotes = self._quote_engine.calculate_quotes(
            mid,
            inventory=self.inventory.net_usd(ticker),
            max_inventory=config.MM_MAX_INVENTORY_USD,
            market_key=ticker,  # G9: volatility widening hook
        )
        bid = self._round_tick(quotes["bid"])
        ask = self._round_tick(quotes["ask"])

        # G12 crossing guard (post-only semantics): never a marketable quote.
        best_yes_ask = book.get("yes_ask")
        best_yes_bid = book.get("yes_bid")
        skip_bid = skip_ask = False
        if best_yes_ask is not None and bid >= best_yes_ask[0]:
            bid = self._round_tick(best_yes_ask[0] - KALSHI_TICK)
            if bid < KALSHI_TICK:
                skip_bid = True
        if best_yes_bid is not None and ask <= best_yes_bid[0]:
            ask = self._round_tick(best_yes_bid[0] + KALSHI_TICK)
            if ask > 1.0 - KALSHI_TICK:
                skip_ask = True
        self._write_decision("G12_crossing_guard", ticker,
                             not (skip_bid and skip_ask),
                             f"bid={bid:.2f} ask={ask:.2f} "
                             f"skip_bid={skip_bid} skip_ask={skip_ask}")

        one_side = plan.get("one_side")
        if one_side == "ask_only":
            skip_bid = True
        elif one_side == "bid_only":
            skip_ask = True

        size_usd = self._quote_size_usd()

        # G11 depth sizing: quote count <= fraction of same-side best size.
        def _sized_count(price: float, best: tuple | None) -> int:
            if price <= 0:
                return 0
            count = int(size_usd / price)
            if best is not None:
                depth_cap = int(config.MM_MAX_BOOK_DEPTH_FRACTION * best[1])
                count = min(count, depth_cap)
            return count

        # Our bid = buy YES at `bid`; same side of the book = resting YES bids.
        bid_count = 0 if skip_bid else _sized_count(bid, book.get("yes_bid"))
        # Our ask = buy NO at (1 - ask); same side = resting NO bids.
        no_price = self._round_tick(1.0 - ask)
        ask_count = 0 if skip_ask else _sized_count(no_price, book.get("no_bid"))
        self._write_decision("G11_depth_sizing", ticker,
                             bid_count >= 1 or ask_count >= 1,
                             f"bid_count={bid_count} ask_count={ask_count}")

        # Cancel/replace: pull existing quote orders, then place fresh GTC.
        for order in self.resting_orders(ticker):
            if order["purpose"] in ("quote_bid", "quote_ask"):
                self._cancel_order(order["order_id"])

        placed: list[str] = []
        if bid_count >= 1:
            oid = self.place_pilot_order(ticker, side="yes", action="buy",
                                         count=bid_count, price=bid,
                                         purpose="quote_bid")
            if oid:
                placed.append(oid)
        if ask_count >= 1 and 0 < no_price < 1:
            oid = self.place_pilot_order(ticker, side="no", action="buy",
                                         count=ask_count, price=no_price,
                                         purpose="quote_ask")
            if oid:
                placed.append(oid)
        return placed

    def refresh_all(self) -> list[str]:
        placed: list[str] = []
        for ticker in self.pilot_tickers():
            if self.halted:
                break
            placed.extend(self.refresh_market(ticker))
        return placed

    # -- fill detection (spec section 3) ----------------------------------------

    def _fill_id(self, fill: dict) -> str:
        fid = fill.get("trade_id")
        if fid:
            return str(fid)
        return f"{fill.get('order_id')}|{fill.get('created_time')}|{fill.get('count')}"

    def _mark_seen(self, fid: str) -> None:
        self._seen_fill_ids[fid] = None
        while len(self._seen_fill_ids) > 1000:
            self._seen_fill_ids.pop(next(iter(self._seen_fill_ids)))

    def poll_fills(self) -> list[FillEvent]:
        """One fill-poll cycle: fetch, dedupe, attribute, process."""
        if self.halted:
            return []
        if self.dry_run:
            raw_fills = self._simulate_dry_fills()
        else:
            if self._client is None:
                return []
            min_ts = int(self._last_fill_ts - 60)  # 60s overlap window
            try:
                raw_fills = self._client.get_fills(min_ts=min_ts)
            except Exception:
                logger.exception("MM pilot get_fills failed — no fills "
                                 "processed this cycle (fail closed on next "
                                 "hedge check)")
                return []
        events: list[FillEvent] = []
        pilot_markets = set(self.pilot_tickers())
        for fill in reversed(raw_fills):  # oldest first
            fid = self._fill_id(fill)
            if fid in self._seen_fill_ids:
                continue
            ticker = fill.get("ticker", "")
            order_id = str(fill.get("order_id", ""))
            with self._lock:
                known = order_id in self._orders
                info = self._orders.get(order_id)
            if not known:
                if ticker in pilot_markets:
                    # A fill on an unknown order_id in a pilot market is a
                    # deviation: something else is trading our markets on
                    # this account (spec sections 3 and 8).
                    self._mark_seen(fid)
                    self.halt_all(f"fill on unknown order_id {order_id} in "
                                  f"pilot market {ticker}")
                    return events
                continue  # someone else's market — not ours to account
            self._mark_seen(fid)
            event = self._build_event(fid, fill, info)
            if event is None:
                continue
            self._last_fill_ts = max(self._last_fill_ts, event.created_ts)
            events.append(event)
            self._process_fill(event, info)
            if self.halted:
                break
        if not self.halted:
            self._check_pending_hedges()
        return events

    def _check_pending_hedges(self) -> None:
        """Cancel-remainder path: a hedge order still resting past the latency
        ceiling means the position was NOT flattened — hedge failure, fail
        closed (spec section 4)."""
        import config
        now = self._time_fn()
        for order in self.resting_orders():
            if order["purpose"] != "hedge":
                continue
            if now - order["placed_at"] <= config.MM_HEDGE_MAX_LATENCY_SECONDS:
                continue
            self._cancel_order(order["order_id"])  # cancel remainder
            ticker = order["ticker"]
            if not self.canary_graduated:
                self.halt_all(f"hedge order unfilled past latency ceiling on "
                              f"{ticker}")
                return
            self.halt_market(ticker, "hedge order unfilled past latency ceiling")

    def _build_event(self, fid: str, fill: dict, info: dict) -> FillEvent | None:
        from kalshi_vip import fill_price_dollars
        price = fill_price_dollars(fill)
        if price is None:
            logger.warning("MM pilot fill %s has no price — ignored", fid)
            return None
        book = self._book(info["ticker"])
        mid = (book or {}).get("mid") or price
        try:
            count = int(fill.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            return None
        return FillEvent(
            fill_id=fid,
            order_id=str(fill.get("order_id", "")),
            ticker=info["ticker"],
            side=str(fill.get("side", info["side"])),
            action=str(fill.get("action", info["action"])),
            count=count,
            price=price,
            is_taker=bool(fill.get("is_taker", False)),
            created_ts=_parse_created_ts(fill, self._time_fn()),
            mid_at_detect=float(mid),
        )

    # -- fill processing / canary (spec sections 4 and 8) ------------------------

    def _process_fill(self, event: FillEvent, order_info: dict) -> None:
        import config

        purpose = order_info.get("purpose", "")
        is_hedge = purpose == "hedge"

        # Canary deviation: a resting quote should never be the taker.
        if event.is_taker and not is_hedge:
            self.halt_all(f"taker fill on resting quote {event.order_id}")
            return

        # Registry maintenance: shrink/remove the filled order.
        with self._lock:
            live = self._orders.get(event.order_id)
            if live is not None:
                live["count"] -= event.count
                if live["count"] <= 0:
                    self._orders.pop(event.order_id, None)

        # 1. Inventory update (signed dollar delta / avg-cost accounting).
        self.inventory.apply_fill(event.ticker, event.side, event.action,
                                  event.count, event.price)

        # Trade log: strategy-tagged from trade one (operating rule 5).
        self._log_fill(event)
        side_price = event.price if event.side == "yes" else 1.0 - event.price
        notional = event.count * side_price

        # 2. Toxicity feed (quote fills only — hedges are deliberate takers).
        if not is_hedge:
            quote_side = "bid" if purpose == "quote_bid" else "ask"
            try:
                self._toxic.record_fill(event.ticker, quote_side, event.price,
                                        notional, event.mid_at_detect)
                if (self._toxic.get_toxicity(event.ticker)
                        >= config.MM_TOXIC_FLOW_THRESHOLD):
                    self._toxic.trigger_pause(event.ticker)
            except Exception:
                logger.exception("MM pilot toxicity record failed")

        # 3. Canary accounting (deviation checks halt the whole pilot).
        # Realized-loss ceiling considers ALL fills — hedge exits are where
        # canary losses actually crystallize.
        if not self.canary_graduated:
            realized = self.inventory.realized_pnl_total()
            if realized < -config.MM_CANARY_MAX_LOSS_USD:
                self.halt_all(f"canary realized P&L ${realized:.2f} below "
                              f"-${config.MM_CANARY_MAX_LOSS_USD:.2f}")
                return
        if not is_hedge and not self._check_canary(event, notional):
            return

        # Post-trade cap observation: caps are pre-trade; a breach observed
        # here means the choke point leaked — the worst possible bug.
        if self._observed_cap_breach(event.ticker):
            self.halt_all(f"post-trade inventory above cap on {event.ticker} "
                          "(choke point leak)")
            return

        # Multi-market toxicity deviation.
        if self._multi_market_toxicity():
            self.halt_all("toxicity over threshold on >=2 pilot markets")
            return

        # 4. Hedge decision (skip re-hedging on the hedge's own fill).
        if not is_hedge:
            self._hedge_on_fill(event)

        # Graduation check rides on fill processing and the run loop.
        self._maybe_graduate()

    def _log_fill(self, event: FillEvent) -> None:
        if self._db is None:
            return
        try:
            with self._lock:
                opp_ids = getattr(self, "_opp_ids", None)
                if opp_ids is None:
                    opp_ids = self._opp_ids = {}
                opp_id = opp_ids.get(event.ticker)
            if opp_id is None:
                opp_id = self._db.log_opportunity(
                    opp_type="KalshiMMPilot", market=event.ticker,
                    prices="", total_cost=0.0, net_profit=0.0, net_roi=0.0,
                    depth=0.0, action="quote",
                ) or 0
                with self._lock:
                    self._opp_ids[event.ticker] = opp_id
            side_price = event.price if event.side == "yes" else 1.0 - event.price
            self._db.log_trade(
                opportunity_id=opp_id, platform=PLATFORM,
                side=f"{event.action}_{event.side}", price=side_price,
                size=event.count * side_price, status="filled",
                fill_price=side_price, order_id=event.order_id,
            )
        except Exception:
            logger.exception("MM pilot fill DB log failed")

    def _check_canary(self, event: FillEvent, notional: float) -> bool:
        """Canary bookkeeping. Returns False when a deviation halted the pilot."""
        import config
        if self.canary_graduated:
            return True
        if notional > config.MM_CANARY_QUOTE_SIZE_USD + 0.01:
            self.halt_all(f"canary fill ${notional:.2f} exceeds canary size "
                          f"${config.MM_CANARY_QUOTE_SIZE_USD:.2f} on "
                          f"{event.ticker}")
            return False
        self.canary_clean_fills += 1
        return True

    def _maybe_graduate(self) -> None:
        import config
        if self.canary_graduated or self.halted or self.dry_run:
            return
        runtime_h = (self._time_fn() - self._live_started_ts) / 3600.0
        if (self.canary_clean_fills >= config.MM_CANARY_FILLS
                and runtime_h >= config.MM_CANARY_MIN_HOURS):
            self.canary_graduated = True
            self._write_decision("canary", "*", True, "CANARY PASSED",
                                 clean_fills=self.canary_clean_fills,
                                 runtime_hours=round(runtime_h, 2))
            self._alert("MM_PILOT_CANARY_PASSED", "INFO",
                        f"MM pilot canary passed after "
                        f"{self.canary_clean_fills} clean fills / "
                        f"{runtime_h:.1f}h — graduating to pilot size")

    def _observed_cap_breach(self, ticker: str) -> bool:
        import config
        return (abs(self.inventory.net_contracts(ticker))
                > config.MM_MAX_INVENTORY_CONTRACTS
                or abs(self.inventory.net_usd(ticker))
                > config.MM_MAX_INVENTORY_USD + 0.01
                or self.inventory.total_net_usd()
                > config.MM_MAX_TOTAL_INVENTORY_USD + 0.01)

    def _multi_market_toxicity(self) -> bool:
        import config
        toxic = 0
        for ticker in self.pilot_tickers():
            try:
                if self._toxic.get_toxicity(ticker) >= config.MM_TOXIC_FLOW_THRESHOLD:
                    toxic += 1
            except Exception:
                continue
        return toxic >= 2

    # -- auto-hedge (spec section 4) -----------------------------------------------

    def _hedge_on_fill(self, event: FillEvent) -> None:
        """Hedge decision per fill: deadband rebalance or reducing order."""
        import config

        net_usd = self.inventory.net_usd(event.ticker)
        excess = abs(net_usd) - config.MM_INVENTORY_TARGET_USD
        if excess <= config.MM_HEDGE_DEADBAND_USD:
            # Rebalance arm: inventory skew in QuoteEngine works it off.
            self._write_decision("hedge", event.ticker, True, "hedge_deadband",
                                 net_usd=round(net_usd, 2))
            return

        net_ct = self.inventory.net_contracts(event.ticker)
        # Long YES exposure -> sell yes at the bid; long NO -> sell no.
        hold_side = "yes" if net_ct > 0 else "no"
        avg = self.inventory.avg_cost(event.ticker)

        success = False
        error: str = ""
        for attempt in (1, 2):  # retry once on failure (spec section 4 table)
            try:
                success = self._hedger.hedge_inventory(
                    market_key=event.ticker,
                    platform=PLATFORM,
                    side=hold_side,
                    fill_price=avg,
                    size=excess,
                    token_id=event.ticker,
                    reduce_action="sell",
                )
            except Exception as exc:
                success = False
                error = str(exc)
                logger.exception("MM pilot hedge attempt %d raised on %s",
                                 attempt, event.ticker)
            if success:
                break

        latency = self._time_fn() - event.created_ts
        latency_exceeded = latency > config.MM_HEDGE_MAX_LATENCY_SECONDS
        self._write_decision("hedge", event.ticker, success and not latency_exceeded,
                             "hedged" if success else (error or "hedge_failed"),
                             excess=round(excess, 2),
                             latency_s=round(latency, 2))

        if latency_exceeded:
            # Canary deviation 4: latency ceiling exceeded even if the hedge
            # eventually landed.
            if not self.canary_graduated:
                self.halt_all(f"hedge latency {latency:.1f}s exceeded "
                              f"{config.MM_HEDGE_MAX_LATENCY_SECONDS:.0f}s "
                              f"ceiling on {event.ticker}")
                return
            if success:
                self.halt_market(event.ticker,
                                 f"hedge latency {latency:.1f}s over ceiling")
                return
            success = False

        if not success:
            # Fail closed: a market carrying inventory it cannot hedge never
            # keeps live quotes. Any hedge failure is a canary deviation.
            if not self.canary_graduated:
                self.halt_all(f"hedge failure on {event.ticker}")
            else:
                self.halt_market(event.ticker, "hedge failure")

    # -- dry-run fill simulation (rollout phase D0) ------------------------------

    def _simulate_dry_fills(self) -> list[dict]:
        """Synthesize FillEvents from book crosses against dry-run orders."""
        fills: list[dict] = []
        now = self._time_fn()
        for order in self.resting_orders():
            ticker = order["ticker"]
            book = self._book(ticker)
            if not book or book.get("mid") is None:
                continue
            mid = book["mid"]
            crossed = False
            if order["purpose"] == "hedge":
                crossed = True  # IOC at touch: assume immediate execution
            elif order["purpose"] == "quote_bid" and mid <= order["price"]:
                crossed = True
            elif order["purpose"] == "quote_ask" and mid >= 1.0 - order["price"]:
                crossed = True
            if not crossed:
                continue
            self._order_seq += 1
            yes_price = (order["price"] if order["side"] == "yes"
                         else 1.0 - order["price"])
            fills.append({
                "trade_id": f"dryfill_{self._order_seq}",
                "order_id": order["order_id"],
                "ticker": ticker,
                "side": order["side"],
                "action": order["action"],
                "count": order["count"],
                "yes_price": int(round(yes_price * 100)),
                "is_taker": order["purpose"] == "hedge",
                "created_time": now,
            })
        return fills

    # -- lifecycle ------------------------------------------------------------------

    def run_loop(self, stop_event: threading.Event,
                 selection_provider=None) -> None:
        """Drive controls / fill / refresh cadences until stopped.

        Intended as a daemon-thread target from continuous mode. Every stage
        is exception-guarded; three consecutive loop errors halt the pilot
        (fail closed) rather than spinning blind.
        """
        import config

        last_controls = last_fills = last_refresh = last_selection = 0.0
        while not stop_event.is_set():
            now = self._time_fn()
            try:
                if now - last_controls >= config.MM_CONTROLS_POLL_SECONDS:
                    last_controls = now
                    self._controls.poll()
                if selection_provider is not None and now - last_selection >= 3600:
                    last_selection = now
                    tickers = selection_provider()
                    if tickers is not None:
                        self.update_selection(list(tickers))
                if now - last_fills >= config.MM_FILL_POLL_SECONDS:
                    last_fills = now
                    self.poll_fills()
                if now - last_refresh >= config.MM_REFRESH_INTERVAL:
                    last_refresh = now
                    self.refresh_all()
                self._loop_error_streak = 0
            except Exception:
                logger.exception("MM pilot loop iteration failed")
                self._loop_error_streak += 1
                if self._loop_error_streak >= 3:
                    self.halt_all("3 consecutive pilot loop errors")
            stop_event.wait(0.5)
        self.stop()

    def stop(self) -> None:
        """Cancel all resting pilot orders (SIGTERM / shutdown path)."""
        cancelled = self.pull_all("pilot stop")
        logger.info("MM pilot stopped: cancelled %d resting orders", cancelled)
        with self._decision_lock:
            if self._decision_fh is not None and not self._decision_fh.closed:
                self._decision_fh.close()
                self._decision_fh = None

    def get_status(self) -> dict:
        """Status snapshot for dashboards / digests."""
        return {
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "markets_halted": dict(self._market_halted),
            "canary_graduated": self.canary_graduated,
            "canary_clean_fills": self.canary_clean_fills,
            "resting_orders": len(self._orders),
            "total_inventory_usd": self.inventory.total_net_usd(),
            "realized_pnl": self.inventory.realized_pnl_total(),
            "dry_run": self.dry_run,
        }
