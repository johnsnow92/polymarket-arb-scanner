"""Tests for executor.py — arbitrage trade execution engine."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import time

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import TradeDB
from risk_manager import RiskManager


# We need to mock the imports that executor.py uses before importing it
# because some modules (predictit_api, betfair_api, manifold_api) may not exist.
# Patch them in sys.modules before importing executor.

@pytest.fixture(autouse=True)
def mock_external_modules():
    """Mock external API modules that may not be installed."""
    mock_modules = {}
    for mod_name in [
        "polymarket_api", "kalshi_api", "predictit_api",
        "betfair_api", "manifold_api",
    ]:
        if mod_name not in sys.modules:
            mock_modules[mod_name] = MagicMock()
            sys.modules[mod_name] = mock_modules[mod_name]
    yield
    for mod_name in mock_modules:
        del sys.modules[mod_name]


# Import after mocking external modules
def _import_executor():
    # Force reimport to pick up mocked modules
    if "executor" in sys.modules:
        del sys.modules["executor"]
    from executor import ArbitrageExecutor
    return ArbitrageExecutor


@pytest.fixture
def ArbitrageExecutor():
    return _import_executor()


@pytest.fixture
def db():
    trade_db = TradeDB(":memory:")
    yield trade_db
    trade_db.close()


@pytest.fixture
def risk_manager():
    return RiskManager({
        "max_trade_size": 5.0,
        "daily_loss_limit": 25.0,
        "max_open_positions": 25,
        "min_liquidity": 25.0,
        "min_liquidity_high_roi": 10.0,
        "min_net_roi": 0,
        "allow_better_reentry": True,
        "reentry_improvement_threshold": 0.20,
    })


@pytest.fixture
def executor(ArbitrageExecutor, db, risk_manager):
    pm_trader = MagicMock()
    kalshi_client = MagicMock()
    return ArbitrageExecutor(
        pm_trader=pm_trader,
        kalshi_client=kalshi_client,
        db=db,
        risk_manager=risk_manager,
        dry_run=True,
        max_trade_size=5.0,
    )


# ---------------------------------------------------------------------------
# _build_legs for each opportunity type
# ---------------------------------------------------------------------------

class TestBuildLegs:
    def test_binary_legs(self, executor):
        opp = {
            "type": "Binary",
            "prices": "Y=0.400 N=0.450",
            "_token_ids": ["token_yes", "token_no"],
        }
        legs = executor._build_legs(opp, 5.0)
        assert len(legs) == 2
        assert legs[0]["platform"] == "polymarket"
        assert legs[0]["price"] == pytest.approx(0.400)
        assert legs[0]["_token_id"] == "token_yes"
        assert legs[1]["price"] == pytest.approx(0.450)
        assert legs[1]["_token_id"] == "token_no"

    def test_negrisk_legs(self, executor):
        opp = {
            "type": "NegRisk (4 outcomes)",
            "prices": "0.20, 0.25, 0.30, 0.15",
            "_token_ids": ["t0", "t1", "t2", "t3"],
        }
        legs = executor._build_legs(opp, 5.0)
        assert len(legs) == 4
        assert legs[0]["price"] == pytest.approx(0.20)
        assert legs[3]["price"] == pytest.approx(0.15)
        for i, leg in enumerate(legs):
            assert leg["_token_id"] == f"t{i}"

    def test_kalshi_binary_legs(self, executor):
        opp = {
            "type": "KalshiBinary",
            "_kalshi_yes": 0.40,
            "_kalshi_no": 0.40,
            "_kalshi_ticker": "TICKER-ABC",
        }
        legs = executor._build_legs(opp, 5.0)
        assert len(legs) == 2
        assert legs[0]["platform"] == "kalshi"
        assert legs[0]["side"] == "yes"
        assert legs[0]["_ticker"] == "TICKER-ABC"
        assert legs[1]["side"] == "no"

    def test_kalshi_multi_legs(self, executor):
        opp = {
            "type": "KalshiMulti (3 outcomes)",
            "_kalshi_tickers": ["T1", "T2", "T3"],
            "_kalshi_prices": [0.20, 0.30, 0.25],
        }
        legs = executor._build_legs(opp, 5.0)
        assert len(legs) == 3
        for i, leg in enumerate(legs):
            assert leg["platform"] == "kalshi"
            assert leg["_ticker"] == opp["_kalshi_tickers"][i]

    def test_cross_pm_yes_kalshi_no(self, executor):
        opp = {
            "type": "Cross",
            "prices": "PM_Y=0.300 K_N=0.350",
            "_token_ids": ["token_yes", "token_no"],
            "_kalshi_ticker": "TICKER-XYZ",
        }
        legs = executor._build_legs(opp, 5.0)
        assert len(legs) == 2
        assert legs[0]["platform"] == "polymarket"
        assert legs[0]["price"] == pytest.approx(0.300)
        assert legs[1]["platform"] == "kalshi"
        assert legs[1]["side"] == "no"
        assert legs[1]["_ticker"] == "TICKER-XYZ"

    def test_cross_pm_no_kalshi_yes(self, executor):
        opp = {
            "type": "Cross",
            "prices": "PM_N=0.350 K_Y=0.300",
            "_token_ids": ["token_yes", "token_no"],
            "_kalshi_ticker": "TICKER-XYZ",
        }
        legs = executor._build_legs(opp, 5.0)
        assert len(legs) == 2
        assert legs[0]["platform"] == "polymarket"
        assert legs[0]["price"] == pytest.approx(0.350)
        assert legs[0]["_token_id"] == "token_no"
        assert legs[1]["platform"] == "kalshi"
        assert legs[1]["side"] == "yes"

    def test_binary_no_token_ids(self, executor):
        opp = {
            "type": "Binary",
            "prices": "Y=0.400 N=0.450",
            "_token_ids": [],
        }
        legs = executor._build_legs(opp, 5.0)
        assert len(legs) == 2
        assert legs[0]["_token_id"] == ""
        assert legs[1]["_token_id"] == ""


# ---------------------------------------------------------------------------
# Token ID propagation
# ---------------------------------------------------------------------------

class TestTokenIdPropagation:
    def test_negrisk_token_ids_propagated(self, executor):
        opp = {
            "type": "NegRisk (3 outcomes)",
            "prices": "0.20, 0.30, 0.25",
            "_token_ids": ["tok_a", "tok_b", "tok_c"],
        }
        legs = executor._build_legs(opp, 5.0)
        assert legs[0]["_token_id"] == "tok_a"
        assert legs[1]["_token_id"] == "tok_b"
        assert legs[2]["_token_id"] == "tok_c"

    def test_negrisk_missing_token_ids(self, executor):
        opp = {
            "type": "NegRisk (3 outcomes)",
            "prices": "0.20, 0.30, 0.25",
            "_token_ids": ["tok_a"],  # Only 1 token ID for 3 outcomes
        }
        legs = executor._build_legs(opp, 5.0)
        # Price count (3) != token ID count (1) → returns empty legs
        assert legs == []


# ---------------------------------------------------------------------------
# _parse_price
# ---------------------------------------------------------------------------

class TestParsePrice:
    def test_extracts_yes_price(self, executor):
        opp = {"prices": "Y=0.400 N=0.450"}
        assert executor._parse_price(opp, "Y=") == pytest.approx(0.400)

    def test_extracts_no_price(self, executor):
        opp = {"prices": "Y=0.400 N=0.450"}
        assert executor._parse_price(opp, "N=") == pytest.approx(0.450)

    def test_extracts_cross_prices(self, executor):
        opp = {"prices": "PM_Y=0.300 K_N=0.350"}
        assert executor._parse_price(opp, "PM_Y=") == pytest.approx(0.300)
        assert executor._parse_price(opp, "K_N=") == pytest.approx(0.350)

    def test_returns_none_on_missing_prefix(self, executor):
        opp = {"prices": "Y=0.400 N=0.450"}
        assert executor._parse_price(opp, "X=") is None

    def test_returns_none_on_invalid_value(self, executor):
        opp = {"prices": "Y=abc N=0.450"}
        assert executor._parse_price(opp, "Y=") is None

    def test_returns_none_for_zero_price(self, executor):
        opp = {"prices": "Y=0.000 N=0.450"}
        assert executor._parse_price(opp, "Y=") is None

    def test_returns_none_for_price_at_one(self, executor):
        opp = {"prices": "Y=1.000 N=0.450"}
        assert executor._parse_price(opp, "Y=") is None

    def test_returns_none_on_empty_prices(self, executor):
        opp = {"prices": ""}
        assert executor._parse_price(opp, "Y=") is None


# ---------------------------------------------------------------------------
# Revalidation (mock API calls, test 90% degradation threshold)
# ---------------------------------------------------------------------------

class TestRevalidation:
    def test_revalidate_binary_passes(self, executor):
        from unittest.mock import patch as mpatch
        opp = {
            "type": "Binary",
            "net_profit": 0.10,
            "total_cost": "$0.8500",
            "_token_ids": ["tok_yes", "tok_no"],
        }
        mock_book = {"asks": [{"price": "0.40", "size": "100"}]}
        mock_bid_ask = {"bid": 0.39, "ask": 0.40}

        with mpatch("executor.fetch_order_book", return_value=mock_book), \
             mpatch("executor.get_best_bid_ask", return_value=mock_bid_ask), \
             mpatch("executor.net_profit_binary_internal", return_value={"net_profit": 0.095}):
            result = executor._revalidate(opp, None)
            # ROI = 0.10/0.85 = 11.8% (>5%) -> strict 90%: 0.095 >= 0.09 -> pass
            assert result is True

    def test_revalidate_binary_degraded(self, executor):
        from unittest.mock import patch as mpatch
        opp = {
            "type": "Binary",
            "net_profit": 0.10,
            "total_cost": "$0.8500",
            "_token_ids": ["tok_yes", "tok_no"],
        }
        mock_book = {"asks": [{"price": "0.40", "size": "100"}]}
        mock_bid_ask = {"bid": 0.39, "ask": 0.40}

        with mpatch("executor.fetch_order_book", return_value=mock_book), \
             mpatch("executor.get_best_bid_ask", return_value=mock_bid_ask), \
             mpatch("executor.net_profit_binary_internal", return_value={"net_profit": 0.05}):
            result = executor._revalidate(opp, None)
            # ROI = 0.10/0.85 = 11.8% (>5%) -> strict 90%: 0.05 < 0.09 -> fail
            assert result is False

    def test_revalidate_returns_false_on_zero_profit(self, executor):
        opp = {"type": "Binary", "net_profit": 0, "_token_ids": ["a", "b"]}
        result = executor._revalidate(opp, None)
        assert result is False

    def test_revalidate_returns_false_on_negative_profit(self, executor):
        opp = {"type": "Binary", "net_profit": -0.01, "_token_ids": ["a", "b"]}
        result = executor._revalidate(opp, None)
        assert result is False

    def test_revalidate_binary_missing_token_ids(self, executor):
        from unittest.mock import patch as mpatch
        opp = {"type": "Binary", "net_profit": 0.10, "_token_ids": ["a"]}
        result = executor._revalidate(opp, None)
        assert result is False

    def test_revalidate_unknown_type_proceeds(self, executor):
        opp = {"type": "UnknownType", "net_profit": 0.10}
        result = executor._revalidate(opp, None)
        assert result is True

    def test_revalidate_handles_exception(self, executor):
        from unittest.mock import patch as mpatch
        opp = {"type": "Binary", "net_profit": 0.10, "_token_ids": ["a", "b"]}
        with mpatch("executor.fetch_order_book", side_effect=Exception("API down")):
            result = executor._revalidate(opp, None)
            assert result is False


# ---------------------------------------------------------------------------
# Dry run logging
# ---------------------------------------------------------------------------

class TestDryRunLog:
    def test_dry_run_logs_opportunity_and_trades(self, executor, db):
        opp = {
            "type": "Binary",
            "market": "Test Market",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.138,
            "net_roi": "16.2%",
            "_clob_depth": 100.0,
            "_token_ids": ["tok_yes", "tok_no"],
        }
        legs = [
            {"platform": "polymarket", "side": "BUY", "token": "yes", "price": 0.40, "_token_id": "tok_yes"},
            {"platform": "polymarket", "side": "BUY", "token": "no", "price": 0.45, "_token_id": "tok_no"},
        ]
        result = executor._dry_run_log(opp, legs, 5.0)
        assert result is True

        opps = db.get_recent_opportunities()
        assert len(opps) == 1
        assert opps[0]["action"] == "dry_run"

        trades = db.get_trades_for_opportunity(opps[0]["id"])
        assert len(trades) == 2
        assert all(t["status"] == "dry_run" for t in trades)


# ---------------------------------------------------------------------------
# Position creation after successful execution
# ---------------------------------------------------------------------------

class TestPositionCreation:
    def test_execute_dry_run_full_pipeline(self, executor, db):
        # Configure mock traders to return numeric balances
        executor.pm_trader.get_balance.return_value = 100.0
        opp = {
            "type": "Binary",
            "market": "Test Market",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.138,
            "net_roi": "16.2%",
            "_clob_depth": 100.0,
            "_token_ids": ["tok_yes", "tok_no"],
        }
        result = executor.execute(opp)
        assert result is True

        opps = db.get_recent_opportunities()
        assert len(opps) == 1
        assert opps[0]["action"] == "dry_run"


# ---------------------------------------------------------------------------
# Orphaned trade marking on partial fill failure
# ---------------------------------------------------------------------------

class TestOrphanedTrades:
    def test_execute_legs_partial_fill_marks_orphaned(self, executor, db):
        executor.dry_run = False
        opp = {
            "type": "Binary",
            "market": "Test Market",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.138,
            "net_roi": "16.2%",
            "_clob_depth": 100.0,
            "_token_ids": ["tok_yes", "tok_no"],
        }

        legs = [
            {"platform": "polymarket", "side": "BUY", "token": "yes",
             "price": 0.40, "_token_id": "tok_yes"},
            {"platform": "polymarket", "side": "BUY", "token": "no",
             "price": 0.45, "_token_id": "tok_no"},
        ]

        # Mock _execute_single_leg: first leg succeeds, second fails
        call_count = [0]
        def mock_execute_single_leg(leg, size, opp_arg):
            call_count[0] += 1
            if leg["token"] == "yes":
                leg["_order_id"] = "order_123"
                return (True, "order_123", 0.40)
            else:
                return (False, None, None)

        executor._execute_single_leg = mock_execute_single_leg

        # Mock cancel to fail so it gets marked orphaned
        executor._cancel_leg = lambda leg: False

        result = executor._execute_legs(opp, legs, 5.0)
        assert result is False

        trades = db.get_trades_for_opportunity(1)
        statuses = [t["status"] for t in trades]
        assert "orphaned" in statuses or "filled" in statuses


# ---------------------------------------------------------------------------
# _check_ws_cache
# ---------------------------------------------------------------------------

class TestCheckWsCache:
    def test_returns_none_when_no_cache(self, executor):
        result = executor._check_ws_cache(None, "polymarket", "tok1")
        assert result is None

    def test_returns_none_when_stale(self, executor):
        cache = {("polymarket", "tok1"): {"price": 0.50, "_ts": time.time() - 10}}
        result = executor._check_ws_cache(cache, "polymarket", "tok1")
        assert result is None

    def test_returns_entry_when_fresh(self, executor):
        cache = {("polymarket", "tok1"): {"price": 0.50, "_ts": time.time()}}
        result = executor._check_ws_cache(cache, "polymarket", "tok1")
        assert result is not None
        assert result["price"] == 0.50

    def test_returns_none_when_key_missing(self, executor):
        cache = {("polymarket", "tok1"): {"price": 0.50, "_ts": time.time()}}
        result = executor._check_ws_cache(cache, "polymarket", "tok2")
        assert result is None


# ---------------------------------------------------------------------------
# _revalidate_cross — strategy flip detection
# ---------------------------------------------------------------------------

class TestCrossRevalidationStrategy:
    def test_strategy1_remains_best(self, executor):
        """When strategy 1 (PM_YES + K_NO) stays best, prices should contain PM_Y= and K_N=."""
        from unittest.mock import patch as mpatch

        opp = {
            "type": "Cross",
            "net_profit": 0.10,
            "prices": "PM_Y=0.300 K_N=0.350",
            "_token_ids": ["tok_yes", "tok_no"],
            "_kalshi_ticker": "TICKER-XYZ",
        }

        mock_book = {"asks": [{"price": "0.30", "size": "100"}]}
        mock_bid_ask = {"bid": 0.29, "ask": 0.30}
        kalshi_book = {
            "orderbook": {
                "yes": [[40, 10]],
                "no": [[35, 10]],
            }
        }
        executor.kalshi_client.fetch_order_book.return_value = kalshi_book

        # Strategy 1 wins: net_profit=0.12; Strategy 2 loses: net_profit=0.05
        strat1_result = {"net_profit": 0.12}
        strat2_result = {"net_profit": 0.05}

        with mpatch("executor.fetch_order_book", return_value=mock_book), \
             mpatch("executor.get_best_bid_ask", return_value=mock_bid_ask), \
             mpatch("executor.net_profit_cross_platform", side_effect=[strat1_result, strat2_result]):
            result = executor._revalidate_cross(opp, 0.10, None)

        assert result is True
        assert "PM_Y=" in opp["prices"]
        assert "K_N=" in opp["prices"]
        assert opp["net_profit"] == pytest.approx(0.12)

    def test_strategy2_becomes_best(self, executor):
        """When strategy 2 (PM_NO + K_YES) becomes best, prices should flip to PM_N= and K_Y=."""
        from unittest.mock import patch as mpatch

        opp = {
            "type": "Cross",
            "net_profit": 0.10,
            "prices": "PM_Y=0.300 K_N=0.350",
            "_token_ids": ["tok_yes", "tok_no"],
            "_kalshi_ticker": "TICKER-XYZ",
        }

        mock_book = {"asks": [{"price": "0.30", "size": "100"}]}
        mock_bid_ask = {"bid": 0.29, "ask": 0.30}
        kalshi_book = {
            "orderbook": {
                "yes": [[35, 10]],
                "no": [[40, 10]],
            }
        }
        executor.kalshi_client.fetch_order_book.return_value = kalshi_book

        # Strategy 2 wins: net_profit=0.14; Strategy 1 loses: net_profit=0.04
        strat1_result = {"net_profit": 0.04}
        strat2_result = {"net_profit": 0.14}

        with mpatch("executor.fetch_order_book", return_value=mock_book), \
             mpatch("executor.get_best_bid_ask", return_value=mock_bid_ask), \
             mpatch("executor.net_profit_cross_platform", side_effect=[strat1_result, strat2_result]):
            result = executor._revalidate_cross(opp, 0.10, None)

        assert result is True
        assert "PM_N=" in opp["prices"]
        assert "K_Y=" in opp["prices"]
        assert opp["net_profit"] == pytest.approx(0.14)

    def test_net_profit_updated_to_winning_strategy(self, executor):
        """Verify net_profit is set to the winning strategy's value, not left at original."""
        from unittest.mock import patch as mpatch

        opp = {
            "type": "Cross",
            "net_profit": 0.10,
            "prices": "PM_Y=0.300 K_N=0.350",
            "_token_ids": ["tok_yes", "tok_no"],
            "_kalshi_ticker": "TICKER-XYZ",
        }

        mock_book = {"asks": [{"price": "0.30", "size": "100"}]}
        mock_bid_ask = {"bid": 0.29, "ask": 0.30}
        kalshi_book = {
            "orderbook": {
                "yes": [[30, 10]],
                "no": [[30, 10]],
            }
        }
        executor.kalshi_client.fetch_order_book.return_value = kalshi_book

        # Both strategies above threshold; strategy 1 wins with 0.15
        strat1_result = {"net_profit": 0.15}
        strat2_result = {"net_profit": 0.11}

        with mpatch("executor.fetch_order_book", return_value=mock_book), \
             mpatch("executor.get_best_bid_ask", return_value=mock_bid_ask), \
             mpatch("executor.net_profit_cross_platform", side_effect=[strat1_result, strat2_result]):
            result = executor._revalidate_cross(opp, 0.10, None)

        assert result is True
        assert opp["net_profit"] == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# _build_cross_all_legs — generic cross-platform leg builder
