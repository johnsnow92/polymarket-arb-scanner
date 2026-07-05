"""Plan 10 tests — inventory caps in the order path, hot-path gates, invariants.

Spec test-plan cases 6-12, 16, 17 (docs/plans/10-mm-pilot-prep.md section 10).
Fail-before: on origin/master InventoryTracker.can_trade is never consulted at
placement time, toxicity is permanently 0.0 (record_fill unwired), and none of
the MM_ pilot config keys exist — every test here fails on master.
"""

import re
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import importlib

import pytest

import config
import market_maker
from market_maker import ToxicFlowDetector, VolatilityTracker
from mm_pilot import KalshiMMPilot

from test_mm_pilot import (TICKER, FakeKalshiClient, RecordingHedger,
                           build_pilot, live_config, make_book, pilot_env,
                           clock)


def live_market_maker():
    """Resolve the LIVE market_maker module (see live_config)."""
    return importlib.import_module("market_maker")


# ---------------------------------------------------------------------------
# 6. Per-market USD cap enforced in the order path; reducing orders exempt
# ---------------------------------------------------------------------------

class TestPerMarketCap:
    def test_accumulating_order_past_usd_cap_rejected(self, pilot_env, clock):
        pilot = build_pilot(clock, client=FakeKalshiClient())
        # Drive net inventory to $99 (198 contracts @ $0.50)
        pilot.inventory.apply_fill(TICKER, "yes", "buy", 198, 0.50)
        assert pilot.inventory.net_usd(TICKER) == pytest.approx(99.0)
        # Next accumulating $10 quote -> rejected with the cap reason
        result = pilot.authorize_order(TICKER, "yes", "buy", 20, 0.50)
        assert result.allowed is False
        assert result.reason == "per_market_inventory_cap"

    def test_reducing_direction_order_still_allowed_at_cap(self, pilot_env,
                                                           clock):
        pilot = build_pilot(clock, client=FakeKalshiClient())
        pilot.inventory.apply_fill(TICKER, "yes", "buy", 198, 0.50)
        # Buying NO reduces |net| on fill: caps must never block the exit
        result = pilot.authorize_order(TICKER, "no", "buy", 20, 0.50)
        assert result.allowed is True
        # And an explicit reducing (hedge) order likewise
        hedge = pilot.authorize_order(TICKER, "yes", "sell", 20, 0.50,
                                      reducing=True)
        assert hedge.allowed is True
        assert hedge.reason == "ok_reducing"


# ---------------------------------------------------------------------------
# 7. Contract-unit cap binds independently at low prices
# ---------------------------------------------------------------------------

class TestContractCap:
    def test_contract_cap_rejects_before_usd_cap_at_low_price(self, pilot_env,
                                                              clock):
        pilot = build_pilot(clock, client=FakeKalshiClient())
        # 240 contracts @ $0.10 = only $24 of the $100 USD cap
        pilot.inventory.apply_fill(TICKER, "yes", "buy", 240, 0.10)
        result = pilot.authorize_order(TICKER, "yes", "buy", 20, 0.10)
        assert result.allowed is False
        assert result.reason == "per_market_contract_cap"


# ---------------------------------------------------------------------------
# 8. Total cap one-sides ALL markets (bids stopped, asks kept)
# ---------------------------------------------------------------------------

