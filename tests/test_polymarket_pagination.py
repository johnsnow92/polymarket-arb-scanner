"""Gamma API pagination: the server silently caps page size (limit=500 -> 100
rows), so pagination must advance by the ACTUAL page length and only stop on an
empty page — not on len(page) < requested limit (detection post-mortem 2026-07-21)."""
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub the CLOB SDK before importing the module under test.
for mod in ("py_clob_client_v2", "py_clob_client_v2.client", "py_clob_client_v2.clob_types", "py_clob_client_v2.http_helpers", "py_clob_client_v2.http_helpers.helpers"):
    sys.modules.setdefault(mod, MagicMock())

import polymarket_api


def _resp(rows):
    r = MagicMock()
    r.json.return_value = rows
    return r


def _capped_server(total_rows, cap=100):
    """Simulate Gamma: ignores requested limit above `cap`, honors offset."""
    data = [{"id": i} for i in range(total_rows)]

    def handler(url, params=None, timeout=15):
        offset = params["offset"]
        page = min(params["limit"], cap)
        return _resp(data[offset:offset + page])

    return handler


class TestGammaPagination:
    def test_markets_paginate_past_server_capped_page(self):
        with patch.object(polymarket_api, "_get_with_retry", side_effect=_capped_server(250)):
            markets = polymarket_api.fetch_all_markets(limit=500, max_pages=20)
        assert len(markets) == 250

    def test_markets_stop_on_empty_page(self):
        with patch.object(polymarket_api, "_get_with_retry", side_effect=_capped_server(100)) as mock_get:
            markets = polymarket_api.fetch_all_markets(limit=500, max_pages=20)
        assert len(markets) == 100
        assert mock_get.call_count == 2  # full page, then empty page

    def test_events_paginate_past_server_capped_page(self):
        with patch.object(polymarket_api, "_get_with_retry", side_effect=_capped_server(250)):
            events = polymarket_api.fetch_events(limit=500, max_pages=20)
        assert len(events) == 250
