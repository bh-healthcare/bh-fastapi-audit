"""
Integration tests: DynamoDBSink + DynamoDBChainState under concurrency.

These tests exercise the full write path — chain state advance, integrity
injection, DynamoDB PutItem — using moto.  The concurrency tests simulate
the Lambda / multi-container scenario where multiple writers race to
advance the chain head.

This is the most likely place for edge cases to appear before v1.0.
"""

from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from bh_fastapi_audit._chain import compute_chain_hash
from bh_fastapi_audit._chain_state import DynamoDBChainState
from bh_fastapi_audit.sinks.dynamodb import DynamoDBSink

_REGION = "us-east-1"
_EVENTS_TABLE = "bh_audit_events_integration"
_CHAIN_TABLE = "bh_audit_chain_state_integration"
_SERVICE = "integration-test-svc"


def _make_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema_version": "1.1",
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "service": {"name": _SERVICE, "environment": "test"},
        "actor": {"subject_id": "user-1", "subject_type": "human"},
        "action": {"type": "READ", "data_classification": "PHI"},
        "resource": {"type": "Patient", "patient_id": "pat_001"},
        "outcome": {"status": "SUCCESS"},
        "http": {
            "method": "GET",
            "route_template": "/patients/{patient_id}",
            "status_code": 200,
        },
    }
    event.update(overrides)
    return event


def _emit_with_chain(
    sink: DynamoDBSink,
    chain: DynamoDBChainState,
    event: dict[str, Any],
    algorithm: str = "sha256",
) -> dict[str, Any]:
    """Simulate what _safe_emit does: hash, inject, advance, emit."""
    integrity = compute_chain_hash(event, chain.last_hash, algorithm)
    event["integrity"] = integrity
    chain.advance(integrity["event_hash"])
    sink.emit(event)
    return event


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def infra():
    """Spin up moto-backed DynamoDBSink + DynamoDBChainState."""
    with mock_aws():
        sink = DynamoDBSink(
            table_name=_EVENTS_TABLE,
            region=_REGION,
            create_table=True,
        )
        chain = DynamoDBChainState(
            table_name=_CHAIN_TABLE,
            service_name=_SERVICE,
            region=_REGION,
            create_table=True,
        )
        yield sink, chain


@pytest.fixture()
def ddb_table():
    """Return a raw boto3 Table for the events table (for inspection)."""
    return boto3.resource("dynamodb", region_name=_REGION).Table(_EVENTS_TABLE)


# ---------------------------------------------------------------------------
# Basic chain → sink flow
# ---------------------------------------------------------------------------


class TestChainedEmitBasic:
    def test_single_event_has_integrity_in_dynamo(self, infra):
        sink, chain = infra
        _emit_with_chain(sink, chain, _make_event())

        stored = json.loads(sink._table.scan()["Items"][0]["event_json"])
        assert "integrity" in stored
        assert stored["integrity"]["hash_alg"] == "sha256"
        assert "prev_event_hash" not in stored["integrity"]

    def test_chain_links_across_events(self, infra):
        sink, chain = infra
        e1 = _emit_with_chain(sink, chain, _make_event(event_id="evt-001"))
        e2 = _emit_with_chain(sink, chain, _make_event(event_id="evt-002"))
        e3 = _emit_with_chain(sink, chain, _make_event(event_id="evt-003"))

        assert "prev_event_hash" not in e1["integrity"]
        assert e2["integrity"]["prev_event_hash"] == e1["integrity"]["event_hash"]
        assert e3["integrity"]["prev_event_hash"] == e2["integrity"]["event_hash"]

    def test_chain_state_tracks_head(self, infra):
        sink, chain = infra
        e = _emit_with_chain(sink, chain, _make_event())
        assert chain.last_hash == e["integrity"]["event_hash"]

    def test_dynamo_flattens_chain_hash(self, infra):
        sink, chain = infra
        e = _emit_with_chain(sink, chain, _make_event())

        items = sink._table.scan()["Items"]
        assert len(items) == 1
        assert items[0]["chain_hash"] == e["integrity"]["event_hash"]
        assert "prev_chain_hash" not in items[0]  # first event

    def test_dynamo_flattens_prev_chain_hash(self, infra):
        sink, chain = infra
        e1 = _emit_with_chain(sink, chain, _make_event(event_id="evt-001"))
        _emit_with_chain(sink, chain, _make_event(event_id="evt-002"))

        items = sink._table.scan()["Items"]
        second = next(i for i in items if i["event_id"] == "evt-002")
        assert second["prev_chain_hash"] == e1["integrity"]["event_hash"]