class TestTotalCap:
    TICKERS = ("KXAAA-1", "KXBBB-2", "KXCCC-3")

    def test_total_cap_stops_accumulating_side_everywhere(self, pilot_env,
                                                          clock):
        books = {t: make_book() for t in self.TICKERS}
        client = FakeKalshiClient(books=books)
        pilot = build_pilot(clock, client=client, selection=self.TICKERS)
        pilot.canary_graduated = True
        # ~$84 net long in each of 3 markets -> total $252 >= $250 cap
        for t in self.TICKERS:
            pilot.inventory.apply_fill(t, "yes", "buy", 168, 0.50)
        placed = pilot.refresh_all()
        assert placed, "reducing-side quotes must still be placed"
        purposes = {pilot._orders[oid]["purpose"] for oid in placed}
        assert "quote_bid" not in purposes   # accumulating side stopped
        assert purposes == {"quote_ask"}     # only inventory-reducing quotes
        # ...in every market
        quoted_markets = {pilot._orders[oid]["ticker"] for oid in placed}
        assert quoted_markets == set(self.TICKERS)

    def test_authorize_rejects_accumulating_order_at_total_cap(self, pilot_env,
                                                               clock):
        pilot = build_pilot(clock, client=FakeKalshiClient(),
                            selection=self.TICKERS)
        for t in self.TICKERS:
            pilot.inventory.apply_fill(t, "yes", "buy", 168, 0.50)
        result = pilot.authorize_order("KXAAA-1", "yes", "buy", 10, 0.50)
        assert result.allowed is False
        assert result.reason == "total_inventory_cap"


# ---------------------------------------------------------------------------
# 9. Gross cap: inventory at cost + resting notional <= $300
# ---------------------------------------------------------------------------

class TestGrossCap:
    def test_gross_over_300_rejected(self, pilot_env, clock):
        pilot = build_pilot(clock, client=FakeKalshiClient())
        # $60 net inventory + $230 resting notional
        pilot.inventory.apply_fill(TICKER, "yes", "buy", 120, 0.50)
        pilot._orders["resting_1"] = {
            "ticker": TICKER, "side": "yes", "action": "buy",
            "count": 460, "price": 0.50, "purpose": "quote_bid",
            "placed_at": clock[0],
        }
        # New $20 quote: 60 + 230 + 20 = $310 > $300 gross ceiling
        result = pilot.authorize_order(TICKER, "yes", "buy", 40, 0.50)
        assert result.allowed is False
        assert result.reason == "gross_cap"

    def test_gross_under_ceiling_allowed(self, pilot_env, clock):
        pilot = build_pilot(clock, client=FakeKalshiClient())
        pilot.inventory.apply_fill(TICKER, "yes", "buy", 120, 0.50)
        pilot._orders["resting_1"] = {
            "ticker": TICKER, "side": "yes", "action": "buy",
            "count": 400, "price": 0.50, "purpose": "quote_bid",
            "placed_at": clock[0],
        }
        # 60 + 200 + 20 = $280 <= $300
        result = pilot.authorize_order(TICKER, "yes", "buy", 40, 0.50)
        assert result.allowed is True


# ---------------------------------------------------------------------------
# 10. No bypass: place_order referenced exactly once in mm_pilot.py
# ---------------------------------------------------------------------------

class TestNoBypass:
    def test_place_order_referenced_exactly_once_in_pilot_source(self):
        src_path = os.path.join(os.path.dirname(__file__), "..", "mm_pilot.py")
        with open(src_path, encoding="utf-8") as fh:
            source = fh.read()
        refs = re.findall(r"_client\.place_order\(", source)
        assert len(refs) == 1, (
            "mm_pilot.py must reach kalshi_client.place_order in exactly one "
            f"place (behind authorize_order); found {len(refs)}"
        )
        # And that single reference sits inside place_pilot_order, which
        # authorizes first.
        idx = source.index("_client.place_order(")
        fn_start = source.rindex("def place_pilot_order", 0, idx)
        assert "authorize_order" in source[fn_start:idx]

    def test_rejected_order_never_reaches_the_client(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        pilot.inventory.apply_fill(TICKER, "yes", "buy", 198, 0.50)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 40, 0.50,
                                      purpose="quote_bid")
        assert oid is None
        assert client.place_order_calls == 0


# ---------------------------------------------------------------------------
# 11. Toxicity pause pulls quotes (fail-before: ratio stayed 0.0 on master)
# ---------------------------------------------------------------------------

