"""Tests for scripts/tune.py — threshold tuning loop (#20).

Coverage:
- format_recommendations_md emits well-formed markdown with the
  expected sections, columns, and delta arithmetic
- run_tune writes both the markdown and JSON artefacts and returns
  the right paths
- _format_num / _format_delta edge cases
"""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stable module reference (tune does not pop sys.modules so simple import
# is fine).
import scripts.tune as tune_mod


# ---------------------------------------------------------------------------
# format_recommendations_md
# ---------------------------------------------------------------------------


def _sample_rec(**overrides):
    base = {
        "generated_at": "2026-05-10T12:00:00Z",
        "period_days": 30,
        "total_trades": 42,
        "win_rate": 0.65,
        "recommended": {
            "MIN_NET_ROI": 0.025,
            "FUZZY_MATCH_THRESHOLD": 75,
            "MIN_PROFIT_THRESHOLD": 0.012,
        },
        "current": {
            "MIN_NET_ROI": 0.020,
            "FUZZY_MATCH_THRESHOLD": 72,
            "MIN_PROFIT_THRESHOLD": 0.010,
        },
        "by_strategy": {
            "Binary": {"win_rate": 0.80, "avg_profit": 0.015},
            "Cross": {"win_rate": 0.55, "avg_profit": 0.005},
        },
    }
    base.update(overrides)
    return base


class TestMarkdown:
    def test_includes_header_and_summary(self):
        out = tune_mod.format_recommendations_md(_sample_rec(), window_days=30)
        assert "# Tuning Report" in out
        assert "rolling 30 days" in out
        assert "Trades evaluated:** 42" in out
        assert "65.0%" in out  # win rate

    def test_threshold_table_includes_all_keys(self):
        out = tune_mod.format_recommendations_md(_sample_rec(), window_days=30)
        for key in ("MIN_NET_ROI", "FUZZY_MATCH_THRESHOLD", "MIN_PROFIT_THRESHOLD"):
            assert f"`{key}`" in out

    def test_delta_arrow_correctly_renders_increase(self):
        rec = _sample_rec(
            recommended={"MIN_NET_ROI": 0.025},
            current={"MIN_NET_ROI": 0.020},
        )
        out = tune_mod.format_recommendations_md(rec, window_days=30)
        # Recommended raised → up arrow with +25% drift.
        assert "↑" in out
        assert "+25.0%" in out

    def test_delta_arrow_correctly_renders_decrease(self):
        rec = _sample_rec(
            recommended={"MIN_NET_ROI": 0.018},
            current={"MIN_NET_ROI": 0.020},
        )
        out = tune_mod.format_recommendations_md(rec, window_days=30)
        assert "↓" in out
        assert "-10.0%" in out

    def test_no_change_label(self):
        rec = _sample_rec(
            recommended={"MIN_NET_ROI": 0.020},
            current={"MIN_NET_ROI": 0.020},
        )
        out = tune_mod.format_recommendations_md(rec, window_days=30)
        assert "no change" in out

    def test_per_strategy_table_renders(self):
        out = tune_mod.format_recommendations_md(_sample_rec(), window_days=30)
        assert "## Per-strategy breakdown" in out
        assert "Binary" in out
        assert "Cross" in out
        assert "80.0%" in out
        assert "55.0%" in out

    def test_omits_strategy_section_when_empty(self):
        rec = _sample_rec(by_strategy={})
        out = tune_mod.format_recommendations_md(rec, window_days=30)
        assert "## Per-strategy breakdown" not in out

    def test_handles_zero_trades(self):
        rec = _sample_rec(total_trades=0, win_rate=0.0, by_strategy={})
        out = tune_mod.format_recommendations_md(rec, window_days=30)
        assert "Trades evaluated:** 0" in out
        assert "0.0%" in out

    def test_apply_section_present(self):
        out = tune_mod.format_recommendations_md(_sample_rec(), window_days=30)
        assert "## Apply" in out
        assert "advisory only" in out


# ---------------------------------------------------------------------------
# _format_num / _format_delta
# ---------------------------------------------------------------------------


class TestFormatHelpers:
    def test_format_num_handles_none(self):
        assert tune_mod._format_num(None) == "—"

    def test_format_num_int(self):
        assert tune_mod._format_num(72) == "72"

    def test_format_num_small_float(self):
        assert tune_mod._format_num(0.025) == "0.0250"

    def test_format_num_large_float(self):
        assert tune_mod._format_num(125.5) == "125.50"

    def test_format_delta_none_inputs(self):
        assert tune_mod._format_delta(None, 0.5) == "—"
        assert tune_mod._format_delta(0.5, None) == "—"

    def test_format_delta_zero_change(self):
        assert tune_mod._format_delta(0.5, 0.5) == "no change"

    def test_format_delta_handles_zero_current(self):
        # Should not raise on division by zero.
        out = tune_mod._format_delta(0.0, 0.1)
        assert "↑" in out

    def test_format_delta_non_numeric(self):
        assert tune_mod._format_delta("five", "ten") == "—"


# ---------------------------------------------------------------------------
# run_tune end-to-end with an injected fake engine
# ---------------------------------------------------------------------------


def _fake_engine_and_rec_module():
    """Build a fake BacktestEngine + monkey-patch build_recommendations
    so we don't need any real snapshots database."""
    fake_result = MagicMock(name="BacktestResult")
    fake_engine = MagicMock(name="BacktestEngine")
    fake_engine.run.return_value = fake_result
    return fake_engine, fake_result


class TestRunTune:
    def test_writes_markdown_and_json(self, tmp_path):
        fake_engine, fake_result = _fake_engine_and_rec_module()
        # Patch build_recommendations on the imported backtest module so
        # tune_mod's lazy import gets the fake.
        import backtest
        original_build = backtest.build_recommendations
        try:
            backtest.build_recommendations = lambda _r: _sample_rec()
            paths = tune_mod.run_tune(
                window_days=14,
                output_dir=str(tmp_path),
                now=datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc),
                backtest_engine=fake_engine,
            )
        finally:
            backtest.build_recommendations = original_build

        assert paths["markdown_path"].endswith("tuning_2026-05-10.md")
        assert os.path.exists(paths["markdown_path"])
        assert "json_path" in paths
        assert os.path.exists(paths["json_path"])

        md = open(paths["markdown_path"], encoding="utf-8").read()
        assert "rolling 14 days" in md

        # JSON should be parseable and have the same recommendations.
        with open(paths["json_path"], encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["recommended"]["MIN_NET_ROI"] == 0.025
        # period_days is overridden to match --window-days.
        assert payload["period_days"] == 14

    def test_no_json_flag(self, tmp_path):
        fake_engine, _ = _fake_engine_and_rec_module()
        import backtest
        original_build = backtest.build_recommendations
        try:
            backtest.build_recommendations = lambda _r: _sample_rec()
            paths = tune_mod.run_tune(
                window_days=7,
                output_dir=str(tmp_path),
                now=datetime(2026, 5, 10, tzinfo=timezone.utc),
                backtest_engine=fake_engine,
                write_json=False,
            )
        finally:
            backtest.build_recommendations = original_build

        assert "json_path" not in paths
        # Only the markdown was written.
        files = sorted(os.listdir(tmp_path))
        assert files == ["tuning_2026-05-10.md"]

    def test_creates_output_dir_if_missing(self, tmp_path):
        fake_engine, _ = _fake_engine_and_rec_module()
        target = tmp_path / "nested" / "subdir"
        import backtest
        original_build = backtest.build_recommendations
        try:
            backtest.build_recommendations = lambda _r: _sample_rec()
            paths = tune_mod.run_tune(
                window_days=30,
                output_dir=str(target),
                now=datetime(2026, 5, 10, tzinfo=timezone.utc),
                backtest_engine=fake_engine,
            )
        finally:
            backtest.build_recommendations = original_build
        assert target.exists()
        assert os.path.exists(paths["markdown_path"])
