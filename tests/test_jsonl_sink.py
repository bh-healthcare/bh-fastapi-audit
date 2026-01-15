"""
Tests for JSONL file sink.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bh_fastapi_audit.sinks.jsonl import JsonlFileSink


def make_test_event(event_id: str | None = None) -> dict:
    """Create a minimal valid audit event for testing."""
    return {
        "schema_version": "1.0",
        "event_id": event_id or str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
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


class TestJsonlFileSink:
    """Tests for JsonlFileSink."""

    def test_writes_single_event(self, tmp_path: Path) -> None:
        """Sink should write a single event as one JSON line."""
        output_file = tmp_path / "audit.jsonl"
        sink = JsonlFileSink(output_file)

        event = make_test_event()
        sink.emit(event)
        sink.close()

        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["event_id"] == event["event_id"]

    def test_writes_multiple_events(self, tmp_path: Path) -> None:
        """Sink should write multiple events, one per line."""
        output_file = tmp_path / "audit.jsonl"
        sink = JsonlFileSink(output_file)

        event1 = make_test_event("event-1")
        event2 = make_test_event("event-2")
        sink.emit(event1)
        sink.emit(event2)
        sink.close()

        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 2

        parsed1 = json.loads(lines[0])
        parsed2 = json.loads(lines[1])
        assert parsed1["event_id"] == "event-1"
        assert parsed2["event_id"] == "event-2"

    def test_event_has_required_keys(self, tmp_path: Path) -> None:
        """Written events should contain all required schema keys."""
        output_file = tmp_path / "audit.jsonl"
        sink = JsonlFileSink(output_file)

        event = make_test_event()
        sink.emit(event)
        sink.close()

        lines = output_file.read_text().strip().split("\n")
        parsed = json.loads(lines[0])

        # Required top-level fields from bh-audit-schema v1.0
        assert "schema_version" in parsed
        assert "event_id" in parsed
        assert "timestamp" in parsed
        assert "service" in parsed
        assert "actor" in parsed
        assert "action" in parsed
        assert "resource" in parsed
        assert "outcome" in parsed

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Sink should create parent directories if they don't exist."""
        output_file = tmp_path / "nested" / "path" / "audit.jsonl"
        sink = JsonlFileSink(output_file)

        event = make_test_event()
        sink.emit(event)
        sink.close()

        assert output_file.exists()
        assert len(output_file.read_text().strip().split("\n")) == 1

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        """Sink should append to existing file, not overwrite."""
        output_file = tmp_path / "audit.jsonl"

        # First sink writes first event
        sink1 = JsonlFileSink(output_file)
        sink1.emit(make_test_event("event-1"))
        sink1.close()

        # Second sink writes second event
        sink2 = JsonlFileSink(output_file)
        sink2.emit(make_test_event("event-2"))
        sink2.close()

        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_compact_json_no_whitespace(self, tmp_path: Path) -> None:
        """Output should be compact JSON with no unnecessary whitespace."""
        output_file = tmp_path / "audit.jsonl"
        sink = JsonlFileSink(output_file)

        event = make_test_event()
        sink.emit(event)
        sink.close()

        content = output_file.read_text()
        # No pretty-print indentation
        assert "\n  " not in content
        # No spaces after colons or commas (compact separators)
        assert ": " not in content.split("\n")[0]

    def test_context_manager(self, tmp_path: Path) -> None:
        """Sink should work as a context manager."""
        output_file = tmp_path / "audit.jsonl"

        with JsonlFileSink(output_file) as sink:
            sink.emit(make_test_event())

        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_flush_enabled_by_default(self, tmp_path: Path) -> None:
        """Flush should be enabled by default."""
        output_file = tmp_path / "audit.jsonl"
        sink = JsonlFileSink(output_file)

        assert sink._flush is True  # noqa: SLF001

    def test_flush_can_be_disabled(self, tmp_path: Path) -> None:
        """Flush can be disabled for performance."""
        output_file = tmp_path / "audit.jsonl"
        sink = JsonlFileSink(output_file, flush=False)

        assert sink._flush is False  # noqa: SLF001

    def test_path_property(self, tmp_path: Path) -> None:
        """Path property should return the output file path."""
        output_file = tmp_path / "audit.jsonl"
        sink = JsonlFileSink(output_file)

        assert sink.path == output_file

    def test_handles_unicode(self, tmp_path: Path) -> None:
        """Sink should handle unicode characters correctly."""
        output_file = tmp_path / "audit.jsonl"
        sink = JsonlFileSink(output_file)

        event = make_test_event()
        event["actor"]["subject_id"] = "user_日本語_émoji_🏥"
        sink.emit(event)
        sink.close()

        lines = output_file.read_text(encoding="utf-8").strip().split("\n")
        parsed = json.loads(lines[0])
        assert parsed["actor"]["subject_id"] == "user_日本語_émoji_🏥"


class TestJsonlFileSinkConformsToProtocol:
    """Test that JsonlFileSink conforms to AuditSink protocol."""

    def test_implements_emit(self, tmp_path: Path) -> None:
        """JsonlFileSink should implement the emit method."""
        from bh_fastapi_audit.sinks.base import AuditSink

        sink = JsonlFileSink(tmp_path / "audit.jsonl")
        assert isinstance(sink, AuditSink)
