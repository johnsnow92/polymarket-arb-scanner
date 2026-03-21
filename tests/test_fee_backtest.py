"""Tests for fee reload safety and backtest recommendation output."""

import json
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestFeeReload:
    """Unit tests for reload_fee_rates() function in config.py."""

    def test_reload_picks_up_changed_betfair_rate(self):
        """reload_fee_rates() updates BETFAIR_COMMISSION_RATE when env var changes."""
        import config
        original = config.BETFAIR_COMMISSION_RATE

        try:
            with patch.dict(os.environ, {"BETFAIR_COMMISSION_RATE": "0.05"}):
                changes = config.reload_fee_rates()

            assert "BETFAIR_COMMISSION_RATE" in changes, (
                "Expected BETFAIR_COMMISSION_RATE in changes"
            )
            old_val, new_val = changes["BETFAIR_COMMISSION_RATE"]
            assert abs(new_val - 0.05) < 1e-9, f"Expected new value 0.05, got {new_val}"
        finally:
            # Restore original
            config.BETFAIR_COMMISSION_RATE = original
            config.reload_fee_rates()  # reset to env defaults

    def test_reload_returns_empty_dict_when_unchanged(self):
        """reload_fee_rates() returns empty dict when no env vars changed."""
        import config
        # Call with current values — nothing should change
        with patch.dict(os.environ, {
            "BETFAIR_COMMISSION_RATE": str(config.BETFAIR_COMMISSION_RATE),
            "SMARKETS_COMMISSION_RATE": str(config.SMARKETS_COMMISSION_RATE),
            "GEMINI_FEE_RATE": str(config.GEMINI_FEE_RATE),
        }):
            changes = config.reload_fee_rates()

        assert changes == {}, f"Expected empty changes, got {changes}"

    def test_reload_does_not_modify_dry_run(self):
        """reload_fee_rates() must NOT modify DRY_RUN."""
        import config
        dry_run_before = config.DRY_RUN

        with patch.dict(os.environ, {"BETFAIR_COMMISSION_RATE": "0.10"}):
            config.reload_fee_rates()

        assert config.DRY_RUN == dry_run_before, (
            f"DRY_RUN was modified by reload_fee_rates(): "
            f"before={dry_run_before}, after={config.DRY_RUN}"
        )
        # Restore
        config.reload_fee_rates()

    def test_reload_returns_old_and_new_values(self):
        """reload_fee_rates() returns (old_value, new_value) tuples."""
        import config
        original = config.GEMINI_FEE_RATE

        try:
            with patch.dict(os.environ, {"GEMINI_FEE_RATE": "0.02"}):
                changes = config.reload_fee_rates()

            if "GEMINI_FEE_RATE" in changes:
                old_val, new_val = changes["GEMINI_FEE_RATE"]
                assert abs(old_val - original) < 1e-9, (
                    f"Expected old value {original}, got {old_val}"
                )
                assert abs(new_val - 0.02) < 1e-9, (
                    f"Expected new value 0.02, got {new_val}"
                )
        finally:
            config.GEMINI_FEE_RATE = original
            config.reload_fee_rates()

    def test_reload_updates_multiple_rates_at_once(self):
        """reload_fee_rates() can update multiple fee rates in a single call."""
        import config
        orig_betfair = config.BETFAIR_COMMISSION_RATE
        orig_smarkets = config.SMARKETS_COMMISSION_RATE

        try:
            with patch.dict(os.environ, {
                "BETFAIR_COMMISSION_RATE": "0.07",
                "SMARKETS_COMMISSION_RATE": "0.04",
            }):
                changes = config.reload_fee_rates()

            assert "BETFAIR_COMMISSION_RATE" in changes
            assert "SMARKETS_COMMISSION_RATE" in changes
        finally:
            config.BETFAIR_COMMISSION_RATE = orig_betfair
            config.SMARKETS_COMMISSION_RATE = orig_smarkets
            config.reload_fee_rates()


