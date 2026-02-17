"""Webhook notifier for arbitrage opportunities."""

import json
import logging
import threading

import requests

logger = logging.getLogger(__name__)


class WebhookNotifier:
    """Send opportunity alerts to a webhook URL (Slack, Discord, or generic)."""

    def __init__(self, url: str, min_profit: float = 0.01):
        """
        Args:
            url: Webhook URL to POST JSON payloads to.
            min_profit: Only notify for opportunities with net_profit >= this value.
        """
        self.url = url
        self.min_profit = min_profit
        self._session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"

    def notify(self, opportunities: list[dict]):
        """Send notification for qualifying opportunities (async, non-blocking)."""
        qualifying = [o for o in opportunities if o.get("net_profit", 0) >= self.min_profit]
        if not qualifying:
            return

        thread = threading.Thread(target=self._send, args=(qualifying,), daemon=True)
        thread.start()

    def _send(self, opportunities: list[dict]):
        """POST opportunity data to the webhook URL."""
        try:
            payload = self._build_payload(opportunities)
            resp = self._session.post(self.url, json=payload, timeout=10)
            if resp.status_code < 300:
                logger.debug("Webhook sent: %d opportunities", len(opportunities))
            else:
                logger.warning("Webhook returned %d: %s", resp.status_code, resp.text[:200])
        except requests.RequestException as e:
            logger.warning("Webhook request failed: %s", e)

    def _build_payload(self, opportunities: list[dict]) -> dict:
        """Build the webhook payload, auto-detecting Slack/Discord format."""
        items = []
        for opp in opportunities:
            items.append({
                "type": opp.get("type", ""),
                "market": opp.get("market", ""),
                "net_profit": f"${opp.get('net_profit', 0):.4f}",
                "net_roi": opp.get("net_roi", ""),
                "prices": opp.get("prices", ""),
                "depth": opp.get("_clob_depth", 0),
            })

        # Detect Slack or Discord by URL pattern
        if "hooks.slack.com" in self.url:
            lines = [f"*{o['type']}* {o['market']}: {o['net_profit']} ({o['net_roi']})" for o in items]
            return {"text": f"Arb Scanner: {len(items)} opportunities\n" + "\n".join(lines)}
        elif "discord.com/api/webhooks" in self.url:
            lines = [f"**{o['type']}** {o['market']}: {o['net_profit']} ({o['net_roi']})" for o in items]
            return {"content": f"Arb Scanner: {len(items)} opportunities\n" + "\n".join(lines)}
        else:
            return {"opportunities": items, "count": len(items)}

    def notify_partial_fill(self, trade_id: int, platform: str, market: str, fill_price: float, status: str):
        """Send urgent notification about a partial fill event."""
        if not self.url:
            return
        payload = self._build_partial_fill_payload(trade_id, platform, market, fill_price, status)
        thread = threading.Thread(target=self._send_raw, args=(payload,), daemon=True)
        thread.start()

    def _send_raw(self, payload: dict):
        """POST a raw payload to the webhook URL."""
        try:
            resp = self._session.post(self.url, json=payload, timeout=10)
            if resp.status_code >= 300:
                logger.warning("Webhook returned %d: %s", resp.status_code, resp.text[:200])
        except requests.RequestException as e:
            logger.warning("Webhook request failed: %s", e)

    def _build_partial_fill_payload(self, trade_id: int, platform: str, market: str, fill_price: float, status: str) -> dict:
        """Build partial fill alert payload."""
        msg = f"PARTIAL FILL #{trade_id} on {platform}: {market} @ ${fill_price:.3f} — {status}"
        if "hooks.slack.com" in self.url:
            return {"text": f":warning: {msg}"}
        elif "discord.com/api/webhooks" in self.url:
            return {"content": f"\u26a0\ufe0f {msg}"}
        else:
            return {"event": "partial_fill", "trade_id": trade_id, "platform": platform,
                    "market": market, "fill_price": fill_price, "status": status}
