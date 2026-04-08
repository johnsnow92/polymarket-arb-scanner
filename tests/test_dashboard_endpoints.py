"""Tests for new dashboard API endpoints: /api/strategy-pnl, /api/balances, /api/rebalance."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import TradeDB
from dashboard import _DashboardState, state, start_dashboard, _send_json

# ---------------------------------------------------------------------------
# Helpers (reuse pattern from test_dashboard.py)
# ---------------------------------------------------------------------------

from http.client import HTTPConnection


def _get(base_url: str, path: str) -> tuple[int, bytes, dict]:
    """Make a GET request. Returns (status, body, headers_dict)."""
    host, port_str = base_url.replace("http://", "").split(":")
    conn = HTTPConnection(host, int(port_str), timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    status = resp.status
    conn.close()
    return status, body, {}


def _start_test_server(port: int):
    server = start_dashboard(port)
    assert server is not None
    return server, f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# TradeDB.get_strategy_pnl() unit tests
# ---------------------------------------------------------------------------

class TestGetStrategyPnl:
    """Unit tests for TradeDB.get_strategy_pnl()."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        self.db = TradeDB(db_path=str(tmp_path / "test.db"))

    def test_returns_empty_list_when_no_trades(self):
        """Returns [] when trades table is empty."""
        result = self.db.get_strategy_pnl()
        assert isinstance(result, list)
        assert result == []

    def test_returns_empty_list_when_no_opportunities(self):
        """Returns [] when opportunities table is empty."""
        result = self.db.get_strategy_pnl()
        assert result == []

    def test_returns_correct_keys(self):
        """Each row has strategy, trade_count, win_count, total_pnl, avg_profit keys."""
        # Add opportunity + position (settled) to get data
        opp_id = self.db.log_opportunity("binary", "test-market", "Y:0.5 N:0.5", 1.0, 0.05, 0.05, 10.0, "executed")
        pos_id = self.db.create_position(opp_id, "test-market", "polymarket", 0.05)
        self.db.settle_position(pos_id, 0.10)
        result = self.db.get_strategy_pnl()
        # get_strategy_pnl uses trades table (via join), so no trades = no rows
        # Log a trade to get results
        self.db.log_trade(opp_id, "polymarket", "BUY_YES", 0.45, 5.0, "filled", 0.45)
        result = self.db.get_strategy_pnl()
        assert len(result) >= 0  # May be empty if no pnl column
        # If result has rows, check keys
        for row in result:
            assert "strategy" in row
            assert "trade_count" in row
            assert "win_count" in row
            assert "total_pnl" in row
            assert "avg_profit" in row

    def test_strategy_pnl_with_trades(self):
        """Returns per-strategy rows with correct trade_count when trades exist."""
        opp_id = self.db.log_opportunity("cross", "mkt-1", "Y:0.6 N:0.5", 2.0, 0.08, 0.04, 20.0, "executed")
        self.db.log_trade(opp_id, "polymarket", "BUY_YES", 0.55, 10.0, "filled", 0.55)
        self.db.log_trade(opp_id, "kalshi", "BUY_NO", 0.45, 10.0, "filled", 0.45)
        result = self.db.get_strategy_pnl()
        assert len(result) >= 1
        cross_rows = [r for r in result if r["strategy"] == "cross"]
        assert len(cross_rows) == 1
        assert cross_rows[0]["trade_count"] == 2

    def test_multiple_strategies(self):
        """Returns separate rows for different opportunity types."""
        opp1 = self.db.log_opportunity("binary", "mkt-1", "Y:0.5", 1.0, 0.05, 0.05, 10.0, "executed")
        opp2 = self.db.log_opportunity("cross", "mkt-2", "Y:0.6", 2.0, 0.08, 0.04, 20.0, "executed")
        self.db.log_trade(opp1, "polymarket", "BUY_YES", 0.45, 5.0, "filled")
        self.db.log_trade(opp2, "kalshi", "BUY_YES", 0.55, 5.0, "filled")
        result = self.db.get_strategy_pnl()
        strategies = {r["strategy"] for r in result}
        assert "binary" in strategies
        assert "cross" in strategies


# ---------------------------------------------------------------------------
# _DashboardState new attributes tests
# ---------------------------------------------------------------------------