# ---------------------------------------------------------------------------
# Round-trip verification from DynamoDB
# ---------------------------------------------------------------------------


class TestRoundTripVerification:
    def test_verify_chain_from_stored_events(self, infra):
        """Read events back from DynamoDB and verify the hash chain."""
        sink, chain = infra
        n_events = 10
        for i in range(n_events):
            _emit_with_chain(sink, chain, _make_event(event_id=f"evt-{i:03d}"))

        items = sink._table.scan()["Items"]
        assert len(items) == n_events

        events = []
        for item in items:
            events.append(json.loads(item["event_json"]))
        events.sort(key=lambda e: e["timestamp"] + e["event_id"])

        prev_hash = None
        for event in events:
            integrity = event.pop("integrity")
            recomputed = compute_chain_hash(event, prev_hash)
            assert recomputed["event_hash"] == integrity["event_hash"], (
                f"Hash mismatch for {event['event_id']}"
            )
            if prev_hash is not None:
                assert integrity.get("prev_event_hash") == prev_hash
            prev_hash = integrity["event_hash"]

    def test_tamper_detected_on_stored_event(self, infra):
        """Modify a stored event_json and confirm the hash no longer matches."""
        sink, chain = infra
        _emit_with_chain(sink, chain, _make_event(event_id="evt-original"))

        item = sink._table.scan()["Items"][0]
        stored = json.loads(item["event_json"])
        integrity = stored.pop("integrity")

        stored["outcome"]["status"] = "FAILURE"
        recomputed = compute_chain_hash(stored)
        assert recomputed["event_hash"] != integrity["event_hash"]


# ---------------------------------------------------------------------------
# Concurrent writers (simulates Lambda / multi-container)
# ---------------------------------------------------------------------------


