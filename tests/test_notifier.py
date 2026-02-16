"""Tests for notifier.py — webhook notification system."""

import pytest
from unittest.mock import patch, MagicMock
from notifier import WebhookNotifier


class TestWebhookNotifier:
    def test_init_sets_url_and_min_profit(self):
        n = WebhookNotifier("https://example.com/hook", min_profit=0.05)
        assert n.url == "https://example.com/hook"
        assert n.min_profit == 0.05

    def test_default_min_profit(self):
        n = WebhookNotifier("https://example.com/hook")
        assert n.min_profit == 0.01

    def test_notify_skips_when_no_qualifying_opportunities(self):
        n = WebhookNotifier("https://example.com/hook", min_profit=1.0)
        with patch.object(n, "_send") as mock_send:
            n.notify([{"net_profit": 0.01}])
            # _send should not be called via thread
            mock_send.assert_not_called()

    def test_notify_spawns_thread_for_qualifying_opportunities(self):
        n = WebhookNotifier("https://example.com/hook", min_profit=0.01)
        with patch("notifier.threading.Thread") as mock_thread:
            mock_instance = MagicMock()
            mock_thread.return_value = mock_instance
            n.notify([{"net_profit": 0.05}])
            mock_thread.assert_called_once()
            mock_instance.start.assert_called_once()

    def test_notify_filters_below_min_profit(self):
        n = WebhookNotifier("https://example.com/hook", min_profit=0.03)
        with patch("notifier.threading.Thread") as mock_thread:
            mock_instance = MagicMock()
            mock_thread.return_value = mock_instance
            n.notify([
                {"net_profit": 0.01},  # below threshold
                {"net_profit": 0.05},  # above threshold
            ])
            # Thread should be spawned (at least one qualifying)
            mock_thread.assert_called_once()

    def test_notify_empty_list_does_nothing(self):
        n = WebhookNotifier("https://example.com/hook")
        with patch("notifier.threading.Thread") as mock_thread:
            n.notify([])
            mock_thread.assert_not_called()


class TestBuildPayload:
    def test_slack_format(self):
        n = WebhookNotifier("https://hooks.slack.com/services/T00/B00/X00")
        opps = [{"type": "Binary", "market": "Test Market", "net_profit": 0.05, "net_roi": "5%", "prices": "Y=0.45 N=0.50", "_clob_depth": 100}]
        payload = n._build_payload(opps)
        assert "text" in payload
        assert "Binary" in payload["text"]
        assert "$0.0500" in payload["text"]

    def test_discord_format(self):
        n = WebhookNotifier("https://discord.com/api/webhooks/123/abc")
        opps = [{"type": "Cross", "market": "Test", "net_profit": 0.02, "net_roi": "2%", "prices": "PM_Y=0.40"}]
        payload = n._build_payload(opps)
        assert "content" in payload
        assert "Cross" in payload["content"]

    def test_generic_format(self):
        n = WebhookNotifier("https://example.com/hook")
        opps = [{"type": "Binary", "market": "Test", "net_profit": 0.03, "net_roi": "3%", "prices": "Y=0.45"}]
        payload = n._build_payload(opps)
        assert "opportunities" in payload
        assert "count" in payload
        assert payload["count"] == 1

    def test_multiple_opportunities(self):
        n = WebhookNotifier("https://example.com/hook")
        opps = [
            {"type": "Binary", "market": "Test1", "net_profit": 0.05, "net_roi": "5%", "prices": "Y=0.45"},
            {"type": "Cross", "market": "Test2", "net_profit": 0.03, "net_roi": "3%", "prices": "PM_Y=0.40"},
        ]
        payload = n._build_payload(opps)
        assert payload["count"] == 2
        assert len(payload["opportunities"]) == 2


class TestSend:
    def test_send_success(self):
        n = WebhookNotifier("https://example.com/hook")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        n._session = MagicMock()
        n._session.post.return_value = mock_resp
        n._send([{"type": "Binary", "market": "T", "net_profit": 0.05, "net_roi": "5%", "prices": "Y=0.45"}])
        n._session.post.assert_called_once()

    def test_send_handles_http_error(self):
        n = WebhookNotifier("https://example.com/hook")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        n._session = MagicMock()
        n._session.post.return_value = mock_resp
        # Should not raise
        n._send([{"type": "Binary", "market": "T", "net_profit": 0.05, "net_roi": "5%", "prices": "Y=0.45"}])

    def test_send_handles_request_exception(self):
        import requests
        n = WebhookNotifier("https://example.com/hook")
        n._session = MagicMock()
        n._session.post.side_effect = requests.RequestException("timeout")
        # Should not raise
        n._send([{"type": "Binary", "market": "T", "net_profit": 0.05, "net_roi": "5%", "prices": "Y=0.45"}])