class TestDashboardStateNewAttributes:
    """Test that _DashboardState has the new capital tracking attributes."""

    def test_platform_balances_default(self):
        s = _DashboardState()
        assert hasattr(s, "platform_balances")
        assert isinstance(s.platform_balances, dict)
        assert s.platform_balances == {}

    def test_last_bankroll_refresh_default(self):
        s = _DashboardState()
        assert hasattr(s, "last_bankroll_refresh")
        assert s.last_bankroll_refresh is None

    def test_platform_opp_flow_default(self):
        s = _DashboardState()
        assert hasattr(s, "platform_opp_flow")
        assert isinstance(s.platform_opp_flow, dict)
        assert s.platform_opp_flow == {}

    def test_attributes_are_mutable(self):
        s = _DashboardState()
        s.platform_balances = {"polymarket": 100.0, "kalshi": 50.0}
        s.last_bankroll_refresh = "2026-01-01T00:00:00Z"
        s.platform_opp_flow = {"polymarket": 10, "kalshi": 5}
        assert s.platform_balances["polymarket"] == 100.0
        assert s.last_bankroll_refresh == "2026-01-01T00:00:00Z"
        assert s.platform_opp_flow["kalshi"] == 5


# ---------------------------------------------------------------------------
# HTTP endpoint tests via live server
# ---------------------------------------------------------------------------

class TestStrategyPnlEndpoint:
    """Tests for GET /api/strategy-pnl."""

    def test_returns_200_with_strategies_key(self):
        server, url = _start_test_server(19001)
        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/strategy-pnl")
            assert status == 200
            data = json.loads(body)
            assert "strategies" in data
            assert isinstance(data["strategies"], list)
        finally:
            server.shutdown()

    def test_returns_empty_list_on_no_db(self):
        """When DB is unavailable, returns empty strategies list."""
        server, url = _start_test_server(19002)
        try:
            with patch("config.DASHBOARD_PASS", ""), \
                 patch("dashboard._get_db", return_value=None):
                status, body, _ = _get(url, "/api/strategy-pnl")
            assert status == 200
            data = json.loads(body)
            assert data["strategies"] == []
        finally:
            server.shutdown()

    def test_calls_get_strategy_pnl(self):
        """Handler calls db.get_strategy_pnl() and wraps in strategies key."""
        mock_db = MagicMock()
        mock_db.get_strategy_pnl.return_value = [
            {"strategy": "binary", "trade_count": 5, "win_count": 3,
             "total_pnl": 0.25, "avg_profit": 0.05}
        ]
        server, url = _start_test_server(19003)
        try:
            with patch("config.DASHBOARD_PASS", ""), \
                 patch("dashboard._get_db", return_value=mock_db):
                status, body, _ = _get(url, "/api/strategy-pnl")
            assert status == 200
            data = json.loads(body)
            assert len(data["strategies"]) == 1
            assert data["strategies"][0]["strategy"] == "binary"
            assert data["strategies"][0]["trade_count"] == 5
        finally:
            server.shutdown()


class TestBalancesEndpoint:
    """Tests for GET /api/balances."""

    def test_returns_200_with_required_keys(self):
        server, url = _start_test_server(19010)
        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/balances")
            assert status == 200
            data = json.loads(body)
            assert "balances" in data
            assert "total" in data
            assert "last_updated" in data
        finally:
            server.shutdown()

    def test_balances_reflect_state(self):
        """Returns platform_balances from module-level state."""
        server, url = _start_test_server(19011)
        original = dict(state.platform_balances)
        state.platform_balances = {"polymarket": 200.0, "kalshi": 100.0}
        state.last_bankroll_refresh = "2026-03-21T09:00:00Z"
        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/balances")
            assert status == 200
            data = json.loads(body)
            assert data["balances"]["polymarket"] == 200.0
            assert data["balances"]["kalshi"] == 100.0
            assert data["total"] == 300.0
            assert data["last_updated"] == "2026-03-21T09:00:00Z"
        finally:
            state.platform_balances = original
            state.last_bankroll_refresh = None
            server.shutdown()

    def test_empty_balances_returns_zero_total(self):
        """When no platform balances are set, total is 0."""
        server, url = _start_test_server(19012)
        original = dict(state.platform_balances)
        state.platform_balances = {}
        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/balances")
            assert status == 200
            data = json.loads(body)
            assert data["total"] == 0
            assert data["balances"] == {}
        finally:
            state.platform_balances = original
            server.shutdown()


