"""
Shared test fixtures for bh-fastapi-audit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bh_fastapi_audit import AuditConfig, AuditMiddleware, MemorySink


def make_test_event(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid v1.1 audit event, with optional overrides."""
    event: dict[str, Any] = {
        "schema_version": "1.1",
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "service": {"name": "test-svc", "environment": "test"},
        "actor": {"subject_id": "user-1", "subject_type": "human"},
        "action": {"type": "READ", "data_classification": "UNKNOWN"},
        "resource": {"type": "TestResource"},
        "outcome": {"status": "SUCCESS"},
    }
    event.update(overrides)
    return event


@pytest.fixture()
def memory_sink() -> MemorySink:
    """Fresh MemorySink instance."""
    return MemorySink()


def audit_config(**overrides: Any) -> AuditConfig:
    """Factory that returns an AuditConfig with sensible test defaults."""
    defaults: dict[str, Any] = {
        "service_name": "test-service",
        "service_environment": "test",
        "service_version": "1.0.0",
        "emit_mode": "sync",
    }
    defaults.update(overrides)
    return AuditConfig(**defaults)


def _find_middleware(app: Any) -> AuditMiddleware | None:
    """Walk a Starlette/FastAPI ASGI stack and return the AuditMiddleware."""
    current = app
    while current is not None:
        if isinstance(current, AuditMiddleware):
            return current
        current = getattr(current, "app", None)
    return None


def build_app(
    config: AuditConfig,
    sink: MemorySink,
) -> tuple[TestClient, MemorySink, AuditMiddleware]:
    """Build a FastAPI app with the given config + sink, return (client, sink, mw)."""
    app = FastAPI()
    app.add_middleware(AuditMiddleware, sink=sink, config=config)

    @app.get("/items/{item_id}")
    def get_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    @app.post("/items")
    def create_item() -> dict[str, bool]:
        return {"created": True}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app, raise_server_exceptions=False)

    # Force Starlette to build the middleware stack by making a request
    # to an excluded path (no audit event emitted).
    client.get("/health")

    mw = _find_middleware(app.middleware_stack)
    assert mw is not None, "AuditMiddleware not found on app stack"
    return client, sink, mw
