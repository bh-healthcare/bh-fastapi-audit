"""
Schema validation tests for bh-fastapi-audit v0.3.

Validates that events emitted by the middleware conform to the vendored
bh-audit-schema v1.1 JSON schema with FormatChecker enabled (uuid,
date-time, ipv4/ipv6 formats are enforced).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bh_fastapi_audit import AuditConfig, AuditMiddleware, MemorySink

jsonschema = pytest.importorskip("jsonschema")

from bh_fastapi_audit.schema import load_schema  # noqa: E402

_FORMAT_CHECKER = jsonschema.FormatChecker()


def _make_app(sink: MemorySink, **config_overrides: Any) -> FastAPI:
    app = FastAPI()
    config = AuditConfig(
        service_name="schema-test",
        service_environment="test",
        service_version="0.3.0",
        include_client_ip=False,
        emit_mode="sync",
        **config_overrides,
    )
    app.add_middleware(AuditMiddleware, sink=sink, config=config)

    @app.get("/patients/{patient_id}")
    def get_patient(patient_id: str) -> dict:
        return {"id": patient_id}

    @app.post("/patients")
    def create_patient() -> dict:
        return {"created": True}

    @app.get("/fail")
    def fail_endpoint() -> dict:
        raise ValueError("something went wrong")

    @app.get("/forbidden")
    def forbidden_endpoint() -> dict:
        raise HTTPException(status_code=403, detail="Access denied")

    @app.get("/unauthorized")
    def unauthorized_endpoint() -> dict:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return app


@pytest.fixture
def schema() -> dict[str, Any]:
    return load_schema()


@pytest.fixture
def sink() -> MemorySink:
    return MemorySink()


@pytest.fixture
def client(sink: MemorySink) -> TestClient:
    return TestClient(_make_app(sink))


def _validate(event: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate with FormatChecker so uuid/date-time/ip formats are enforced."""
    jsonschema.validate(instance=event, schema=schema, format_checker=_FORMAT_CHECKER)


class TestEmittedEventsValidateAgainstSchema:
    """Every emitted event must pass full JSON schema validation with format checking."""

    def test_success_event_validates(
        self, client: TestClient, sink: MemorySink, schema: dict[str, Any]
    ) -> None:
        client.get("/patients/pat_1")
        assert len(sink) == 1
        _validate(sink.events[0], schema)

    def test_post_event_validates(
        self, client: TestClient, sink: MemorySink, schema: dict[str, Any]
    ) -> None:
        client.post("/patients")
        assert len(sink) == 1
        _validate(sink.events[0], schema)

    def test_exception_event_validates(self, sink: MemorySink, schema: dict[str, Any]) -> None:
        client = TestClient(_make_app(sink), raise_server_exceptions=False)
        client.get("/fail")
        assert len(sink) == 1
        event = sink.events[0]
        assert event["outcome"]["status"] == "FAILURE"
        assert "error_type" in event["outcome"]
        assert "error_message" in event["outcome"]
        _validate(event, schema)

    def test_denied_403_validates(self, sink: MemorySink, schema: dict[str, Any]) -> None:
        client = TestClient(_make_app(sink))
        client.get("/forbidden")
        assert len(sink) == 1
        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"
        assert event["outcome"]["error_type"] == "HTTP403"
        assert "error_message" not in event["outcome"]
        _validate(event, schema)

    def test_denied_401_validates(self, sink: MemorySink, schema: dict[str, Any]) -> None:
        client = TestClient(_make_app(sink))
        client.get("/unauthorized")
        assert len(sink) == 1
        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"
        assert event["outcome"]["error_type"] == "HTTP401"
        _validate(event, schema)

    def test_schema_version_is_1_1(self, client: TestClient, sink: MemorySink) -> None:
        client.get("/patients/pat_1")
        assert sink.events[0]["schema_version"] == "1.1"

    def test_event_id_is_valid_uuid(self, client: TestClient, sink: MemorySink) -> None:
        import re

        client.get("/patients/pat_1")
        event_id = sink.events[0]["event_id"]
        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            event_id,
        )

    def test_truncated_header_fits_within_schema_maxlength(
        self, sink: MemorySink, schema: dict[str, Any]
    ) -> None:
        """Truncated correlation IDs must be <= 256 chars (schema maxLength)."""
        client = TestClient(_make_app(sink))
        client.get(
            "/patients/pat_1",
            headers={"X-Request-ID": "r" * 1000, "X-Trace-ID": "t" * 1000},
        )
        event = sink.events[0]
        assert len(event["correlation"]["request_id"]) <= 256
        assert len(event["correlation"]["trace_id"]) <= 256
        _validate(event, schema)


class TestGetActionCallback:
    """get_action callback enables phi_touched / data_classification."""

    def test_phi_touched_via_callback(self, sink: MemorySink) -> None:
        def tag_phi(request: Any) -> dict:
            if "/patients" in str(request.url):
                return {"phi_touched": True, "data_classification": "PHI"}
            return None

        app = _make_app(sink, get_action=tag_phi)
        client = TestClient(app)
        client.get("/patients/pat_1")

        event = sink.events[0]
        assert event["action"]["phi_touched"] is True
        assert event["action"]["data_classification"] == "PHI"
        assert event["action"]["type"] == "READ"

    def test_callback_failure_falls_back(self, sink: MemorySink) -> None:
        def broken(request: Any) -> dict:
            raise RuntimeError("boom")

        app = _make_app(sink, get_action=broken)
        client = TestClient(app)
        resp = client.get("/patients/pat_1")
        assert resp.status_code == 200

        event = sink.events[0]
        assert event["action"]["type"] == "READ"
        assert event["action"]["data_classification"] == "UNKNOWN"