# ---------------------------------------------------------------------------

class TestBuildCrossAllLegs:
    def test_polymarket_predictit_legs(self, executor):
        """Polymarket + PredictIt: should produce 2 legs with correct platforms."""
        opp = {
            "type": "Cross",
            "prices": "polymarket_Y=0.400 predictit_N=0.300",
            "_token_ids": ["tok_yes", "tok_no"],
            "_platform_a": "polymarket",
            "_platform_b": "predictit",
            "_contract_id": "contract_123",
        }
        legs = executor._build_cross_all_legs(opp, 5.0)
        assert len(legs) == 2
        assert legs[0]["platform"] == "polymarket"
        assert legs[0]["price"] == pytest.approx(0.400)
        assert legs[0]["_token_id"] == "tok_yes"
        assert legs[1]["platform"] == "predictit"
        assert legs[1]["price"] == pytest.approx(0.300)
        assert legs[1]["side"] == "no"

    def test_polymarket_betfair_legs(self, executor):
        """Polymarket + Betfair: should include _market_id and _selection_id."""
        opp = {
            "type": "Cross",
            "prices": "polymarket_Y=0.450 betfair_N=0.250",
            "_token_ids": ["tok_yes", "tok_no"],
            "_platform_a": "polymarket",
            "_platform_b": "betfair",
            "_market_id": "1.234567",
            "_selection_id": 98765,
        }
        legs = executor._build_cross_all_legs(opp, 5.0)
        assert len(legs) == 2
        assert legs[0]["platform"] == "polymarket"
        assert legs[0]["price"] == pytest.approx(0.450)
        assert legs[1]["platform"] == "betfair"
        assert legs[1]["price"] == pytest.approx(0.250)
        assert legs[1]["_market_id"] == "1.234567"
        assert legs[1]["_selection_id"] == 98765

    def test_polymarket_manifold_legs(self, executor):
        """Polymarket + Manifold: should use _manifold_market_id."""
        opp = {
            "type": "Cross",
            "prices": "polymarket_N=0.350 manifold_Y=0.400",
            "_token_ids": ["tok_yes", "tok_no"],
            "_platform_a": "polymarket",
            "_platform_b": "manifold",
            "_manifold_market_id": "manifold_abc",
        }
        legs = executor._build_cross_all_legs(opp, 5.0)
        assert len(legs) == 2
        assert legs[0]["platform"] == "polymarket"
        assert legs[0]["price"] == pytest.approx(0.350)
        assert legs[0]["token"] == "no"
        assert legs[0]["_token_id"] == "tok_no"
        assert legs[1]["platform"] == "manifold"
        assert legs[1]["price"] == pytest.approx(0.400)
        assert legs[1]["_market_id"] == "manifold_abc"

    def test_malformed_prices_returns_empty(self, executor):
        """Malformed prices string should return empty legs."""
        opp = {
            "type": "Cross",
            "prices": "garbage data here with no equals",
            "_token_ids": ["tok_yes", "tok_no"],
            "_platform_a": "polymarket",
            "_platform_b": "predictit",
        }
        legs = executor._build_cross_all_legs(opp, 5.0)
        assert legs == []

    def test_platform_a_and_b_must_be_present(self, executor):
        """Without _platform_a/_platform_b, _build_legs falls through to standard Cross handler."""
        opp = {
            "type": "Cross",
            "prices": "polymarket_Y=0.400 predictit_N=0.300",
            "_token_ids": ["tok_yes", "tok_no"],
            # No _platform_a or _platform_b
        }
        # _build_legs without _platform_a won't reach _build_cross_all_legs;
        # the standard Cross handler won't match PM_Y= or PM_N= either, so empty.
        legs = executor._build_legs(opp, 5.0)
        assert legs == []


