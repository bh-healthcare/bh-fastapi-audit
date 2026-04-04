"""
Tests for DynamoDBSink using moto to mock AWS DynamoDB.

All tests run against a local mocked DynamoDB — no AWS credentials needed.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws

from bh_fastapi_audit.sinks.dynamodb import DynamoDBSink, _iso_to_epoch

_REGION = "us-east-1"
_TABLE = "bh_audit_events_test"


def _make_event(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid v1.1 audit event with optional overrides."""
    event: dict[str, Any] = {
        "schema_version": "1.1",
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "service": {"name": "test-svc", "environment": "test"},
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


@pytest.fixture()
def sink():
    """Create a DynamoDBSink backed by moto."""
    with mock_aws():
        s = DynamoDBSink(
            table_name=_TABLE,
            region=_REGION,
            create_table=True,
        )
        yield s


@pytest.fixture()
def ddb_table(sink):
    """Return the underlying boto3 Table for direct inspection."""
    return boto3.resource("dynamodb", region_name=_REGION).Table(_TABLE)


# ------------------------------------------------------------------
# Basic emit + retrieve
# ------------------------------------------------------------------


class TestEmit:
    def test_emit_stores_event(self, sink, ddb_table):
        event = _make_event()
        sink.emit(event)

        resp = ddb_table.scan()
        assert resp["Count"] == 1
        item = resp["Items"][0]
        assert item["event_id"] == event["event_id"]

    def test_emit_stores_event_json(self, sink, ddb_table):
        event = _make_event()
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        roundtripped = json.loads(item["event_json"])
        assert roundtripped["event_id"] == event["event_id"]
        assert roundtripped["service"]["name"] == "test-svc"

    def test_emit_multiple_events(self, sink, ddb_table):
        for _ in range(5):
            sink.emit(_make_event())

        resp = ddb_table.scan()
        assert resp["Count"] == 5


# ------------------------------------------------------------------
# PK / SK structure
# ------------------------------------------------------------------


class TestKeyStructure:
    def test_pk_is_service_hash_date(self, sink, ddb_table):
        event = _make_event(
            timestamp="2026-04-15T14:32:07.123Z",
            service={"name": "intake-api", "environment": "prod"},
        )
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        assert item["service_date"] == "intake-api#2026-04-15"

    def test_sk_is_timestamp_hash_event_id(self, sink, ddb_table):
        eid = str(uuid.uuid4())
        event = _make_event(
            event_id=eid,
            timestamp="2026-04-15T14:32:07.123Z",
        )
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        assert item["ts_event"] == f"2026-04-15T14:32:07.123Z#{eid}"


# ------------------------------------------------------------------
# Conditional write (idempotency)
# ------------------------------------------------------------------


class TestIdempotency:
    def test_duplicate_event_id_is_ignored(self, sink, ddb_table):
        event = _make_event()
        sink.emit(event)
        sink.emit(event)

        resp = ddb_table.scan()
        assert resp["Count"] == 1

    def test_different_event_ids_both_stored(self, sink, ddb_table):
        sink.emit(_make_event())
        sink.emit(_make_event())

        resp = ddb_table.scan()
        assert resp["Count"] == 2


# ------------------------------------------------------------------
# GSI queries
# ------------------------------------------------------------------


class TestQueryByPatient:
    def test_returns_events_for_patient(self, sink):
        sink.emit(_make_event(resource={"type": "Patient", "patient_id": "pat_100"}))
        sink.emit(_make_event(resource={"type": "Patient", "patient_id": "pat_200"}))
        sink.emit(_make_event(resource={"type": "Patient", "patient_id": "pat_100"}))

        results = sink.query_by_patient("pat_100")
        assert len(results) == 2
        assert all(r["resource"]["patient_id"] == "pat_100" for r in results)

    def test_returns_empty_for_unknown_patient(self, sink):
        sink.emit(_make_event(resource={"type": "Patient", "patient_id": "pat_100"}))
        results = sink.query_by_patient("pat_999")
        assert results == []

    def test_filters_by_time_range(self, sink):
        sink.emit(
            _make_event(
                timestamp="2026-03-01T10:00:00.000Z",
                resource={"type": "Patient", "patient_id": "pat_100"},
            )
        )
        sink.emit(
            _make_event(
                timestamp="2026-04-15T10:00:00.000Z",
                resource={"type": "Patient", "patient_id": "pat_100"},
            )
        )

        results = sink.query_by_patient("pat_100", start="2026-04-01", end="2026-04-30")
        assert len(results) == 1
        assert results[0]["timestamp"] == "2026-04-15T10:00:00.000Z"

    def test_filters_with_start_only(self, sink):
        sink.emit(
            _make_event(
                timestamp="2026-01-01T00:00:00.000Z",
                resource={"type": "Patient", "patient_id": "pat_100"},
            )
        )
        sink.emit(
            _make_event(
                timestamp="2026-06-01T00:00:00.000Z",
                resource={"type": "Patient", "patient_id": "pat_100"},
            )
        )

        results = sink.query_by_patient("pat_100", start="2026-05-01")
        assert len(results) == 1


class TestQueryByActor:
    def test_returns_events_for_actor(self, sink):
        sink.emit(_make_event(actor={"subject_id": "user-A", "subject_type": "human"}))
        sink.emit(_make_event(actor={"subject_id": "user-B", "subject_type": "human"}))
        sink.emit(_make_event(actor={"subject_id": "user-A", "subject_type": "human"}))

        results = sink.query_by_actor("user-A")
        assert len(results) == 2

    def test_filters_by_start(self, sink):
        sink.emit(
            _make_event(
                timestamp="2026-01-01T00:00:00.000Z",
                actor={"subject_id": "user-A", "subject_type": "human"},
            )
        )
        sink.emit(
            _make_event(
                timestamp="2026-06-01T00:00:00.000Z",
                actor={"subject_id": "user-A", "subject_type": "human"},
            )
        )

        results = sink.query_by_actor("user-A", start="2026-05-01")
        assert len(results) == 1


class TestQueryDenials:
    def test_returns_denied_events(self, sink):
        sink.emit(_make_event(outcome={"status": "DENIED", "error_type": "RoleDenied"}))
        sink.emit(_make_event(outcome={"status": "SUCCESS"}))
        sink.emit(_make_event(outcome={"status": "DENIED", "error_type": "ConsentRequired"}))

        results = sink.query_denials()
        assert len(results) == 2
        assert all(r["outcome"]["status"] == "DENIED" for r in results)

    def test_filters_by_start(self, sink):
        sink.emit(
            _make_event(
                timestamp="2026-01-01T00:00:00.000Z",
                outcome={"status": "DENIED", "error_type": "RoleDenied"},
            )
        )
        sink.emit(
            _make_event(
                timestamp="2026-06-01T00:00:00.000Z",
                outcome={"status": "DENIED", "error_type": "RoleDenied"},
            )
        )

        results = sink.query_denials(start="2026-05-01")
        assert len(results) == 1


# ------------------------------------------------------------------
# TTL
# ------------------------------------------------------------------


class TestTTL:
    def test_ttl_calculated_correctly(self, sink, ddb_table):
        ts = "2026-04-15T12:00:00.000Z"
        event = _make_event(timestamp=ts)
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        expected_ttl = _iso_to_epoch(ts) + (2190 * 86_400)
        assert int(item["ttl"]) == expected_ttl

    def test_ttl_disabled_when_none(self):
        with mock_aws():
            sink = DynamoDBSink(
                table_name=_TABLE,
                region=_REGION,
                ttl_days=None,
                create_table=True,
            )
            event = _make_event()
            sink.emit(event)

            table = boto3.resource("dynamodb", region_name=_REGION).Table(_TABLE)
            resp = table.scan()
            item = resp["Items"][0]
            assert "ttl" not in item


# ------------------------------------------------------------------
# Flattening
# ------------------------------------------------------------------


class TestFlatten:
    def test_flattens_core_fields(self, sink, ddb_table):
        event = _make_event(
            actor={"subject_id": "dr-smith", "subject_type": "human"},
            action={"type": "CREATE", "data_classification": "PHI"},
            resource={"type": "Encounter", "patient_id": "pat_42"},
            outcome={"status": "SUCCESS"},
        )
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        assert item["actor_subject_id"] == "dr-smith"
        assert item["action_type"] == "CREATE"
        assert item["data_classification"] == "PHI"
        assert item["resource_type"] == "Encounter"
        assert item["patient_id"] == "pat_42"
        assert item["outcome_status"] == "SUCCESS"

    def test_optional_fields_omitted_when_absent(self, sink, ddb_table):
        event = _make_event(resource={"type": "Config"})
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        assert "patient_id" not in item
        assert "resource_id" not in item
        assert "actor_org_id" not in item

    def test_error_type_included_for_failures(self, sink, ddb_table):
        event = _make_event(
            outcome={"status": "FAILURE", "error_type": "HTTP500", "error_message": "fail"},
        )
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        assert item["error_type"] == "HTTP500"

    def test_integrity_fields_flattened(self, sink, ddb_table):
        event = _make_event(
            integrity={
                "event_hash": "abc123",
                "prev_event_hash": "xyz789",
                "hash_alg": "sha256",
            }
        )
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        assert item["chain_hash"] == "abc123"
        assert item["prev_chain_hash"] == "xyz789"

    def test_v10_event_flattens_correctly(self, sink, ddb_table):
        event = _make_event(
            schema_version="1.0",
            outcome={"status": "FAILURE", "error_type": "HTTP403", "error_message": "denied"},
        )
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        assert item["outcome_status"] == "FAILURE"
        assert item["error_type"] == "HTTP403"

    def test_http_fields_flattened(self, sink, ddb_table):
        event = _make_event(
            http={
                "method": "POST",
                "route_template": "/patients",
                "status_code": 201,
            }
        )
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        assert item["http_method"] == "POST"
        assert item["http_route_template"] == "/patients"
        assert int(item["http_status_code"]) == 201


# ------------------------------------------------------------------
# Table creation
# ------------------------------------------------------------------


class TestCreateTable:
    def test_create_table_is_idempotent(self):
        with mock_aws():
            DynamoDBSink(table_name=_TABLE, region=_REGION, create_table=True)
            DynamoDBSink(table_name=_TABLE, region=_REGION, create_table=True)

            client = boto3.client("dynamodb", region_name=_REGION)
            tables = client.list_tables()["TableNames"]
            assert tables.count(_TABLE) == 1

    def test_table_has_correct_gsis(self):
        with mock_aws():
            DynamoDBSink(table_name=_TABLE, region=_REGION, create_table=True)

            client = boto3.client("dynamodb", region_name=_REGION)
            desc = client.describe_table(TableName=_TABLE)["Table"]
            gsi_names = {g["IndexName"] for g in desc.get("GlobalSecondaryIndexes", [])}
            assert gsi_names == {"patient_id-index", "actor-index", "outcome-index"}


# ------------------------------------------------------------------
# event_json round-trip
# ------------------------------------------------------------------


class TestEventJsonRoundTrip:
    def test_full_event_recoverable(self, sink, ddb_table):
        event = _make_event(
            correlation={"request_id": "req-123", "trace_id": "trace-456"},
            metadata={"department": "cardiology"},
        )
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        recovered = json.loads(item["event_json"])
        assert recovered == event


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


class TestEdgeCases:
    def test_missing_service_name_uses_unknown(self, sink, ddb_table):
        event = _make_event()
        event["service"] = {}
        sink.emit(event)

        resp = ddb_table.scan()
        item = resp["Items"][0]
        assert item["service_date"].startswith("unknown#")

    def test_empty_timestamp_raises(self, sink):
        """DynamoDB rejects empty strings on GSI key attributes."""
        from botocore.exceptions import ClientError

        event = _make_event(timestamp="")
        with pytest.raises(ClientError):
            sink.emit(event)

    def test_table_name_property(self, sink):
        assert sink.table_name == _TABLE
