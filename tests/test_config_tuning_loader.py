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


class TestRecommendationAgeGate:
    """Stale or unverifiable recommendations must never be applied (B3)."""

    def _write_rec(self, tmp, generated_at, min_net_roi=0.02):
        path = os.path.join(tmp, "rec.json")
        payload = {"recommended": {"MIN_NET_ROI": min_net_roi}}
        if generated_at is not None:
            payload["generated_at"] = generated_at
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return path

    def test_fresh_recommendation_applies(self):
        import config
        from datetime import datetime, timezone
        original_roi = config.MIN_NET_ROI
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fresh = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
                path = self._write_rec(tmp, fresh, min_net_roi=0.02)
                applied = config.apply_backtest_recommendations(path)
            assert applied.get("MIN_NET_ROI") == 0.02
            assert config.MIN_NET_ROI == 0.02
        finally:
            config.MIN_NET_ROI = original_roi

    def test_stale_recommendation_not_applied(self):
        import config
        from datetime import datetime, timedelta, timezone
        original_roi = config.MIN_NET_ROI
        try:
            with tempfile.TemporaryDirectory() as tmp:
                stale_dt = datetime.now(timezone.utc) - timedelta(
                    hours=config.BACKTEST_RECOMMENDATIONS_MAX_AGE_HOURS + 1)
                stale = stale_dt.replace(tzinfo=None).isoformat() + "Z"
                path = self._write_rec(tmp, stale, min_net_roi=0.03)
                applied = config.apply_backtest_recommendations(path)
            assert applied == {}
            assert config.MIN_NET_ROI == original_roi
        finally:
            config.MIN_NET_ROI = original_roi

    def test_missing_generated_at_not_applied(self):
        import config
        original_roi = config.MIN_NET_ROI
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = self._write_rec(tmp, None, min_net_roi=0.03)
                applied = config.apply_backtest_recommendations(path)
            assert applied == {}
            assert config.MIN_NET_ROI == original_roi
        finally:
            config.MIN_NET_ROI = original_roi

    def test_unparseable_generated_at_not_applied(self):
        import config
        original_roi = config.MIN_NET_ROI
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = self._write_rec(tmp, "yesterday-ish", min_net_roi=0.03)
                applied = config.apply_backtest_recommendations(path)
            assert applied == {}
            assert config.MIN_NET_ROI == original_roi
        finally:
            config.MIN_NET_ROI = original_roi

    def test_age_helper_parses_utc_z_format(self):
        import config
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        age = config._recommendation_age_hours(fresh)
        assert age is not None and 0 <= age < 0.1
        assert config._recommendation_age_hours("") is None
        assert config._recommendation_age_hours("not-a-date") is None


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
