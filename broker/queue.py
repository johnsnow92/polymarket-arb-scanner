"""Append-only intent queue + halt ledger for the policy broker.

SQLite-backed (WAL + thread lock, same pattern as db.TradeDB). Append-only is
enforced in the database itself: RAISE(ABORT) triggers block every UPDATE and
DELETE, so status changes are appended events and history can never be
rewritten. Idempotency-key dedupe makes a repeated submit a no-op.
"""

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

VALID_INTENT_TYPES = frozenset(["flip_lane", "move_capital", "rotate_secret"])

STATUS_PENDING = "PENDING"
STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_IN_DOUBT = "IN_DOUBT"
STATUS_HARD_STOP = "HARD_STOP"

VALID_STATUSES = frozenset([
    STATUS_PENDING, STATUS_EXECUTED, STATUS_REJECTED,
    STATUS_IN_DOUBT, STATUS_HARD_STOP,
])


class IntentError(ValueError):
    """Malformed intent — rejected before it ever reaches the queue."""


@dataclass(frozen=True)
class Intent:
    intent_type: str
    payload: dict = field(default_factory=dict)
    idempotency_key: str = ""

    def __post_init__(self):
        if self.intent_type not in VALID_INTENT_TYPES:
            raise IntentError(
                f"unknown intent_type {self.intent_type!r} "
                f"(valid: {sorted(VALID_INTENT_TYPES)})"
            )
        if not isinstance(self.payload, dict):
            raise IntentError("payload must be a dict")
        if not self.idempotency_key or not isinstance(self.idempotency_key, str):
            raise IntentError("idempotency_key is required on every intent")


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

HALT_SCOPE_CAPITAL = "capital_moves"
HALT_SCOPE_ALL = "all"

_APPEND_ONLY_TABLES = ("intents", "intent_events", "halts")


class IntentQueue:
    """Thread-safe append-only intent queue + halt ledger."""

    def __init__(self, db_path: str = ":memory:"):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                intent_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS intent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                intent_id INTEGER NOT NULL REFERENCES intents(id),
                status TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS halts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('halt', 'clear')),
                reason TEXT,
                operator TEXT,
                created_at TEXT NOT NULL
            );
        """)
        # Append-only enforcement lives in the DB, not in Python discipline.
        for table in _APPEND_ONLY_TABLES:
            for op in ("UPDATE", "DELETE"):
                self.conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} "
                    f"BEFORE {op} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
                )
        self.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- intents ------------------------------------------------------------

    def submit(self, intent: Intent) -> tuple[int, bool]:
        """Append an intent. Returns (intent_id, created).

        A repeated idempotency key is a no-op: the existing row id is returned
        with created=False and nothing is written.
        """
        payload_json = json.dumps(intent.payload, sort_keys=True)
        with self._lock:
            row = self.conn.execute(
                "SELECT id, intent_type, payload FROM intents WHERE idempotency_key = ?",
                (intent.idempotency_key,),
            ).fetchone()
            if row is not None:
                # A key reused for DIFFERENT content is not a retry — it is a
                # bug or an attempt to smuggle a new action under an old key.
                if (row["intent_type"] != intent.intent_type
                        or row["payload"] != payload_json):
                    raise IntentError(
                        f"idempotency_key {intent.idempotency_key!r} reused "
                        "for a different intent — rejected"
                    )
                logger.info(
                    "Duplicate intent %s (id=%d) — no-op",
                    intent.idempotency_key, row["id"],
                )
                return row["id"], False
            cur = self.conn.execute(
                "INSERT INTO intents (idempotency_key, intent_type, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    intent.idempotency_key,
                    intent.intent_type,
                    payload_json,
                    self._now(),
                ),
            )
            self.conn.commit()
            return cur.lastrowid, True

    def append_event(self, intent_id: int, status: str, reason: str = "") -> None:
        if status not in VALID_STATUSES:
            raise IntentError(f"invalid status {status!r}")
        with self._lock:
            self.conn.execute(
                "INSERT INTO intent_events (intent_id, status, reason, created_at) "
                "VALUES (?, ?, ?, ?)",
                (intent_id, status, reason, self._now()),
            )
            self.conn.commit()

    def current_status(self, intent_id: int) -> str:
        """Derived status: the most recent appended event, else PENDING."""
        with self._lock:
            row = self.conn.execute(
                "SELECT status FROM intent_events WHERE intent_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (intent_id,),
            ).fetchone()
        return row["status"] if row else STATUS_PENDING

    def last_reason(self, intent_id: int) -> str:
        with self._lock:
            row = self.conn.execute(
                "SELECT reason FROM intent_events WHERE intent_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (intent_id,),
            ).fetchone()
        return (row["reason"] or "") if row else ""

    # -- halts ---------------------------------------------------------------

    def record_halt(self, scope: str, reason: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO halts (scope, action, reason, created_at) "
                "VALUES (?, 'halt', ?, ?)",
                (scope, reason, self._now()),
            )
            self.conn.commit()
        logger.critical("BROKER HALT scope=%s reason=%s", scope, reason)

    def clear_halt(self, scope: str, operator: str) -> None:
        """Only a named human operator clears a halt (hard-stop discipline)."""
        if not operator or not operator.strip():
            raise IntentError("clearing a halt requires a named operator")
        with self._lock:
            self.conn.execute(
                "INSERT INTO halts (scope, action, operator, created_at) "
                "VALUES (?, 'clear', ?, ?)",
                (scope, operator.strip(), self._now()),
            )
            self.conn.commit()
        logger.warning("Halt cleared scope=%s by operator=%s", scope, operator)

    def halt_active(self, scope: str) -> bool:
        """True if the most recent event for this scope (or 'all') is a halt."""
        with self._lock:
            for check_scope in {scope, HALT_SCOPE_ALL}:
                row = self.conn.execute(
                    "SELECT action FROM halts WHERE scope = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (check_scope,),
                ).fetchone()
                if row is not None and row["action"] == "halt":
                    return True
        return False
