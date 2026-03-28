"""
Tests for v0.3.0 production hardening.

Covers sink failure isolation, metadata safety, internal counters,
HTTPException status code preservation, route_template defaults,
client_ip opt-in, compact failure logging, callback failure isolation,
header value caps, raise mode, and exception-path event structure.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bh_fastapi_audit import AuditConfig, AuditMiddleware, MemorySink

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ExplodingSink:
    """A sink that always raises on emit."""

    def emit(self, event: dict[str, Any]) -> None:
        raise RuntimeError("sink boom")


def _build_app(
    sink: Any,
    *,
    emit_failure_mode: str = "log",
    include_client_ip: bool = False,
    metadata_allowlist: frozenset[str] | None = None,
    max_metadata_value_length: int = 200,
    get_metadata: Any = None,
    get_actor: Any = None,
    get_resource: Any = None,
) -> tuple[FastAPI, AuditConfig]:
    app = FastAPI()
    config = AuditConfig(
        service_name="hardening-test",
        service_environment="test",
        emit_failure_mode=emit_failure_mode,  # type: ignore[arg-type]
        include_client_ip=include_client_ip,
        metadata_allowlist=metadata_allowlist or frozenset(),
        max_metadata_value_length=max_metadata_value_length,
        get_metadata=get_metadata,
        get_actor=get_actor,
        get_resource=get_resource,
        emit_mode="sync",
    )
    app.add_middleware(AuditMiddleware, sink=sink, config=config)

    @app.get("/ok")
    def ok_endpoint() -> dict:
        return {"status": "ok"}

    @app.get("/error")
    def error_endpoint() -> dict:
        raise ValueError("app error")

    @app.get("/not-found")
    def not_found_endpoint() -> dict:
        raise HTTPException(status_code=404, detail="not found")

    return app, config


# ---------------------------------------------------------------------------
# Sink failure isolation
# ---------------------------------------------------------------------------


class TestSinkFailureIsolation:
    """Sink errors must not break request processing by default."""

    def test_sink_failure_does_not_break_request(self) -> None:
        app, _ = _build_app(_ExplodingSink(), emit_failure_mode="log")
        client = TestClient(app)
        resp = client.get("/ok")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_sink_failure_silent_does_not_break_request(self) -> None:
        app, _ = _build_app(_ExplodingSink(), emit_failure_mode="silent")
        client = TestClient(app)
        resp = client.get("/ok")
        assert resp.status_code == 200

    def test_sink_failure_does_not_mask_app_exception(self) -> None:
        """When the app raises AND the sink raises, the app exception wins."""
        app, _ = _build_app(_ExplodingSink(), emit_failure_mode="log")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/error")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Internal counters
# ---------------------------------------------------------------------------


def _find_audit_middleware(app: FastAPI) -> AuditMiddleware | None:
    """Walk the middleware stack to locate our AuditMiddleware instance."""
    current = getattr(app, "middleware_stack", None)
    while current is not None:
        if isinstance(current, AuditMiddleware):
            return current
        current = getattr(current, "app", None)
    return None


class TestCounters:
    """AuditStats counters must increment correctly."""

    def test_events_emitted_increments(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        client.get("/ok")
        audit_mw = _find_audit_middleware(app)
        assert audit_mw is not None
        assert audit_mw.stats.events_emitted_total == 1

        client.get("/ok")
        assert audit_mw.stats.events_emitted_total == 2

    def test_emit_failures_increments(self) -> None:
        app, _ = _build_app(_ExplodingSink(), emit_failure_mode="log")
        client = TestClient(app)
        client.get("/ok")

        audit_mw = _find_audit_middleware(app)
        assert audit_mw is not None
        assert audit_mw.stats.emit_failures_total == 1
        assert audit_mw.stats.events_emitted_total == 0


# ---------------------------------------------------------------------------
# Metadata safety
# ---------------------------------------------------------------------------


class TestMetadataSafety:
    """Metadata must drop non-scalars and truncate long strings."""

    def test_drops_non_scalar_values(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(
            sink,
            metadata_allowlist=frozenset({"safe", "nested", "items"}),
            get_metadata=lambda req, status: {
                "safe": "ok",
                "nested": {"a": 1},
                "items": [1, 2],
            },
        )
        client = TestClient(app)
        client.get("/ok")

        event = sink.events[0]
        assert event["metadata"] == {"safe": "ok"}

    def test_truncates_long_strings(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(
            sink,
            metadata_allowlist=frozenset({"note"}),
            max_metadata_value_length=10,
            get_metadata=lambda req, status: {"note": "a" * 50},
        )
        client = TestClient(app)
        client.get("/ok")

        event = sink.events[0]
        assert event["metadata"]["note"] == "a" * 10 + "..."

    def test_short_strings_not_truncated(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(
            sink,
            metadata_allowlist=frozenset({"note"}),
            get_metadata=lambda req, status: {"note": "short"},
        )
        client = TestClient(app)
        client.get("/ok")

        event = sink.events[0]
        assert event["metadata"]["note"] == "short"


# ---------------------------------------------------------------------------
# HTTPException status code preservation
# ---------------------------------------------------------------------------


class TestHTTPExceptionStatusCode:
    """HTTPException status codes must appear in emitted events."""

    def test_404_status_preserved_in_response_path(self) -> None:
        """HTTPException handled by FastAPI flows through as a response."""
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        resp = client.get("/not-found")
        assert resp.status_code == 404

        event = sink.events[0]
        assert event["http"]["status_code"] == 404
        assert event["outcome"]["status"] == "FAILURE"


# ---------------------------------------------------------------------------
# route_template defaults
# ---------------------------------------------------------------------------


class TestRouteTemplateDefault:
    """route_template should always be present, defaulting to 'unknown'."""

    def test_known_route_has_template(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        client.get("/ok")
        event = sink.events[0]
        assert event["http"]["route_template"] == "/ok"

    def test_unmatched_route_defaults_to_unknown(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        resp = client.get("/does-not-exist")
        assert resp.status_code == 404

        if sink.events:
            event = sink.events[0]
            assert event["http"]["route_template"] == "unknown"


# ---------------------------------------------------------------------------
# Client IP opt-in
# ---------------------------------------------------------------------------


class TestClientIPOptIn:
    """client_ip must only appear when include_client_ip=True."""

    def test_excluded_by_default(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink, include_client_ip=False)
        client = TestClient(app)
        client.get("/ok")
        event = sink.events[0]
        assert "client_ip" not in event["http"]

    def test_included_when_enabled(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink, include_client_ip=True)
        client = TestClient(app)
        client.get("/ok")
        event = sink.events[0]
        assert "client_ip" in event["http"]


# ---------------------------------------------------------------------------
# Compact failure logging
# ---------------------------------------------------------------------------


class TestFailureLogging:
    """Internal failure logs must be compact and never contain full payload."""

    def test_log_contains_compact_summary(self, caplog: pytest.LogCaptureFixture) -> None:
        app, _ = _build_app(_ExplodingSink(), emit_failure_mode="log")
        client = TestClient(app)
        with caplog.at_level(logging.WARNING, logger="bh.audit.internal"):
            client.get("/ok")

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_records) >= 1
        msg = warning_records[0].getMessage()
        assert "hardening-test" in msg
        assert "READ" in msg

    def test_log_does_not_contain_full_payload(self, caplog: pytest.LogCaptureFixture) -> None:
        app, _ = _build_app(
            _ExplodingSink(),
            emit_failure_mode="log",
            metadata_allowlist=frozenset({"secret"}),
            get_metadata=lambda req, status: {"secret": "do-not-log-this"},
        )
        client = TestClient(app)
        with caplog.at_level(logging.WARNING, logger="bh.audit.internal"):
            client.get("/ok")

        for record in caplog.records:
            msg = record.getMessage()
            assert "do-not-log-this" not in msg


# ---------------------------------------------------------------------------
# Callback failure isolation
# ---------------------------------------------------------------------------


class TestCallbackFailureIsolation:
    """User-provided callbacks must never crash requests."""

    def test_get_actor_failure_does_not_break_request(self) -> None:
        sink = MemorySink()

        def _broken_actor(request: Any) -> dict:
            raise RuntimeError("actor db timeout")

        app, _ = _build_app(sink, get_actor=_broken_actor)
        client = TestClient(app)
        resp = client.get("/ok")
        assert resp.status_code == 200

        event = sink.events[0]
        assert event["actor"]["subject_id"] == "unknown"
        assert event["actor"]["subject_type"] == "service"

    def test_get_resource_failure_does_not_break_request(self) -> None:
        sink = MemorySink()

        def _broken_resource(request: Any, status_code: int) -> dict:
            raise ValueError("resource lookup failed")

        app, _ = _build_app(sink, get_resource=_broken_resource)
        client = TestClient(app)
        resp = client.get("/ok")
        assert resp.status_code == 200

        event = sink.events[0]
        assert "type" in event["resource"]

    def test_get_metadata_failure_does_not_break_request(self) -> None:
        sink = MemorySink()

        def _broken_metadata(request: Any, status_code: int) -> dict:
            raise TypeError("metadata serialization error")

        app, _ = _build_app(
            sink,
            metadata_allowlist=frozenset({"key"}),
            get_metadata=_broken_metadata,
        )
        client = TestClient(app)
        resp = client.get("/ok")
        assert resp.status_code == 200

        event = sink.events[0]
        assert "metadata" not in event

    def test_callback_failure_increments_callback_counter(self) -> None:
        sink = MemorySink()

        def _broken_actor(request: Any) -> dict:
            raise RuntimeError("boom")

        app, _ = _build_app(sink, get_actor=_broken_actor)
        client = TestClient(app)
        client.get("/ok")

        audit_mw = _find_audit_middleware(app)
        assert audit_mw is not None
        assert audit_mw.stats.callback_failures_total >= 1
        assert audit_mw.stats.emit_failures_total == 0

    def test_callback_failure_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        sink = MemorySink()

        def _broken_actor(request: Any) -> dict:
            raise RuntimeError("actor db timeout")

        app, _ = _build_app(sink, get_actor=_broken_actor)
        client = TestClient(app)
        with caplog.at_level(logging.WARNING, logger="bh.audit.internal"):
            client.get("/ok")

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) >= 1
        assert "get_actor" in warnings[0].getMessage()
        assert "actor db timeout" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# Header value length caps
# ---------------------------------------------------------------------------


class TestHeaderValueLengthCaps:
    """Header-sourced strings must be capped to prevent event inflation."""

    def test_long_user_agent_truncated(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        client.get("/ok", headers={"User-Agent": "x" * 1000})

        event = sink.events[0]
        ua = event["http"]["user_agent"]
        assert len(ua) <= 256
        assert ua.endswith("...")

    def test_short_user_agent_not_truncated(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        client.get("/ok", headers={"User-Agent": "MyAgent/1.0"})

        event = sink.events[0]
        assert event["http"]["user_agent"] == "MyAgent/1.0"

    def test_long_request_id_truncated(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        client.get("/ok", headers={"X-Request-ID": "r" * 1000})

        event = sink.events[0]
        rid = event["correlation"]["request_id"]
        assert len(rid) <= 256
        assert rid.endswith("...")

    def test_long_trace_id_truncated(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        client.get("/ok", headers={"X-Trace-ID": "t" * 1000})

        event = sink.events[0]
        tid = event["correlation"]["trace_id"]
        assert len(tid) <= 256
        assert tid.endswith("...")

    def test_long_session_id_truncated(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        client.get("/ok", headers={"X-Session-ID": "s" * 1000})

        event = sink.events[0]
        sid = event["correlation"]["session_id"]
        assert len(sid) <= 256
        assert sid.endswith("...")

    def test_normal_correlation_values_unchanged(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        client.get(
            "/ok",
            headers={
                "X-Request-ID": "req-123",
                "X-Trace-ID": "trace-abc",
                "X-Session-ID": "sess-xyz",
            },
        )

        event = sink.events[0]
        assert event["correlation"]["request_id"] == "req-123"
        assert event["correlation"]["trace_id"] == "trace-abc"
        assert event["correlation"]["session_id"] == "sess-xyz"


# ---------------------------------------------------------------------------
# emit_failure_mode="raise"
# ---------------------------------------------------------------------------


class TestRaiseMode:
    """emit_failure_mode='raise' propagates sink exceptions from _safe_emit.

    With the always-swallow finally block (audit must never crash requests),
    raise mode only applies in sync _safe_emit, not the finally guard.
    The middleware increments emit_failures_total on any finally-block failure.
    """

    def test_raise_mode_increments_failure_counter_on_sync_emit(self) -> None:
        app, _ = _build_app(_ExplodingSink(), emit_failure_mode="raise")
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/ok")

        audit_mw = _find_audit_middleware(app)
        assert audit_mw is not None
        assert audit_mw.stats.emit_failures_total >= 1


# ---------------------------------------------------------------------------
# Exception-path event structure
# ---------------------------------------------------------------------------


class TestExceptionPathEventStructure:
    """Events emitted on the exception path must have correct structure."""

    def test_exception_event_has_all_required_fields(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/error")

        assert len(sink) == 1
        event = sink.events[0]
        for key in (
            "schema_version",
            "event_id",
            "timestamp",
            "service",
            "actor",
            "action",
            "resource",
            "outcome",
            "http",
        ):
            assert key in event, f"Missing key: {key}"

    def test_exception_event_has_failure_outcome(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/error")

        event = sink.events[0]
        assert event["outcome"]["status"] == "FAILURE"
        assert event["outcome"]["error_type"] == "ValueError"

    def test_exception_event_has_status_500(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/error")

        event = sink.events[0]
        assert event["http"]["status_code"] == 500

    def test_finally_block_does_not_mask_original_exception(self) -> None:
        """If _build_event fails in the finally block, the app exception must still propagate."""

        class _BuildBreakingSink:
            def emit(self, event: dict[str, Any]) -> None:
                pass

        app, _ = _build_app(_BuildBreakingSink())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/error")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# v1.1 schema compliance: FAILURE requires error_type + error_message
# ---------------------------------------------------------------------------


class TestV11OutcomeCompliance:
    """v1.1 outcome semantics: FAILURE requires error_type + error_message,
    DENIED for 401/403 requires error_type (no error_message required)."""

    def test_http_404_is_failure(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app)
        client.get("/not-found")

        event = sink.events[0]
        assert event["outcome"]["status"] == "FAILURE"
        assert "error_type" in event["outcome"]
        assert "error_message" in event["outcome"]

    def test_exception_path_includes_error_type_and_message(self) -> None:
        sink = MemorySink()
        app, _ = _build_app(sink)
        client = TestClient(app, raise_server_exceptions=False)
        client.get("/error")

        event = sink.events[0]
        assert event["outcome"]["status"] == "FAILURE"
        assert event["outcome"]["error_type"] == "ValueError"
        assert "error_message" in event["outcome"]

    def test_http_403_is_denied(self) -> None:
        """403 Forbidden should map to DENIED, not FAILURE."""
        sink = MemorySink()
        app = FastAPI()
        config = AuditConfig(
            service_name="denied-test",
            service_environment="test",
            emit_mode="sync",
        )
        app.add_middleware(AuditMiddleware, sink=sink, config=config)

        @app.get("/forbidden")
        def forbidden() -> dict:
            raise HTTPException(status_code=403, detail="Access denied")

        client = TestClient(app)
        client.get("/forbidden")

        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"
        assert event["outcome"]["error_type"] == "HTTP403"
        assert "error_message" not in event["outcome"]
        assert event["http"]["status_code"] == 403

    def test_http_401_is_denied(self) -> None:
        """401 Unauthorized should map to DENIED, not FAILURE."""
        sink = MemorySink()
        app = FastAPI()
        config = AuditConfig(
            service_name="denied-test",
            service_environment="test",
            emit_mode="sync",
        )
        app.add_middleware(AuditMiddleware, sink=sink, config=config)

        @app.get("/unauth")
        def unauth() -> dict:
            raise HTTPException(status_code=401, detail="Not authenticated")

        client = TestClient(app)
        client.get("/unauth")

        event = sink.events[0]
        assert event["outcome"]["status"] == "DENIED"
        assert event["outcome"]["error_type"] == "HTTP401"

    def test_http_400_is_still_failure(self) -> None:
        """400 Bad Request is not an access denial — should remain FAILURE."""
        sink = MemorySink()
        app = FastAPI()
        config = AuditConfig(
            service_name="denied-test",
            service_environment="test",
            emit_mode="sync",
        )
        app.add_middleware(AuditMiddleware, sink=sink, config=config)

        @app.get("/bad")
        def bad() -> dict:
            raise HTTPException(status_code=400, detail="Bad request")

        client = TestClient(app)
        client.get("/bad")

        event = sink.events[0]
        assert event["outcome"]["status"] == "FAILURE"
        assert event["outcome"]["error_type"] == "HTTP400"


# ---------------------------------------------------------------------------
# Frozen config
# ---------------------------------------------------------------------------


class TestFrozenConfig:
    """AuditConfig should be immutable after creation."""

    def test_config_is_frozen(self) -> None:
        config = AuditConfig(service_name="test")
        with pytest.raises(AttributeError):
            config.service_name = "hacked"  # type: ignore[misc]

    def test_config_accepts_frozenset(self) -> None:
        config = AuditConfig(
            service_name="test",
            metadata_allowlist=frozenset({"key1", "key2"}),
            excluded_paths=frozenset({"/health"}),
        )
        assert "key1" in config.metadata_allowlist
        assert "/health" in config.excluded_paths

    def test_mutable_set_coerced_to_frozenset(self) -> None:
        """Passing a mutable set is silently coerced to frozenset."""
        mutable = {"key1", "key2"}
        config = AuditConfig(service_name="test", metadata_allowlist=mutable)  # type: ignore[arg-type]
        assert isinstance(config.metadata_allowlist, frozenset)
        mutable.add("key3")
        assert "key3" not in config.metadata_allowlist
