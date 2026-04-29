"""Tests for scans/kalshi.py — Kalshi binary and multi-outcome scan logic."""

import pytest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def mock_external_modules():
    """Mock kalshi_api if not installed."""
    mocked = {}
    if "kalshi_api" not in sys.modules:
        mocked["kalshi_api"] = MagicMock()
        sys.modules["kalshi_api"] = mocked["kalshi_api"]
    for key in list(sys.modules):
        if key == "scans.kalshi":
            del sys.modules[key]
    yield
    for mod_name in mocked:
        if mod_name in sys.modules:
            del sys.modules[mod_name]


# ---------------------------------------------------------------------------
# _fetch_kalshi_data
# ---------------------------------------------------------------------------

class TestFetchKalshiData:
    def _reset_cache(self):
        import scans.kalshi as sk
        with sk._kalshi_data_cache_lock:
            sk._kalshi_data_cache["ts"] = 0.0
            sk._kalshi_data_cache["value"] = None

    def test_returns_empty_without_client(self):
        self._reset_cache()
        from scans.kalshi import _fetch_kalshi_data
        events, by_event, titles = _fetch_kalshi_data(None)
        assert events == []
        assert by_event == {}
        assert titles == {}

    def test_returns_empty_when_no_events(self):
        self._reset_cache()
        from scans.kalshi import _fetch_kalshi_data
        client = MagicMock()
        client.fetch_all_events.return_value = []
        events, by_event, titles = _fetch_kalshi_data(client)
        assert events == []

    def test_uses_nested_markets_when_present(self):
        """Events with nested ``markets`` arrays must NOT trigger _parallel_fetch_kalshi."""
        self._reset_cache()
        from scans.kalshi import _fetch_kalshi_data

        client = MagicMock()
        client.fetch_all_events.return_value = [
            {"event_ticker": "E1", "title": "Event 1",
             "markets": [{"ticker": "M1A"}, {"ticker": "M1B"}]},
            {"event_ticker": "E2", "title": "Event 2",
             "markets": [{"ticker": "M2A"}]},
        ]

        with patch("scans.kalshi._parallel_fetch_kalshi") as mock_parallel:
            events, by_event, titles = _fetch_kalshi_data(client)

        assert mock_parallel.call_count == 0, "must not fall back to per-event REST"
        assert by_event == {
            "E1": [{"ticker": "M1A"}, {"ticker": "M1B"}],
            "E2": [{"ticker": "M2A"}],
        }
        assert titles == {"E1": "Event 1", "E2": "Event 2"}

    def test_falls_back_to_parallel_fetch_when_no_nested_markets(self):
        """Events lacking ``markets`` field still work via legacy REST path."""
        self._reset_cache()
        from scans.kalshi import _fetch_kalshi_data

        client = MagicMock()
        client.fetch_all_events.return_value = [
            {"event_ticker": "E1", "title": "Event 1"},  # no markets field
        ]
        with patch("scans.kalshi._parallel_fetch_kalshi", return_value={"E1": [{"ticker": "MX"}]}) as mock_parallel:
            events, by_event, titles = _fetch_kalshi_data(client)

        assert mock_parallel.call_count == 1
        assert by_event == {"E1": [{"ticker": "MX"}]}

    def test_cache_hit_skips_api_within_ttl(self):
        """Second call within TTL returns cached value without re-calling fetch_all_events."""
        self._reset_cache()
        import scans.kalshi as sk
        from scans.kalshi import _fetch_kalshi_data

        client = MagicMock()
        client.fetch_all_events.return_value = [
            {"event_ticker": "E1", "title": "T", "markets": [{"ticker": "MA"}]},
        ]
        # First call populates cache
        _fetch_kalshi_data(client)
        first_calls = client.fetch_all_events.call_count
        # Force long TTL so the second call hits cache
        original_ttl = sk._KALSHI_DATA_CACHE_TTL
        sk._KALSHI_DATA_CACHE_TTL = 3600
        try:
            _fetch_kalshi_data(client)
        finally:
            sk._KALSHI_DATA_CACHE_TTL = original_ttl
        assert client.fetch_all_events.call_count == first_calls, "cache hit must not re-call API"

    def test_cache_expires_after_ttl(self):
        """After TTL elapses, cache is bypassed and fetch_all_events runs again."""
        self._reset_cache()
        import scans.kalshi as sk
        from scans.kalshi import _fetch_kalshi_data

        client = MagicMock()
        client.fetch_all_events.return_value = [
            {"event_ticker": "E1", "title": "T", "markets": []},
        ]
        original_ttl = sk._KALSHI_DATA_CACHE_TTL
        sk._KALSHI_DATA_CACHE_TTL = 0  # treat any age as expired
        try:
            _fetch_kalshi_data(client)
            _fetch_kalshi_data(client)
        finally:
            sk._KALSHI_DATA_CACHE_TTL = original_ttl
        assert client.fetch_all_events.call_count == 2


# ---------------------------------------------------------------------------
# scan_kalshi_binary
# ---------------------------------------------------------------------------

