"""Tests for broker/policy.py — out-of-band config loading + gate hashing.

DoD item 5: policy config demonstrably lives outside the loop-mergeable repo.
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from broker.policy import (
    DEFAULT_POLICY_PATH,
    REPO_ROOT,
    PolicyError,
    compute_gate_hash,
    load_policy,
)
from broker_helpers import policy_data


def write_policy(tmp_path, data) -> str:
    path = tmp_path / "broker-policy.json"
    path.write_text(json.dumps(data))
    return str(path)


# ---------------------------------------------------------------------------
# Loading — happy path
# ---------------------------------------------------------------------------

class TestLoadPolicy:
    def test_loads_valid_config(self, tmp_path):
        policy = load_policy(write_policy(tmp_path, policy_data()))
        assert policy.tranche == "T1"
        assert policy.principal_cap_usd == 8000.0
        assert policy.per_market_cap_usd == 300.0
        assert "kalshi" in policy.venue_allowlist

    def test_venues_normalized_lowercase(self, tmp_path):
        data = policy_data(venue_allowlist=["Kalshi", "POLYMARKET"])
        policy = load_policy(write_policy(tmp_path, data))
        assert policy.venue_allowlist == frozenset({"kalshi", "polymarket"})

    def test_env_var_override(self, tmp_path, monkeypatch):
        path = write_policy(tmp_path, policy_data(tranche="T1-env"))
        monkeypatch.setenv("BROKER_POLICY_PATH", path)
        assert load_policy().tranche == "T1-env"


# ---------------------------------------------------------------------------
# Fail-closed error paths
# ---------------------------------------------------------------------------

class TestLoadPolicyFailClosed:
    def test_missing_file(self, tmp_path):
        with pytest.raises(PolicyError, match="unreadable"):
            load_policy(str(tmp_path / "nonexistent.json"))

    def test_malformed_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(PolicyError, match="not valid JSON"):
            load_policy(str(path))

    def test_non_object_json(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[1, 2]")
        with pytest.raises(PolicyError, match="JSON object"):
            load_policy(str(path))

    def test_missing_required_key(self, tmp_path):
        data = policy_data()
        del data["gate_hashes"]
        with pytest.raises(PolicyError, match="gate_hashes"):
            load_policy(write_policy(tmp_path, data))

    def test_empty_allowlist(self, tmp_path):
        with pytest.raises(PolicyError, match="venue_allowlist"):
            load_policy(write_policy(tmp_path, policy_data(venue_allowlist=[])))

    def test_empty_gate_hashes(self, tmp_path):
        with pytest.raises(PolicyError, match="gate_hashes"):
            load_policy(write_policy(tmp_path, policy_data(gate_hashes={})))

    def test_zero_principal_cap(self, tmp_path):
        with pytest.raises(PolicyError, match="principal_cap_usd"):
            load_policy(write_policy(tmp_path, policy_data(principal_cap_usd=0)))

    def test_micro_entry_missing_key(self, tmp_path):
        data = policy_data(micro_entry={"max_first_order_usd": 10.0})
        with pytest.raises(PolicyError, match="micro_entry"):
            load_policy(write_policy(tmp_path, data))

    def test_micro_entry_zero_fills(self, tmp_path):
        data = policy_data(micro_entry={
            "max_first_order_usd": 10.0, "first_n_fills": 0,
            "max_fill_deviation_pct": 0.05,
        })
        with pytest.raises(PolicyError, match="first_n_fills"):
            load_policy(write_policy(tmp_path, data))


# ---------------------------------------------------------------------------
# Config isolation (DoD item 5) — a merge to this repo cannot alter policy
# ---------------------------------------------------------------------------

class TestConfigIsolation:
    def test_refuses_config_inside_repo(self):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=str(REPO_ROOT), delete=False
        )
        try:
            json.dump(policy_data(), tmp)
            tmp.close()
            with pytest.raises(PolicyError, match="INSIDE the loop-mergeable repo"):
                load_policy(tmp.name)
        finally:
            os.unlink(tmp.name)

    def test_default_path_is_outside_repo(self):
        assert not DEFAULT_POLICY_PATH.resolve().is_relative_to(REPO_ROOT)

    def test_accepts_config_outside_repo(self, tmp_path):
        # tmp_path is outside the repo — must load fine.
        assert load_policy(write_policy(tmp_path, policy_data())) is not None


# ---------------------------------------------------------------------------
# Gate hashing
# ---------------------------------------------------------------------------

class TestComputeGateHash:
    def test_deterministic(self):
        cfg = {"threshold": 0.02, "cap": 300}
        assert compute_gate_hash(cfg) == compute_gate_hash(cfg)

    def test_key_order_independent(self):
        assert compute_gate_hash({"a": 1, "b": 2}) == compute_gate_hash({"b": 2, "a": 1})

    def test_value_change_changes_hash(self):
        # This is the property that detects a merged threshold edit.
        assert compute_gate_hash({"min_net_roi": 0.02}) != compute_gate_hash(
            {"min_net_roi": 0.05}
        )