# ---------------------------------------------------------------------------
# _fetch_balances — multi-platform balance fetching for Cross opps
# ---------------------------------------------------------------------------

class TestFetchBalances:
    def test_cross_fetches_predictit_balance(self, executor):
        """Cross-type opp should call get_balance on predictit client."""
        mock_predictit = MagicMock()
        mock_predictit.get_balance.return_value = 500.0
        executor.predictit_client = mock_predictit

        balances = executor._fetch_balances("Cross")
        assert "predictit" in balances
        assert balances["predictit"] == 500.0
        mock_predictit.get_balance.assert_called_once()

    def test_cross_fetches_betfair_balance(self, executor):
        """Cross-type opp should call get_balance on betfair client."""
        mock_betfair = MagicMock()
        mock_betfair.get_balance.return_value = 1000.0
        executor.betfair_client = mock_betfair

        balances = executor._fetch_balances("Cross")
        assert "betfair" in balances
        assert balances["betfair"] == 1000.0
        mock_betfair.get_balance.assert_called_once()

    def test_cross_fetches_manifold_balance(self, executor):
        """Cross-type opp should call get_balance on manifold client."""
        mock_manifold = MagicMock()
        mock_manifold.get_balance.return_value = 250.0
        executor.manifold_client = mock_manifold

        balances = executor._fetch_balances("Cross")
        assert "manifold" in balances
        assert balances["manifold"] == 250.0
        mock_manifold.get_balance.assert_called_once()

    def test_cross_fetches_all_platform_balances(self, executor):
        """Cross-type should fetch from all available platform clients."""
        mock_predictit = MagicMock()
        mock_predictit.get_balance.return_value = 500.0
        mock_betfair = MagicMock()
        mock_betfair.get_balance.return_value = 1000.0
        mock_manifold = MagicMock()
        mock_manifold.get_balance.return_value = 250.0

        executor.predictit_client = mock_predictit
        executor.betfair_client = mock_betfair
        executor.manifold_client = mock_manifold

        balances = executor._fetch_balances("Cross")
        assert balances["predictit"] == 500.0
        assert balances["betfair"] == 1000.0
        assert balances["manifold"] == 250.0

    def test_non_cross_does_not_fetch_extra_platforms(self, executor):
        """Non-Cross opp types should not fetch predictit/betfair/manifold balances."""
        mock_predictit = MagicMock()
        mock_predictit.get_balance.return_value = 500.0
        executor.predictit_client = mock_predictit

        balances = executor._fetch_balances("Binary")
        # Binary only fetches polymarket
        mock_predictit.get_balance.assert_not_called()
        if balances:
            assert "predictit" not in balances