class TestToxicityGate:
    @staticmethod
    def feed_fills(detector, adverse: int, total: int = 20, ticker=TICKER):
        for i in range(total):
            mid = 0.45 if i < adverse else 0.55  # bid fill, mid below = adverse
            detector.record_fill(ticker, "bid", 0.50, 2.0, mid)

    def test_toxic_ratio_over_threshold_pulls_quotes(self, pilot_env, clock):
        detector = ToxicFlowDetector()
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client, detector=detector)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        self.feed_fills(detector, adverse=13)  # 0.65 >= 0.60
        assert pilot.refresh_market(TICKER) == []
        assert oid in client.cancelled
        assert pilot.resting_orders(TICKER) == []
        # Observability scan surfaces the pause for the pilot tickers
        detector.trigger_pause(TICKER)
        from scans.toxic_flow_pause import scan_toxic_flow_pause
        opps = scan_toxic_flow_pause([TICKER], detector=detector)
        assert len(opps) == 1
        assert opps[0]["type"] == "ToxicFlowPause"

    def test_ratio_below_threshold_quotes_placed(self, pilot_env, clock):
        detector = ToxicFlowDetector()
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client, detector=detector)
        self.feed_fills(detector, adverse=11)  # 0.55 < 0.60
        placed = pilot.refresh_market(TICKER)
        assert placed != []

    def test_pilot_fills_feed_the_detector_and_arm_the_pause(self, pilot_env,
                                                             clock,
                                                             monkeypatch):
        monkeypatch.setattr(live_config(), "MM_CANARY_QUOTE_SIZE_USD", 100.0)
        detector = ToxicFlowDetector()
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client, detector=detector)
        # Seed 19 adverse fills; the 20th comes from the pilot's own poll.
        self.feed_fills(detector, adverse=19, total=19)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        from test_mm_pilot import kfill
        client.fills_script = [kfill(oid, count=4, created=clock[0])]
        pilot.poll_fills()
        assert detector.get_toxicity(TICKER) >= 0.60
        assert detector.get_pause_remaining(TICKER) > 0  # trigger_pause armed


# ---------------------------------------------------------------------------
# 12. Volatility: widen first (G9), pull at the ceiling (G8)
# ---------------------------------------------------------------------------

class FakeVolTracker:
    def __init__(self, multiplier):
        self.multiplier = multiplier

    def get_spread_multiplier(self, market_key, base_multiplier=1.0,
                              max_multiplier=3.0):
        return self.multiplier

    def record_price(self, market_key, price):
        pass


class TestVolatilityGate:
    def _pilot_with_multiplier(self, clock, multiplier):
        fake = FakeVolTracker(multiplier)
        # QuoteEngine reads the module singleton for widening; G8 reads the
        # injected tracker. Point both at the fake (on the LIVE module).
        live_market_maker()._volatility_tracker = fake
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client, vol=fake)
        return pilot, client

    def teardown_method(self, method):
        live_market_maker()._volatility_tracker = None

    def test_multiplier_two_widens_spread(self, pilot_env, clock):
        pilot, _client = self._pilot_with_multiplier(clock, 2.0)
        placed = pilot.refresh_market(TICKER)
        assert placed
        orders = {pilot._orders[oid]["purpose"]: pilot._orders[oid]
                  for oid in placed}
        bid = orders["quote_bid"]["price"]
        ask_yes_terms = 1.0 - orders["quote_ask"]["price"]
        base_spread = config.MM_MIN_SPREAD
        assert ask_yes_terms - bid > base_spread  # wider than base

    def test_multiplier_at_ceiling_pulls_quotes(self, pilot_env, clock):
        pilot, client = self._pilot_with_multiplier(clock, 2.6)  # >= 2.5
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        assert pilot.refresh_market(TICKER) == []
        assert oid in client.cancelled


# ---------------------------------------------------------------------------
# 16. Config invariants: pilot refuses live start without its preconditions
# ---------------------------------------------------------------------------

