"""Supabase (PostgREST) backend for the policy-broker intent queue + lease.

Same interface as broker.queue.IntentQueue, plus a single-writer lease. The
append-only guarantee and idempotency/lease atomicity live in Postgres
(see migrations policy_broker_intent_queue / policy_broker_truncate_guard):
this client is a thin, deterministic wrapper over the REST endpoints.

Credentials come from the environment (never hardcoded, never logged):
  SUPABASE_URL                 e.g. https://<ref>.supabase.co
  SUPABASE_SERVICE_ROLE_KEY    service-role key (bypasses RLS; server-side only)
The broker is a trusted server-side component; the broker tables are RLS-deny
by default so only the service role can touch them.
"""

import logging
import os

import requests

from .queue import (
    STATUS_PENDING,
    VALID_STATUSES,
    Intent,
    IntentError,
)

logger = logging.getLogger(__name__)

_DEFAULT_LEASE_NAME = "policy_broker_loop"


class SupabaseConfigError(RuntimeError):
    """Supabase URL/key not configured. Callers may skip or fail-closed."""


def _require_env(url: str | None, key: str | None) -> tuple[str, str]:
    url = url or os.getenv("SUPABASE_URL")
    key = (
        key
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_KEY")
    )
    if not url or not key:
        raise SupabaseConfigError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set for the "
            "Supabase broker backend"
        )
    return url.rstrip("/"), key


class SupabaseIntentQueue:
    """Append-only intent queue + halt ledger + single-writer lease on Supabase.

    Interchangeable with broker.queue.IntentQueue (duck-typed): the broker and
    validator depend only on these methods, never on the backing store.
    """

    def __init__(
        self,
        url: str | None = None,
        key: str | None = None,
        *,
        lease_name: str = _DEFAULT_LEASE_NAME,
        timeout: float = 15.0,
    ):
        self._url, api_key = _require_env(url, key)
        self._rest = f"{self._url}/rest/v1"
        self._rpc = f"{self._rest}/rpc"
        self._lease_name = lease_name
        self._timeout = timeout
        self._session = requests.Session()
        # api_key/authorization are set once; the key is never logged.
        self._session.headers.update({
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    # -- low-level helpers --------------------------------------------------

    def _post_rpc(self, fn: str, payload: dict):
        resp = self._session.post(
            f"{self._rpc}/{fn}", json=payload, timeout=self._timeout
        )
        if resp.status_code >= 400:
            self._raise(fn, resp)
        return resp.json()

    def _get(self, table: str, params: dict):
        resp = self._session.get(
            f"{self._rest}/{table}", params=params, timeout=self._timeout
        )
        if resp.status_code >= 400:
            self._raise(f"GET {table}", resp)
        return resp.json()

    def _insert(self, table: str, row: dict):
        resp = self._session.post(
            f"{self._rest}/{table}",
            json=row,
            params={"select": "id"},
            headers={"Prefer": "return=representation"},
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            self._raise(f"INSERT {table}", resp)
        return resp.json()

    @staticmethod
    def _raise(op: str, resp: requests.Response) -> None:
        # PostgREST surfaces our RAISE(...) and CHECK messages in the body.
        body = resp.text or ""
        if "append-only" in body:
            raise IntentError(f"{op}: append-only violation ({resp.status_code})")
        if "reused for a different intent" in body:
            raise IntentError(f"{op}: idempotency_key reused for a different intent")
        raise RuntimeError(f"Supabase {op} failed ({resp.status_code}): {body}")

    # -- intents ------------------------------------------------------------

    def submit(self, intent: Intent) -> tuple[int, bool]:
        """Append an intent (server-side atomic dedupe). Returns (id, created)."""
        rows = self._post_rpc("broker_submit_intent", {
            "p_key": intent.idempotency_key,
            "p_type": intent.intent_type,
            "p_payload": intent.payload,
        })
        row = rows[0] if isinstance(rows, list) else rows
        return int(row["id"]), bool(row["created"])

    def append_event(self, intent_id: int, status: str, reason: str = "") -> None:
        if status not in VALID_STATUSES:
            raise IntentError(f"invalid status {status!r}")
        self._insert("broker_intent_events", {
            "intent_id": intent_id, "status": status, "reason": reason,
        })

    def current_status(self, intent_id: int) -> str:
        rows = self._get("broker_intent_events", {
            "intent_id": f"eq.{intent_id}",
            "select": "status",
            "order": "id.desc",
            "limit": "1",
        })
        return rows[0]["status"] if rows else STATUS_PENDING

    def last_reason(self, intent_id: int) -> str:
        rows = self._get("broker_intent_events", {
            "intent_id": f"eq.{intent_id}",
            "select": "reason",
            "order": "id.desc",
            "limit": "1",
        })
        return (rows[0]["reason"] or "") if rows else ""

    # -- halts ---------------------------------------------------------------

    def record_halt(self, scope: str, reason: str) -> None:
        self._insert("broker_halts", {
            "scope": scope, "action": "halt", "reason": reason,
        })
        logger.critical("BROKER HALT scope=%s reason=%s", scope, reason)

    def clear_halt(self, scope: str, operator: str) -> None:
        if not operator or not operator.strip():
            raise IntentError("clearing a halt requires a named operator")
        self._insert("broker_halts", {
            "scope": scope, "action": "clear", "operator": operator.strip(),
        })
        logger.warning("Halt cleared scope=%s by operator=%s", scope, operator)

    def halt_active(self, scope: str) -> bool:
        for check_scope in {scope, "all"}:
            rows = self._get("broker_halts", {
                "scope": f"eq.{check_scope}",
                "select": "action",
                "order": "id.desc",
                "limit": "1",
            })
            if rows and rows[0]["action"] == "halt":
                return True
        return False

    # -- single-writer lease -------------------------------------------------

    def acquire_lease(self, holder: str, ttl_seconds: float) -> bool:
        """Acquire (or idempotently renew) the loop lease. False if held."""
        return bool(self._post_rpc("broker_acquire_lease", {
            "p_name": self._lease_name,
            "p_holder": holder,
            "p_ttl_seconds": float(ttl_seconds),
        }))

    def renew_lease(self, holder: str, ttl_seconds: float) -> bool:
        return bool(self._post_rpc("broker_renew_lease", {
            "p_name": self._lease_name,
            "p_holder": holder,
            "p_ttl_seconds": float(ttl_seconds),
        }))

    def release_lease(self, holder: str) -> bool:
        return bool(self._post_rpc("broker_release_lease", {
            "p_name": self._lease_name,
            "p_holder": holder,
        }))

    def lease_holder(self) -> str | None:
        rows = self._get("broker_leases", {
            "name": f"eq.{self._lease_name}",
            "select": "holder,expires_at",
        })
        return rows[0]["holder"] if rows else None