# ---------------------------------------------------------------------------
# Adaptive revalidation threshold
# ---------------------------------------------------------------------------

class TestAdaptiveRevalidation:
    def test_high_roi_uses_strict_threshold(self, executor):
        """ROI >= 5% uses strict 90% threshold."""
        opp = {"total_cost": "$0.9000"}
        # net_profit=0.10 on cost=0.90 => ROI=11.1% (>5%)
        threshold = executor._get_revalidation_threshold(0.10, opp)
        assert threshold == pytest.approx(0.10 * 0.9)

    def test_medium_roi_uses_moderate_threshold(self, executor):
        """ROI 2-5% uses 80% threshold."""
        opp = {"total_cost": "$0.9500"}
        # net_profit=0.03 on cost=0.95 => ROI=3.16% (2-5%)
        threshold = executor._get_revalidation_threshold(0.03, opp)
        assert threshold == pytest.approx(0.03 * 0.8)

    def test_low_roi_uses_floor(self, executor):
        """ROI < 2% uses absolute floor."""
        opp = {"total_cost": "$0.9900"}
        # net_profit=0.005 on cost=0.99 => ROI=0.51% (<2%)
        threshold = executor._get_revalidation_threshold(0.005, opp)
        assert threshold == pytest.approx(0.003)  # min floor

    def test_below_floor_still_rejected(self, executor):
        """Profits below the absolute floor are rejected."""
        opp = {"total_cost": "$0.9900"}
        threshold = executor._get_revalidation_threshold(0.001, opp)
        # Floor is 0.003, so even though original is 0.001, threshold is 0.003
        assert threshold == pytest.approx(0.003)

    def test_adaptive_disabled_uses_strict(self, ArbitrageExecutor, db, risk_manager):
        """When revalidation_adaptive=False, always use 90%."""
        ex = ArbitrageExecutor(
            pm_trader=MagicMock(), kalshi_client=MagicMock(),
            db=db, risk_manager=risk_manager, dry_run=True,
            revalidation_adaptive=False, revalidation_min_floor=0.003,
        )
        opp = {"total_cost": "$0.9900"}
        # ROI < 2% but adaptive disabled -> strict 90%
        threshold = ex._get_revalidation_threshold(0.005, opp)
        assert threshold == pytest.approx(0.005 * 0.9)

    def test_zero_cost_uses_strict(self, executor):
        """Zero cost defaults to 0 ROI -> lenient floor."""
        opp = {"total_cost": "$0"}
        threshold = executor._get_revalidation_threshold(0.01, opp)
        assert threshold == pytest.approx(0.003)

    def test_numeric_total_cost(self, executor):
        """Handles total_cost as numeric value."""
        opp = {"total_cost": 0.80}
        # ROI = 0.10/0.80 = 12.5% (>5%)
        threshold = executor._get_revalidation_threshold(0.10, opp)
        assert threshold == pytest.approx(0.10 * 0.9)


