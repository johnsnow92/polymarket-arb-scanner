"""Tests for metrics.py — lightweight Prometheus-compatible metrics collection."""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from metrics import MetricsCollector, _Counter, _Gauge, _Histogram, _labels_to_tuple, _labels_to_prom


class TestCounter:
    def test_increment_default(self):
        c = _Counter()
        c.inc()
        assert c.get()[()] == 1

    def test_increment_by_value(self):
        c = _Counter()
        c.inc(value=5)
        assert c.get()[()] == 5

    def test_increment_with_labels(self):
        c = _Counter()
        labels = (("platform", "kalshi"),)
        c.inc(labels, 1)
        c.inc(labels, 2)
        assert c.get()[labels] == 3

    def test_multiple_label_sets(self):
        c = _Counter()
        c.inc((("a", "1"),), 1)
        c.inc((("a", "2"),), 10)
        values = c.get()
        assert values[(("a", "1"),)] == 1
        assert values[(("a", "2"),)] == 10


class TestGauge:
    def test_set_and_get(self):
        g = _Gauge()
        g.set((), 42)
        assert g.get()[()] == 42

    def test_overwrite(self):
        g = _Gauge()
        g.set((), 1)
        g.set((), 99)
        assert g.get()[()] == 99

    def test_labeled(self):
        g = _Gauge()
        g.set((("x", "y"),), 5)
        assert g.get()[(("x", "y"),)] == 5


class TestHistogram:
    def test_observe_single(self):
        h = _Histogram()
        h.observe((), 0.5)
        data = h.get()
        assert data[()]["count"] == 1
        assert data[()]["sum"] == 0.5

    def test_observe_multiple(self):
        h = _Histogram()
        h.observe((), 0.1)
        h.observe((), 0.2)
        h.observe((), 5.0)
        data = h.get()
        assert data[()]["count"] == 3
        assert abs(data[()]["sum"] - 5.3) < 0.001

    def test_bucket_counts(self):
        h = _Histogram(buckets=(1.0, 5.0, float("inf")))
        h.observe((), 0.5)
        h.observe((), 2.0)
        h.observe((), 10.0)
        data = h.get()
        buckets = data[()]["buckets"]
        # Buckets are cumulative: each bucket counts values <= its bound
        assert buckets[1.0] == 1   # 0.5 <= 1.0
        assert buckets[5.0] == 2   # 0.5 <= 5.0, 2.0 <= 5.0
        assert buckets[float("inf")] == 3  # all values <= inf


class TestLabelHelpers:
    def test_labels_to_tuple_none(self):
        assert _labels_to_tuple(None) == ()

    def test_labels_to_tuple_empty(self):
        assert _labels_to_tuple({}) == ()

    def test_labels_to_tuple_sorted(self):
        result = _labels_to_tuple({"z": "1", "a": "2"})
        assert result == (("a", "2"), ("z", "1"))

    def test_labels_to_prom_empty(self):
        assert _labels_to_prom(()) == ""

    def test_labels_to_prom_single(self):
        result = _labels_to_prom((("platform", "kalshi"),))
        assert result == '{platform="kalshi"}'

    def test_labels_to_prom_multiple(self):
        result = _labels_to_prom((("a", "1"), ("b", "2")))
        assert result == '{a="1",b="2"}'


class TestMetricsCollector:
    def test_inc_counter(self):
        m = MetricsCollector()
        m.inc("scans_total")
        m.inc("scans_total")
        data = m.get_all()
        assert data["counters"]["scans_total"] == 2

    def test_inc_with_labels(self):
        m = MetricsCollector()
        m.inc("trades_executed", {"platform": "kalshi", "status": "filled"})
        m.inc("trades_executed", {"platform": "kalshi", "status": "filled"})
        data = m.get_all()
        assert isinstance(data["counters"]["trades_executed"], dict)

    def test_set_gauge(self):
        m = MetricsCollector()
        m.set("active_positions", value=7)
        data = m.get_all()
        assert data["gauges"]["active_positions"] == 7

    def test_observe_histogram(self):
        m = MetricsCollector()
        m.observe("scan_duration_seconds", value=1.5)
        m.observe("scan_duration_seconds", value=2.5)
        data = m.get_all()
        assert data["histograms"]["scan_duration_seconds"]["count"] == 2
        assert abs(data["histograms"]["scan_duration_seconds"]["sum"] - 4.0) < 0.001

    def test_get_all_has_uptime(self):
        m = MetricsCollector()
        data = m.get_all()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    def test_get_prometheus_text_format(self):
        m = MetricsCollector()
        m.inc("scans_total", value=5)
        m.set("active_positions", value=3)
        m.observe("scan_duration_seconds", value=1.0)
        text = m.get_prometheus_text()
        assert "# TYPE arb_scans_total counter" in text
        assert "arb_scans_total 5" in text
        assert "# TYPE arb_active_positions gauge" in text
        assert "arb_active_positions 3" in text
        assert "# TYPE arb_scan_duration_seconds histogram" in text
        assert "arb_scan_duration_seconds_count 1" in text
        assert "arb_uptime_seconds" in text

    def test_prometheus_text_with_labels(self):
        m = MetricsCollector()
        m.inc("ws_messages_received", {"platform": "kalshi"}, 10)
        text = m.get_prometheus_text()
        assert 'arb_ws_messages_received{platform="kalshi"} 10' in text

    def test_reset_daily(self):
        m = MetricsCollector()
        m.inc("scans_total", value=10)
        m.inc("trades_executed", {"platform": "pm", "status": "filled"}, 5)
        m.reset_daily()
        data = m.get_all()
        assert data["counters"]["scans_total"] == 0
        assert data["counters"]["trades_executed"] == 0

    def test_dynamic_counter_creation(self):
        m = MetricsCollector()
        m.inc("custom_counter")
        data = m.get_all()
        assert data["counters"]["custom_counter"] == 1

    def test_dynamic_gauge_creation(self):
        m = MetricsCollector()
        m.set("custom_gauge", value=42)
        data = m.get_all()
        assert data["gauges"]["custom_gauge"] == 42

    def test_thread_safety(self):
        m = MetricsCollector()
        def worker():
            for _ in range(1000):
                m.inc("scans_total")
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = m.get_all()
        assert data["counters"]["scans_total"] == 10000

    def test_histogram_buckets_in_prometheus(self):
        m = MetricsCollector()
        m.observe("scan_duration_seconds", value=0.003)
        m.observe("scan_duration_seconds", value=0.5)
        m.observe("scan_duration_seconds", value=100.0)
        text = m.get_prometheus_text()
        assert "arb_scan_duration_seconds_bucket" in text
        assert '+Inf' in text
        assert "arb_scan_duration_seconds_sum" in text


class TestModuleSingleton:
    def test_singleton_exists(self):
        from metrics import metrics
        assert isinstance(metrics, MetricsCollector)

    def test_singleton_is_usable(self):
        from metrics import metrics
        # Just ensure these don't error out
        metrics.inc("scans_total")
        metrics.set("active_positions", value=0)
        metrics.observe("scan_duration_seconds", value=0.001)
        text = metrics.get_prometheus_text()
        assert isinstance(text, str)
