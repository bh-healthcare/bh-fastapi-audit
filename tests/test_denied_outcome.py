"""
Tests for DENIED outcome, denial callbacks, and configurable denied status codes.
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


def _make_status_app(
    config: AuditConfig,
    sink: MemorySink,
) -> tuple[TestClient, MemorySink, AuditMiddleware]:
    """App with endpoints returning various status codes."""
    app = FastAPI()
    app.add_middleware(AuditMiddleware, sink=sink, config=config)

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/forbidden")
    def forbidden() -> None:
        raise HTTPException(status_code=403, detail="Forbidden")

    @app.get("/unauthorized")
    def unauthorized() -> None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/not-found")
    def not_found() -> None:
        raise HTTPException(status_code=404, detail="Not Found")

    @app.get("/server-error")
    def server_error() -> None:
        raise HTTPException(status_code=500, detail="Internal Server Error")

    @app.get("/consent-denied")
    def consent_denied() -> None:
        raise HTTPException(status_code=403, detail="ConsentRequired: patient did not consent")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app, raise_server_exceptions=False)
    client.get("/health")

    mw = _find_middleware(app.middleware_stack)
    assert mw is not None, "AuditMiddleware not found on app stack"
    return client, sink, mw


class TestDeniedOutcome:
    def test_403_produces_denied(self) -> None:
        cfg = audit_config()
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/forbidden")

        assert len(sink) == 1
        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"

    def test_401_produces_denied(self) -> None:
        cfg = audit_config()
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/unauthorized")

        assert len(sink) == 1
        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"

    def test_403_denied_has_error_type(self) -> None:
        cfg = audit_config()
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/forbidden")

        event = sink.events[0]
        assert "error_type" in event["outcome"]
        assert event["outcome"]["error_type"] == "HTTP403"

    def test_200_produces_success(self) -> None:
        cfg = audit_config()
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/ok")

        event = sink.events[0]
        assert event["outcome"]["status"] == "SUCCESS"

    def test_500_produces_failure(self) -> None:
        cfg = audit_config()
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/server-error")

        event = sink.events[0]
        assert event["outcome"]["status"] == "FAILURE"
        assert "error_type" in event["outcome"]
        assert "error_message" in event["outcome"]


class TestDenialCallback:
    def test_custom_denial_reason_callback(self) -> None:
        def denial_reason(request: Any, exc_info: Any) -> str | None:
            return "CustomDenialReason"

        cfg = audit_config(get_denial_reason=denial_reason)
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/forbidden")

        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"
        assert event["outcome"]["error_type"] == "CustomDenialReason"

    def test_denial_callback_failure_falls_back(self) -> None:
        def bad_callback(request: Any, exc_info: Any) -> str | None:
            raise ValueError("callback broke")

        cfg = audit_config(get_denial_reason=bad_callback)
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/forbidden")

        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"
        assert event["outcome"]["error_type"] == "HTTP403"
        assert mw.stats.callback_failures_total >= 1

    def test_denial_callback_none_uses_default(self) -> None:
        def returns_none(request: Any, exc_info: Any) -> str | None:
            return None

        cfg = audit_config(get_denial_reason=returns_none)
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/forbidden")

        event = sink.events[0]
        assert event["outcome"]["error_type"] == "HTTP403"


class TestConfigurableDeniedStatusCodes:
    def test_configurable_denied_status_codes(self) -> None:
        cfg = audit_config(denied_status_codes=frozenset({401, 403, 451}))
        sink = MemorySink()
        app = FastAPI()
        app.add_middleware(AuditMiddleware, sink=sink, config=cfg)

        @app.get("/legal-block")
        def legal_block() -> None:
            raise HTTPException(status_code=451, detail="Unavailable For Legal Reasons")

        client = TestClient(app, raise_server_exceptions=False)
        client.get("/legal-block")

        assert len(sink) == 1
        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"

    def test_404_not_denied_by_default(self) -> None:
        cfg = audit_config()
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/not-found")

        event = sink.events[0]
        assert event["outcome"]["status"] == "FAILURE"


class TestDeniedWithErrorMessage:
    def test_denied_with_error_message(self) -> None:
        """DENIED outcome from exc_info should carry error_type."""
        cfg = audit_config()
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/consent-denied")

        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"
        assert "error_type" in event["outcome"]

    def test_failure_always_has_error_type_and_message(self) -> None:
        cfg = audit_config()
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/server-error")

        event = sink.events[0]
        outcome = event["outcome"]
        assert outcome["status"] == "FAILURE"
        assert "error_type" in outcome
        assert "error_message" in outcome


class TestDeniedSchemaCompliance:
    def test_denied_emitted_events_pass_schema(self) -> None:
        cfg = audit_config()
        sink = MemorySink()
        client, sink, mw = _make_status_app(cfg, sink)

        client.get("/forbidden")
        client.get("/unauthorized")
        client.get("/ok")
        client.get("/server-error")

        for event in sink.events:
            errors = validate_event(event, schema_version="1.1")
            assert errors == [], f"Event {event['event_id']} failed: {errors}"