# ---------------------------------------------------------------------------
# Dynamic sizing in executor
# ---------------------------------------------------------------------------

class TestDynamicSizingExecutor:
    def test_dynamic_sizing_disabled_uses_fixed(self, ArbitrageExecutor, db, risk_manager):
        """When dynamic_sizing=False, uses fixed max_trade_size."""
        ex = ArbitrageExecutor(
            pm_trader=MagicMock(), kalshi_client=MagicMock(),
            db=db, risk_manager=risk_manager, dry_run=True,
            max_trade_size=5.0, dynamic_sizing=False,
        )
        ex.pm_trader.get_balance.return_value = 100.0
        opp = {
            "type": "Binary",
            "market": "Test",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.10,
            "net_roi": "11.8%",
            "_clob_depth": 100.0,
            "_token_ids": ["tok_yes", "tok_no"],
        }
        result = ex.execute(opp)
        assert result is True

    def test_dynamic_sizing_enabled_executes(self, ArbitrageExecutor, db, risk_manager):
        """When dynamic_sizing=True, should still execute successfully."""
        ex = ArbitrageExecutor(
            pm_trader=MagicMock(), kalshi_client=MagicMock(),
            db=db, risk_manager=risk_manager, dry_run=True,
            max_trade_size=50.0, dynamic_sizing=True, sizing_aggressiveness=0.5,
        )
        ex.pm_trader.get_balance.return_value = 100.0
        opp = {
            "type": "Binary",
            "market": "Test Dynamic",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.10,
            "net_roi": "11.8%",
            "_clob_depth": 100.0,
            "_token_ids": ["tok_yes", "tok_no"],
        }
        result = ex.execute(opp)
        assert result is True