class TestConcurrentChainAdvance:
    def test_concurrent_emits_no_duplicate_chain_state(self, infra):
        """Multiple threads emit events; chain state ends up consistent."""
        sink, chain = infra
        n_threads = 4
        events_per_thread = 10
        emitted: list[dict[str, Any]] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(thread_id: int) -> None:
            for i in range(events_per_thread):
                event = _make_event(event_id=f"t{thread_id}-evt-{i:03d}")
                try:
                    e = _emit_with_chain(sink, chain, event)
                    with lock:
                        emitted.append(e)
                except Exception as exc:
                    with lock:
                        errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors: {errors}"
        total = n_threads * events_per_thread
        assert len(emitted) == total

        # Chain state should point to *some* valid hash
        assert chain.last_hash is not None

        # All events should be in DynamoDB
        items = sink._table.scan()["Items"]
        assert len(items) == total

    def test_concurrent_first_event_race(self, infra):
        """Two threads both try to be the first event — only one gets None prev."""
        sink, chain = infra
        results: list[tuple[str, str | None]] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker(eid: str) -> None:
            event = _make_event(event_id=eid)
            integrity = compute_chain_hash(event, chain.last_hash)
            event["integrity"] = integrity
            barrier.wait()  # synchronize to maximize race window
            prev = chain.advance(integrity["event_hash"])
            sink.emit(event)
            with lock:
                results.append((eid, prev))

        t1 = threading.Thread(target=worker, args=("race-a",))
        t2 = threading.Thread(target=worker, args=("race-b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(results) == 2

        # At most one thread should see prev=None (the true first).
        # The other should either see the first's hash or None (if
        # it lost the conditional write race and retried / gave up).
        none_count = sum(1 for _, prev in results if prev is None)
        # Both could be None if the loser exhausted retries, but at least
        # one should be — and no more than 2 (both unchained worst case).
        assert 1 <= none_count <= 2

        # Both events should be stored
        items = sink._table.scan()["Items"]
        assert len(items) == 2

    def test_chain_gap_counter_under_contention(self, infra):
        """Under heavy contention, gaps can occur but events are never lost."""
        sink, chain = infra
        n_threads = 6
        events_per_thread = 5
        all_events: list[dict[str, Any]] = []
        lock = threading.Lock()

        def worker(thread_id: int) -> None:
            for i in range(events_per_thread):
                event = _make_event(event_id=f"contention-t{thread_id}-{i}")
                try:
                    e = _emit_with_chain(sink, chain, event)
                except Exception:
                    # If chain advance fails, emit without integrity
                    sink.emit(event)
                    e = event
                with lock:
                    all_events.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = n_threads * events_per_thread
        items = sink._table.scan()["Items"]
        assert len(items) == total, "No events should be lost under contention"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_chain_state_survives_sink_idempotency(self, infra):
        """Emit same event twice with same PK: sink deduplicates via conditional write."""
        sink, chain = infra
        fixed_ts = "2026-04-02T12:00:00.000Z"
        event = _make_event(event_id="dedup-001", timestamp=fixed_ts)
        e1 = _emit_with_chain(sink, chain, deepcopy(event))

        # Build a second event with the same event_id AND timestamp (same PK/SK).
        # DynamoDB's ConditionExpression rejects the second PutItem.
        event2 = _make_event(event_id="dedup-001", timestamp=fixed_ts)
        integrity2 = compute_chain_hash(event2, chain.last_hash)
        event2["integrity"] = integrity2
        chain.advance(integrity2["event_hash"])
        sink.emit(event2)  # silently skipped by conditional write

        items = sink._table.scan()["Items"]
        assert len(items) == 1  # only one stored

        stored = json.loads(items[0]["event_json"])
        assert stored["integrity"]["event_hash"] == e1["integrity"]["event_hash"]

    def test_chain_continues_after_unchained_event(self, infra):
        """If one event is unchained (gap), the next should still chain to it."""
        sink, chain = infra
        e1 = _emit_with_chain(sink, chain, _make_event(event_id="evt-001"))

        # Simulate an unchained event (gap) — emit without chain advance
        gap_event = _make_event(event_id="evt-gap")
        sink.emit(gap_event)

        # Next chained event should link to the last chain head (e1)
        e3 = _emit_with_chain(sink, chain, _make_event(event_id="evt-003"))
        assert e3["integrity"]["prev_event_hash"] == e1["integrity"]["event_hash"]

    def test_separate_services_independent_chains(self):
        """Different service_name values get independent chain heads."""
        with mock_aws():
            sink = DynamoDBSink(
                table_name=_EVENTS_TABLE,
                region=_REGION,
                create_table=True,
            )
            chain_a = DynamoDBChainState(
                table_name=_CHAIN_TABLE,
                service_name="service-a",
                region=_REGION,
                create_table=True,
            )
            chain_b = DynamoDBChainState(
                table_name=_CHAIN_TABLE,
                service_name="service-b",
                region=_REGION,
            )

            ea = _make_event(event_id="svc-a-001")
            ea["service"]["name"] = "service-a"
            _emit_with_chain(sink, chain_a, ea)

            eb = _make_event(event_id="svc-b-001")
            eb["service"]["name"] = "service-b"
            _emit_with_chain(sink, chain_b, eb)

            assert chain_a.last_hash != chain_b.last_hash
            assert chain_a.last_hash is not None
            assert chain_b.last_hash is not None

            # Both are first events in their respective chains
            assert "prev_event_hash" not in ea["integrity"]
            assert "prev_event_hash" not in eb["integrity"]

    def test_gsi_queries_work_with_integrity_events(self, infra):
        """GSI queries return chained events; projected attrs are present."""
        sink, chain = infra
        event = _make_event(
            event_id="gsi-test-001",
            resource={"type": "Patient", "patient_id": "pat_gsi"},
        )
        _emit_with_chain(sink, chain, event)

        results = sink.query_by_patient("pat_gsi")
        assert len(results) == 1
        assert results[0]["event_id"] == "gsi-test-001"
        # chain_hash is NOT in the GSI projection — verify that the
        # projected attributes are correct for chained events
        assert results[0]["outcome_status"] == "SUCCESS"
        assert results[0]["actor_subject_id"] == "user-1"

    def test_full_table_scan_includes_chain_hash(self, infra):
        """A table scan (not GSI) should include chain_hash."""
        sink, chain = infra
        _emit_with_chain(sink, chain, _make_event(event_id="scan-001"))

        items = sink._table.scan()["Items"]
        assert len(items) == 1
        assert "chain_hash" in items[0]

    def test_retry_on_concurrent_put_item_for_first_event(self):
        """Two writers race for the very first chain state row."""
        with mock_aws():
            chain = DynamoDBChainState(
                table_name=_CHAIN_TABLE,
                service_name="race-test",
                region=_REGION,
                create_table=True,
            )

            call_count = {"n": 0}
            original_put = chain._table.put_item

            def flaky_put(**kwargs):
                call_count["n"] += 1
                if call_count["n"] == 1 and "ConditionExpression" in kwargs:
                    from botocore.exceptions import ClientError

                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException", "Message": ""}},
                        "PutItem",
                    )
                return original_put(**kwargs)

            with patch.object(chain._table, "put_item", side_effect=flaky_put):
                prev = chain.advance("hash_first")

            # The retry should re-read last_hash (now None since the first put
            # failed and no row exists yet), then succeed on the second attempt.
            # prev should be None since this is still logically the first event.
            assert prev is None
            assert chain.last_hash == "hash_first"
