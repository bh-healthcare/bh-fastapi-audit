"""Tests for bh_fastapi_audit._verifier."""

from __future__ import annotations

from typing import Any

from bh_fastapi_audit._chain import compute_chain_hash
from bh_fastapi_audit._verifier import VerifyFailure, VerifyResult, verify_chain


def _make_event(
    event_id: str = "evt-001",
    timestamp: str = "2026-01-01T00:00:00.000Z",
    action_type: str = "READ",
) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "event_id": event_id,
        "timestamp": timestamp,
        "service": {"name": "test-svc", "environment": "test"},
        "actor": {"subject_id": "user-1", "subject_type": "human"},
        "action": {"type": action_type, "data_classification": "PHI"},
        "resource": {"type": "Patient"},
        "outcome": {"status": "SUCCESS"},
    }


def _build_chain(events: list[dict[str, Any]], algorithm: str = "sha256") -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    prev_hash: str | None = None
    for evt in events:
        integrity = compute_chain_hash(evt, prev_hash, algorithm)
        chained = {**evt, "integrity": integrity}
        prev_hash = integrity["event_hash"]
        chain.append(chained)
    return chain


class TestVerifyChainPass:
    def test_intact_chain_three_events(self) -> None:
        events = [
            _make_event("e1", "2026-01-01T00:00:00.000Z"),
            _make_event("e2", "2026-01-01T00:01:00.000Z"),
            _make_event("e3", "2026-01-01T00:02:00.000Z"),
        ]
        chain = _build_chain(events)
        result = verify_chain(chain)

        assert result.result == "PASS"
        assert result.events_scanned == 3
        assert result.chain_length == 3
        assert result.chain_gaps == 0
        assert result.hash_mismatches == 0
        assert result.unchained_events == 0
        assert result.failures == []

    def test_single_event_no_prev(self) -> None:
        events = [_make_event()]
        chain = _build_chain(events)
        result = verify_chain(chain)

        assert result.result == "PASS"
        assert result.chain_length == 1
        assert result.chain_gaps == 0

    def test_empty_input(self) -> None:
        result = verify_chain([])
        assert result.result == "PASS"
        assert result.events_scanned == 0
        assert result.chain_length == 0

    def test_time_range_captured(self) -> None:
        events = [
            _make_event("e1", "2026-01-01T00:00:00.000Z"),
            _make_event("e2", "2026-01-01T12:00:00.000Z"),
        ]
        chain = _build_chain(events)
        result = verify_chain(chain)

        assert result.time_range_start == "2026-01-01T00:00:00.000Z"
        assert result.time_range_end == "2026-01-01T12:00:00.000Z"


class TestVerifyChainHashMismatch:
    def test_tampered_event_detected(self) -> None:
        events = [
            _make_event("e1", "2026-01-01T00:00:00.000Z"),
            _make_event("e2", "2026-01-01T00:01:00.000Z"),
        ]
        chain = _build_chain(events)
        chain[1]["action"]["type"] = "DELETE"

        result = verify_chain(chain)
        assert result.result == "FAIL"
        assert result.hash_mismatches == 1
        assert len(result.failures) == 1
        assert result.failures[0].failure_type == "hash_mismatch"
        assert result.failures[0].event_id == "e2"

    def test_tampered_first_event(self) -> None:
        events = [_make_event("e1")]
        chain = _build_chain(events)
        chain[0]["outcome"]["status"] = "FAILURE"

        result = verify_chain(chain)
        assert result.result == "FAIL"
        assert result.hash_mismatches == 1

    def test_failure_details(self) -> None:
        events = [_make_event("e1")]
        chain = _build_chain(events)
        chain[0]["service"]["name"] = "tampered"

        result = verify_chain(chain)
        failure = result.failures[0]
        assert failure.event_index == 0
        assert failure.expected is not None
        assert failure.actual is not None
        assert failure.expected != failure.actual
        assert "modified" in failure.message.lower()


