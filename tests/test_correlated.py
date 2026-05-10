"""Tests for scans/correlated.py — correlated market pairs arbitrage."""

import pytest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Mock setup
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cleanup_modules():
    """Clean up sys.modules after each test to prevent cross-test pollution."""
    yield
    sys.modules.pop("scans.correlated", None)


# ---------------------------------------------------------------------------
# Config loading tests
# ---------------------------------------------------------------------------

class TestConfigLoad:
    """Test _load_correlated_pairs() JSON parsing."""

    def test_loads_valid_json(self):
        from scans.correlated import _load_correlated_pairs

        config_json = '[["Bitcoin $100k", "Bitcoin $90k"]]'
        pairs = _load_correlated_pairs(config_json)

        assert len(pairs) == 1
        assert pairs[0] == ("Bitcoin $100k", "Bitcoin $90k")

    def test_rejects_malformed_json(self):
        from scans.correlated import _load_correlated_pairs

        config_json = '[["Bitcoin $100k", "Bitcoin $90k"'  # Missing closing bracket
        with pytest.raises(ValueError, match="not valid JSON"):
            _load_correlated_pairs(config_json)

    def test_rejects_wrong_tuple_size(self):
        from scans.correlated import _load_correlated_pairs

        # 3-tuple instead of 2-tuple
        config_json = '[["Bitcoin $100k", "Bitcoin $90k", "extra"]]'
        with pytest.raises(ValueError, match="exactly 2 elements"):
            _load_correlated_pairs(config_json)

    def test_multiple_pairs(self):
        from scans.correlated import _load_correlated_pairs

        config_json = '[["BTC $100k", "BTC $90k"], ["ETH $5k", "ETH $4k"], ["SOL $200", "SOL $150"]]'
        pairs = _load_correlated_pairs(config_json)

        assert len(pairs) == 3
        assert pairs[0] == ("BTC $100k", "BTC $90k")
        assert pairs[1] == ("ETH $5k", "ETH $4k")
        assert pairs[2] == ("SOL $200", "SOL $150")

    def test_empty_config(self):
        from scans.correlated import _load_correlated_pairs

        pairs = _load_correlated_pairs("")
        assert pairs == []

    def test_empty_list_config(self):
        from scans.correlated import _load_correlated_pairs

        pairs = _load_correlated_pairs("[]")
        assert pairs == []

    def test_rejects_not_list(self):
        from scans.correlated import _load_correlated_pairs

        config_json = '{"pair1": ["Bitcoin $100k", "Bitcoin $90k"]}'  # Object instead of list
        with pytest.raises(ValueError, match="must be a list"):
            _load_correlated_pairs(config_json)


# ---------------------------------------------------------------------------
# Spread calculation tests
# ---------------------------------------------------------------------------

class TestSpreadCalculation:
    """Test _calculate_spread() formula."""

    def test_spread_calculation(self):
        from scans.correlated import _calculate_spread

        # price_a=0.75, price_b=0.50 -> spread = |0.75-0.50|/0.75 = 0.25/0.75 = 0.333...
        spread = _calculate_spread(0.75, 0.50)
        assert spread == pytest.approx(0.333, rel=0.01)

    def test_zero_spread(self):
        from scans.correlated import _calculate_spread

        spread = _calculate_spread(0.60, 0.60)
        assert spread == 0.0

    def test_symmetric_spread(self):
        from scans.correlated import _calculate_spread

        spread_ab = _calculate_spread(0.75, 0.50)
        spread_ba = _calculate_spread(0.50, 0.75)
        assert spread_ab == pytest.approx(spread_ba)

    def test_division_by_zero_protection(self):
        from scans.correlated import _calculate_spread

        spread = _calculate_spread(0.0, 0.0)
        assert spread == 0.0

    def test_one_price_zero(self):
        from scans.correlated import _calculate_spread

        # max(0, 0.50) = 0.50, spread = |0 - 0.50| / 0.50 = 1.0
        spread = _calculate_spread(0.0, 0.50)
        assert spread == 1.0

    def test_prices_at_boundaries(self):
        from scans.correlated import _calculate_spread

        # Both at 1.0
        spread = _calculate_spread(1.0, 1.0)
        assert spread == 0.0

        # 1.0 vs 0.0
        spread = _calculate_spread(1.0, 0.0)
        assert spread == 1.0


