"""
Tests for _chain.py — canonical serialization and chain hash computation.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from bh_fastapi_audit._chain import (
    _SUPPORTED_ALGORITHMS,
    canonical_serialize,
    compute_chain_hash,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_EVENT: dict = {
    "schema_version": "1.1",
    "event_id": "aaa-bbb-ccc",
    "timestamp": "2026-04-02T12:00:00.000Z",
    "service": {"name": "test-svc", "environment": "test"},
    "actor": {"subject_id": "user-1", "subject_type": "human"},
    "action": {"type": "READ", "data_classification": "UNKNOWN"},
    "resource": {"type": "Patient", "id": "pat_123"},
    "outcome": {"status": "SUCCESS"},
}


def _event(**overrides: object) -> dict:
    e = deepcopy(_SAMPLE_EVENT)
    e.update(overrides)
    return e


# ---------------------------------------------------------------------------
# canonical_serialize
# ---------------------------------------------------------------------------


class TestCanonicalSerialize:
    def test_returns_bytes(self):
        assert isinstance(canonical_serialize(_event()), bytes)

    def test_deterministic_output(self):
        """Same event produces the same bytes every time."""
        a = canonical_serialize(_event())
        b = canonical_serialize(_event())
        assert a == b

    def test_excludes_integrity_key(self):
        event_with_integrity = _event(integrity={"event_hash": "abc", "hash_alg": "sha256"})
        raw = canonical_serialize(event_with_integrity)
        parsed = json.loads(raw)
        assert "integrity" not in parsed

    def test_key_ordering_stable(self):
        """Insertion order must not affect output."""
        e1 = {"b": 2, "a": 1}
        e2 = {"a": 1, "b": 2}
        assert canonical_serialize(e1) == canonical_serialize(e2)

    def test_compact_json(self):
        raw = canonical_serialize({"a": 1, "b": "hello"})
        text = raw.decode("utf-8")
        assert " " not in text
        assert '{"a":1,"b":"hello"}' == text

    def test_unicode_content(self):
        event = _event(metadata={"note": "caf\u00e9 \u2603"})
        raw = canonical_serialize(event)
        assert "caf\u00e9".encode() in raw

    def test_nested_structures_deterministic(self):
        e1 = _event(metadata={"z": {"b": 2, "a": 1}, "a": 0})
        e2 = _event(metadata={"a": 0, "z": {"a": 1, "b": 2}})
        assert canonical_serialize(e1) == canonical_serialize(e2)


# ---------------------------------------------------------------------------
# compute_chain_hash
# ---------------------------------------------------------------------------


class TestComputeChainHash:
    def test_returns_event_hash_and_alg(self):
        result = compute_chain_hash(_event())
        assert "event_hash" in result
        assert "hash_alg" in result
        assert result["hash_alg"] == "sha256"

    def test_no_prev_event_hash_when_none(self):
        result = compute_chain_hash(_event(), prev_hash=None)
        assert "prev_event_hash" not in result

    def test_prev_event_hash_present_when_provided(self):
        result = compute_chain_hash(_event(), prev_hash="abc123")
        assert result["prev_event_hash"] == "abc123"

    def test_different_events_different_hashes(self):
        h1 = compute_chain_hash(_event(event_id="aaa"))["event_hash"]
        h2 = compute_chain_hash(_event(event_id="bbb"))["event_hash"]
        assert h1 != h2

    def test_same_event_same_hash(self):
        event = _event()
        h1 = compute_chain_hash(event)["event_hash"]
        h2 = compute_chain_hash(event)["event_hash"]
        assert h1 == h2

    def test_tamper_detection(self):
        """Modifying any field changes the hash."""
        original = compute_chain_hash(_event())["event_hash"]
        tampered = compute_chain_hash(_event(outcome={"status": "FAILURE"}))["event_hash"]
        assert original != tampered

    def test_prev_hash_affects_event_hash(self):
        """prev_hash is incorporated into event_hash (chain binding)."""
        h_no_prev = compute_chain_hash(_event())["event_hash"]
        h_with_prev = compute_chain_hash(_event(), prev_hash="xyz")["event_hash"]
        assert h_no_prev != h_with_prev

    def test_sha384_support(self):
        result = compute_chain_hash(_event(), algorithm="sha384")
        assert result["hash_alg"] == "sha384"
        assert len(result["event_hash"]) == 96  # sha384 hex = 96 chars

    def test_sha512_support(self):
        result = compute_chain_hash(_event(), algorithm="sha512")
        assert result["hash_alg"] == "sha512"
        assert len(result["event_hash"]) == 128  # sha512 hex = 128 chars

    def test_unsupported_algorithm_raises(self):
        with pytest.raises(ValueError, match="Unsupported hash algorithm"):
            compute_chain_hash(_event(), algorithm="md5")

    def test_hash_matches_manual_computation(self):
        event = _event()
        canonical = canonical_serialize(event)
        expected = hashlib.sha256(canonical).hexdigest()
        result = compute_chain_hash(event)
        assert result["event_hash"] == expected

    def test_hash_with_prev_matches_manual(self):
        event = _event()
        prev = "deadbeef"
        canonical = canonical_serialize(event)
        h = hashlib.sha256()
        h.update(prev.encode("utf-8"))
        h.update(canonical)
        expected = h.hexdigest()
        result = compute_chain_hash(event, prev_hash=prev)
        assert result["event_hash"] == expected

    def test_supported_algorithms_constant(self):
        assert _SUPPORTED_ALGORITHMS == {"sha256", "sha384", "sha512"}