class TestConfigInvariants:
    @pytest.mark.parametrize("missing_flag", [
        "MM_AUTO_HEDGE_ENABLED",
        "MM_TOXIC_FLOW_ENABLED",
        "MM_VOLATILITY_ADJUSTED_ENABLED",
    ])
    def test_live_pilot_without_precondition_raises(self, monkeypatch,
                                                    missing_flag):
        cfg = live_config()
        monkeypatch.setattr(cfg, "MM_KALSHI_PILOT_ENABLED", True)
        monkeypatch.setattr(cfg, "DRY_RUN", False)
        for flag in ("MM_AUTO_HEDGE_ENABLED", "MM_TOXIC_FLOW_ENABLED",
                     "MM_VOLATILITY_ADJUSTED_ENABLED"):
            monkeypatch.setattr(cfg, flag, flag != missing_flag)
        with pytest.raises(cfg.ConfigError, match=missing_flag):
            cfg.validate_config()

    def test_live_pilot_with_all_preconditions_validates(self, monkeypatch):
        cfg = live_config()
        monkeypatch.setattr(cfg, "MM_KALSHI_PILOT_ENABLED", True)
        monkeypatch.setattr(cfg, "DRY_RUN", False)
        for flag in ("MM_AUTO_HEDGE_ENABLED", "MM_TOXIC_FLOW_ENABLED",
                     "MM_VOLATILITY_ADJUSTED_ENABLED"):
            monkeypatch.setattr(cfg, flag, True)
        cfg.validate_config()  # must not raise

    def test_dry_run_pilot_does_not_require_preconditions(self, monkeypatch):
        cfg = live_config()
        monkeypatch.setattr(cfg, "MM_KALSHI_PILOT_ENABLED", True)
        monkeypatch.setattr(cfg, "DRY_RUN", True)
        monkeypatch.setattr(cfg, "MM_AUTO_HEDGE_ENABLED", False)
        cfg.validate_config()  # D0 dry-run may start without them

    def test_pilot_without_kalshi_in_allowlist_raises(self, monkeypatch):
        cfg = live_config()
        monkeypatch.setattr(cfg, "MM_KALSHI_PILOT_ENABLED", True)
        monkeypatch.setattr(cfg, "ENABLED_EXECUTION_PLATFORMS",
                            frozenset({"polymarket"}))
        with pytest.raises(cfg.ConfigError, match="Kalshi-only"):
            cfg.validate_config()

    def test_cap_sanity_warnings(self, monkeypatch):
        cfg = live_config()
        monkeypatch.setattr(cfg, "MM_KALSHI_PILOT_ENABLED", True)
        monkeypatch.setattr(cfg, "MM_MAX_GROSS_PER_MARKET_USD", 500.0)
        monkeypatch.setattr(cfg, "MM_MAX_TOTAL_INVENTORY_USD", 400.0)
        warnings = cfg.validate_config()
        assert any("MM pilot cap sanity" in w for w in warnings)

    def test_pilot_flag_default_is_false(self):
        # Default must stay false — activation is operator-gated (D1/D2).
        cfg = live_config()
        assert os.getenv("MM_KALSHI_PILOT_ENABLED") in (None, "", "false") or \
            cfg.MM_KALSHI_PILOT_ENABLED is False


# ---------------------------------------------------------------------------
# 17. Crossing guard: never a marketable quote (post-only semantics)
# ---------------------------------------------------------------------------

class FixedQuoteEngine:
    def __init__(self, bid, ask):
        self._bid, self._ask = bid, ask

    def calculate_quotes(self, mid_price, inventory=0.0, max_inventory=0.0,
                         volatility=0.0, market_key=""):
        return {"bid": self._bid, "ask": self._ask,
                "spread": self._ask - self._bid, "skew": 0.0,
                "mid": mid_price}