# ---------------------------------------------------------------------------
# Spread threshold tests
# ---------------------------------------------------------------------------

class TestSpreadThreshold:
    """Test Stage 1 scan_correlated() filtering by spread threshold."""

    def test_includes_spread_above_threshold(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "bitcoin-100k": {
                "id": "bitcoin-100k",
                "question": "Bitcoin $100k",
                "price": 0.75,
                "clobTokenIds": ["tok_yes", "tok_no"],
            },
            "bitcoin-90k": {
                "id": "bitcoin-90k",
                "question": "Bitcoin $90k",
                "price": 0.50,
                "clobTokenIds": ["tok_yes2", "tok_no2"],
            },
        }
        pairs = [("bitcoin-100k", "bitcoin-90k")]

        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)

        assert len(opps) == 1
        assert opps[0]["type"] == "Correlated"
        assert opps[0]["spread"] >= 0.10

    def test_excludes_spread_below_threshold(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "bitcoin-100k": {
                "id": "bitcoin-100k",
                "question": "Bitcoin $100k",
                "price": 0.60,
                "clobTokenIds": ["tok_yes", "tok_no"],
            },
            "bitcoin-90k": {
                "id": "bitcoin-90k",
                "question": "Bitcoin $90k",
                "price": 0.55,
                "clobTokenIds": ["tok_yes2", "tok_no2"],
            },
        }
        pairs = [("bitcoin-100k", "bitcoin-90k")]

        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)

        assert len(opps) == 0

    def test_custom_threshold(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "mkt_a": {
                "id": "mkt_a",
                "question": "Market A",
                "price": 0.70,
                "clobTokenIds": ["tok_yes", "tok_no"],
            },
            "mkt_b": {
                "id": "mkt_b",
                "question": "Market B",
                "price": 0.65,
                "clobTokenIds": ["tok_yes2", "tok_no2"],
            },
        }
        pairs = [("mkt_a", "mkt_b")]

        # spread = |0.70-0.65|/0.70 = 0.0714... = 7.14%
        # Should be excluded at 10% threshold, included at 5% threshold
        opps_10 = scan_correlated(markets_by_key, pairs, min_spread=0.10)
        assert len(opps_10) == 0

        opps_5 = scan_correlated(markets_by_key, pairs, min_spread=0.05)
        assert len(opps_5) == 1

    def test_returns_multiple_opportunities(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "btc_100k": {"id": "btc_100k", "question": "Bitcoin $100k", "price": 0.75, "clobTokenIds": ["t1", "t2"]},
            "btc_90k": {"id": "btc_90k", "question": "Bitcoin $90k", "price": 0.50, "clobTokenIds": ["t3", "t4"]},
            "eth_5k": {"id": "eth_5k", "question": "Ethereum $5k", "price": 0.80, "clobTokenIds": ["t5", "t6"]},
            "eth_4k": {"id": "eth_4k", "question": "Ethereum $4k", "price": 0.40, "clobTokenIds": ["t7", "t8"]},
            "sol_200": {"id": "sol_200", "question": "Solana $200", "price": 0.55, "clobTokenIds": ["t9", "t10"]},
            "sol_150": {"id": "sol_150", "question": "Solana $150", "price": 0.54, "clobTokenIds": ["t11", "t12"]},
        }
        pairs = [
            ("btc_100k", "btc_90k"),  # spread = 0.333 >= 0.10 ✓
            ("eth_5k", "eth_4k"),     # spread = 0.50 >= 0.10 ✓
            ("sol_200", "sol_150"),   # spread = 0.0182 < 0.10 ✗
        ]

        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)

        assert len(opps) == 2
        assert all(o["type"] == "Correlated" for o in opps)


# ---------------------------------------------------------------------------
# Leg directionality tests
# ---------------------------------------------------------------------------

