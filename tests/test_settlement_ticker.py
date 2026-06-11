"""Regression tests for the Kalshi settlement-by-ticker fix.

Before the fix, positions stored the human-readable title in market_identifier
and check_settlements looked markets up by that title — which the Kalshi API
rejects — so positions never settled and realized_pnl stayed NULL. These tests
prove the ticker is stored and used.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import TradeDB
import continuous


class TestPositionTickerStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = TradeDB(self.tmp.name)

    def tearDown(self):
        self.db.close() if hasattr(self.db, "close") else None
        os.unlink(self.tmp.name)

    def test_market_ticker_column_exists_and_persists(self):
        opp_id = self.db.log_opportunity("KalshiMulti(4)", "Chelsea: Spreads", "Y=0.40", 0.95, 0.05, 0.05, 1, "exec")
        pid = self.db.create_position(
            opportunity_id=opp_id,
            market_identifier="Chelsea: Spreads",
            platform="kalshi",
            expected_pnl=0.05,
            market_ticker="KXEPLSPREAD-26MAY19CFCTOT-CFC1",
        )
        pos = self.db.get_open_positions()[0]
        self.assertEqual(pos["id"], pid)
        self.assertEqual(pos["market_identifier"], "Chelsea: Spreads")
        self.assertEqual(pos["market_ticker"], "KXEPLSPREAD-26MAY19CFCTOT-CFC1")

    def test_market_ticker_defaults_none_for_legacy_rows(self):
        opp_id = self.db.log_opportunity("Binary", "Some Market", "Y=0.50", 0.99, 0.01, 0.01, 1, "exec")
        self.db.create_position(
            opportunity_id=opp_id, market_identifier="Some Market",
            platform="kalshi", expected_pnl=0.01,
        )
        pos = self.db.get_open_positions()[0]
        self.assertIsNone(pos["market_ticker"])


class TestCheckSettlementsUsesTicker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = TradeDB(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _make_kalshi_client(self):
        client = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"market": {"result": "yes"}}
        client._request.return_value = resp
        return client

    def test_settlement_queries_by_ticker_and_settles(self):
        opp_id = self.db.log_opportunity("KalshiMulti(4)", "Chelsea: Spreads", "Y=0.40", 0.95, 0.05, 0.05, 1, "exec")
        # A filled trade so realized P&L can be computed.
        tid = self.db.log_trade(opp_id, "kalshi", "yes", 0.40, 1.0, "filled", fill_price=0.40)
        pid = self.db.create_position(
            opportunity_id=opp_id, market_identifier="Chelsea: Spreads",
            platform="kalshi", expected_pnl=0.05,
            market_ticker="KXEPLSPREAD-26MAY19CFCTOT-CFC1",
        )
        client = self._make_kalshi_client()

        continuous.check_settlements(self.db, client, None)

        # The API must have been called with the TICKER, not the title.
        called_path = client._request.call_args[0][1]
        self.assertIn("KXEPLSPREAD-26MAY19CFCTOT-CFC1", called_path)
        self.assertNotIn("Chelsea", called_path)
        # And the position must now be settled.
        self.assertEqual(self.db.get_open_positions_count(), 0)

    def test_legacy_row_without_ticker_falls_back_to_identifier(self):
        opp_id = self.db.log_opportunity("Binary", "KXLEGACY-1", "Y=0.50", 0.99, 0.01, 0.01, 1, "exec")
        self.db.log_trade(opp_id, "kalshi", "yes", 0.50, 1.0, "filled", fill_price=0.50)
        self.db.create_position(
            opportunity_id=opp_id, market_identifier="KXLEGACY-1",
            platform="kalshi", expected_pnl=0.01,
        )
        client = self._make_kalshi_client()

        continuous.check_settlements(self.db, client, None)

        called_path = client._request.call_args[0][1]
        self.assertIn("KXLEGACY-1", called_path)
        self.assertEqual(self.db.get_open_positions_count(), 0)


if __name__ == "__main__":
    unittest.main()
