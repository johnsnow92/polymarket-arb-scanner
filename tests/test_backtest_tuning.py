"""Tests for the backtest tuning loop — build_recommendations per-strategy
output, config-side loader/applier, and the scripts/tune.py CLI."""

import importlib
import json
import logging
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _make_result(by_strategy: dict, *, win_rate: float = 0.6, total_trades: int = 20):
    """Build a minimal BacktestResult with the given per-strategy stats."""
    from backtest import BacktestResult
    result = BacktestResult()
    result.total_trades = total_trades
    result.winning_trades = int(total_trades * win_rate)
    result.losing_trades = total_trades - result.winning_trades
    result.win_rate = win_rate
    result.total_pnl = 1.0
    result.initial_balance = 1000.0
    result.final_balance = 1001.0
    result.trades_by_type = by_strategy
    return result


class TestBuildRecommendationsByStrategy:
    """Layer 1 — backtest.build_recommendations must emit per-strategy entries."""

    def test_recommended_by_strategy_present(self):
        from backtest import build_recommendations
        result = _make_result({
            "Binary": {"count": 12, "wins": 9, "pnl": 1.2, "win_rate": 0.75},
        })
        rec = build_recommendations(result)
        assert "recommended_by_strategy" in rec, "missing recommended_by_strategy key"
        assert isinstance(rec["recommended_by_strategy"], dict)

    def test_includes_strategy_with_at_least_10_trades(self):
        from backtest import build_recommendations
        result = _make_result({
            "Binary": {"count": 10, "wins": 7, "pnl": 0.9, "win_rate": 0.7},
            "KalshiBinary": {"count": 25, "wins": 14, "pnl": 1.4, "win_rate": 0.56},
        })
        rec = build_recommendations(result)
        rbs = rec["recommended_by_strategy"]
        assert "Binary" in rbs, "Strategy with exactly 10 trades must be included"
        assert "KalshiBinary" in rbs
        for entry in rbs.values():
            assert "MIN_NET_ROI" in entry
            assert "FUZZY_MATCH_THRESHOLD" in entry
            assert 0.001 <= entry["MIN_NET_ROI"] <= 0.05
            assert 60 <= entry["FUZZY_MATCH_THRESHOLD"] <= 90

    def test_omits_strategy_below_threshold(self):
        from backtest import build_recommendations
        result = _make_result({
            "Binary": {"count": 12, "wins": 9, "pnl": 1.2, "win_rate": 0.75},
            "RareStrat": {"count": 3, "wins": 2, "pnl": 0.1, "win_rate": 0.67},
        })
        rec = build_recommendations(result)
        rbs = rec["recommended_by_strategy"]
        assert "Binary" in rbs
        assert "RareStrat" not in rbs, "Strategy with <10 trades must be omitted"

    def test_empty_result_yields_empty_recommended_by_strategy(self):
        from backtest import build_recommendations
        result = _make_result({}, total_trades=0)
        rec = build_recommendations(result)
        assert rec["recommended_by_strategy"] == {}