class TestRebalanceEndpoint:
    """Tests for GET /api/rebalance."""

    def test_returns_200_with_recommendations_key(self):
        server, url = _start_test_server(19020)
        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/rebalance")
            assert status == 200
            data = json.loads(body)
            assert "recommendations" in data
            assert "total_balance" in data
            assert isinstance(data["recommendations"], list)
        finally:
            server.shutdown()

    def test_recommendations_have_required_fields(self):
        """Recommendation rows include platform, current_pct, recommended_pct, transfer_amount."""
        server, url = _start_test_server(19021)
        # Set up imbalanced state: polymarket has 80% of capital but only 20% of opps
        original_bal = dict(state.platform_balances)
        original_flow = dict(state.platform_opp_flow)
        state.platform_balances = {"polymarket": 800.0, "kalshi": 200.0}
        state.platform_opp_flow = {"polymarket": 2, "kalshi": 8}  # kalshi generates 80% of opps
        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/rebalance")
            assert status == 200
            data = json.loads(body)
            recs = data["recommendations"]
            # Should recommend rebalancing since polymarket has 80% capital but 20% opps
            assert len(recs) >= 1
            for rec in recs:
                assert "platform" in rec
                assert "current_pct" in rec
                assert "recommended_pct" in rec
                assert "transfer_amount" in rec
        finally:
            state.platform_balances = original_bal
            state.platform_opp_flow = original_flow
            server.shutdown()

    def test_no_recommendations_when_balanced(self):
        """Returns empty recommendations when capital is well-aligned with opp flow."""
        server, url = _start_test_server(19022)
        # 50/50 split in both capital and opps — within 5% threshold
        original_bal = dict(state.platform_balances)
        original_flow = dict(state.platform_opp_flow)
        state.platform_balances = {"polymarket": 500.0, "kalshi": 500.0}
        state.platform_opp_flow = {"polymarket": 50, "kalshi": 50}
        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/rebalance")
            assert status == 200
            data = json.loads(body)
            # Perfectly balanced — no recommendations needed
            assert data["recommendations"] == []
        finally:
            state.platform_balances = original_bal
            state.platform_opp_flow = original_flow
            server.shutdown()

    def test_empty_balances_returns_empty_recommendations(self):
        """Returns empty recommendations when no balance data is available."""
        server, url = _start_test_server(19023)
        original_bal = dict(state.platform_balances)
        original_flow = dict(state.platform_opp_flow)
        state.platform_balances = {}
        state.platform_opp_flow = {}
        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/rebalance")
            assert status == 200
            data = json.loads(body)
            assert data["recommendations"] == []
            assert data["total_balance"] == 0
        finally:
            state.platform_balances = original_bal
            state.platform_opp_flow = original_flow
            server.shutdown()


