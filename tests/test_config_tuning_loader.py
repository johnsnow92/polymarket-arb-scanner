"""Tests for the config-side tuning loader (Strategy #20 follow-up).

Covers the graceful-fallback contract:
- ``config.load_backtest_recommendations()`` returns ``None`` (never raises)
  when the JSON file is missing, empty, malformed, schema-mismatched, or
  contains a non-object payload.
- ``config.apply_backtest_recommendations()`` returns a dict without
  bubbling exceptions even when the source file is bad.
- The module-level ``BACKTEST_RECOMMENDATIONS_APPLIED`` attribute is
  unconditionally defined and is either ``False`` or a dict — never None.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TestLoaderGracefulFallback:
    """The loader must survive every form of missing / bad input."""

    def test_missing_file_returns_none(self):
        import config
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nope.json")
            assert config.load_backtest_recommendations(missing) is None

    def test_empty_file_returns_none(self):
        import config
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rec.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("")
            assert config.load_backtest_recommendations(path) is None

    def test_malformed_json_returns_none(self):
        import config
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rec.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not: valid, json")
            assert config.load_backtest_recommendations(path) is None

    def test_array_payload_returns_none(self):
        import config
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rec.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([1, 2, 3], fh)
            assert config.load_backtest_recommendations(path) is None

    def test_scalar_payload_returns_none(self):
        import config
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rec.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(42, fh)
            assert config.load_backtest_recommendations(path) is None


class TestApplyGracefulFallback:
    """apply_* must never raise; preserves existing defaults on bad input."""

    def test_apply_missing_file_returns_empty_dict(self):
        import config
        original_roi = config.MIN_NET_ROI
        original_fuzzy = config.FUZZY_MATCH_THRESHOLD
        try:
            with tempfile.TemporaryDirectory() as tmp:
                missing = os.path.join(tmp, "absent.json")
                applied = config.apply_backtest_recommendations(missing)
            assert applied == {}
            assert config.MIN_NET_ROI == original_roi
            assert config.FUZZY_MATCH_THRESHOLD == original_fuzzy
        finally:
            config.MIN_NET_ROI = original_roi
            config.FUZZY_MATCH_THRESHOLD = original_fuzzy

    def test_apply_malformed_json_does_not_raise(self):
        import config
        original_roi = config.MIN_NET_ROI
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "rec.json")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("garbage{}{")
                applied = config.apply_backtest_recommendations(path)
            assert applied == {}
            assert config.MIN_NET_ROI == original_roi
        finally:
            config.MIN_NET_ROI = original_roi


class TestModuleLevelAttribute:
    """BACKTEST_RECOMMENDATIONS_APPLIED must always be defined."""

    def test_attribute_exists_after_import(self):
        import config
        assert hasattr(config, "BACKTEST_RECOMMENDATIONS_APPLIED")

    def test_attribute_is_dict_or_false_never_none(self):
        import config
        value = config.BACKTEST_RECOMMENDATIONS_APPLIED
        assert value is not None
        assert isinstance(value, (dict, bool))
        if isinstance(value, bool):
            assert value is False, (
                "When BACKTEST_RECOMMENDATIONS_APPLIED is a bool it must be False; "
                f"got {value!r}"
            )