class TestCrossingGuard:
    def test_crossing_bid_repriced_one_tick_inside(self, pilot_env, clock):
        # Best ask 0.52 (no_bid 0.48); engine wants bid 0.53 -> reprice 0.51
        client = FakeKalshiClient(
            books={TICKER: make_book(yes_bid=0.40, no_bid=0.48)})
        pilot = build_pilot(clock, client=client)
        pilot._quote_engine = FixedQuoteEngine(bid=0.53, ask=0.60)
        placed = pilot.refresh_market(TICKER)
        bids = [pilot._orders[oid] for oid in placed
                if pilot._orders[oid]["purpose"] == "quote_bid"]
        assert len(bids) == 1
        assert bids[0]["price"] == pytest.approx(0.51)
        assert bids[0]["price"] < 0.52  # never marketable

    def test_crossing_ask_repriced_or_skipped(self, pilot_env, clock):
        # Best yes bid 0.50; engine wants ask 0.45 -> reprice to 0.51
        client = FakeKalshiClient(
            books={TICKER: make_book(yes_bid=0.50, no_bid=0.40)})
        pilot = build_pilot(clock, client=client)
        pilot._quote_engine = FixedQuoteEngine(bid=0.30, ask=0.45)
        placed = pilot.refresh_market(TICKER)
        asks = [pilot._orders[oid] for oid in placed
                if pilot._orders[oid]["purpose"] == "quote_ask"]
        assert len(asks) == 1
        ask_yes_terms = 1.0 - asks[0]["price"]
        assert ask_yes_terms == pytest.approx(0.51)
        assert ask_yes_terms > 0.50  # never marketable


# ---------------------------------------------------------------------------
# G6/G11 companions: staleness and depth sizing
# ---------------------------------------------------------------------------

class TestBookGates:
    def test_stale_book_pulls_quotes(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        client.books = {}  # book feed dies: refresh can no longer update it
        clock[0] += 31  # past MM_BOOK_MAX_STALE_SECONDS
        assert pilot.refresh_market(TICKER) == []
        assert pilot.resting_orders(TICKER) == []
        assert oid in client.cancelled

    def test_live_pilot_without_client_places_nothing(self, pilot_env, clock):
        pilot = build_pilot(clock, client=None)
        pilot.update_book(TICKER, make_book())
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        assert oid is None  # fail closed, no AttributeError

    def test_depth_sizing_caps_quote_to_book_fraction(self, pilot_env, clock,
                                                      monkeypatch):
        monkeypatch.setattr(live_config(), "MM_CANARY_QUOTE_SIZE_USD", 50.0)
        # Same-side best size 8 -> depth cap = int(0.25 * 8) = 2 contracts
        client = FakeKalshiClient(
            books={TICKER: make_book(yes_qty=8.0, no_qty=8.0)})
        pilot = build_pilot(clock, client=client)
        placed = pilot.refresh_market(TICKER)
        assert placed
        for oid in placed:
            assert pilot._orders[oid]["count"] <= 2

    def test_depth_below_one_contract_skips_side(self, pilot_env, clock,
                                                 monkeypatch):
        monkeypatch.setattr(live_config(), "MM_CANARY_QUOTE_SIZE_USD", 50.0)
        # Best size 3 -> int(0.25 * 3) = 0 contracts -> side skipped
        client = FakeKalshiClient(
            books={TICKER: make_book(yes_qty=3.0, no_qty=500.0)})
        pilot = build_pilot(clock, client=client)
        placed = pilot.refresh_market(TICKER)
        purposes = {pilot._orders[oid]["purpose"] for oid in placed}
        assert "quote_bid" not in purposes

    def test_deselected_market_gracefully_exits(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        oid = pilot.place_pilot_order(TICKER, "yes", "buy", 4, 0.49,
                                      purpose="quote_bid")
        pilot.update_selection([])  # lip_select dropped the market
        assert pilot.refresh_market(TICKER) == []
        assert oid in client.cancelled

    def test_no_selection_snapshot_fails_closed(self, pilot_env, clock):
        client = FakeKalshiClient()
        pilot = build_pilot(clock, client=client)
        pilot._selected = None  # PR #43 selector never ran
        assert pilot.refresh_market(TICKER) == []