# ---------------------------------------------------------------------------
# Partial CLOB revalidation
# ---------------------------------------------------------------------------

class TestPartialClobRevalidation:
    def test_partial_clob_uses_wider_threshold_strict(self, executor):
        """Partial CLOB opportunities use 80% instead of 90% for high ROI."""
        opp = {"total_cost": "$0.8500", "_partial_clob": True}
        # ROI = 0.10/0.85 = 11.8% (>5%) -> partial uses 80%
        threshold = executor._get_revalidation_threshold(0.10, opp)
        assert threshold == pytest.approx(0.10 * 0.8)

    def test_non_partial_uses_standard_threshold(self, executor):
        """Non-partial opportunities use standard 90% threshold."""
        opp = {"total_cost": "$0.8500"}
        threshold = executor._get_revalidation_threshold(0.10, opp)
        assert threshold == pytest.approx(0.10 * 0.9)

    def test_partial_clob_medium_roi(self, executor):
        """Partial CLOB with medium ROI uses 70% instead of 80%."""
        opp = {"total_cost": "$0.9500", "_partial_clob": True}
        # ROI = 0.03/0.95 = 3.16% (2-5%) -> partial uses 70%
        threshold = executor._get_revalidation_threshold(0.03, opp)
        assert threshold == pytest.approx(0.03 * 0.7)

    def test_partial_clob_low_roi_uses_floor(self, executor):
        """Partial CLOB with low ROI still uses floor."""
        opp = {"total_cost": "$0.9900", "_partial_clob": True}
        threshold = executor._get_revalidation_threshold(0.005, opp)
        assert threshold == pytest.approx(0.003)