class TestConfigLoader:
    """Layer 2 — config.load_backtest_recommendations + apply must be defensive
    and must respect the BACKTEST_TUNING_ENABLED flag."""

    def test_load_missing_file_returns_none(self):
        import config
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "does_not_exist.json")
            assert config.load_backtest_recommendations(missing) is None

    def test_load_malformed_json_returns_none(self):
        import config
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rec.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not valid json")
            assert config.load_backtest_recommendations(path) is None

    def test_load_non_object_payload_returns_none(self):
        import config
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rec.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(["not", "an", "object"], fh)
            assert config.load_backtest_recommendations(path) is None

    def test_apply_with_missing_keys_keeps_defaults(self):
        import config
        original_roi = config.MIN_NET_ROI
        original_fuzzy = config.FUZZY_MATCH_THRESHOLD
        original_per = dict(config.RECOMMENDED_BY_STRATEGY)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "rec.json")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({"generated_at": "now", "period_days": 7}, fh)
                applied = config.apply_backtest_recommendations(path)
            assert config.MIN_NET_ROI == original_roi
            assert config.FUZZY_MATCH_THRESHOLD == original_fuzzy
            assert "MIN_NET_ROI" not in applied
            assert "FUZZY_MATCH_THRESHOLD" not in applied
        finally:
            config.MIN_NET_ROI = original_roi
            config.FUZZY_MATCH_THRESHOLD = original_fuzzy
            config.RECOMMENDED_BY_STRATEGY = original_per

    def test_apply_out_of_range_values_keeps_defaults(self):
        import config
        original_roi = config.MIN_NET_ROI
        original_fuzzy = config.FUZZY_MATCH_THRESHOLD
        original_per = dict(config.RECOMMENDED_BY_STRATEGY)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "rec.json")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({
                        "recommended": {
                            "MIN_NET_ROI": 99.0,
                            "FUZZY_MATCH_THRESHOLD": 9001,
                        },
                        "recommended_by_strategy": "not-a-dict",
                    }, fh)
                config.apply_backtest_recommendations(path)
            assert config.MIN_NET_ROI == original_roi
            assert config.FUZZY_MATCH_THRESHOLD == original_fuzzy
        finally:
            config.MIN_NET_ROI = original_roi
            config.FUZZY_MATCH_THRESHOLD = original_fuzzy
            config.RECOMMENDED_BY_STRATEGY = original_per

    def test_apply_valid_recommendations_updates_globals_and_logs(self, caplog):
        import config
        original_roi = config.MIN_NET_ROI
        original_fuzzy = config.FUZZY_MATCH_THRESHOLD
        original_per = dict(config.RECOMMENDED_BY_STRATEGY)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "rec.json")
                from datetime import datetime, timezone
                fresh_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
                payload = {
                    "generated_at": fresh_ts,
                    "recommended": {
                        "MIN_NET_ROI": 0.0123,
                        "FUZZY_MATCH_THRESHOLD": 78,
                    },
                    "recommended_by_strategy": {
                        "Binary": {"MIN_NET_ROI": 0.011, "FUZZY_MATCH_THRESHOLD": 80},
                        "Bad": {"MIN_NET_ROI": 5.0},
                    },
                }
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                with caplog.at_level(logging.WARNING, logger="config"):
                    applied = config.apply_backtest_recommendations(path)

            assert abs(config.MIN_NET_ROI - 0.0123) < 1e-9
            assert config.FUZZY_MATCH_THRESHOLD == 78
            assert applied["MIN_NET_ROI"] == 0.0123
            assert applied["FUZZY_MATCH_THRESHOLD"] == 78
            assert "Binary" in config.RECOMMENDED_BY_STRATEGY
            assert config.RECOMMENDED_BY_STRATEGY["Binary"]["FUZZY_MATCH_THRESHOLD"] == 80
            assert "Bad" not in config.RECOMMENDED_BY_STRATEGY
            joined = " ".join(record.getMessage() for record in caplog.records)
            assert "Loaded backtest recommendations" in joined
        finally:
            config.MIN_NET_ROI = original_roi
            config.FUZZY_MATCH_THRESHOLD = original_fuzzy
            config.RECOMMENDED_BY_STRATEGY = original_per

    def test_flag_off_leaves_min_net_roi_at_env_default_even_when_file_exists(self):
        """Invariant: with BACKTEST_TUNING_ENABLED=false, importing config
        must yield the env-var default MIN_NET_ROI even when a valid
        recommendations file exists at DATA_DIR/backtest_recommendations.json.
        """
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            rec_path = os.path.join(tmp, "backtest_recommendations.json")
            with open(rec_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "recommended": {
                        "MIN_NET_ROI": 0.0421,
                        "FUZZY_MATCH_THRESHOLD": 88,
                    },
                    "recommended_by_strategy": {},
                }, fh)
            env = os.environ.copy()
            env["BACKTEST_TUNING_ENABLED"] = "false"
            env["BACKTEST_RECOMMENDATIONS_PATH"] = rec_path
            env["MIN_NET_ROI"] = "0.0077"
            env["FUZZY_MATCH_THRESHOLD"] = "71"
            env["DATA_DIR"] = tmp
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import config; print(config.MIN_NET_ROI); print(config.FUZZY_MATCH_THRESHOLD)"],
                cwd=repo_root, env=env, capture_output=True, text=True, timeout=60,
            )
            assert proc.returncode == 0, proc.stderr
            roi_line, fuzzy_line = proc.stdout.strip().splitlines()[-2:]
            assert abs(float(roi_line) - 0.0077) < 1e-9, (
                f"With flag OFF, MIN_NET_ROI should be env-var default; got {roi_line!r}"
            )
            assert int(fuzzy_line) == 71

    def test_flag_on_applies_recommendations_at_import(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as tmp:
            rec_path = os.path.join(tmp, "backtest_recommendations.json")
            with open(rec_path, "w", encoding="utf-8") as fh:
                from datetime import datetime, timezone
                fresh_ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
                json.dump({
                    "generated_at": fresh_ts,
                    "recommended": {
                        "MIN_NET_ROI": 0.0321,
                        "FUZZY_MATCH_THRESHOLD": 81,
                    },
                    "recommended_by_strategy": {
                        "Binary": {"MIN_NET_ROI": 0.018, "FUZZY_MATCH_THRESHOLD": 76},
                    },
                }, fh)
            env = os.environ.copy()
            env["BACKTEST_TUNING_ENABLED"] = "true"
            env["BACKTEST_RECOMMENDATIONS_PATH"] = rec_path
            env["MIN_NET_ROI"] = "0.0077"
            env["FUZZY_MATCH_THRESHOLD"] = "71"
            env["DATA_DIR"] = tmp
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import config; print(config.MIN_NET_ROI); "
                 "print(config.FUZZY_MATCH_THRESHOLD); "
                 "print(sorted(config.RECOMMENDED_BY_STRATEGY.keys()))"],
                cwd=repo_root, env=env, capture_output=True, text=True, timeout=60,
            )
            assert proc.returncode == 0, proc.stderr
            out_lines = proc.stdout.strip().splitlines()
            assert abs(float(out_lines[-3]) - 0.0321) < 1e-9
            assert int(out_lines[-2]) == 81
            assert "Binary" in out_lines[-1]
            assert "Loaded backtest recommendations" in proc.stderr


class TestTuneScript:
    """Layer 3 — scripts/tune.py CLI must accept --days and write to file path."""

    def test_cli_accepts_days_and_writes_file_path(self):
        from scripts import tune as tune_mod
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "rec.json")

            class _StubEngine:
                def run(self, **_kwargs):
                    from backtest import BacktestResult
                    r = BacktestResult()
                    r.total_trades = 12
                    r.winning_trades = 8
                    r.losing_trades = 4
                    r.win_rate = 0.66
                    r.initial_balance = 1000.0
                    r.final_balance = 1001.0
                    r.trades_by_type = {
                        "Binary": {"count": 12, "wins": 8, "pnl": 0.5, "win_rate": 0.66},
                    }
                    return r

            paths = tune_mod.run_tune(
                window_days=7,
                output_dir=tmp,
                backtest_engine=_StubEngine(),
                json_path=out_path,
                write_markdown=False,
            )
            assert paths["json_path"] == out_path
            assert os.path.exists(out_path)
            with open(out_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            assert "recommended_by_strategy" in data
            assert "Binary" in data["recommended_by_strategy"]
            assert data["period_days"] == 7
