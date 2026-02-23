"""Tests for IBKR scan functions — binary internal arbs only (BUY-only platform)."""

import pytest
from unittest.mock import MagicMock, patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def mock_external_modules():
    """Mock external API modules that may not be installed."""
    mock_modules = {}
    for mod_name in [
        "polymarket_api", "kalshi_api",
        "betfair_api", "smarkets_api", "sxbet_api", "matchbook_api",
        "ib_insync",
        "ws_feeds", "db", "risk_manager", "executor",
    ]:
        if mod_name not in sys.modules:
            mock_modules[mod_name] = MagicMock()
            sys.modules[mod_name] = mock_modules[mod_name]
    yield
    for mod_name in mock_modules:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    # Only remove the scan module under test — NOT scans.helpers or scans.__init__
    if "scans.ibkr" in sys.modules:
        del sys.modules["scans.ibkr"]


def _make_ibkr_event(event_id, title, yes_price, no_price):
    return {
        "id": event_id,
        "title": title,
        "contracts": [
            {"conid": f"{event_id}_Y", "label": "YES", "side": "YES", "price": yes_price},
            {"conid": f"{event_id}_N", "label": "NO", "side": "NO", "price": no_price},
        ],
        "status": "active",
    }


# ============================================================
# scan_ibkr_binary tests
# ============================================================


class TestScanIBKRBinary:
    def _import_scan(self):
        if "scans.ibkr" in sys.modules:
            del sys.modules["scans.ibkr"]
        from scans.ibkr import scan_ibkr_binary
        return scan_ibkr_binary

    def test_finds_profitable_under_round(self):
        """BUY YES + BUY NO when sum < 1.0."""
        scan_fn = self._import_scan()
        client = MagicMock()
        client.authenticated = True
        # YES=0.40, NO=0.40 -> total=0.80, profit=0.20
        client.fetch_all_markets.return_value = [
            _make_ibkr_event("E1", "Test Market", 0.40, 0.40),
        ]
        client.get_market_price.return_value = (0.40, 0.40)

        result = scan_fn(client, min_profit=0.001)
        assert len(result) >= 1
        assert result[0]["type"] == "IBKRBinary"
        assert result[0]["net_profit"] > 0
        assert "_ibkr_event_id" in result[0]
        assert "_ibkr_yes_conid" in result[0]
        assert "_ibkr_no_conid" in result[0]

    def test_no_arb_when_sum_exceeds_one(self):
        scan_fn = self._import_scan()
        client = MagicMock()
        client.authenticated = True
        # YES=0.55, NO=0.50 -> total=1.05
        client.fetch_all_markets.return_value = [
            _make_ibkr_event("E1", "Test", 0.55, 0.50),
        ]
        client.get_market_price.return_value = (0.55, 0.50)

        result = scan_fn(client, min_profit=0.001)
        assert len(result) == 0

    def test_skips_events_with_wrong_contract_count(self):
        """IBKR binary scan requires exactly 2 contracts."""
        scan_fn = self._import_scan()
        client = MagicMock()
        client.authenticated = True
        event = {
            "id": "E1", "title": "Test",
            "contracts": [
                {"conid": "C1", "side": "YES", "price": 0.3},
            ],
        }
        client.fetch_all_markets.return_value = [event]

        result = scan_fn(client, min_profit=0.001)
        assert len(result) == 0

    def test_handles_empty_markets(self):
        scan_fn = self._import_scan()
        client = MagicMock()
        client.authenticated = True
        client.fetch_all_markets.return_value = []

        result = scan_fn(client, min_profit=0.001)
        assert result == []

    def test_returns_empty_when_not_authenticated(self):
        scan_fn = self._import_scan()
        client = MagicMock()
        client.authenticated = False

        result = scan_fn(client, min_profit=0.001)
        assert result == []

    def test_returns_empty_for_none_client(self):
        scan_fn = self._import_scan()
        result = scan_fn(None, min_profit=0.001)
        assert result == []

    def test_zero_fee_profit_calculation(self):
        """IBKR has $0.00 fees — net profit should equal gross spread."""
        scan_fn = self._import_scan()
        client = MagicMock()
        client.authenticated = True
        client.fetch_all_markets.return_value = [
            _make_ibkr_event("E1", "Test", 0.30, 0.30),
        ]
        client.get_market_price.return_value = (0.30, 0.30)

        result = scan_fn(client, min_profit=0.001)
        assert len(result) >= 1
        # $0.00 fees: net_profit == gross_spread == 1.0 - 0.60 = 0.40
        assert result[0]["net_profit"] == pytest.approx(0.40)
