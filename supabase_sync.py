"""Mirror arbgrid reward data into the shared Supabase rewards_events table.

SQLite stays the source of truth; this module reads local reward rows and
upserts them into Supabase so multiple engines roll up P&L in one place
(architecture locked 2026-06-13). Deterministic, no LLM.

The Supabase client is injected (anything exposing the supabase-py
``table(name).upsert(rows, on_conflict=...).execute()`` chain) so the mapping
and upsert logic are testable without a live project. ``build_client_from_env``
constructs a real client from SUPABASE_URL + SUPABASE_SERVICE_KEY when needed.
"""

from __future__ import annotations

import datetime
import logging
import os

logger = logging.getLogger(__name__)

REWARDS_TABLE = 'rewards_events'

# reward_metrics is prediction-market market-making activity; rebates are
# ordinary income for tax purposes (doc 07 tax map).
_DEFAULT_LANE = 'prediction-markets'
_DEFAULT_TAX_BUCKET = 'ordinary'


def _engine_for(platform: str) -> str:
    """Map a reward_metrics platform to a rewards_events engine tag."""
    return f'{platform}-lip' if platform == 'kalshi' else f'{platform}-rewards'


def reward_metric_to_event(row: dict) -> dict:
    """Convert one reward_metrics row into a rewards_events record.

    Placement/cancellation metrics are liquidity activity, not realised cash, so
    reward_usd is left None; the row is an auditable accrual-input keyed for
    idempotent upsert. Realised reward USD is attached downstream when Kalshi
    settles the period.

    Args:
        row: A reward_metrics row (id, platform, market_key, order_id, event,
            size, spread, resting_seconds, timestamp).

    Returns:
        A rewards_events record dict.
    """
    ts = int(row.get('timestamp', 0))
    event_date = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()
    return {
        'engine': _engine_for(row.get('platform', 'unknown')),
        'lane': _DEFAULT_LANE,
        'tax_bucket': _DEFAULT_TAX_BUCKET,
        'event_date': event_date,
        'asset': None,
        'reward_usd': None,
        'source_key': f"reward_metric:{row.get('id')}",
        'notes': (
            f"{row.get('event')} {row.get('market_key')} "
            f"size={row.get('size')} spread={row.get('spread')} "
            f"resting_s={row.get('resting_seconds')}"
        ),
    }


def build_client_from_env():
    """Build a Supabase client from SUPABASE_URL + SUPABASE_SERVICE_KEY.

    Returns:
        A supabase Client.

    Raises:
        RuntimeError: If credentials are missing or supabase-py is not installed.
    """
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_KEY')
    if not url or not key:
        raise RuntimeError(
            'SUPABASE_URL and SUPABASE_SERVICE_KEY (or SUPABASE_KEY) must be set'
        )
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError('supabase-py not installed (pip install supabase)') from exc
    return create_client(url, key)


class RewardSync:
    """Upsert local reward rows into the shared Supabase rewards_events table."""

    def __init__(self, supabase_client, db=None):
        """Initialise the sync.

        Args:
            supabase_client: Client exposing table().upsert().execute().
            db: Optional TradeDB for reading reward_metrics directly.
        """
        self._client = supabase_client
        self._db = db

    def upsert_events(self, events: list[dict]) -> int:
        """Upsert rewards_events records, deduped on (engine, source_key).

        Args:
            events: Records with at least engine, lane, tax_bucket, event_date,
                source_key.

        Returns:
            Number of records sent.

        Raises:
            ValueError: If any record is missing a required field.
        """
        if not events:
            return 0
        required = ('engine', 'lane', 'tax_bucket', 'event_date', 'source_key')
        for event in events:
            missing = [field for field in required if not event.get(field)]
            if missing:
                raise ValueError(f'rewards_events record missing {missing}: {event}')
        try:
            self._client.table(REWARDS_TABLE).upsert(
                events, on_conflict='engine,source_key'
            ).execute()
        except Exception as exc:
            logger.error('Supabase rewards upsert failed: %s', exc)
            raise
        return len(events)

    def sync_reward_metrics(self, since_ts: int = 0) -> int:
        """Read reward_metrics from the local DB and upsert them to Supabase.

        Args:
            since_ts: Only sync rows newer than this Unix-seconds value.

        Returns:
            Number of records upserted.

        Raises:
            RuntimeError: If no DB was provided.
        """
        if self._db is None:
            raise RuntimeError('RewardSync has no db to read reward_metrics from')
        rows = self._db.get_reward_metrics(since_ts=since_ts)
        events = [reward_metric_to_event(row) for row in rows]
        return self.upsert_events(events)
