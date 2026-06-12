"""Tests for scans/lip_select.py — LIP pool ranking and filters."""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scans.lip_select import select_lip_markets


FUTURE = "2030-01-01T00:00:00Z"
SOON = "2026-06-12T06:00:00Z"  # < 24h from any 2026-06-12 run; always past by 2030


def _client(programs, prices=None, depths=None):
    c = MagicMock()
    c.fetch_incentive_programs.return_value = programs
    prices = prices or {}
    c.get_market_price.side_effect = lambda m: (prices.get(m["ticker"], 0.50), None)
    depths = depths or {}
    c.get_order_book_depth.side_effect = lambda t: depths.get(t, {"yes_ask_size": 0, "no_ask_size": 0})
    return c


def _data(markets_by_event, categories=None):
    categories = categories or {}
    events = [{"event_ticker": ev, "category": categories.get(ev, "Politics")}
              for ev in markets_by_event]
    return (events, markets_by_event, {ev: ev for ev in markets_by_event})


def _prog(ticker, dollars, end=FUTURE):
    return {"market_ticker": ticker, "period_reward_dollars": dollars,
            "discount_factor_bps": 5000, "end_date": end}


class TestSelectLipMarkets:
    def test_ranks_by_pool_over_competition(self):
        programs = [_prog("BIG", 100.0), _prog("CROWDED", 100.0), _prog("SMALL", 20.0)]
        data = _data({"EV": [
            {"ticker": "BIG", "close_time": FUTURE},
            {"ticker": "CROWDED", "close_time": FUTURE},
            {"ticker": "SMALL", "close_time": FUTURE},
        ]})
        client = _client(programs, depths={
            "BIG": {"yes_ask_size": 10, "no_ask_size": 10},
            "CROWDED": {"yes_ask_size": 500, "no_ask_size": 500},
            "SMALL": {"yes_ask_size": 0, "no_ask_size": 0},
        })
        out = select_lip_markets(client, kalshi_data=data, max_markets=3)
        assert [o["ticker"] for o in out] == ["SMALL", "BIG", "CROWDED"]
        assert out[1]["pool_dollars"] == pytest.approx(100.0)
        assert out[1]["competition_depth"] == 20

    def test_aggregates_multiple_programs_per_ticker(self):
        programs = [_prog("X", 40.0), _prog("X", 60.0)]
        data = _data({"EV": [{"ticker": "X", "close_time": FUTURE}]})
        out = select_lip_markets(_client(programs), kalshi_data=data)
        assert len(out) == 1
        assert out[0]["pool_dollars"] == pytest.approx(100.0)

    def test_excludes_sports_category(self):
        programs = [_prog("SPORTY", 500.0), _prog("POLI", 50.0)]
        data = _data(
            {"EVS": [{"ticker": "SPORTY", "close_time": FUTURE}],
             "EVP": [{"ticker": "POLI", "close_time": FUTURE}]},
            categories={"EVS": "Sports", "EVP": "Politics"},
        )
        out = select_lip_markets(_client(programs), kalshi_data=data)
        assert [o["ticker"] for o in out] == ["POLI"]

    def test_excludes_small_pools(self):
        programs = [_prog("DUST", 1.0)]  # below LIP_MIN_POOL=10
        data = _data({"EV": [{"ticker": "DUST", "close_time": FUTURE}]})
        assert select_lip_markets(_client(programs), kalshi_data=data) == []

    def test_excludes_imminent_close(self):
        programs = [_prog("ENDING", 100.0)]
        data = _data({"EV": [{"ticker": "ENDING", "close_time": SOON}]})
        assert select_lip_markets(_client(programs), kalshi_data=data) == []

    def test_excludes_imminent_program_end(self):
        programs = [_prog("PROGEND", 100.0, end=SOON)]
        data = _data({"EV": [{"ticker": "PROGEND", "close_time": FUTURE}]})
        assert select_lip_markets(_client(programs), kalshi_data=data) == []

    def test_excludes_price_band_tails(self):
        programs = [_prog("TAIL", 100.0), _prog("MID", 100.0)]
        data = _data({"EV": [
            {"ticker": "TAIL", "close_time": FUTURE},
            {"ticker": "MID", "close_time": FUTURE},
        ]})
        client = _client(programs, prices={"TAIL": 0.03, "MID": 0.50})
        out = select_lip_markets(client, kalshi_data=data)
        assert [o["ticker"] for o in out] == ["MID"]

    def test_unknown_ticker_skipped(self):
        programs = [_prog("GHOST", 100.0)]
        data = _data({"EV": [{"ticker": "OTHER", "close_time": FUTURE}]})
        assert select_lip_markets(_client(programs), kalshi_data=data) == []

    def test_no_programs_returns_empty(self):
        data = _data({"EV": [{"ticker": "X", "close_time": FUTURE}]})
        assert select_lip_markets(_client([]), kalshi_data=data) == []

    def test_no_client_returns_empty(self):
        assert select_lip_markets(None) == []

    def test_respects_max_markets(self):
        programs = [_prog(f"T{i}", 100.0 - i) for i in range(8)]
        data = _data({"EV": [{"ticker": f"T{i}", "close_time": FUTURE} for i in range(8)]})
        out = select_lip_markets(_client(programs), kalshi_data=data, max_markets=3)
        assert len(out) == 3


if __name__ == "__main__":
    import unittest
    unittest.main()
