"""
Pure ASGI audit middleware for emitting structured audit events.

v0.3 rewrites the middleware as a direct ASGI implementation (no longer
based on ``BaseHTTPMiddleware``).  This enables streaming response support,
avoids known Starlette issues, and reduces per-request overhead.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from starlette.requests import Request
from starlette.routing import Match

from bh_fastapi_audit._queue import EmitQueue
from bh_fastapi_audit._stats import AuditStats
from bh_fastapi_audit._types import (
    ActionBlock,
    ActorBlock,
    AuditEvent,
    CorrelationBlock,
    HttpBlock,
    OutcomeBlock,
    ServiceBlock,
)
from bh_fastapi_audit.redaction import sanitize_error_message
from bh_fastapi_audit.schema import SCHEMA_VERSION
from bh_fastapi_audit.sinks.base import AuditSink

HTTP_METHOD_TO_ACTION: dict[str, str] = {
    "GET": "READ",
    "HEAD": "READ",
    "POST": "CREATE",
    "PUT": "UPDATE",
    "PATCH": "UPDATE",
    "DELETE": "DELETE",
}

_SCALAR_TYPES = (str, int, float, bool, type(None))
_MAX_HEADER_VALUE_LENGTH = 256
_DENIED_STATUS_CODES = frozenset({401, 403})


@dataclass(frozen=True)
class AuditConfig:
    """Configuration for the audit middleware.

    Frozen after creation to prevent runtime mutation of security
    settings (e.g. ``metadata_allowlist``).
    """

    service_name: str
    service_environment: str = "unknown"
    service_version: str | None = None
    default_actor_id: str = "unknown"
    default_actor_type: Literal["human", "service"] = "service"
    get_actor: Callable[[Request], dict[str, Any] | None] | None = None
    get_action: Callable[[Request], dict[str, Any] | None] | None = None
    get_resource: Callable[[Request, int], dict[str, Any] | None] | None = None
    get_metadata: Callable[[Request, int], dict[str, Any] | None] | None = None
    metadata_allowlist: frozenset[str] = field(default_factory=frozenset)
    excluded_paths: frozenset[str] = field(
        default_factory=lambda: frozenset({"/health", "/healthz", "/ready"}),
    )
    emit_failure_mode: Literal["silent", "log", "raise"] = "log"
    failure_logger_name: str = "bh.audit.internal"
    max_metadata_value_length: int = 200
    include_client_ip: bool = False
    emit_mode: Literal["sync", "queue"] = "queue"
    queue_size: int = 10_000
    queue_drain_timeout: float = 5.0

    def __post_init__(self) -> None:
        if isinstance(self.metadata_allowlist, set):
            object.__setattr__(self, "metadata_allowlist", frozenset(self.metadata_allowlist))
        if isinstance(self.excluded_paths, set):
            object.__setattr__(self, "excluded_paths", frozenset(self.excluded_paths))


class AuditMiddleware:
    """Pure ASGI middleware that emits audit events for each HTTP request.

    Events conform to bh-audit-schema v1.1.

    PHI Safety Guarantees:
    - Never reads or logs request/response bodies
    - Only extracts allowlisted headers (no Authorization, Cookie, etc.)
    - Sanitizes exception messages before logging
    - Metadata is opt-in via allowlist
    """

    def __init__(
        self,
        app: Any,
        sink: AuditSink,
        config: AuditConfig,
    ) -> None:
        self.app = app
        self.sink = sink
        self.config = config
        self._stats = AuditStats()
        self._failure_log = logging.getLogger(config.failure_logger_name)
        self._queue: EmitQueue | None = None
        if config.emit_mode == "queue":
            self._queue = EmitQueue(
                sink,
                self._stats,
                maxsize=config.queue_size,
                emit_failure_mode=config.emit_failure_mode,
                failure_logger=self._failure_log,
            )

    @property
    def stats(self) -> AuditStats:
        """Return the internal emission counters."""
        return self._stats

    async def shutdown(self) -> None:
        """Drain the async emit queue (call on app shutdown)."""
        if self._queue is not None:
            await self._queue.shutdown(timeout=self.config.queue_drain_timeout)

    # ------------------------------------------------------------------
    # ASGI entry point
    # ------------------------------------------------------------------

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path in self.config.excluded_paths:
            await self.app(scope, receive, send)
            return

        start_time = datetime.now(UTC)
        response_status: int = 500
        exc_info: tuple[str, str, int] | None = None

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 500)
            exc_info = (
                exc.__class__.__name__,
                sanitize_error_message(str(exc)),
                status_code,
            )
            raise
        finally:
            try:
                event = self._build_event(
                    scope,
                    response_status,
                    start_time,
                    exc_info,
                )
                self._safe_emit(event)
            except Exception:
                self._stats.increment("emit_failures_total")

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    def _safe_emit(self, event: dict[str, Any]) -> None:
        """Emit via queue (non-blocking) or direct sink call."""
        if self._queue is not None:
            self._queue.enqueue(event)
            return

        try:
            self.sink.emit(event)
        except Exception as exc:
            self._stats.increment("emit_failures_total")
            mode = self.config.emit_failure_mode
            if mode == "raise":
                raise
            if mode == "log":
                self._failure_log.warning(
                    "Audit sink emit failed: event_id=%s service=%s action=%s resource=%s error=%s",
                    event.get("event_id"),
                    event.get("service", {}).get("name"),
                    event.get("action", {}).get("type"),
                    event.get("resource", {}).get("type"),
                    exc,
                )
        else:
            self._stats.increment("events_emitted_total")

    # ------------------------------------------------------------------
    # Event construction
    # ------------------------------------------------------------------

    def _build_event(
        self,
        scope: dict[str, Any],
        status_code: int,
        timestamp: datetime,
        exc_info: tuple[str, str, int] | None = None,
    ) -> AuditEvent:
        request = Request(scope)

        event: AuditEvent = {
            "schema_version": SCHEMA_VERSION,
            "event_id": str(uuid.uuid4()),
            "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "service": self._build_service(),
            "actor": self._build_actor(request),
            "action": self._build_action(request),
            "resource": self._build_resource(request, status_code),
            "http": self._build_http(request, status_code, exc_info),
            "outcome": self._build_outcome(status_code, exc_info),
        }

        correlation = self._build_correlation(request)
        if correlation:
            event["correlation"] = correlation

        metadata = self._build_metadata(request, status_code)
        if metadata:
            event["metadata"] = metadata

        return event

    def _build_service(self) -> ServiceBlock:
        service: ServiceBlock = {
            "name": self.config.service_name,
            "environment": self.config.service_environment,
        }
        if self.config.service_version:
            service["version"] = self.config.service_version
        return service

    def _build_actor(self, request: Request) -> ActorBlock:
        if self.config.get_actor:
            try:
                custom_actor = self.config.get_actor(request)
                if custom_actor:
                    return custom_actor  # type: ignore[return-value]
            except Exception as exc:
                self._stats.increment("callback_failures_total")
                self._failure_log.warning(
                    "get_actor callback failed, falling back to defaults: %s",
                    exc,
                )
        return {
            "subject_id": self.config.default_actor_id,
            "subject_type": self.config.default_actor_type,
        }

    def _build_action(self, request: Request) -> ActionBlock:
        if self.config.get_action:
            try:
                custom_action = self.config.get_action(request)
                if custom_action:
                    result = dict(custom_action)
                    result.setdefault("type", HTTP_METHOD_TO_ACTION.get(request.method, "OTHER"))
                    result.setdefault("data_classification", "UNKNOWN")
                    return result  # type: ignore[return-value]
            except Exception as exc:
                self._stats.increment("callback_failures_total")
                self._failure_log.warning(
                    "get_action callback failed, falling back to defaults: %s",
                    exc,
                )
        action_type = HTTP_METHOD_TO_ACTION.get(request.method, "OTHER")
        return {
            "type": action_type,  # type: ignore[typeddict-item]
            "data_classification": "UNKNOWN",
        }

    def _build_resource(self, request: Request, status_code: int) -> dict[str, Any]:
        if self.config.get_resource:
            try:
                custom_resource = self.config.get_resource(request, status_code)
                if custom_resource:
                    return custom_resource
            except Exception as exc:
                self._stats.increment("callback_failures_total")
                self._failure_log.warning(
                    "get_resource callback failed, falling back to defaults: %s",
                    exc,
                )

        route = self._resolve_route(request)
        if route and hasattr(route, "name") and route.name:
            return {"type": route.name}
        return {"type": "Unknown"}

    def _build_http(
        self,
        request: Request,
        status_code: int,
        exc_info: tuple[str, str, int] | None = None,
    ) -> HttpBlock:
        if exc_info is not None:
            status_code = exc_info[2]

        http: HttpBlock = {
            "method": request.method,  # type: ignore[typeddict-item]
            "status_code": status_code,
        }

        route = self._resolve_route(request)
        http["route_template"] = route.path if (route and hasattr(route, "path")) else "unknown"

        if self.config.include_client_ip:
            client = request.client
            if client and client.host:
                http["client_ip"] = client.host

        user_agent = request.headers.get("user-agent")
        if user_agent:
            http["user_agent"] = self._cap_header(user_agent)

        return http

    def _build_outcome(
        self,
        status_code: int,
        exc_info: tuple[str, str, int] | None = None,
    ) -> OutcomeBlock:
        if exc_info is not None:
            error_type, error_message, exc_status = exc_info
            if exc_status in _DENIED_STATUS_CODES:
                return {
                    "status": "DENIED",
                    "error_type": error_type,
                }
            return {
                "status": "FAILURE",
                "error_type": error_type,
                "error_message": error_message,
            }

        if status_code in _DENIED_STATUS_CODES:
            return {
                "status": "DENIED",
                "error_type": f"HTTP{status_code}",
            }
        if status_code >= 400:
            return {
                "status": "FAILURE",
                "error_type": f"HTTP{status_code}",
                "error_message": f"HTTP {status_code} response",
            }
        return {"status": "SUCCESS"}

    @staticmethod
    def _cap_header(value: str, maxlen: int = _MAX_HEADER_VALUE_LENGTH) -> str:
        """Truncate a header-sourced string so the output fits within *maxlen*."""
        if len(value) > maxlen:
            return value[: maxlen - 3] + "..."
        return value

    def _build_correlation(self, request: Request) -> CorrelationBlock | None:
        correlation: CorrelationBlock = {}

        request_id = request.headers.get("x-request-id")
        if request_id:
            correlation["request_id"] = self._cap_header(request_id)

        trace_id = request.headers.get("x-trace-id")
        if not trace_id:
            traceparent = request.headers.get("traceparent")
            if traceparent:
                parts = traceparent.split("-")
                if len(parts) >= 2:
                    trace_id = parts[1]
        if trace_id:
            correlation["trace_id"] = self._cap_header(trace_id)

        session_id = request.headers.get("x-session-id")
        if session_id:
            correlation["session_id"] = self._cap_header(session_id)

        return correlation if correlation else None

    def _build_metadata(
        self,
        request: Request,
        status_code: int,
    ) -> dict[str, Any] | None:
        if not self.config.get_metadata or not self.config.metadata_allowlist:
            return None

        try:
            raw_metadata = self.config.get_metadata(request, status_code)
        except Exception as exc:
            self._stats.increment("callback_failures_total")
            self._failure_log.warning(
                "get_metadata callback failed, skipping metadata: %s",
                exc,
            )
            return None
        if not raw_metadata:
            return None

        allowlist = self.config.metadata_allowlist
        max_len = self.config.max_metadata_value_length
        filtered: dict[str, Any] = {}

        for key, value in raw_metadata.items():
            if key not in allowlist:
                continue
            if not isinstance(value, _SCALAR_TYPES):
                continue
            if isinstance(value, str) and len(value) > max_len:
                value = value[:max_len] + "..."
            filtered[key] = value

        return filtered if filtered else None

    @staticmethod
    def _resolve_route(request: Request) -> Any:
        """Resolve the matched Starlette route from the request scope."""
        route = request.scope.get("route")
        if route is not None:
            return route
        app = request.scope.get("app")
        if app is not None:
            for r in getattr(app, "routes", []):
                match, _ = r.matches(request.scope)
                if match == Match.FULL:
                    return r
        return None
