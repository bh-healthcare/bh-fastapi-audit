"""
Test that audit middleware emits events correctly.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bh_fastapi_audit import AuditConfig, AuditMiddleware, MemorySink


@pytest.fixture
def memory_sink() -> MemorySink:
    """Create a fresh memory sink for testing."""
    return MemorySink()


@pytest.fixture
def audit_config() -> AuditConfig:
    """Create standard test audit config."""
    return AuditConfig(
        service_name="test-service",
        service_environment="test",
        service_version="1.0.0",
        emit_mode="sync",
    )


@pytest.fixture
def app(memory_sink: MemorySink, audit_config: AuditConfig) -> FastAPI:
    """Create a test FastAPI app with audit middleware."""
    app = FastAPI()
    app.add_middleware(AuditMiddleware, sink=memory_sink, config=audit_config)

    @app.get("/items/{item_id}")
    def get_item(item_id: str) -> dict:
        return {"item_id": item_id}

    @app.post("/items")
    def create_item() -> dict:
        return {"created": True}

    @app.delete("/items/{item_id}")
    def delete_item(item_id: str) -> dict:
        return {"deleted": True}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client."""
    return TestClient(app)


class TestAuditMiddlewareEmitsEvent:
    """Test that middleware emits one event per request."""

    def test_emits_event_on_get(self, client: TestClient, memory_sink: MemorySink) -> None:
        """A GET request should emit exactly one audit event."""
        response = client.get("/items/123")

        assert response.status_code == 200
        assert len(memory_sink) == 1

    def test_emits_event_on_post(self, client: TestClient, memory_sink: MemorySink) -> None:
        """A POST request should emit exactly one audit event."""
        response = client.post("/items")

        assert response.status_code == 200
        assert len(memory_sink) == 1

    def test_emits_event_on_delete(self, client: TestClient, memory_sink: MemorySink) -> None:
        """A DELETE request should emit exactly one audit event."""
        response = client.delete("/items/456")

        assert response.status_code == 200
        assert len(memory_sink) == 1

    def test_skips_excluded_paths(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Health check endpoints should not emit audit events."""
        response = client.get("/health")

        assert response.status_code == 200
        assert len(memory_sink) == 0


class TestAuditEventStructure:
    """Test that emitted events have required fields."""

    def test_event_has_required_fields(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Event should contain all required schema fields."""
        client.get("/items/123")

        assert len(memory_sink) == 1
        event = memory_sink.events[0]

        assert "schema_version" in event
        assert "event_id" in event
        assert "timestamp" in event
        assert "service" in event
        assert "actor" in event
        assert "action" in event
        assert "resource" in event
        assert "outcome" in event

    def test_schema_version(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Schema version should be 1.1."""
        client.get("/items/123")

        event = memory_sink.events[0]
        assert event["schema_version"] == "1.1"

    def test_event_id_is_uuid(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Event ID should be a valid UUID string."""
        client.get("/items/123")

        event = memory_sink.events[0]
        event_id = event["event_id"]
        assert len(event_id) == 36
        assert event_id.count("-") == 4

    def test_service_block(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Service block should contain configured values."""
        client.get("/items/123")

        event = memory_sink.events[0]
        assert event["service"]["name"] == "test-service"
        assert event["service"]["environment"] == "test"
        assert event["service"]["version"] == "1.0.0"

    def test_actor_defaults(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Actor should have default values when not configured."""
        client.get("/items/123")

        event = memory_sink.events[0]
        assert event["actor"]["subject_id"] == "unknown"
        assert event["actor"]["subject_type"] == "service"

    def test_action_type_from_http_method(
        self, client: TestClient, memory_sink: MemorySink
    ) -> None:
        """Action type should be derived from HTTP method."""
        client.get("/items/123")
        assert memory_sink.events[0]["action"]["type"] == "READ"

        memory_sink.clear()

        client.post("/items")
        assert memory_sink.events[0]["action"]["type"] == "CREATE"

        memory_sink.clear()

        client.delete("/items/789")
        assert memory_sink.events[0]["action"]["type"] == "DELETE"

    def test_http_block(self, client: TestClient, memory_sink: MemorySink) -> None:
        """HTTP block should contain method and status code."""
        client.get("/items/123")

        event = memory_sink.events[0]
        assert event["http"]["method"] == "GET"
        assert event["http"]["status_code"] == 200
        assert "route_template" in event["http"]

    def test_outcome_success(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Outcome should be SUCCESS for 2xx responses."""
        client.get("/items/123")

        event = memory_sink.events[0]
        assert event["outcome"]["status"] == "SUCCESS"


class TestCorrelation:
    """Test correlation header extraction."""

    def test_extracts_request_id(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Should extract X-Request-ID header into correlation."""
        client.get("/items/123", headers={"X-Request-ID": "req-12345"})

        event = memory_sink.events[0]
        assert "correlation" in event
        assert event["correlation"]["request_id"] == "req-12345"

    def test_extracts_trace_id(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Should extract X-Trace-ID header into correlation."""
        client.get("/items/123", headers={"X-Trace-ID": "trace-abc"})

        event = memory_sink.events[0]
        assert "correlation" in event
        assert event["correlation"]["trace_id"] == "trace-abc"

    def test_extracts_traceparent(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Should extract trace_id from OpenTelemetry traceparent header."""
        traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        client.get("/items/123", headers={"traceparent": traceparent})

        event = memory_sink.events[0]
        assert "correlation" in event
        assert event["correlation"]["trace_id"] == "0af7651916cd43dd8448eb211c80319c"

    def test_no_correlation_without_headers(
        self, client: TestClient, memory_sink: MemorySink
    ) -> None:
        """Should not include correlation block when no headers present."""
        client.get("/items/123")

        event = memory_sink.events[0]
        assert "correlation" not in event


class TestMultipleRequests:
    """Test behavior across multiple requests."""

    def test_emits_separate_events(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Each request should emit a separate event."""
        client.get("/items/1")
        client.get("/items/2")
        client.post("/items")

        assert len(memory_sink) == 3

    def test_unique_event_ids(self, client: TestClient, memory_sink: MemorySink) -> None:
        """Each event should have a unique event_id."""
        client.get("/items/1")
        client.get("/items/2")

        event_ids = [e["event_id"] for e in memory_sink.events]
        assert len(event_ids) == len(set(event_ids))
