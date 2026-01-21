"""
Tests for Logging sink.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

import pytest

from bh_fastapi_audit.sinks.logging_sink import LoggingSink


def make_test_event(event_id: str | None = None) -> dict:
    """Create a minimal valid audit event for testing."""
    return {
        "schema_version": "1.0",
        "event_id": event_id or str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "service": {
            "name": "test-service",
            "environment": "test",
        },
        "actor": {
            "subject_id": "user_123",
            "subject_type": "human",
        },
        "action": {
            "type": "READ",
        },
        "resource": {
            "type": "Patient",
            "id": "patient_456",
        },
        "outcome": {
            "status": "SUCCESS",
        },
    }


class TestLoggingSink:
    """Tests for LoggingSink."""

    def test_emits_single_log_record(self, caplog: pytest.LogCaptureFixture) -> None:
        """Sink should emit exactly one log record per event."""
        sink = LoggingSink(logger_name="bh.audit.test", level="INFO")

        with caplog.at_level(logging.INFO, logger="bh.audit.test"):
            sink.emit(make_test_event())

        assert len(caplog.records) == 1

    def test_log_message_is_valid_json(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log message should parse as valid JSON."""
        sink = LoggingSink(logger_name="bh.audit.test", level="INFO")

        with caplog.at_level(logging.INFO, logger="bh.audit.test"):
            sink.emit(make_test_event())

        record = caplog.records[0]
        # Should not raise
        parsed = json.loads(record.message)
        assert isinstance(parsed, dict)

    def test_log_message_is_single_line(self, caplog: pytest.LogCaptureFixture) -> None:
        """Log message should be a single line with no newlines."""
        sink = LoggingSink(logger_name="bh.audit.test", level="INFO")

        with caplog.at_level(logging.INFO, logger="bh.audit.test"):
            sink.emit(make_test_event())

        record = caplog.records[0]
        assert "\n" not in record.message

    def test_log_message_contains_required_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log message should contain all required audit schema fields."""
        sink = LoggingSink(logger_name="bh.audit.test", level="INFO")

        with caplog.at_level(logging.INFO, logger="bh.audit.test"):
            sink.emit(make_test_event())

        record = caplog.records[0]
        parsed = json.loads(record.message)

        # Required top-level fields from bh-audit-schema v1.0
        assert "schema_version" in parsed
        assert "event_id" in parsed
        assert "timestamp" in parsed
        assert "service" in parsed
        assert "actor" in parsed
        assert "action" in parsed
        assert "resource" in parsed
        assert "outcome" in parsed

    def test_compact_json_no_whitespace(self, caplog: pytest.LogCaptureFixture) -> None:
        """Output should be compact JSON with no unnecessary whitespace."""
        sink = LoggingSink(logger_name="bh.audit.test", level="INFO")

        with caplog.at_level(logging.INFO, logger="bh.audit.test"):
            sink.emit(make_test_event())

        record = caplog.records[0]
        # No spaces after colons or commas (compact separators)
        assert ": " not in record.message
        # No pretty-print indentation
        assert "\n  " not in record.message

    def test_default_logger_name(self) -> None:
        """Default logger name should be bh.audit."""
        sink = LoggingSink()
        assert sink.logger_name == "bh.audit"

    def test_custom_logger_name(self) -> None:
        """Custom logger name should be used."""
        sink = LoggingSink(logger_name="custom.audit")
        assert sink.logger_name == "custom.audit"

    def test_default_level_is_info(self) -> None:
        """Default level should be INFO."""
        sink = LoggingSink()
        assert sink.level == logging.INFO

    def test_custom_level_as_string(self) -> None:
        """Level can be specified as string."""
        sink = LoggingSink(level="WARNING")
        assert sink.level == logging.WARNING

    def test_custom_level_as_int(self) -> None:
        """Level can be specified as int."""
        sink = LoggingSink(level=logging.DEBUG)
        assert sink.level == logging.DEBUG

    def test_level_string_case_insensitive(self) -> None:
        """Level string should be case insensitive."""
        sink = LoggingSink(level="warning")
        assert sink.level == logging.WARNING

    def test_emits_at_correct_level(self, caplog: pytest.LogCaptureFixture) -> None:
        """Events should be emitted at the configured level."""
        sink = LoggingSink(logger_name="bh.audit.test", level="WARNING")

        with caplog.at_level(logging.WARNING, logger="bh.audit.test"):
            sink.emit(make_test_event())

        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.WARNING

    def test_handles_unicode(self, caplog: pytest.LogCaptureFixture) -> None:
        """Sink should handle unicode characters correctly."""
        sink = LoggingSink(logger_name="bh.audit.test", level="INFO")

        event = make_test_event()
        event["actor"]["subject_id"] = "user_日本語_émoji_🏥"

        with caplog.at_level(logging.INFO, logger="bh.audit.test"):
            sink.emit(event)

        record = caplog.records[0]
        parsed = json.loads(record.message)
        assert parsed["actor"]["subject_id"] == "user_日本語_émoji_🏥"

    def test_multiple_events_multiple_records(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Multiple events should produce multiple log records."""
        sink = LoggingSink(logger_name="bh.audit.test", level="INFO")

        with caplog.at_level(logging.INFO, logger="bh.audit.test"):
            sink.emit(make_test_event("event-1"))
            sink.emit(make_test_event("event-2"))

        assert len(caplog.records) == 2

        parsed1 = json.loads(caplog.records[0].message)
        parsed2 = json.loads(caplog.records[1].message)
        assert parsed1["event_id"] == "event-1"
        assert parsed2["event_id"] == "event-2"


class TestLoggingSinkConformsToProtocol:
    """Test that LoggingSink conforms to AuditSink protocol."""

    def test_implements_emit(self) -> None:
        """LoggingSink should implement the emit method."""
        from bh_fastapi_audit.sinks.base import AuditSink

        sink = LoggingSink()
        assert isinstance(sink, AuditSink)
