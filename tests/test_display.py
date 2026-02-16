"""Tests for display.py — result formatting."""

import io
import sys
import json
import pytest
from display import display_results


class TestDisplayResults:
    def test_no_opportunities_message(self, capsys):
        display_results([])
        captured = capsys.readouterr()
        assert "0 arbitrage opportunities found" in captured.out
        assert "No opportunities above" in captured.out

    def test_table_output(self, capsys):
        opps = [{
            "type": "Binary",
            "market": "Test Market",
            "prices": "Y=0.45 N=0.50",
            "total_cost": "$0.9500",
            "gross_spread": "0.0500",
            "fees": "$0.0200",
            "net_profit": 0.03,
            "net_roi": "3.16%",
            "volume": "$1,000",
        }]
        display_results(opps, json_output=False)
        captured = capsys.readouterr()
        assert "1 arbitrage opportunities found" in captured.out
        assert "Binary" in captured.out
        assert "$0.0300" in captured.out

    def test_json_output(self, capsys):
        opps = [{
            "type": "Binary",
            "market": "Test",
            "prices": "Y=0.45",
            "total_cost": "$0.95",
            "gross_spread": "0.05",
            "fees": "$0.02",
            "net_profit": 0.03,
            "net_roi": "3%",
        }]
        display_results(opps, json_output=True)
        captured = capsys.readouterr()
        # JSON should be parseable
        lines = captured.out.strip().split("\n")
        # Find the JSON portion (after the header)
        json_start = None
        for i, line in enumerate(lines):
            if line.strip().startswith("["):
                json_start = i
                break
        assert json_start is not None
        json_text = "\n".join(lines[json_start:])
        # Remove trailing disclaimer
        json_text = json_text.split("\n  Disclaimer")[0]
        data = json.loads(json_text)
        assert len(data) == 1
        assert data[0]["type"] == "Binary"

    def test_cross_platform_columns(self, capsys):
        opps = [{
            "type": "Cross",
            "market": "Test",
            "kalshi": "Kalshi Test",
            "match": "95%",
            "confidence": "HIGH",
            "prices": "PM_Y=0.40 K_N=0.30",
            "total_cost": "$0.70",
            "gross_spread": "0.30",
            "fees": "$0.05",
            "net_profit": 0.25,
            "net_roi": "35%",
        }]
        display_results(opps, json_output=False)
        captured = capsys.readouterr()
        assert "Kalshi" in captured.out
        assert "Match" in captured.out
        assert "HIGH" in captured.out

    def test_depth_column(self, capsys):
        opps = [{
            "type": "Binary",
            "market": "Test",
            "prices": "Y=0.45",
            "total_cost": "$0.95",
            "gross_spread": "0.05",
            "fees": "$0.02",
            "net_profit": 0.03,
            "net_roi": "3%",
            "_clob_depth": 150,
        }]
        display_results(opps, json_output=False)
        captured = capsys.readouterr()
        assert "Depth" in captured.out
        assert "150" in captured.out

    def test_disclaimer_always_shown(self, capsys):
        display_results([])
        captured = capsys.readouterr()
        # Disclaimer should not be shown for empty results
        # Actually, the function returns early for no opps
        display_results([{
            "type": "Binary", "market": "T", "prices": "Y=0.45",
            "total_cost": "$0.95", "gross_spread": "0.05", "fees": "$0.02",
            "net_profit": 0.03, "net_roi": "3%",
        }])
        captured = capsys.readouterr()
        assert "Disclaimer" in captured.out