class TestDirectionality:
    """Test that long leg is underpriced, short leg is overpriced."""

    def test_longs_underpriced_leg(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "mkt_a": {
                "id": "mkt_a",
                "question": "Market A (underpriced)",
                "price": 0.40,
                "clobTokenIds": ["tok_yes_a", "tok_no_a"],
            },
            "mkt_b": {
                "id": "mkt_b",
                "question": "Market B (overpriced)",
                "price": 0.75,
                "clobTokenIds": ["tok_yes_b", "tok_no_b"],
            },
        }
        pairs = [("mkt_a", "mkt_b")]

        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)

        assert len(opps) == 1
        opp = opps[0]
        assert opp["_long_leg"] == "mkt_a"
        assert opp["_long_price"] == 0.40
        assert opp["_short_leg"] == "mkt_b"
        assert opp["_short_price"] == 0.75

    def test_shorts_overpriced_leg(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "mkt_a": {
                "id": "mkt_a",
                "question": "Market A (overpriced)",
                "price": 0.75,
                "clobTokenIds": ["tok_yes_a", "tok_no_a"],
            },
            "mkt_b": {
                "id": "mkt_b",
                "question": "Market B (underpriced)",
                "price": 0.40,
                "clobTokenIds": ["tok_yes_b", "tok_no_b"],
            },
        }
        pairs = [("mkt_a", "mkt_b")]

        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)

        assert len(opps) == 1
        opp = opps[0]
        assert opp["_long_leg"] == "mkt_b"
        assert opp["_long_price"] == 0.40
        assert opp["_short_leg"] == "mkt_a"
        assert opp["_short_price"] == 0.75


# ---------------------------------------------------------------------------
# Refinement tests
# ---------------------------------------------------------------------------

