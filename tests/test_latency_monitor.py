"""Tests for latency_monitor.py — Strategy #49."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from latency_monitor import (
    LatencyTracker,
    LatencyMonitor,
    RegionRouter,
    get_latency_monitor,
    get_region_router,
)


class TestLatencyTracker:
    def test_record_and_get_average(self):
        tracker = LatencyTracker(max_samples=10)
        tracker.record(100.0)
        tracker.record(200.0)
        tracker.record(150.0)

        avg = tracker.get_average()
        assert avg == pytest.approx(150.0, abs=0.1)

    def test_get_p95(self):
        tracker = LatencyTracker(max_samples=100)
        for i in range(100):
            tracker.record(float(i + 1))

        p95 = tracker.get_p95()
        assert 95 <= p95 <= 100

    def test_get_min_max(self):
        tracker = LatencyTracker()
        tracker.record(50.0)
        tracker.record(200.0)
        tracker.record(100.0)

        assert tracker.get_min() == 50.0
        assert tracker.get_max() == 200.0

    def test_get_sample_count(self):
        tracker = LatencyTracker(max_samples=5)
        for i in range(3):
            tracker.record(100.0)
        assert tracker.get_sample_count() == 3

    def test_max_samples_limit(self):
        tracker = LatencyTracker(max_samples=5)
        for i in range(10):
            tracker.record(float(i))
        assert tracker.get_sample_count() == 5

    def test_is_healthy(self):
        with patch("latency_monitor.LATENCY_CRITICAL_MS", 500):
            tracker = LatencyTracker()
            tracker.record(100.0)
            assert tracker.is_healthy() is True

            tracker.record(600.0)
            tracker.record(600.0)
            tracker.record(600.0)
            assert tracker.is_healthy() is False

    def test_is_stale(self):
        tracker = LatencyTracker()
        tracker.record(100.0)
        assert tracker.is_stale(max_age_seconds=300.0) is False

        with patch.object(tracker, "_last_update", time.time() - 400):
            assert tracker.is_stale(max_age_seconds=300.0) is True


class TestLatencyMonitor:
    def test_record_and_get_latency(self):
        monitor = LatencyMonitor()
        monitor.record_latency("polymarket", 50.0)
        monitor.record_latency("polymarket", 60.0)

        avg = monitor.get_latency("polymarket")
        assert avg == pytest.approx(55.0, abs=0.1)

    def test_get_fastest_platform(self):
        with patch("latency_monitor.GEOGRAPHIC_LATENCY_ENABLED", True):
            monitor = LatencyMonitor()
            monitor.record_latency("polymarket", 100.0)
            monitor.record_latency("kalshi", 50.0)
            monitor.record_latency("betfair", 200.0)

            fastest = monitor.get_fastest_platform(["polymarket", "kalshi", "betfair"])
            assert fastest == "kalshi"

    def test_get_fastest_platform_disabled(self):
        with patch("latency_monitor.GEOGRAPHIC_LATENCY_ENABLED", False):
            monitor = LatencyMonitor()
            monitor.record_latency("polymarket", 100.0)
            monitor.record_latency("kalshi", 50.0)

            fastest = monitor.get_fastest_platform(["polymarket", "kalshi"])
            assert fastest == "polymarket"

    def test_get_fastest_platform_empty_returns_none(self):
        monitor = LatencyMonitor()
        assert monitor.get_fastest_platform([]) is None

    def test_get_platform_health(self):
        monitor = LatencyMonitor()
        monitor.record_latency("polymarket", 100.0)
        monitor.record_latency("polymarket", 150.0)

        health = monitor.get_platform_health()
        assert "polymarket" in health
        assert "avg_latency_ms" in health["polymarket"]
        assert health["polymarket"]["samples"] == 2

    def test_is_platform_healthy(self):
        monitor = LatencyMonitor()
        monitor.record_latency("polymarket", 100.0)
        assert monitor.is_platform_healthy("polymarket") is True

    def test_start_stop_monitoring(self):
        monitor = LatencyMonitor()
        monitor.start_monitoring(interval_seconds=60.0)
        assert monitor._running is True

        monitor.stop_monitoring()
        assert monitor._running is False


class TestRegionRouter:
    def test_record_region_latency(self):
        router = RegionRouter(local_region="us-east")
        router.record_region_latency("polymarket", "us-east", 50.0)
        router.record_region_latency("polymarket", "eu-west", 150.0)

        assert router._region_latencies["polymarket"]["us-east"] == 50.0
        assert router._region_latencies["polymarket"]["eu-west"] == 150.0

    def test_get_best_region(self):
        with patch("latency_monitor.GEOGRAPHIC_LATENCY_ENABLED", True):
            router = RegionRouter(local_region="us-east")
            router.record_region_latency("polymarket", "us-east", 100.0)
            router.record_region_latency("polymarket", "us-west", 50.0)

            best = router.get_best_region("polymarket")
            assert best == "us-west"

    def test_get_best_region_disabled(self):
        with patch("latency_monitor.GEOGRAPHIC_LATENCY_ENABLED", False):
            router = RegionRouter(local_region="us-east")
            router.record_region_latency("polymarket", "us-west", 50.0)

            best = router.get_best_region("polymarket")
            assert best == "us-east"

    def test_get_best_region_no_data(self):
        router = RegionRouter(local_region="us-east")
        best = router.get_best_region("unknown")
        assert best == "us-east"

    def test_should_route_externally(self):
        with patch("latency_monitor.GEOGRAPHIC_LATENCY_ENABLED", True):
            router = RegionRouter(local_region="us-east")
            router.record_region_latency("polymarket", "us-east", 200.0)
            router.record_region_latency("polymarket", "us-west", 50.0)

            assert router.should_route_externally("polymarket") is True

    def test_should_not_route_externally_similar_latency(self):
        with patch("latency_monitor.GEOGRAPHIC_LATENCY_ENABLED", True):
            router = RegionRouter(local_region="us-east")
            router.record_region_latency("polymarket", "us-east", 100.0)
            router.record_region_latency("polymarket", "us-west", 90.0)

            assert router.should_route_externally("polymarket") is False


class TestGetLatencyMonitor:
    def test_returns_singleton(self):
        monitor1 = get_latency_monitor()
        monitor2 = get_latency_monitor()
        assert monitor1 is monitor2


class TestGetRegionRouter:
    def test_returns_singleton(self):
        router1 = get_region_router()
        router2 = get_region_router()
        assert router1 is router2
