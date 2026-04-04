"""
Tests for sinks/ledger.py — LedgerSink (JSONL + chain hashing).
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from bh_fastapi_audit._chain import compute_chain_hash
from bh_fastapi_audit.sinks.ledger import LedgerSink

_SAMPLE_EVENT: dict[str, Any] = {
    "schema_version": "1.1",
    "event_id": "evt-001",
    "timestamp": "2026-04-02T12:00:00.000Z",
    "service": {"name": "test-svc", "environment": "test"},
    "actor": {"subject_id": "user-1", "subject_type": "human"},
    "action": {"type": "READ", "data_classification": "UNKNOWN"},
    "resource": {"type": "Patient", "id": "pat_123"},
    "outcome": {"status": "SUCCESS"},
}


def _event(**overrides: Any) -> dict[str, Any]:
    e = deepcopy(_SAMPLE_EVENT)
    e.update(overrides)
    return e


def _read_lines(path: Path) -> list[dict[str, Any]]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


class TestLedgerSink:
    def test_emit_writes_jsonl_with_integrity(self, tmp_path: Path):
        p = tmp_path / "audit.jsonl"
        with LedgerSink(p) as sink:
            sink.emit(_event())

        lines = _read_lines(p)
        assert len(lines) == 1
        assert "integrity" in lines[0]
        assert "event_hash" in lines[0]["integrity"]
        assert lines[0]["integrity"]["hash_alg"] == "sha256"

    def test_first_event_no_prev_hash(self, tmp_path: Path):
        p = tmp_path / "audit.jsonl"
        with LedgerSink(p) as sink:
            sink.emit(_event())

        lines = _read_lines(p)
        assert "prev_event_hash" not in lines[0]["integrity"]

    def test_chain_links_correctly(self, tmp_path: Path):
        p = tmp_path / "audit.jsonl"
        with LedgerSink(p) as sink:
            sink.emit(_event(event_id="evt-001"))
            sink.emit(_event(event_id="evt-002"))
            sink.emit(_event(event_id="evt-003"))

        lines = _read_lines(p)
        assert len(lines) == 3

        assert "prev_event_hash" not in lines[0]["integrity"]
        assert lines[1]["integrity"]["prev_event_hash"] == lines[0]["integrity"]["event_hash"]
        assert lines[2]["integrity"]["prev_event_hash"] == lines[1]["integrity"]["event_hash"]

    def test_round_trip_verify(self, tmp_path: Path):
        """Read back JSONL, recompute hashes, verify chain is intact."""
        p = tmp_path / "audit.jsonl"
        with LedgerSink(p) as sink:
            for i in range(5):
                sink.emit(_event(event_id=f"evt-{i:03d}"))

        lines = _read_lines(p)
        prev_hash = None
        for line in lines:
            integrity = line.pop("integrity")
            recomputed = compute_chain_hash(line, prev_hash)
            assert recomputed["event_hash"] == integrity["event_hash"]
            if prev_hash is not None:
                assert integrity["prev_event_hash"] == prev_hash
            prev_hash = integrity["event_hash"]

    def test_tamper_detection(self, tmp_path: Path):
        """Modifying a line breaks the chain when re-verified."""
        p = tmp_path / "audit.jsonl"
        with LedgerSink(p) as sink:
            sink.emit(_event(event_id="evt-001"))
            sink.emit(_event(event_id="evt-002"))

        lines = _read_lines(p)
        # Tamper with line 0
        lines[0]["outcome"]["status"] = "FAILURE"

        # Recompute hash for tampered line — it should differ
        integrity_0 = lines[0].pop("integrity")
        recomputed = compute_chain_hash(lines[0])
        assert recomputed["event_hash"] != integrity_0["event_hash"]

    def test_file_created_with_parent_dirs(self, tmp_path: Path):
        p = tmp_path / "deep" / "nested" / "audit.jsonl"
        with LedgerSink(p) as sink:
            sink.emit(_event())
        assert p.exists()

    def test_context_manager_closes_file(self, tmp_path: Path):
        p = tmp_path / "audit.jsonl"
        sink = LedgerSink(p)
        sink.emit(_event())
        sink.close()
        # Can open for reading after close
        assert len(_read_lines(p)) == 1

    def test_path_property(self, tmp_path: Path):
        p = tmp_path / "audit.jsonl"
        sink = LedgerSink(p)
        assert sink.path == p
        sink.close()

    def test_custom_algorithm(self, tmp_path: Path):
        p = tmp_path / "audit.jsonl"
        with LedgerSink(p, algorithm="sha512") as sink:
            sink.emit(_event())

        lines = _read_lines(p)
        assert lines[0]["integrity"]["hash_alg"] == "sha512"
        assert len(lines[0]["integrity"]["event_hash"]) == 128

    def test_flush_default_true(self, tmp_path: Path):
        p = tmp_path / "audit.jsonl"
        sink = LedgerSink(p)
        sink.emit(_event())
        # File should contain data even before close
        assert p.stat().st_size > 0
        sink.close()

    def test_multiple_close_safe(self, tmp_path: Path):
        p = tmp_path / "audit.jsonl"
        sink = LedgerSink(p)
        sink.emit(_event())
        sink.close()
        sink.close()  # should not raise