class TestScanKalshiBinary:
    def test_returns_empty_without_client(self):
        from scans.kalshi import scan_kalshi_binary
        result = scan_kalshi_binary(None, 0.01)
        assert result == []

    def test_returns_empty_with_no_markets(self):
        from scans.kalshi import scan_kalshi_binary
        client = MagicMock()
        result = scan_kalshi_binary(client, 0.01, kalshi_data=([], {}, {}))
        assert result == []

    def test_finds_binary_arb(self):
        from scans.kalshi import scan_kalshi_binary

        client = MagicMock()
        client.get_market_price.return_value = (0.45, 0.45)
        client.get_order_book_depth.return_value = {"yes_ask_size": 100, "no_ask_size": 100}

        market = {
            "ticker": "KXTICKER",
            "title": "Will X happen?",
            "close_time": "2030-01-01T00:00:00Z",
            "expiration_time": "2030-01-01T00:00:00Z",
        }
        kalshi_data = (
            [{"event_ticker": "EV1", "title": "Event 1"}],
            {"EV1": [market]},
            {"EV1": "Event 1"},
        )

        with patch("scans.kalshi._within_resolution_window", return_value=True), \
             patch("scans.kalshi.filter_dust", side_effect=lambda x: x), \
             patch("scans.kalshi.net_profit_kalshi_binary", return_value={
                 "gross_spread": 0.10, "fees": 0.02, "net_profit": 0.08,
             }):
            result = scan_kalshi_binary(client, 0.01, kalshi_data=kalshi_data)

        assert len(result) == 1
        opp = result[0]
        assert opp["type"] == "KalshiBinary"
        assert opp["_kalshi_ticker"] == "KXTICKER"
        assert opp["_kalshi_yes"] == 0.45
        assert opp["_kalshi_no"] == 0.45
        assert opp["net_profit"] == 0.08

    def test_skips_dust_prices(self):
        from scans.kalshi import scan_kalshi_binary

        client = MagicMock()
        client.get_market_price.return_value = (0.001, 0.999)

        market = {"ticker": "K-DUST", "title": "Dust market",
                  "close_time": "2030-01-01T00:00:00Z"}
        kalshi_data = ([{"event_ticker": "EV1"}], {"EV1": [market]}, {"EV1": "Event"})

        with patch("scans.kalshi._within_resolution_window", return_value=True), \
             patch("scans.kalshi.filter_dust", side_effect=lambda x: x):
            result = scan_kalshi_binary(client, 0.01, kalshi_data=kalshi_data)

        assert result == []

    def test_skips_resolved_markets(self):
        from scans.kalshi import scan_kalshi_binary

        client = MagicMock()
        client.get_market_price.return_value = (0.45, 0.45)

        market = {"ticker": "K-OLD", "title": "Old market",
                  "close_time": "2020-01-01T00:00:00Z"}
        kalshi_data = ([{"event_ticker": "EV1"}], {"EV1": [market]}, {"EV1": "Event"})

        with patch("scans.kalshi._within_resolution_window", return_value=False), \
             patch("scans.kalshi.filter_dust", side_effect=lambda x: x):
            result = scan_kalshi_binary(client, 0.01, kalshi_data=kalshi_data)

        assert result == []


# ---------------------------------------------------------------------------
# scan_kalshi_multi
# ---------------------------------------------------------------------------

class TestScanKalshiMulti:
    def test_returns_empty_without_client(self):
        from scans.kalshi import scan_kalshi_multi
        result = scan_kalshi_multi(None, 0.01)
        assert result == []

    def test_skips_single_market_events(self):
        from scans.kalshi import scan_kalshi_multi

        client = MagicMock()
        kalshi_data = (
            [{"event_ticker": "EV1"}],
            {"EV1": [{"ticker": "K-1", "title": "Only one"}]},
            {"EV1": "Single event"},
        )
        result = scan_kalshi_multi(client, 0.01, kalshi_data=kalshi_data)
        assert result == []

    def test_finds_multi_arb(self):
        from scans.kalshi import scan_kalshi_multi

        client = MagicMock()
        client.get_market_price.side_effect = [
            (0.25, 0.75), (0.30, 0.70), (0.20, 0.80),
        ]
        client.get_order_book_depth.return_value = {"yes_ask_size": 50}

        markets = [
            {"ticker": "K-A", "title": "A", "close_time": "2030-01-01T00:00:00Z",
             "expiration_time": "2030-01-01T00:00:00Z"},
            {"ticker": "K-B", "title": "B", "close_time": "2030-01-01T00:00:00Z",
             "expiration_time": "2030-01-01T00:00:00Z"},
            {"ticker": "K-C", "title": "C", "close_time": "2030-01-01T00:00:00Z",
             "expiration_time": "2030-01-01T00:00:00Z"},
        ]
        kalshi_data = (
            [{"event_ticker": "EV1", "title": "Multi Event"}],
            {"EV1": markets},
            {"EV1": "Multi Event"},
        )

        with patch("scans.kalshi._within_resolution_window", return_value=True), \
             patch("scans.kalshi.filter_dust", side_effect=lambda x: x), \
             patch("scans.kalshi.net_profit_kalshi_multi", return_value={
                 "gross_spread": 0.15, "fees": 0.03, "net_profit": 0.12,
             }):
            result = scan_kalshi_multi(client, 0.01, kalshi_data=kalshi_data)

        assert len(result) >= 1
        opp = result[0]
        assert opp["type"].startswith("KalshiMulti")
        assert opp["net_profit"] > 0
        assert "_kalshi_tickers" in opp
        assert "_kalshi_prices" in opp