class TestVerifyChainGap:
    def test_chain_gap_missing_prev(self) -> None:
        events = [
            _make_event("e1", "2026-01-01T00:00:00.000Z"),
            _make_event("e2", "2026-01-01T00:01:00.000Z"),
            _make_event("e3", "2026-01-01T00:02:00.000Z"),
        ]
        chain = _build_chain(events)
        del chain[2]["integrity"]["prev_event_hash"]

        result = verify_chain(chain)
        assert result.result == "FAIL"
        assert result.chain_gaps == 1
        assert result.failures[0].failure_type == "chain_gap"
        assert result.failures[0].event_id == "e3"

    def test_chain_gap_wrong_prev(self) -> None:
        events = [
            _make_event("e1", "2026-01-01T00:00:00.000Z"),
            _make_event("e2", "2026-01-01T00:01:00.000Z"),
        ]
        chain = _build_chain(events)
        chain[1]["integrity"]["prev_event_hash"] = "badhash"

        result = verify_chain(chain)
        assert result.result == "FAIL"
        assert result.chain_gaps == 1
        assert "deleted or reordered" in result.failures[0].message.lower()


class TestVerifyChainUnchainedEvents:
    def test_events_without_integrity_counted(self) -> None:
        events = [
            _make_event("e1"),
            _make_event("e2"),
        ]
        chain = _build_chain(events[:1])
        chain.append(events[1])

        result = verify_chain(chain)
        assert result.unchained_events == 1
        assert result.chain_length == 1
        assert result.result == "PASS"

    def test_all_unchained(self) -> None:
        events = [_make_event("e1"), _make_event("e2")]
        result = verify_chain(events)

        assert result.unchained_events == 2
        assert result.chain_length == 0
        assert result.result == "PASS"


class TestVerifyChainMultipleFailures:
    def test_multiple_failures_reported(self) -> None:
        events = [
            _make_event("e1", "2026-01-01T00:00:00.000Z"),
            _make_event("e2", "2026-01-01T00:01:00.000Z"),
            _make_event("e3", "2026-01-01T00:02:00.000Z"),
        ]
        chain = _build_chain(events)
        chain[1]["action"]["type"] = "DELETE"
        chain[2]["action"]["type"] = "DELETE"

        result = verify_chain(chain)
        assert result.result == "FAIL"
        assert len(result.failures) >= 2


class TestVerifyChainAlgorithms:
    def test_sha384_chain(self) -> None:
        events = [
            _make_event("e1", "2026-01-01T00:00:00.000Z"),
            _make_event("e2", "2026-01-01T00:01:00.000Z"),
        ]
        chain = _build_chain(events, algorithm="sha384")
        result = verify_chain(chain, algorithm="sha384")
        assert result.result == "PASS"

    def test_sha512_chain(self) -> None:
        events = [
            _make_event("e1", "2026-01-01T00:00:00.000Z"),
            _make_event("e2", "2026-01-01T00:01:00.000Z"),
        ]
        chain = _build_chain(events, algorithm="sha512")
        result = verify_chain(chain, algorithm="sha512")
        assert result.result == "PASS"

    def test_algorithm_from_integrity_block(self) -> None:
        events = [_make_event("e1")]
        chain = _build_chain(events, algorithm="sha512")
        result = verify_chain(chain)
        assert result.result == "PASS"


class TestVerifyResultDataclass:
    def test_dataclass_fields(self) -> None:
        r = VerifyResult(
            events_scanned=0,
            time_range_start=None,
            time_range_end=None,
            chain_length=0,
            chain_gaps=0,
            hash_mismatches=0,
            unchained_events=0,
            result="PASS",
        )
        assert r.failures == []

    def test_failure_dataclass_fields(self) -> None:
        f = VerifyFailure(
            event_index=0,
            event_id="test",
            timestamp="2026-01-01",
            failure_type="hash_mismatch",
            expected="abc",
            actual="def",
            message="mismatch",
        )
        assert f.failure_type == "hash_mismatch"
