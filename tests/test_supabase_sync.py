"""Unit tests for the Supabase rewards sync (supabase_sync.py)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supabase_sync import (  # noqa: E402
    REWARDS_TABLE,
    RewardSync,
    reward_metric_to_event,
)


def _metric(**overrides):
    row = {
        'id': 1,
        'platform': 'kalshi',
        'market_key': 'KXBTC-26',
        'order_id': 'ord-1',
        'event': 'placed',
        'size': 100.0,
        'spread': 0.02,
        'resting_seconds': 0,
        'timestamp': 1_700_000_000,
    }
    row.update(overrides)
    return row


class TestMapping:
    def test_kalshi_maps_to_lip_engine(self):
        event = reward_metric_to_event(_metric())
        assert event['engine'] == 'kalshi-lip'
        assert event['lane'] == 'prediction-markets'
        assert event['tax_bucket'] == 'ordinary'
        assert event['source_key'] == 'reward_metric:1'

    def test_event_date_from_timestamp(self):
        event = reward_metric_to_event(_metric(timestamp=1_700_000_000))
        assert event['event_date'] == '2023-11-14'

    def test_notes_capture_activity(self):
        event = reward_metric_to_event(_metric(event='cancelled', resting_seconds=42))
        assert 'cancelled' in event['notes']
        assert 'resting_s=42' in event['notes']


class _FakeUpsertChain:
    def __init__(self, recorder):
        self._recorder = recorder

    def upsert(self, rows, on_conflict=None):
        self._recorder['rows'] = rows
        self._recorder['on_conflict'] = on_conflict
        return self

    def execute(self):
        self._recorder['executed'] = True
        return {'data': self._recorder['rows']}


class _FakeClient:
    def __init__(self):
        self.recorder = {}

    def table(self, name):
        self.recorder['table'] = name
        return _FakeUpsertChain(self.recorder)


class TestUpsert:
    def test_upsert_sends_records_with_conflict_target(self):
        client = _FakeClient()
        sync = RewardSync(client)
        n = sync.upsert_events([reward_metric_to_event(_metric())])
        assert n == 1
        assert client.recorder['table'] == REWARDS_TABLE
        assert client.recorder['on_conflict'] == 'engine,source_key'
        assert client.recorder['executed'] is True

    def test_empty_events_is_noop(self):
        client = _FakeClient()
        sync = RewardSync(client)
        assert sync.upsert_events([]) == 0
        assert 'executed' not in client.recorder

    def test_missing_required_field_raises(self):
        client = _FakeClient()
        sync = RewardSync(client)
        bad = reward_metric_to_event(_metric())
        bad['engine'] = ''
        with pytest.raises(ValueError):
            sync.upsert_events([bad])

    def test_upsert_propagates_client_error(self):
        client = MagicMock()
        client.table.return_value.upsert.return_value.execute.side_effect = RuntimeError('boom')
        sync = RewardSync(client)
        with pytest.raises(RuntimeError):
            sync.upsert_events([reward_metric_to_event(_metric())])


class TestSyncFromDb:
    def test_sync_reads_db_and_upserts(self, tmp_path):
        from db import TradeDB
        db = TradeDB(db_path=str(tmp_path / 'trades.db'))
        try:
            db.log_reward_metric('kalshi', 'KXBTC-26', 'ord-1', 'placed', 100.0, 0.02, 0)
            db.log_reward_metric('kalshi', 'KXETH-26', 'ord-2', 'placed', 50.0, 0.03, 0)

            client = _FakeClient()
            sync = RewardSync(client, db=db)
            n = sync.sync_reward_metrics()
            assert n == 2
            assert len(client.recorder['rows']) == 2
            assert all(r['engine'] == 'kalshi-lip' for r in client.recorder['rows'])
        finally:
            db.close()

    def test_sync_without_db_raises(self):
        sync = RewardSync(_FakeClient())
        with pytest.raises(RuntimeError):
            sync.sync_reward_metrics()
