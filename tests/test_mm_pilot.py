"""Plan 10 tests — fill detection, auto-hedge, kill switch, canary, dry-run.

Spec test-plan cases 1-5 and 13-15 (docs/plans/10-mm-pilot-prep.md section 10).
Fail-before: none of these behaviors exist on origin/master (mm_pilot.py is
new); every test here fails on master by construction.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib

import pytest

from market_maker import ToxicFlowDetector, VolatilityTracker
from mm_pilot import ControlsPoller, FillEvent, KalshiMMPilot


def live_config():
    """Resolve the LIVE config module.

    Other test files in this suite pop/replace sys.modules["config"], so a
    module-level `import config` binding can go stale — mm_pilot reads config
    off sys.modules at call time and would see a different object (same
    pattern as tests/test_negrisk_no_side.py).
    """
    return importlib.import_module("config")


TICKER = "KXTEST-26DEC31"


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------

def make_book(yes_bid=0.49, no_bid=0.49, yes_qty=500.0, no_qty=500.0):
    """Raw Kalshi book (orderbook_fp schema). yes_ask derives as 1 - no_bid."""
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid:.4f}", f"{yes_qty:.2f}"]],
        "no_dollars": [[f"{no_bid:.4f}", f"{no_qty:.2f}"]],
    }}


class FakeKalshiClient:
    def __init__(self, books=None):
        self.books = books if books is not None else {TICKER: make_book()}
        self.fills_script: list[dict] = []
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self.place_order_calls = 0
        self.cancel_order_calls = 0
        self.fail_place = False

    def fetch_order_book(self, ticker):
        return self.books.get(ticker)

    def place_order(self, ticker, side, action, count, price_dollars,
                    time_in_force="fill_or_kill"):
        self.place_order_calls += 1
        if self.fail_place:
            return None
        oid = f"k_{self.place_order_calls}"
        self.placed.append({
            "order_id": oid, "ticker": ticker, "side": side, "action": action,
            "count": count, "price": price_dollars, "tif": time_in_force,
        })
        return {"order": {"order_id": oid}}

    def cancel_order(self, order_id):
        self.cancel_order_calls += 1
        self.cancelled.append(order_id)
        return True

    def get_fills(self, min_ts=None, **kwargs):
        return list(self.fills_script)


class RecordingHedger:
    """Stub hedge executor: records calls, scripted success."""

    def __init__(self, result=True):
        self.result = result
        self.calls: list[dict] = []

    def hedge_inventory(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def kfill(order_id, ticker=TICKER, side="yes", action="buy", count=4,
          yes_price=49, trade_id=None, is_taker=False, created=None):
    fill = {
        "trade_id": trade_id or f"tr_{order_id}_{count}_{yes_price}",
        "order_id": order_id, "ticker": ticker, "side": side,
        "action": action, "count": count, "yes_price": yes_price,
        "is_taker": is_taker,
    }
    if created is not None:
        fill["created_time"] = created
    return fill


@pytest.fixture
def clock():
    return [1_000_000.0]


@pytest.fixture
def pilot_env(monkeypatch):
    """Force the pilot's flag preconditions on (against the LIVE config).

    Yields the live config module so tests can monkeypatch further keys on
    the object mm_pilot actually reads.
    """
    cfg = live_config()
    monkeypatch.setattr(cfg, "MM_KALSHI_PILOT_ENABLED", True)
    monkeypatch.setattr(cfg, "MM_TOXIC_FLOW_ENABLED", True)
    monkeypatch.setattr(cfg, "MM_VOLATILITY_ADJUSTED_ENABLED", True)
    monkeypatch.setattr(cfg, "MM_AUTO_HEDGE_ENABLED", True)
    yield cfg


def build_pilot(clock, client=None, dry_run=False, hedger=None,
                controls_on=True, detector=None, vol=None,
                selection=(TICKER,)):
    time_fn = lambda: clock[0]
    controls = ControlsPoller(time_fn=time_fn)
    controls.set_cached(controls_on)
    decisions: list[dict] = []
    hedger = hedger if hedger is not None else RecordingHedger()
    pilot = KalshiMMPilot(
        kalshi_client=client,
        controls=controls,
        toxic_detector=detector or ToxicFlowDetector(),
        volatility_tracker=vol or VolatilityTracker(),
        hedger_factory=lambda proxy: hedger,
        decision_writer=decisions.append,
        dry_run=dry_run,
        time_fn=time_fn,
    )
    pilot.update_selection(list(selection))
    if client is not None:
        for ticker, book in client.books.items():
            pilot.update_book(ticker, book)
    pilot._decisions = decisions
    pilot._test_controls = controls
    pilot._test_hedger = hedger
    return pilot


# ---------------------------------------------------------------------------
# 1. Fill detection: exactly one FillEvent, deduped across polls
# ---------------------------------------------------------------------------

class TestFillDetection:
    def test_registered_fill_yields_exactly_one_event(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        assert oid is not None
        client.fills_script = [kfill(oid, count=4, created=clock[0])]

        events = pilot.poll_fills()
        assert len(events) == 1
        assert isinstance(events[0], FillEvent)
        assert events[0].order_id == oid
        assert events[0].count == 4
        assert events[0].price == pytest.approx(0.49)

    def test_duplicate_fill_across_overlap_window_deduped(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        fill = kfill(oid, count=4, created=clock[0])
        client.fills_script = [fill]
        first = pilot.poll_fills()
        # Same fill re-served by the 60s overlap window on the next poll
        clock[0] += 2
        second = pilot.poll_fills()
        assert len(first) == 1
        assert len(second) == 0


# ---------------------------------------------------------------------------
# 2. Unknown-order fill in a pilot market -> halt, all cancels issued
# ---------------------------------------------------------------------------

class TestUnknownOrderFill:
    def test_foreign_fill_halts_pilot_and_cancels_everything(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        client.fills_script = [kfill("someone_elses_order", created=clock[0])]

        pilot.poll_fills()
        assert pilot.halted is True
        assert "unknown order_id" in pilot.halt_reason
        assert oid in client.cancelled          # resting quote cancelled
        assert pilot.resting_orders() == []

    def test_fill_in_non_pilot_market_is_ignored(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        client.fills_script = [kfill("arb_executor_order", ticker="OTHER-MKT",
                                     created=clock[0])]
        events = pilot.poll_fills()
        assert events == []
        assert pilot.halted is False


# ---------------------------------------------------------------------------
# 3. Auto-hedge fires past the deadband, correct reduce direction
# ---------------------------------------------------------------------------

class TestAutoHedge:
    def test_long_fill_past_deadband_triggers_sell_yes_hedge(self, pilot_env, clock):
        client = FakeKalshiClient()
        hedger = RecordingHedger(result=True)
        pilot = build_pilot(clock, client=client, hedger=hedger)
        pilot.canary_graduated = True  # isolate hedge logic from canary sizing
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 20, 0.50,
                                      purpose="quote_bid")
        client.fills_script = [kfill(oid, count=20, yes_price=50,
                                     created=clock[0])]
        pilot.poll_fills()
        # |net| = $10 > deadband $5 -> hedge with excess = $10
        assert len(hedger.calls) == 1
        call = hedger.calls[0]
        assert call["side"] == "yes"
        assert call["platform"] == "kalshi"
        assert call["size"] == pytest.approx(10.0)
        assert call["reduce_action"] == "sell"

    def test_short_fill_past_deadband_hedges_no_side(self, pilot_env, clock):
        client = FakeKalshiClient()
        hedger = RecordingHedger(result=True)
        pilot = build_pilot(clock, client=client, hedger=hedger)
        pilot.canary_graduated = True
        oid = pilot.place_pilot_order(TICKER, "no", "buy", 20, 0.50,
                                      purpose="quote_ask")
        client.fills_script = [kfill(oid, side="no", count=20, yes_price=50,
                                     created=clock[0])]
        pilot.poll_fills()
        assert len(hedger.calls) == 1
        assert hedger.calls[0]["side"] == "no"

    def test_fill_inside_deadband_does_not_hedge(self, pilot_env, clock):
        client = FakeKalshiClient()
        hedger = RecordingHedger(result=True)
        pilot = build_pilot(clock, client=client, hedger=hedger)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        client.fills_script = [kfill(oid, count=4, created=clock[0])]
        pilot.poll_fills()
        # $1.96 < $5 deadband: rebalance arm, no hedge order
        assert hedger.calls == []
        assert any(d.get("reason") == "hedge_deadband"
                   for d in pilot._decisions)


# ---------------------------------------------------------------------------
# 4. Hedge failure -> quotes pulled, market halted, no re-quote
# ---------------------------------------------------------------------------

class TestHedgeFailClosed:
    def test_failed_hedge_pulls_quotes_and_halts_market(self, pilot_env, clock):
        client = FakeKalshiClient()
        hedger = RecordingHedger(result=False)  # e.g. book with no bids
        pilot = build_pilot(clock, client=client, hedger=hedger)
        pilot.canary_graduated = True  # post-canary: market halt, not pilot halt
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 20, 0.50,
                                      purpose="quote_bid")
        client.fills_script = [kfill(oid, count=20, yes_price=50,
                                     created=clock[0])]
        pilot.poll_fills()
        # Retry once, then fail closed
        assert len(hedger.calls) == 2
        assert TICKER in pilot.get_status()["markets_halted"]
        assert pilot.resting_orders(TICKER) == []
        # Next refresh places zero quotes in the halted market
        assert pilot.refresh_market(TICKER) == []

    def test_hedge_failure_during_canary_halts_whole_pilot(self, pilot_env,
                                                           clock, monkeypatch):
        monkeypatch.setattr(live_config(), "MM_CANARY_QUOTE_SIZE_USD", 100.0)
        client = FakeKalshiClient()
        hedger = RecordingHedger(result=False)
        pilot = build_pilot(clock, client=client, hedger=hedger)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 20, 0.50,
                                      purpose="quote_bid")
        client.fills_script = [kfill(oid, count=20, yes_price=50,
                                     created=clock[0])]
        pilot.poll_fills()
        assert pilot.halted is True

    def test_second_market_halt_in_window_halts_pilot(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        pilot.canary_graduated = True
        pilot.halt_market(TICKER, "hedge failure")
        assert pilot.halted is False
        clock[0] += 600  # inside MM_HALT_WINDOW_SECONDS (3600)
        pilot._market_halted.pop(TICKER)  # operator reconciled the market
        pilot.halt_market(TICKER, "hedge failure again")
        assert pilot.halted is True


# ---------------------------------------------------------------------------
# 5. Hedge latency ceiling (frozen clock)
# ---------------------------------------------------------------------------

class TestHedgeLatency:
    def test_latency_over_ceiling_takes_halt_path(self, pilot_env, clock,
                                                  monkeypatch):
        monkeypatch.setattr(live_config(), "MM_CANARY_QUOTE_SIZE_USD", 100.0)
        client = FakeKalshiClient()
        hedger = RecordingHedger(result=True)  # hedge eventually lands...
        pilot = build_pilot(clock, client=client, hedger=hedger)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 20, 0.50,
                                      purpose="quote_bid")
        # Fill created 20s ago -> detection + hedge exceed the 10s ceiling
        client.fills_script = [kfill(oid, count=20, yes_price=50,
                                     created=clock[0] - 20)]
        pilot.poll_fills()
        assert pilot.halted is True
        assert "latency" in pilot.halt_reason

    def test_unfilled_hedge_order_past_ceiling_halts(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        hoid = pilot.place_pilot_order(TICKER, "yes", "sell", 10, 0.49,
                                       purpose="hedge", reducing=True)
        assert hoid is not None
        clock[0] += 11  # past MM_HEDGE_MAX_LATENCY_SECONDS with no fill
        pilot.poll_fills()
        assert pilot.halted is True
        assert hoid in client.cancelled  # remainder cancelled


# ---------------------------------------------------------------------------
# 13. Kill switch: flip false / stale cache -> cancel everything
# ---------------------------------------------------------------------------

class TestKillSwitch:
    def test_controls_flip_false_cancels_all_next_cycle(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        assert pilot.resting_orders() != []
        pilot._test_controls.set_cached(False)
        pilot.refresh_market(TICKER)
        assert pilot.halted is True
        assert oid in client.cancelled
        assert pilot.resting_orders() == []

    def test_stale_controls_cache_fails_closed(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                purpose="quote_bid")
        # Cache is true but older than MM_CONTROLS_MAX_STALE_SECONDS (300)
        pilot._test_controls.set_cached(True, fetched_at=clock[0] - 301)
        pilot.refresh_market(TICKER)
        assert pilot.halted is True
        assert pilot.resting_orders() == []

    def test_env_flag_off_rejects_orders(self, clock, monkeypatch):
        monkeypatch.setattr(live_config(), "MM_KALSHI_PILOT_ENABLED", False)
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        result = pilot.authorize_order(TICKER, "yes", "buy", 4, 0.49)
        assert result.allowed is False
        assert result.reason == "kill_switch_env"

    def test_controls_poller_fail_closed_without_client(self, clock):
        poller = ControlsPoller(supabase_client=None,
                                time_fn=lambda: clock[0])
        poller.poll()  # no client — cache never populates
        assert poller.is_enabled() is False


# ---------------------------------------------------------------------------
# 14. Canary: graduation, max loss, oversized fill
# ---------------------------------------------------------------------------

class TestCanary:
    def test_graduation_after_clean_fills_and_min_hours(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 40, 0.49,
                                      purpose="quote_bid")
        for i in range(9):
            client.fills_script = [kfill(oid, count=4, created=clock[0],
                                         trade_id=f"c{i}")]
            pilot.poll_fills()
            clock[0] += 10
        assert pilot.canary_graduated is False
        clock[0] += 25 * 3600  # past MM_CANARY_MIN_HOURS
        client.fills_script = [kfill(oid, count=4, created=clock[0],
                                     trade_id="c9")]
        pilot.poll_fills()
        assert pilot.canary_clean_fills == 10
        assert pilot.canary_graduated is True
        assert any(d.get("reason") == "CANARY PASSED"
                   for d in pilot._decisions)

    def test_canary_loss_over_ceiling_halts(self, pilot_env, clock, monkeypatch):
        monkeypatch.setattr(live_config(), "MM_CANARY_QUOTE_SIZE_USD", 100.0)
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client, hedger=RecordingHedger())
        # Buy 20 @ 0.60, hedge-exit 20 @ 0.05 -> realized -$11.00 < -$10
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 20, 0.60,
                                      purpose="quote_bid")
        client.fills_script = [kfill(oid, count=20, yes_price=60,
                                     created=clock[0], trade_id="in")]
        pilot.poll_fills()
        hoid = pilot.place_pilot_order(TICKER, "yes", "sell", 20, 0.05,
                                       purpose="hedge", reducing=True)
        client.fills_script = [kfill(hoid, action="sell", count=20,
                                     yes_price=5, created=clock[0],
                                     trade_id="out", is_taker=True)]
        pilot.poll_fills()
        assert pilot.inventory.realized_pnl_total() == pytest.approx(-11.0)
        assert pilot.halted is True
        assert "canary realized P&L" in pilot.halt_reason

    def test_canary_fill_oversized_is_a_deviation_halt(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 10, 0.50,
                                      purpose="quote_bid")
        # $5.00 notional > MM_CANARY_QUOTE_SIZE_USD ($2)
        client.fills_script = [kfill(oid, count=10, yes_price=50,
                                     created=clock[0])]
        pilot.poll_fills()
        assert pilot.halted is True
        assert "exceeds canary size" in pilot.halt_reason

    def test_taker_fill_on_resting_quote_halts(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        client.fills_script = [kfill(oid, count=4, created=clock[0],
                                     is_taker=True)]
        pilot.poll_fills()
        assert pilot.halted is True
        assert "taker fill" in pilot.halt_reason


# ---------------------------------------------------------------------------
# 15. Dry-run isolation: zero real client calls across a full session
# ---------------------------------------------------------------------------

class TestDryRunIsolation:
    def test_full_dry_session_never_touches_the_client(self, pilot_env, clock):
        client = FakeKalshiClient(books={TICKER: make_book(yes_bid=0.49,
                                                           no_bid=0.49)})
        pilot = build_pilot(clock, client=client, dry_run=True)
        # Full cycle: quotes placed (dry ids), book crosses generate synthetic
        # fills, hedge fires through the proxy, everything stays local.
        placed = pilot.refresh_market(TICKER)
        assert placed, "dry-run must still produce (synthetic) order ids"
        assert all(oid.startswith("dry_") for oid in placed)
        # Crash the mid through the bid so the dry bid fills
        pilot.update_book(TICKER, make_book(yes_bid=0.10, no_bid=0.88))
        for _ in range(5):
            pilot.poll_fills()
            clock[0] += 2
        pilot.refresh_market(TICKER)
        pilot.stop()
        assert client.place_order_calls == 0
        assert client.cancel_order_calls == 0

    def test_simulated_fill_runs_the_full_pipeline(self, pilot_env, clock):
        client = FakeKalshiClient()
        hedger = RecordingHedger()
        pilot = build_pilot(clock, client=client, dry_run=True, hedger=hedger)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        assert oid.startswith("dry_")
        pilot.update_book(TICKER, make_book(yes_bid=0.10, no_bid=0.88))
        events = pilot.poll_fills()
        assert len(events) == 1
        # End to end in dry-run: inventory updated + audit rows written
        assert pilot.inventory.net_contracts(TICKER) == 4
        assert any(d.get("gate") == "hedge" for d in pilot._decisions)


# ---------------------------------------------------------------------------
# Choke-point runtime counter (test 10 companion; grep half lives in
# test_mm_pilot_gates.py)
# ---------------------------------------------------------------------------

class TestPlaceOrderCounter:
    def test_all_live_placements_flow_through_the_choke_point(self, pilot_env,
                                                              clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        pilot.refresh_market(TICKER)
        pilot.place_pilot_order(TICKER, "yes", "sell", 2, 0.49,
                                purpose="hedge", reducing=True)
        assert client.place_order_calls == pilot.place_order_calls
        assert client.place_order_calls > 0
