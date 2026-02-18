"""Tests for hedger.py — partial fill hedging across all platforms."""

import pytest
from unittest.mock import MagicMock, patch

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import TradeDB


# Mock external API modules before importing hedger
@pytest.fixture(autouse=True)
def mock_external_modules():
    mock_modules = {}
    for mod_name in [
        "polymarket_api", "kalshi_api",
        "betfair_api", "smarkets_api", "sxbet_api", "matchbook_api",
    ]:
        if mod_name not in sys.modules:
            mock_modules[mod_name] = MagicMock()
            sys.modules[mod_name] = mock_modules[mod_name]
    yield
    for mod_name in mock_modules:
        del sys.modules[mod_name]


def _import_hedger():
    if "hedger" in sys.modules:
        del sys.modules["hedger"]
    from hedger import PartialFillHedger
    return PartialFillHedger


@pytest.fixture
def PartialFillHedger():
    return _import_hedger()


@pytest.fixture
def db():
    trade_db = TradeDB(":memory:")
    yield trade_db
    trade_db.close()


# ---------------------------------------------------------------------------
# _attempt_hedge routing
# ---------------------------------------------------------------------------

class TestAttemptHedgeRouting:
    def test_routes_to_smarkets(self, PartialFillHedger, db):
        """Smarkets partial fill should call _hedge_smarkets."""
        mock_smarkets = MagicMock()
        mock_smarkets.authenticated = True
        mock_smarkets.place_order.return_value = {"id": "hedge_123"}
        hedger = PartialFillHedger(smarkets_client=mock_smarkets, db=db)
        pf = {
            "id": 1, "platform": "smarkets", "token_id": "sm_tok",
            "fill_price": 0.40, "size": 5.0, "side": "BACK",
            "_market_id": "sm_market_1", "_contract_id": "sm_contract_1",
            "hedge_attempts": 0,
        }
        result = hedger._attempt_hedge(pf)
        assert result is True
        mock_smarkets.place_order.assert_called_once()

    def test_routes_to_sxbet(self, PartialFillHedger, db):
        """SX Bet partial fill should call _hedge_sxbet."""
        mock_sxbet = MagicMock()
        mock_sxbet.authenticated = True
        mock_sxbet.place_order.return_value = {"orderHash": "hedge_456"}
        hedger = PartialFillHedger(sxbet_client=mock_sxbet, db=db)
        pf = {
            "id": 1, "platform": "sxbet", "token_id": "sx_tok",
            "fill_price": 0.35, "size": 5.0, "side": "BACK",
            "_market_hash": "0xabc", "_outcome_id": "oid_1",
            "hedge_attempts": 0,
        }
        result = hedger._attempt_hedge(pf)
        assert result is True
        mock_sxbet.place_order.assert_called_once()

    def test_routes_to_matchbook(self, PartialFillHedger, db):
        """Matchbook partial fill should call _hedge_matchbook."""
        mock_matchbook = MagicMock()
        mock_matchbook.authenticated = True
        mock_matchbook.place_order.return_value = {"id": "hedge_789"}
        hedger = PartialFillHedger(matchbook_client=mock_matchbook, db=db)
        pf = {
            "id": 1, "platform": "matchbook", "token_id": "mb_tok",
            "fill_price": 0.50, "size": 5.0, "side": "back",
            "_market_id": "mb_market_1", "_runner_id": "runner_1",
            "hedge_attempts": 0,
        }
        result = hedger._attempt_hedge(pf)
        assert result is True
        mock_matchbook.place_order.assert_called_once()


# ---------------------------------------------------------------------------
# _hedge_smarkets
# ---------------------------------------------------------------------------

class TestHedgeSmarkets:
    def test_hedges_with_lay_side(self, PartialFillHedger, db):
        """BACK fill should hedge with LAY."""
        mock_smarkets = MagicMock()
        mock_smarkets.authenticated = True
        mock_smarkets.place_order.return_value = {"id": "h1"}
        hedger = PartialFillHedger(smarkets_client=mock_smarkets, db=db)
        pf = {
            "platform": "smarkets", "fill_price": 0.40, "size": 5.0,
            "side": "BACK", "_market_id": "m1", "_contract_id": "c1",
        }
        result = hedger._hedge_smarkets(pf, 0.40, 5.0, 0.10)
        assert result is True
        call_kwargs = mock_smarkets.place_order.call_args
        assert call_kwargs[1]["side"] == "LAY"

    def test_fails_without_client(self, PartialFillHedger, db):
        """Returns False when no smarkets client."""
        hedger = PartialFillHedger(db=db)
        pf = {"platform": "smarkets", "fill_price": 0.40, "size": 5.0,
              "side": "BACK", "_market_id": "m1"}
        result = hedger._hedge_smarkets(pf, 0.40, 5.0, 0.10)
        assert result is False

    def test_fails_without_market_id(self, PartialFillHedger, db):
        """Returns False when market_id is missing."""
        mock_smarkets = MagicMock()
        mock_smarkets.authenticated = True
        hedger = PartialFillHedger(smarkets_client=mock_smarkets, db=db)
        pf = {"platform": "smarkets", "fill_price": 0.40, "size": 5.0,
              "side": "BACK", "_market_id": ""}
        result = hedger._hedge_smarkets(pf, 0.40, 5.0, 0.10)
        assert result is False


