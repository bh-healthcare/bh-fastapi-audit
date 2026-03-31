"""
Tests for schema negotiation via target_schema_version.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bh_fastapi_audit import AuditConfig, AuditMiddleware, MemorySink
from bh_fastapi_audit._validation import validate_event
from tests.conftest import audit_config


def _find_middleware(app: Any) -> AuditMiddleware | None:
    """Walk a Starlette/FastAPI ASGI stack and return the AuditMiddleware."""
    current = app
    while current is not None:
        if isinstance(current, AuditMiddleware):
            return current
        current = getattr(current, "app", None)
    return None


def _make_app(
    config: AuditConfig,
    sink: MemorySink,
) -> tuple[TestClient, MemorySink, AuditMiddleware]:
    app = FastAPI()
    app.add_middleware(AuditMiddleware, sink=sink, config=config)

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/forbidden")
    def forbidden() -> None:
        raise HTTPException(status_code=403, detail="Forbidden")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app, raise_server_exceptions=False)
    client.get("/health")

    mw = _find_middleware(app.middleware_stack)
    assert mw is not None, "AuditMiddleware not found on app stack"
    return client, sink, mw


class TestSchemaVersionEmission:
    def test_target_1_0_emits_schema_version_1_0(self) -> None:
        cfg = audit_config(target_schema_version="1.0")
        sink = MemorySink()
        client, sink, mw = _make_app(cfg, sink)

        client.get("/ok")

        event = sink.events[0]
        assert event["schema_version"] == "1.0"

    def test_target_1_1_emits_schema_version_1_1(self) -> None:
        cfg = audit_config(target_schema_version="1.1")
        sink = MemorySink()
        client, sink, mw = _make_app(cfg, sink)

        client.get("/ok")

        event = sink.events[0]
        assert event["schema_version"] == "1.1"


class TestDeniedDowngrade:
    def test_target_1_0_downgrades_denied_to_failure(self) -> None:
        cfg = audit_config(target_schema_version="1.0")
        sink = MemorySink()
        client, sink, mw = _make_app(cfg, sink)

        client.get("/forbidden")

        event = sink.events[0]
        assert event["outcome"]["status"] == "FAILURE"
        assert "error_type" in event["outcome"]
        assert "error_message" in event["outcome"]

    def test_target_1_1_emits_denied(self) -> None:
        cfg = audit_config(target_schema_version="1.1")
        sink = MemorySink()
        client, sink, mw = _make_app(cfg, sink)

        client.get("/forbidden")

        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"


class TestSchemaValidation:
    def test_target_1_0_events_pass_1_0_schema(self) -> None:
        cfg = audit_config(target_schema_version="1.0")
        sink = MemorySink()
        client, sink, mw = _make_app(cfg, sink)

        client.get("/ok")
        client.get("/forbidden")

        for event in sink.events:
            errors = validate_event(event, schema_version="1.0")
            assert errors == [], f"v1.0 validation failed: {errors}"

    def test_target_1_1_events_pass_1_1_schema(self) -> None:
        cfg = audit_config(target_schema_version="1.1")
        sink = MemorySink()
        client, sink, mw = _make_app(cfg, sink)

        client.get("/ok")
        client.get("/forbidden")

        for event in sink.events:
            errors = validate_event(event, schema_version="1.1")
            assert errors == [], f"v1.1 validation failed: {errors}"

    def test_validation_uses_matching_schema(self) -> None:
        """When validate_events=True, validation uses the target version."""
        cfg = audit_config(
            validate_events=True,
            target_schema_version="1.0",
            validation_failure_mode="log_and_emit",
        )
        sink = MemorySink()
        client, sink, mw = _make_app(cfg, sink)

        client.get("/ok")

        assert len(sink) == 1
        event = sink.events[0]
        assert event["schema_version"] == "1.0"
        assert mw.stats.validation_time_ms_total > 0