class TestRefinement:
    """Test Stage 2 _refine_correlated_with_depth() validation.

    Post-PR-E: the refiner runs real CLOB checks. These tests inject a
    fake fetch_clob to exercise the gates without network. Detailed
    gate-by-gate coverage lives in tests/test_correlated_refiner.py;
    these are the legacy regression tests, repurposed.
    """

    def _make_book(self, ask, bid, ask_size=100.0, bid_size=100.0):
        return {
            "yes_ask": ask, "yes_bid": bid,
            "yes_ask_size": ask_size, "yes_bid_size": bid_size,
            "no_ask": None, "no_bid": None,
            "no_ask_size": 0, "no_bid_size": 0,
        }

    def _fetch_factory(self, books):
        def fetch(market, _price_cache=None):
            key = (market or {}).get("id")
            return market, books.get(key)
        return fetch

    def test_keeps_stable_spread(self):
        from scans.correlated import _refine_correlated_with_depth

        opportunities = [
            {
                "type": "Correlated",
                "spread": 0.12,
                "_long_leg": "mkt_a",
                "_short_leg": "mkt_b",
                "_market_key_a": "mkt_a",
                "_market_key_b": "mkt_b",
                "_long_market": {"id": "mkt_a"},
                "_short_market": {"id": "mkt_b"},
            }
        ]
        fetch = self._fetch_factory({
            "mkt_a": self._make_book(ask=0.40, bid=0.39),
            "mkt_b": self._make_book(ask=0.51, bid=0.50),
        })
        refined = _refine_correlated_with_depth(
            opportunities, max_spread_collapse=0.50, min_liquidity=10,
            fetch_clob=fetch,
        )
        # Spread held within tolerance and depth is sufficient.
        assert len(refined) == 1
        assert refined[0]["spread"] == 0.12
        assert "_live_spread" in refined[0]

    def test_drops_when_no_clob_for_either_leg(self):
        """Post-PR-E: opps without live CLOB data are dropped, not kept."""
        from scans.correlated import _refine_correlated_with_depth

        opportunities = [
            {
                "type": "Correlated", "spread": 0.15,
                "_long_leg": "a", "_short_leg": "b",
                "_market_key_a": "a", "_market_key_b": "b",
                "_long_market": {"id": "a"}, "_short_market": {"id": "b"},
            },
        ]
        fetch = self._fetch_factory({})  # no books → both legs missing
        refined = _refine_correlated_with_depth(
            opportunities, fetch_clob=fetch,
        )
        assert refined == []


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegration:
    """Integration tests for full correlated pair detection pipeline."""

    def test_full_pipeline_scan_to_refine(self):
        from scans.correlated import scan_correlated, _refine_correlated_with_depth

        markets_by_key = {
            "bitcoin_100k": {
                "id": "bitcoin_100k",
                "question": "Bitcoin will reach $100,000 by end of 2026",
                "price": 0.75,
                "clobTokenIds": ["token-yes-100k", "token-no-100k"],
            },
            "bitcoin_90k": {
                "id": "bitcoin_90k",
                "question": "Bitcoin will reach $90,000 by end of 2026",
                "price": 0.50,
                "clobTokenIds": ["token-yes-90k", "token-no-90k"],
            },
        }
        pairs = [("bitcoin_100k", "bitcoin_90k")]

        # Stage 1
        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)
        assert len(opps) == 1
        assert opps[0]["type"] == "Correlated"
        assert opps[0]["spread"] > 0.10

        # Stage 2 — provide a fake CLOB fetcher so the refiner doesn't
        # touch the network. Long leg (90k) buys at ask; short leg (100k)
        # sells at bid. Both legs return ample depth and a live spread
        # close to the Stage 1 detected spread.
        def _fake_fetch(market, _price_cache=None):
            mkt_id = (market or {}).get("id")
            books = {
                "bitcoin_90k": {
                    "yes_ask": 0.51, "yes_bid": 0.50,
                    "yes_ask_size": 200.0, "yes_bid_size": 200.0,
                    "no_ask": None, "no_bid": None,
                    "no_ask_size": 0, "no_bid_size": 0,
                },
                "bitcoin_100k": {
                    "yes_ask": 0.76, "yes_bid": 0.74,
                    "yes_ask_size": 200.0, "yes_bid_size": 200.0,
                    "no_ask": None, "no_bid": None,
                    "no_ask_size": 0, "no_bid_size": 0,
                },
            }
            return market, books.get(mkt_id)

        refined = _refine_correlated_with_depth(
            opps, fetch_clob=_fake_fetch, min_liquidity=10,
            max_spread_collapse=0.50,
        )
        assert len(refined) == 1
        assert refined[0]["_long_leg"] == "bitcoin_90k"
        assert refined[0]["_short_leg"] == "bitcoin_100k"

    def test_missing_market_is_skipped(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "bitcoin_100k": {
                "id": "bitcoin_100k",
                "question": "Bitcoin $100k",
                "price": 0.75,
                "clobTokenIds": ["token_yes", "token_no"],
            },
            # bitcoin_90k is missing
        }
        pairs = [("bitcoin_100k", "bitcoin_90k")]

        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)

        assert len(opps) == 0

    def test_missing_price_is_skipped(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "bitcoin_100k": {
                "id": "bitcoin_100k",
                "question": "Bitcoin $100k",
                "price": 0.75,
                "clobTokenIds": ["token_yes", "token_no"],
            },
            "bitcoin_90k": {
                "id": "bitcoin_90k",
                "question": "Bitcoin $90k",
                "price": None,  # Missing price
                "clobTokenIds": ["token_yes", "token_no"],
            },
        }
        pairs = [("bitcoin_100k", "bitcoin_90k")]

        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)

        assert len(opps) == 0

    def test_fuzzy_matching_on_titles(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "bitcoin-100k-id": {
                "id": "bitcoin-100k-id",
                "question": "Bitcoin will reach $100,000 by end of 2026",
                "price": 0.75,
                "clobTokenIds": ["token_yes", "token_no"],
            },
            "bitcoin-90k-id": {
                "id": "bitcoin-90k-id",
                "question": "Bitcoin will reach $90,000 by end of 2026",
                "price": 0.50,
                "clobTokenIds": ["token_yes2", "token_no2"],
            },
        }

        # Use market titles (not IDs) as identifiers
        pairs = [("Bitcoin $100,000", "Bitcoin $90,000")]

        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)

        # Fuzzy matching should find the markets despite title differences
        assert len(opps) == 1

    def test_opportunity_includes_token_ids(self):
        from scans.correlated import scan_correlated

        markets_by_key = {
            "mkt_a": {
                "id": "mkt_a",
                "question": "Market A",
                "price": 0.40,
                "clobTokenIds": ["token_yes_a", "token_no_a"],
            },
            "mkt_b": {
                "id": "mkt_b",
                "question": "Market B",
                "price": 0.75,
                "clobTokenIds": ["token_yes_b", "token_no_b"],
            },
        }
        pairs = [("mkt_a", "mkt_b")]

        opps = scan_correlated(markets_by_key, pairs, min_spread=0.10)

        assert len(opps) == 1
        opp = opps[0]
        assert opp["_token_ids_a"] == ["token_yes_a", "token_no_a"]
        assert opp["_token_ids_b"] == ["token_yes_b", "token_no_b"]