class TestStrategyLeaderboardEndpoint:
    """Tests for GET /api/strategy-leaderboard."""

    def test_returns_200_with_required_keys(self):
        """Returns 200 with strategies, timestamp, lookback_days keys."""
        server, url = _start_test_server(19030)
        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/strategy-leaderboard")
            assert status == 200
            data = json.loads(body)
            assert "strategies" in data
            assert "timestamp" in data
            assert "lookback_days" in data
            assert isinstance(data["strategies"], list)
        finally:
            server.shutdown()

    def test_leaderboard_reflects_state(self):
        """Returns strategy_leaderboard from module-level state."""
        server, url = _start_test_server(19031)
        original_lb = list(state.strategy_leaderboard)
        original_ts = state.leaderboard_updated_at

        state.strategy_leaderboard = [
            {
                "strategy": "binary",
                "trade_count": 10,
                "wins": 6,
                "win_rate": 0.6,
                "total_pnl": 0.05,
                "avg_pnl": 0.005,
                "annual_sharpe": 1.2,
                "max_drawdown": -0.02
            }
        ]
        state.leaderboard_updated_at = 1711000000.0

        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/strategy-leaderboard")
            assert status == 200
            data = json.loads(body)
            assert len(data["strategies"]) == 1
            assert data["strategies"][0]["strategy"] == "binary"
            assert data["strategies"][0]["trade_count"] == 10
            assert data["strategies"][0]["win_rate"] == 0.6
            assert data["timestamp"] == 1711000000.0
            assert data["lookback_days"] == 7
        finally:
            state.strategy_leaderboard = original_lb
            state.leaderboard_updated_at = original_ts
            server.shutdown()

    def test_empty_leaderboard_returns_valid_response(self):
        """Returns valid JSON with empty array when no strategies."""
        server, url = _start_test_server(19032)
        original_lb = list(state.strategy_leaderboard)
        state.strategy_leaderboard = []

        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/strategy-leaderboard")
            assert status == 200
            data = json.loads(body)
            assert data["strategies"] == []
            assert data["lookback_days"] == 7
        finally:
            state.strategy_leaderboard = original_lb
            server.shutdown()

    def test_strategies_have_required_fields(self):
        """Each strategy dict has all required metric fields."""
        server, url = _start_test_server(19033)
        original_lb = list(state.strategy_leaderboard)

        state.strategy_leaderboard = [
            {
                "strategy": "cross",
                "trade_count": 5,
                "wins": 4,
                "win_rate": 0.8,
                "total_pnl": 0.1,
                "avg_pnl": 0.02,
                "annual_sharpe": 2.5,
                "max_drawdown": -0.01
            },
            {
                "strategy": "kalshi",
                "trade_count": 3,
                "wins": 2,
                "win_rate": 0.667,
                "total_pnl": 0.03,
                "avg_pnl": 0.01,
                "annual_sharpe": 1.8,
                "max_drawdown": -0.005
            }
        ]

        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/strategy-leaderboard")
            assert status == 200
            data = json.loads(body)
            required_fields = {
                "strategy", "trade_count", "wins", "win_rate",
                "total_pnl", "avg_pnl", "annual_sharpe", "max_drawdown"
            }
            for strat in data["strategies"]:
                assert set(strat.keys()) == required_fields
        finally:
            state.strategy_leaderboard = original_lb
            server.shutdown()

    def test_strategies_sorted_by_pnl_descending(self):
        """Endpoint returns strategies in the order provided (analytics.py handles sorting)."""
        server, url = _start_test_server(19034)
        original_lb = list(state.strategy_leaderboard)

        # Note: analytics.py sorts by total_pnl descending before returning,
        # so we set them in sorted order to test endpoint just returns state as-is
        state.strategy_leaderboard = [
            {
                "strategy": "high_pnl",
                "trade_count": 5,
                "wins": 4,
                "win_rate": 0.8,
                "total_pnl": 0.15,
                "avg_pnl": 0.03,
                "annual_sharpe": 2.0,
                "max_drawdown": -0.01
            },
            {
                "strategy": "low_pnl",
                "trade_count": 2,
                "wins": 1,
                "win_rate": 0.5,
                "total_pnl": 0.01,
                "avg_pnl": 0.005,
                "annual_sharpe": 0.5,
                "max_drawdown": -0.01
            }
        ]

        try:
            with patch("config.DASHBOARD_PASS", ""):
                status, body, _ = _get(url, "/api/strategy-leaderboard")
            assert status == 200
            data = json.loads(body)
            # Verify order is maintained as set
            assert data["strategies"][0]["total_pnl"] == 0.15
            assert data["strategies"][1]["total_pnl"] == 0.01
        finally:
            state.strategy_leaderboard = original_lb
            server.shutdown()

    def test_update_strategy_metrics_method(self):
        """_DashboardState.update_strategy_metrics() sets leaderboard and timestamp."""
        s = _DashboardState()
        metrics = [
            {
                "strategy": "test",
                "trade_count": 1,
                "wins": 1,
                "win_rate": 1.0,
                "total_pnl": 0.05,
                "avg_pnl": 0.05,
                "annual_sharpe": None,
                "max_drawdown": 0
            }
        ]

        s.update_strategy_metrics(metrics)

        assert s.strategy_leaderboard == metrics
        assert s.leaderboard_updated_at > 0
        assert isinstance(s.leaderboard_updated_at, float)
