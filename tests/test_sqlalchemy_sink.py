"""
Tests for SQLAlchemy database sink.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

# Skip all tests in this module if sqlalchemy is not installed
sqlalchemy = pytest.importorskip("sqlalchemy")

from bh_fastapi_audit.sinks.sqlalchemy import SQLAlchemySink  # noqa: E402


def make_test_event(
    event_id: str | None = None,
    patient_id: str | None = None,
    actor_subject_id: str = "user_123",
) -> dict:
    """Create a minimal valid audit event for testing."""
    event = {
        "schema_version": "1.0",
        "event_id": event_id or str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "service": {
            "name": "test-service",
            "environment": "test",
        },
        "actor": {
            "subject_id": actor_subject_id,
            "subject_type": "human",
        },
        "action": {
            "type": "READ",
        },
        "resource": {
            "type": "Patient",
            "id": "resource_789",
        },
        "outcome": {
            "status": "SUCCESS",
        },
    }
    if patient_id:
        event["resource"]["patient_id"] = patient_id
    return event


class TestSQLAlchemySink:
    """Tests for SQLAlchemySink using SQLite in-memory database."""

    @pytest.fixture
    def sink(self) -> SQLAlchemySink:
        """Create a sink with SQLite in-memory database."""
        return SQLAlchemySink("sqlite+pysqlite:///:memory:")

    def test_emits_and_stores_event(self, sink: SQLAlchemySink) -> None:
        """Sink should store an event in the database."""
        event = make_test_event("test-event-1")
        sink.emit(event)

        assert sink.count() == 1

    def test_stores_multiple_events(self, sink: SQLAlchemySink) -> None:
        """Sink should store multiple events."""
        sink.emit(make_test_event("event-1"))
        sink.emit(make_test_event("event-2"))
        sink.emit(make_test_event("event-3"))

        assert sink.count() == 3

    def test_extracts_required_columns(self, sink: SQLAlchemySink) -> None:
        """Sink should extract key fields into columns."""
        event = make_test_event("test-event-cols")
        event["service"]["environment"] = "production"
        event["resource"]["patient_id"] = "patient_456"
        sink.emit(event)

        # Query the raw row
        with sink.engine.connect() as conn:
            result = conn.execute(sink.table.select())
            row = dict(result.fetchone()._mapping)

        assert row["event_id"] == "test-event-cols"
        assert row["service_name"] == "test-service"
        assert row["environment"] == "production"
        assert row["actor_subject_id"] == "user_123"
        assert row["actor_subject_type"] == "human"
        assert row["action_type"] == "READ"
        assert row["resource_type"] == "Patient"
        assert row["resource_id"] == "resource_789"
        assert row["patient_id"] == "patient_456"
        assert row["outcome_status"] == "SUCCESS"

    def test_stores_full_event_json(self, sink: SQLAlchemySink) -> None:
        """Sink should store the complete event as JSON."""
        event = make_test_event("test-event-json")
        sink.emit(event)

        with sink.engine.connect() as conn:
            result = conn.execute(sink.table.select())
            row = dict(result.fetchone()._mapping)

        stored_event = json.loads(row["event_json"])
        assert stored_event["event_id"] == "test-event-json"
        assert stored_event["schema_version"] == "1.0"
        assert stored_event["service"]["name"] == "test-service"

    def test_event_json_matches_original(self, sink: SQLAlchemySink) -> None:
        """Stored event_json should match the original event."""
        event = make_test_event("test-event-match")
        event["correlation"] = {"trace_id": "abc123", "request_id": "req456"}
        event["http"] = {"method": "GET", "status_code": 200}
        sink.emit(event)

        with sink.engine.connect() as conn:
            result = conn.execute(sink.table.select())
            row = dict(result.fetchone()._mapping)

        stored_event = json.loads(row["event_json"])
        assert stored_event == event

    def test_extracts_correlation_ids(self, sink: SQLAlchemySink) -> None:
        """Sink should extract correlation IDs into columns."""
        event = make_test_event("test-event-corr")
        event["correlation"] = {
            "trace_id": "trace-abc-123",
            "request_id": "req-xyz-789",
        }
        sink.emit(event)

        with sink.engine.connect() as conn:
            result = conn.execute(sink.table.select())
            row = dict(result.fetchone()._mapping)

        assert row["trace_id"] == "trace-abc-123"
        assert row["request_id"] == "req-xyz-789"

    def test_extracts_http_status_code(self, sink: SQLAlchemySink) -> None:
        """Sink should extract HTTP status code into column."""
        event = make_test_event("test-event-http")
        event["http"] = {"method": "POST", "status_code": 201}
        sink.emit(event)

        with sink.engine.connect() as conn:
            result = conn.execute(sink.table.select())
            row = dict(result.fetchone()._mapping)

        assert row["http_status_code"] == 201

    def test_handles_missing_optional_fields(self, sink: SQLAlchemySink) -> None:
        """Sink should handle events without optional fields."""
        event = make_test_event("test-event-minimal")
        # No correlation, no http, no patient_id
        sink.emit(event)

        with sink.engine.connect() as conn:
            result = conn.execute(sink.table.select())
            row = dict(result.fetchone()._mapping)

        assert row["trace_id"] is None
        assert row["request_id"] is None
        assert row["http_status_code"] is None
        assert row["patient_id"] is None

    def test_ignores_duplicate_event_ids(self, sink: SQLAlchemySink) -> None:
        """Sink should silently ignore duplicate event_ids."""
        event1 = make_test_event("same-event-id")
        event2 = make_test_event("same-event-id")
        event2["action"]["type"] = "UPDATE"  # Different content, same ID

        sink.emit(event1)
        sink.emit(event2)  # Should be ignored

        assert sink.count() == 1

        # First event should be kept
        with sink.engine.connect() as conn:
            result = conn.execute(sink.table.select())
            row = dict(result.fetchone()._mapping)

        assert row["action_type"] == "READ"  # Original event's action type

    def test_query_by_patient(self, sink: SQLAlchemySink) -> None:
        """query_by_patient should return events for a specific patient."""
        sink.emit(make_test_event("event-1", patient_id="patient_A"))
        sink.emit(make_test_event("event-2", patient_id="patient_B"))
        sink.emit(make_test_event("event-3", patient_id="patient_A"))

        results = sink.query_by_patient("patient_A")
        assert len(results) == 2
        assert all(r["patient_id"] == "patient_A" for r in results)

    def test_query_by_actor(self, sink: SQLAlchemySink) -> None:
        """query_by_actor should return events for a specific actor."""
        sink.emit(make_test_event("event-1", actor_subject_id="user_alice"))
        sink.emit(make_test_event("event-2", actor_subject_id="user_bob"))
        sink.emit(make_test_event("event-3", actor_subject_id="user_alice"))

        results = sink.query_by_actor("user_alice")
        assert len(results) == 2
        assert all(r["actor_subject_id"] == "user_alice" for r in results)

    def test_custom_table_name(self) -> None:
        """Sink should support custom table names."""
        sink = SQLAlchemySink(
            "sqlite+pysqlite:///:memory:",
            table_name="custom_audit_table",
        )
        sink.emit(make_test_event("custom-table-event"))

        assert sink.count() == 1
        assert sink.table.name == "custom_audit_table"

    def test_handles_timestamp_with_z_suffix(self, sink: SQLAlchemySink) -> None:
        """Sink should handle ISO timestamps with Z suffix."""
        event = make_test_event("test-z-timestamp")
        event["timestamp"] = "2026-01-06T18:42:01Z"
        sink.emit(event)

        assert sink.count() == 1

    def test_handles_timestamp_with_offset(self, sink: SQLAlchemySink) -> None:
        """Sink should handle ISO timestamps with timezone offset."""
        event = make_test_event("test-offset-timestamp")
        event["timestamp"] = "2026-01-06T18:42:01+00:00"
        sink.emit(event)

        assert sink.count() == 1


class TestSQLAlchemySinkConformsToProtocol:
    """Test that SQLAlchemySink conforms to AuditSink protocol."""

    def test_implements_emit(self) -> None:
        """SQLAlchemySink should implement the emit method."""
        from bh_fastapi_audit.sinks.base import AuditSink

        sink = SQLAlchemySink("sqlite+pysqlite:///:memory:")
        assert isinstance(sink, AuditSink)
