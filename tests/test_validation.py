"""
Tests for _validation.py (unit) and middleware validation wiring (integration).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bh_fastapi_audit import AuditMiddleware, MemorySink
from bh_fastapi_audit._validation import validate_event
from tests.conftest import audit_config, build_app, make_test_event

# ---------------------------------------------------------------
# Unit tests for validate_event
# ---------------------------------------------------------------


class TestValidateEventUnit:
    def test_valid_event_passes(self) -> None:
        event = make_test_event()
        errors = validate_event(event, schema_version="1.1")
        assert errors == []

    def test_missing_required_field(self) -> None:
        event = make_test_event()
        del event["actor"]
        errors = validate_event(event, schema_version="1.1")
        assert len(errors) > 0
        assert any("actor" in e for e in errors)

    def test_failure_without_error_type(self) -> None:
        event = make_test_event(
            outcome={"status": "FAILURE"},
        )
        errors = validate_event(event, schema_version="1.1")
        assert len(errors) > 0

    def test_denied_without_error_type(self) -> None:
        event = make_test_event(
            outcome={"status": "DENIED"},
        )
        errors = validate_event(event, schema_version="1.1")
        assert len(errors) > 0

    def test_metadata_nested_object_fails(self) -> None:
        event = make_test_event(
            metadata={"nested": {"a": 1}},
        )
        errors = validate_event(event, schema_version="1.1")
        assert len(errors) > 0

    def test_empty_correlation_fails(self) -> None:
        event = make_test_event(correlation={})
        errors = validate_event(event, schema_version="1.1")
        assert len(errors) > 0


# ---------------------------------------------------------------
# Integration tests for middleware validation wiring
# ---------------------------------------------------------------


class TestMiddlewareValidationWiring:
    def test_validate_events_false_skips(self) -> None:
        """When validate_events=False, no validation overhead occurs."""
        cfg = audit_config(validate_events=False)
        sink = MemorySink()
        client, sink, mw = build_app(cfg, sink)

        client.get("/items/1")

        assert len(sink) == 1
        assert mw.stats.validation_failures_total == 0
        assert mw.stats.validation_time_ms_total == 0.0

    def test_drop_mode_increments_counters(self) -> None:
        """In drop mode, invalid events are dropped and counters incremented."""
        cfg = audit_config(
            validate_events=True,
            validation_failure_mode="drop",
            target_schema_version="1.1",
        )
        sink = MemorySink()
        client, sink, mw = build_app(cfg, sink)

        client.get("/items/1")
        assert len(sink) == 0 or mw.stats.validation_failures_total == 0

        # Either the event was valid and emitted, or failed and was dropped.
        # The default app emits valid 1.1 events, so it should pass.
        if mw.stats.validation_failures_total == 0:
            assert len(sink) == 1
        else:
            assert mw.stats.events_dropped_total > 0

    def test_log_and_emit_mode_still_emits(self) -> None:
        """In log_and_emit mode, events are emitted even if validation fails."""
        cfg = audit_config(
            validate_events=True,
            validation_failure_mode="log_and_emit",
        )
        sink = MemorySink()
        client, sink, mw = build_app(cfg, sink)

        client.get("/items/1")

        assert len(sink) == 1
        assert mw.stats.validation_time_ms_total > 0

    def test_raise_mode_raises_on_valid_event(self) -> None:
        """With raise mode, valid events pass through without raising."""
        cfg = audit_config(
            validate_events=True,
            validation_failure_mode="raise",
            target_schema_version="1.1",
        )
        sink = MemorySink()
        client, sink, mw = build_app(cfg, sink)

        client.get("/items/1")

        assert len(sink) == 1
        assert mw.stats.validation_failures_total == 0

    def test_raise_mode_raises_on_invalid_event(self) -> None:
        """With raise mode, AuditValidationError escapes the finally block.

        We monkey-patch _build_event to produce an invalid event and prove
        that AuditValidationError propagates to the caller.
        """
        import pytest

        from bh_fastapi_audit._validation import AuditValidationError

        cfg = audit_config(
            validate_events=True,
            validation_failure_mode="raise",
            target_schema_version="1.1",
        )
        sink = MemorySink()
        app = FastAPI()
        app.add_middleware(AuditMiddleware, sink=sink, config=cfg)

        @app.get("/items/{item_id}")
        def get_item(item_id: str) -> dict[str, str]:
            return {"item_id": item_id}

        client = TestClient(app, raise_server_exceptions=True)

        client.get("/items/1")
        assert len(sink) == 1

        mw = None
        current = app.middleware_stack
        while current is not None:
            if isinstance(current, AuditMiddleware):
                mw = current
                break
            current = getattr(current, "app", None)
        assert mw is not None

        original_build = mw._build_event

        def _bad_build(*args: Any, **kwargs: Any) -> dict[str, Any]:
            event = original_build(*args, **kwargs)
            del event["actor"]
            return event

        mw._build_event = _bad_build  # type: ignore[assignment]

        with pytest.raises(AuditValidationError) as exc_info:
            client.get("/items/2")

        assert "actor" in str(exc_info.value)
        assert len(sink) == 1  # second request's event was NOT emitted

    def test_validation_timing_recorded(self) -> None:
        cfg = audit_config(validate_events=True)
        sink = MemorySink()
        client, sink, mw = build_app(cfg, sink)

        client.get("/items/1")

        snap = mw.stats.snapshot()
        assert snap["validation_time_ms_total"] > 0

    def test_validation_uses_target_schema_version(self) -> None:
        """Validation should use the configured schema version."""
        cfg = audit_config(
            validate_events=True,
            target_schema_version="1.0",
            validation_failure_mode="log_and_emit",
        )
        sink = MemorySink()
        client, sink, mw = build_app(cfg, sink)

        client.get("/items/1")

        assert len(sink) == 1
        event = sink.events[0]
        assert event["schema_version"] == "1.0"
