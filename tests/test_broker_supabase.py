"""Unit tests for broker/supabase_queue.py with a mocked HTTP session.

Proves the SupabaseIntentQueue builds the right PostgREST/RPC calls, sends
credentials in headers, parses responses, and translates DB-level errors
(append-only, idempotency reuse) into IntentError — all without a network or
live project, so it runs in CI. Live behavior is covered by the contract suite
(tests/test_broker_queue_contract.py) when SUPABASE creds are present, and the
DB guarantees themselves are enforced by the Postgres migration.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from broker.queue import Intent, IntentError
from broker.supabase_queue import SupabaseConfigError, SupabaseIntentQueue


def _resp(status=200, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body if json_body is not None else []
    r.text = text
    return r


@pytest.fixture
def q(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-role-secret")
    queue = SupabaseIntentQueue(lease_name="test-lease")
    queue._session = MagicMock()
    return queue


def flip(key="k1"):
    return Intent("flip_lane",
                  {"lane": "kalshi-lip", "venue": "kalshi", "action": "enable"}, key)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_missing_env_raises(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        with pytest.raises(SupabaseConfigError):
            SupabaseIntentQueue()

    def test_credentials_go_in_headers_not_url(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co/")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-secret")
        queue = SupabaseIntentQueue()
        headers = queue._session.headers
        assert headers["apikey"] == "svc-secret"
        assert headers["Authorization"] == "Bearer svc-secret"
        assert queue._rest == "https://proj.supabase.co/rest/v1"  # trailing / stripped

    def test_anon_key_fallback_name(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.setenv("SUPABASE_KEY", "fallback-key")
        assert SupabaseIntentQueue()._session.headers["apikey"] == "fallback-key"


# ---------------------------------------------------------------------------
# submit → RPC
# ---------------------------------------------------------------------------

class TestSubmit:
    def test_calls_submit_rpc_and_parses(self, q):
        q._session.post.return_value = _resp(200, [{"id": 7, "created": True}])
        intent_id, created = q.submit(flip("abc"))
        assert (intent_id, created) == (7, True)
        url, kwargs = q._session.post.call_args[0][0], q._session.post.call_args[1]
        assert url.endswith("/rpc/broker_submit_intent")
        assert kwargs["json"] == {
            "p_key": "abc", "p_type": "flip_lane",
            "p_payload": {"lane": "kalshi-lip", "venue": "kalshi", "action": "enable"},
        }

    def test_duplicate_parsed_as_not_created(self, q):
        q._session.post.return_value = _resp(200, [{"id": 7, "created": False}])
        assert q.submit(flip("abc")) == (7, False)

    def test_reuse_conflict_becomes_intent_error(self, q):
        q._session.post.return_value = _resp(
            400, text="idempotency_key abc reused for a different intent")
        with pytest.raises(IntentError, match="reused"):
            q.submit(flip("abc"))


# ---------------------------------------------------------------------------
# events + append-only translation
# ---------------------------------------------------------------------------

class TestEvents:
    def test_append_event_inserts(self, q):
        q._session.post.return_value = _resp(201, [{"id": 1}])
        q.append_event(3, "EXECUTED", "ok")
        url = q._session.post.call_args[0][0]
        assert url.endswith("/broker_intent_events")
        assert q._session.post.call_args[1]["json"] == {
            "intent_id": 3, "status": "EXECUTED", "reason": "ok"}

    def test_invalid_status_rejected_client_side(self, q):
        with pytest.raises(IntentError, match="invalid status"):
            q.append_event(3, "NOPE")
        q._session.post.assert_not_called()

    def test_append_only_violation_becomes_intent_error(self, q):
        q._session.post.return_value = _resp(
            400, text="broker_intent_events is append-only")
        with pytest.raises(IntentError, match="append-only"):
            q.append_event(3, "EXECUTED", "x")

    def test_current_status_defaults_pending(self, q):
        q._session.get.return_value = _resp(200, [])
        assert q.current_status(3) == "PENDING"

    def test_current_status_reads_latest(self, q):
        q._session.get.return_value = _resp(200, [{"status": "REJECTED"}])
        assert q.current_status(3) == "REJECTED"
        params = q._session.get.call_args[1]["params"]
        assert params["order"] == "id.desc" and params["limit"] == "1"


# ---------------------------------------------------------------------------
# halts
# ---------------------------------------------------------------------------

class TestHalts:
    def test_record_halt_inserts(self, q):
        q._session.post.return_value = _resp(201, [{"id": 1}])
        q.record_halt("capital_moves", "recon break")
        assert q._session.post.call_args[1]["json"] == {
            "scope": "capital_moves", "action": "halt", "reason": "recon break"}

    def test_clear_requires_operator(self, q):
        with pytest.raises(IntentError, match="operator"):
            q.clear_halt("capital_moves", "")
        q._session.post.assert_not_called()

    def test_halt_active_checks_scope_and_all(self, q):
        q._session.get.return_value = _resp(200, [])
        assert q.halt_active("capital_moves") is False
        # queried both the scope and the global 'all' scope
        scopes = {c[1]["params"]["scope"] for c in q._session.get.call_args_list}
        assert scopes == {"eq.capital_moves", "eq.all"}

    def test_halt_active_true_when_latest_is_halt(self, q):
        q._session.get.return_value = _resp(200, [{"action": "halt"}])
        assert q.halt_active("capital_moves") is True


# ---------------------------------------------------------------------------
# lease RPCs
# ---------------------------------------------------------------------------

class TestLease:
    def test_acquire_calls_rpc_with_lease_name(self, q):
        q._session.post.return_value = _resp(200, True)
        assert q.acquire_lease("sessionA", 60) is True
        url, kwargs = q._session.post.call_args[0][0], q._session.post.call_args[1]
        assert url.endswith("/rpc/broker_acquire_lease")
        assert kwargs["json"] == {
            "p_name": "test-lease", "p_holder": "sessionA", "p_ttl_seconds": 60.0}

    def test_acquire_false_when_held(self, q):
        q._session.post.return_value = _resp(200, False)
        assert q.acquire_lease("sessionB", 60) is False

    def test_renew_and_release_rpcs(self, q):
        q._session.post.return_value = _resp(200, True)
        assert q.renew_lease("sessionA", 60) is True
        assert q._session.post.call_args[0][0].endswith("/rpc/broker_renew_lease")
        assert q.release_lease("sessionA") is True
        assert q._session.post.call_args[0][0].endswith("/rpc/broker_release_lease")

    def test_lease_holder_reads_row(self, q):
        q._session.get.return_value = _resp(200, [{"holder": "sessionA"}])
        assert q.lease_holder() == "sessionA"

    def test_lease_holder_none_when_empty(self, q):
        q._session.get.return_value = _resp(200, [])
        assert q.lease_holder() is None