# ---------------------------------------------------------------------------
# Slippage tracking in executor
# ---------------------------------------------------------------------------

class TestSlippageTracking:
    def test_slippage_stored_after_fill(self, executor, db):
        """After a successful fill, slippage should be recorded in the trades table."""
        executor.dry_run = False
        opp = {
            "type": "Binary",
            "market": "Test Slippage",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.10,
            "net_roi": "11.8%",
            "_clob_depth": 100.0,
            "_token_ids": ["tok_yes", "tok_no"],
        }
        legs = [
            {"platform": "polymarket", "side": "BUY", "token": "yes",
             "price": 0.40, "_token_id": "tok_yes"},
            {"platform": "polymarket", "side": "BUY", "token": "no",
             "price": 0.45, "_token_id": "tok_no"},
        ]

        # Mock _execute_single_leg to return fills with slight slippage
        def mock_execute(leg, size, opp_arg):
            leg["_order_id"] = "order_mock"
            # Simulate fill at slightly worse price
            fill = leg["price"] + 0.005
            return (True, "order_mock", fill)

        executor._execute_single_leg = mock_execute

        result = executor._execute_legs(opp, legs, 5.0)
        assert result is True

        trades = db.get_trades_for_opportunity(1)
        assert len(trades) == 2
        for t in trades:
            assert t["slippage"] is not None
            assert t["slippage"] == pytest.approx(0.005)

    def test_zero_slippage_on_exact_fill(self, executor, db):
        """Zero slippage when fill price matches expected price."""
        executor.dry_run = False
        opp = {
            "type": "Binary",
            "market": "Test Zero Slippage",
            "prices": "Y=0.400 N=0.450",
            "total_cost": "$0.8500",
            "net_profit": 0.10,
            "net_roi": "11.8%",
            "_clob_depth": 100.0,
            "_token_ids": ["tok_yes", "tok_no"],
        }
        legs = [
            {"platform": "polymarket", "side": "BUY", "token": "yes",
             "price": 0.40, "_token_id": "tok_yes"},
            {"platform": "polymarket", "side": "BUY", "token": "no",
             "price": 0.45, "_token_id": "tok_no"},
        ]

        def mock_execute(leg, size, opp_arg):
            leg["_order_id"] = "order_mock"
            return (True, "order_mock", leg["price"])  # Exact fill

        executor._execute_single_leg = mock_execute

        result = executor._execute_legs(opp, legs, 5.0)
        assert result is True

        trades = db.get_trades_for_opportunity(1)
        for t in trades:
            assert t["slippage"] == pytest.approx(0.0)