# ---------------------------------------------------------------------------
# _hedge_sxbet
# ---------------------------------------------------------------------------

class TestHedgeSXBet:
    def test_hedges_with_lay_side(self, PartialFillHedger, db):
        """BACK fill should hedge with LAY."""
        mock_sxbet = MagicMock()
        mock_sxbet.authenticated = True
        mock_sxbet.place_order.return_value = {"orderHash": "h2"}
        hedger = PartialFillHedger(sxbet_client=mock_sxbet, db=db)
        pf = {
            "platform": "sxbet", "fill_price": 0.35, "size": 5.0,
            "side": "BACK", "_market_hash": "0xabc", "_outcome_id": "o1",
        }
        result = hedger._hedge_sxbet(pf, 0.35, 5.0, 0.10)
        assert result is True
        call_kwargs = mock_sxbet.place_order.call_args
        assert call_kwargs[1]["side"] == "LAY"

    def test_fails_without_market_hash(self, PartialFillHedger, db):
        """Returns False when market_hash is missing."""
        mock_sxbet = MagicMock()
        mock_sxbet.authenticated = True
        hedger = PartialFillHedger(sxbet_client=mock_sxbet, db=db)
        pf = {"platform": "sxbet", "fill_price": 0.35, "size": 5.0,
              "side": "BACK", "_market_hash": ""}
        result = hedger._hedge_sxbet(pf, 0.35, 5.0, 0.10)
        assert result is False


# ---------------------------------------------------------------------------
# _hedge_matchbook
# ---------------------------------------------------------------------------

class TestHedgeMatchbook:
    def test_hedges_with_lay_side(self, PartialFillHedger, db):
        """BACK fill should hedge with LAY."""
        mock_mb = MagicMock()
        mock_mb.authenticated = True
        mock_mb.place_order.return_value = {"id": "h3"}
        hedger = PartialFillHedger(matchbook_client=mock_mb, db=db)
        pf = {
            "platform": "matchbook", "fill_price": 0.50, "size": 5.0,
            "side": "back", "_market_id": "mb_m1", "_runner_id": "r1",
        }
        result = hedger._hedge_matchbook(pf, 0.50, 5.0, 0.10)
        assert result is True
        call_kwargs = mock_mb.place_order.call_args
        assert call_kwargs[1]["side"] == "lay"

    def test_hedges_lay_with_back(self, PartialFillHedger, db):
        """LAY fill should hedge with BACK."""
        mock_mb = MagicMock()
        mock_mb.authenticated = True
        mock_mb.place_order.return_value = {"id": "h4"}
        hedger = PartialFillHedger(matchbook_client=mock_mb, db=db)
        pf = {
            "platform": "matchbook", "fill_price": 0.50, "size": 5.0,
            "side": "lay", "_market_id": "mb_m1", "_runner_id": "r1",
        }
        result = hedger._hedge_matchbook(pf, 0.50, 5.0, 0.10)
        assert result is True
        call_kwargs = mock_mb.place_order.call_args
        assert call_kwargs[1]["side"] == "back"

    def test_fails_without_runner_id(self, PartialFillHedger, db):
        """Returns False when runner_id is missing."""
        mock_mb = MagicMock()
        mock_mb.authenticated = True
        hedger = PartialFillHedger(matchbook_client=mock_mb, db=db)
        pf = {"platform": "matchbook", "fill_price": 0.50, "size": 5.0,
              "side": "back", "_market_id": "mb_m1", "_runner_id": ""}
        result = hedger._hedge_matchbook(pf, 0.50, 5.0, 0.10)
        assert result is False


# ---------------------------------------------------------------------------
# Unknown platform falls through
# ---------------------------------------------------------------------------

class TestUnknownPlatformHedge:
    def test_unknown_platform_returns_false(self, PartialFillHedger, db):
        """Unknown platform should return False without raising."""
        hedger = PartialFillHedger(db=db)
        pf = {
            "id": 1, "platform": "unknown_exchange", "token_id": "tok",
            "fill_price": 0.40, "size": 5.0, "side": "BACK",
            "hedge_attempts": 0,
        }
        result = hedger._attempt_hedge(pf)
        assert result is False