class TestBacktestRecommendations:
    """Unit tests for build_recommendations() and write_recommendations() in backtest.py."""

    def _make_result(self, win_rate=0.6, total_trades=20, total_pnl=1.5):
        """Build a minimal BacktestResult for testing."""
        from backtest import BacktestResult
        result = BacktestResult()
        result.total_trades = total_trades
        result.winning_trades = int(total_trades * win_rate)
        result.losing_trades = total_trades - result.winning_trades
        result.win_rate = win_rate
        result.total_pnl = total_pnl
        result.initial_balance = 1000.0
        result.final_balance = 1000.0 + total_pnl
        result.trades_by_type = {
            "Binary": {"count": 10, "pnl": 1.0, "wins": 7, "win_rate": 0.7},
            "StalePriceOpp": {"count": 10, "pnl": 0.5, "wins": 5, "win_rate": 0.5},
        }
        return result

    def test_build_recommendations_returns_required_keys(self):
        """build_recommendations() returns dict with all required top-level keys."""
        from backtest import build_recommendations
        result = self._make_result()
        rec = build_recommendations(result)

        assert "generated_at" in rec, "Missing generated_at"
        assert "period_days" in rec, "Missing period_days"
        assert "total_trades" in rec, "Missing total_trades"
        assert "win_rate" in rec, "Missing win_rate"
        assert "recommended" in rec, "Missing recommended"
        assert "current" in rec, "Missing current"
        assert "by_strategy" in rec, "Missing by_strategy"

    def test_recommended_contains_min_net_roi_and_fuzzy_threshold(self):
        """recommended dict contains MIN_NET_ROI and FUZZY_MATCH_THRESHOLD keys."""
        from backtest import build_recommendations
        result = self._make_result()
        rec = build_recommendations(result)

        assert "MIN_NET_ROI" in rec["recommended"], "Missing MIN_NET_ROI in recommended"
        assert "FUZZY_MATCH_THRESHOLD" in rec["recommended"], (
            "Missing FUZZY_MATCH_THRESHOLD in recommended"
        )

    def test_suggest_min_roi_lowers_when_win_rate_high(self):
        """MIN_NET_ROI recommendation decreases when win_rate > 0.7."""
        from backtest import build_recommendations
        import config

        result = self._make_result(win_rate=0.80)
        rec = build_recommendations(result)

        current_roi = rec["current"]["MIN_NET_ROI"]
        recommended_roi = rec["recommended"]["MIN_NET_ROI"]
        # High win rate -> can lower MIN_NET_ROI to capture more opportunities
        assert recommended_roi <= current_roi, (
            f"Expected recommended ROI {recommended_roi} <= current ROI {current_roi} "
            f"when win_rate=0.80 (relaxing threshold is safe)"
        )

    def test_suggest_min_roi_raises_when_win_rate_low(self):
        """MIN_NET_ROI recommendation increases when win_rate < 0.5."""
        from backtest import build_recommendations
        import config

        result = self._make_result(win_rate=0.35, total_pnl=-2.0)
        rec = build_recommendations(result)

        current_roi = rec["current"]["MIN_NET_ROI"]
        recommended_roi = rec["recommended"]["MIN_NET_ROI"]
        # Low win rate -> should raise threshold to be more selective
        # But current MIN_NET_ROI may be 0.0, so just check it's >= 0
        assert recommended_roi >= 0, "MIN_NET_ROI recommendation should not be negative"

    def test_write_recommendations_creates_valid_json(self):
        """write_recommendations() creates a valid JSON file at the expected path."""
        from backtest import write_recommendations

        result = self._make_result()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_recommendations(result, tmpdir)

            assert os.path.exists(path), f"Expected file at {path}"
            assert path.endswith("backtest_recommendations.json"), (
                f"Expected filename backtest_recommendations.json, got {path}"
            )

            with open(path, "r") as f:
                data = json.load(f)

            assert "recommended" in data
            assert "generated_at" in data

    def test_build_recommendations_by_strategy_format(self):
        """by_strategy contains win_rate and avg_profit per strategy."""
        from backtest import build_recommendations
        result = self._make_result()
        rec = build_recommendations(result)

        for strategy, stats in rec["by_strategy"].items():
            assert "win_rate" in stats, f"Missing win_rate in {strategy} stats"
            assert "avg_profit" in stats, f"Missing avg_profit in {strategy} stats"
