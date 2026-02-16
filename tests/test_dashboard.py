"""Tests for dashboard.py — HTTP status endpoint."""

import json
import pytest
from unittest.mock import patch, MagicMock
from dashboard import _DashboardState, _Handler, start_dashboard, state


class TestDashboardState:
    def test_initial_state(self):
        s = _DashboardState()
        assert s.scan_count == 0
        assert s.last_scan_time is None
        assert s.open_positions == 0
        assert s.daily_pnl == 0.0
        assert s.ws_connections == 0
        assert s.opportunities_found == 0
        assert s.last_opportunities == []

    def test_to_dict(self):
        s = _DashboardState()
        s.scan_count = 5
        s.daily_pnl = 1.23456
        d = s.to_dict()
        assert d["scan_count"] == 5
        assert d["daily_pnl"] == 1.2346  # rounded to 4 decimals
        assert d["last_scan_time"] is None
        assert d["ws_connections"] == 0

    def test_to_dict_limits_opportunities(self):
        s = _DashboardState()
        s.last_opportunities = [{"id": i} for i in range(30)]
        d = s.to_dict()
        assert len(d["last_opportunities"]) == 20

    def test_to_dict_pnl_rounding(self):
        s = _DashboardState()
        s.daily_pnl = 0.123456789
        d = s.to_dict()
        assert d["daily_pnl"] == 0.1235


class TestModuleLevelState:
    def test_state_is_dashboard_state_instance(self):
        assert isinstance(state, _DashboardState)

    def test_state_is_mutable(self):
        original = state.scan_count
        state.scan_count = 999
        assert state.scan_count == 999
        state.scan_count = original


class TestStartDashboard:
    def test_returns_none_for_zero_port(self):
        result = start_dashboard(0)
        assert result is None

    def test_returns_none_for_negative_port(self):
        result = start_dashboard(-1)
        assert result is None

    def test_starts_server_on_valid_port(self):
        server = start_dashboard(18765)
        assert server is not None
        server.shutdown()

    def test_server_is_http_server_instance(self):
        from http.server import HTTPServer
        server = start_dashboard(18766)
        assert server is not None
        assert isinstance(server, HTTPServer)
        server.shutdown()
