"""
FastAPI audit middleware for emitting structured audit events.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from bh_fastapi_audit._stats import AuditStats
from bh_fastapi_audit.redaction import sanitize_error_message
from bh_fastapi_audit.sinks.base import AuditSink

# HTTP method to action type mapping
HTTP_METHOD_TO_ACTION: dict[str, str] = {
    "GET": "READ",
    "HEAD": "READ",
    "POST": "CREATE",
    "PUT": "UPDATE",
    "PATCH": "UPDATE",
    "DELETE": "DELETE",
}

SCHEMA_VERSION = "1.0"

# Headers that are safe to extract for correlation
# Authorization, Cookie, and other sensitive headers are explicitly excluded
_SAFE_CORRELATION_HEADERS = frozenset({
    "x-request-id",
    "x-trace-id",
    "traceparent",
    "x-session-id",
})

# Headers that are safe to log (non-sensitive)
_SAFE_LOGGED_HEADERS = frozenset({
    "user-agent",
    "accept",
    "accept-language",
    "content-type",
    "content-length",
})


_SCALAR_TYPES = (str, int, float, bool, type(None))

# Hard cap for header-sourced string values (correlation IDs, user-agent).
# Prevents unbounded user-controlled input from inflating audit events.
_MAX_HEADER_VALUE_LENGTH = 256


@dataclass
class AuditConfig:
    """
    Configuration for the audit middleware.

    Attributes:
        service_name: Name of the service emitting audit events.
        service_environment: Environment (prod, staging, dev, etc.).
        service_version: Optional version string for the service.
        default_actor_id: Default actor ID when no user is authenticated.
        default_actor_type: Default actor type ("human" or "service").
        get_actor: Optional callback to extract actor from request.
        get_resource: Optional callback to extract resource from request/response.
        get_metadata: Optional callback to provide custom metadata.
        metadata_allowlist: Set of allowed metadata keys. Empty = no metadata.
        excluded_paths: Paths to skip auditing.
        emit_failure_mode: How to handle sink emission failures ("silent", "log", "raise").
        failure_logger_name: Logger name used for internal failure diagnostics.
        max_metadata_value_length: Maximum string length for metadata values before truncation.
        include_client_ip: Whether to include client IP address in emitted events.
    """

    service_name: str
    service_environment: str = "unknown"
    service_version: str | None = None
    default_actor_id: str = "unknown"
    default_actor_type: str = "service"
    get_actor: Callable[[Request], dict[str, Any] | None] | None = None
    get_resource: Callable[[Request, Response], dict[str, Any] | None] | None = None
    get_metadata: Callable[[Request, Response], dict[str, Any] | None] | None = None
    metadata_allowlist: set[str] = field(default_factory=set)
    excluded_paths: set[str] = field(default_factory=lambda: {"/health", "/healthz", "/ready"})
    emit_failure_mode: Literal["silent", "log", "raise"] = "log"
    failure_logger_name: str = "bh.audit.internal"
    max_metadata_value_length: int = 200
    include_client_ip: bool = False


class AuditMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that emits audit events for each request.

    Events conform to bh-audit-schema v1.0.

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
        """
        Initialize the audit middleware.

        Args:
            app: The ASGI application.
            sink: The audit sink to emit events to.
            config: Audit configuration.
        """
        super().__init__(app)
        self.sink = sink
        self.config = config
        self._stats = AuditStats()
        self._failure_log = logging.getLogger(config.failure_logger_name)

    @property
    def stats(self) -> AuditStats:
        """Return the internal emission counters."""
        return self._stats

    def _safe_emit(self, event: dict[str, Any]) -> None:
        """Emit via sink with failure isolation governed by config."""
        try:
            self.sink.emit(event)
            self._stats.events_emitted_total += 1
        except Exception as exc:
            self._stats.emit_failures_total += 1
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

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[type-arg]
        """Process request and emit audit event."""
        # Skip excluded paths
        if request.url.path in self.config.excluded_paths:
            return await call_next(request)

        # Capture request start time
        start_time = datetime.now(UTC)

        # Track exception info for audit event: (error_type, message, status_code)
        exc_info: tuple[str, str, int] | None = None

        try:
            # Process request
            response: Response = await call_next(request)
        except Exception as exc:
            # Preserve real status code from HTTPException instead of assuming 500
            status_code = getattr(exc, "status_code", 500)
            exc_info = (
                exc.__class__.__name__,
                sanitize_error_message(str(exc)),
                status_code,
            )
            # Re-raise to let FastAPI handle the error response
            raise
        else:
            # Build and emit audit event for successful processing
            event = self._build_event(request, response, start_time, exc_info=None)
            self._safe_emit(event)
            return response
        finally:
            # If we caught an exception, emit the audit event before propagating
            if exc_info is not None:
                event = self._build_event(
                    request,
                    response=None,
                    timestamp=start_time,
                    exc_info=exc_info,
                )
                self._safe_emit(event)

    def _build_event(
        self,
        request: Request,
        response: Response | None,
        timestamp: datetime,
        exc_info: tuple[str, str, int] | None = None,
    ) -> dict[str, Any]:
        """
        Build an audit event from request/response data.

        Args:
            request: The incoming request.
            response: The response being sent (None if exception occurred).
            timestamp: When the request started.
            exc_info: Optional tuple of (error_type, sanitized_message, status_code).

        Returns:
            An audit event dictionary conforming to bh-audit-schema.
        """
        event: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "event_id": str(uuid.uuid4()),
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "service": self._build_service(),
            "actor": self._build_actor(request),
            "action": self._build_action(request),
            "resource": self._build_resource(request, response),
            "http": self._build_http(request, response, exc_info),
            "outcome": self._build_outcome(response, exc_info),
        }

        # Add correlation if available
        correlation = self._build_correlation(request)
        if correlation:
            event["correlation"] = correlation

        # Add metadata if configured and allowlisted
        metadata = self._build_metadata(request, response)
        if metadata:
            event["metadata"] = metadata

        return event

    def _build_service(self) -> dict[str, Any]:
        """Build the service block."""
        service: dict[str, Any] = {"name": self.config.service_name}

        if self.config.service_environment:
            service["environment"] = self.config.service_environment

        if self.config.service_version:
            service["version"] = self.config.service_version

        return service

    def _build_actor(self, request: Request) -> dict[str, Any]:
        """
        Build the actor block.

        Uses custom get_actor callback if provided, otherwise returns defaults.
        Callback failures are isolated — they never break request processing.
        """
        if self.config.get_actor:
            try:
                custom_actor = self.config.get_actor(request)
                if custom_actor:
                    return custom_actor
            except Exception as exc:
                self._stats.emit_failures_total += 1
                self._failure_log.warning(
                    "get_actor callback failed, falling back to defaults: %s", exc,
                )

        return {
            "subject_id": self.config.default_actor_id,
            "subject_type": self.config.default_actor_type,
        }

    def _build_action(self, request: Request) -> dict[str, Any]:
        """Build the action block from HTTP method."""
        action_type = HTTP_METHOD_TO_ACTION.get(request.method, "OTHER")

        return {
            "type": action_type,
            "data_classification": "UNKNOWN",
        }

    def _build_resource(self, request: Request, response: Response | None) -> dict[str, Any]:
        """
        Build the resource block.

        Uses custom get_resource callback if provided, otherwise defaults.
        Callback failures are isolated — they never break request processing.
        """
        if self.config.get_resource and response is not None:
            try:
                custom_resource = self.config.get_resource(request, response)
                if custom_resource:
                    return custom_resource
            except Exception as exc:
                self._stats.emit_failures_total += 1
                self._failure_log.warning(
                    "get_resource callback failed, falling back to defaults: %s", exc,
                )

        # Default: derive resource type from route or use "Unknown"
        route = getattr(request, "scope", {}).get("route")
        if route and hasattr(route, "name") and route.name:
            return {"type": route.name}

        return {"type": "Unknown"}

    def _build_http(
        self,
        request: Request,
        response: Response | None,
        exc_info: tuple[str, str, int] | None = None,
    ) -> dict[str, Any]:
        """
        Build the HTTP context block.

        PHI Safety: Only includes safe fields. Never includes:
        - Request/response bodies
        - Query string parameters
        - Authorization or Cookie headers
        - Raw URL path (uses route template instead)
        """
        if response is not None:
            status_code = response.status_code
        elif exc_info is not None:
            status_code = exc_info[2]
        else:
            status_code = 500

        http: dict[str, Any] = {
            "method": request.method,
            "status_code": status_code,
        }

        route = getattr(request, "scope", {}).get("route")
        http["route_template"] = route.path if (route and hasattr(route, "path")) else "unknown"

        if self.config.include_client_ip:
            client = request.client
            if client and client.host:
                http["client_ip"] = client.host

        user_agent = request.headers.get("user-agent")
        if user_agent:
            if len(user_agent) > _MAX_HEADER_VALUE_LENGTH:
                user_agent = user_agent[:_MAX_HEADER_VALUE_LENGTH] + "..."
            http["user_agent"] = user_agent

        return http

    def _build_outcome(
        self,
        response: Response | None,
        exc_info: tuple[str, str, int] | None = None,
    ) -> dict[str, Any]:
        """
        Build the outcome block based on response status code or exception.

        If an exception occurred, includes sanitized error information.
        """
        if exc_info is not None:
            error_type, error_message, _status_code = exc_info
            return {
                "status": "FAILURE",
                "error_type": error_type,
                "error_message": error_message,
            }

        if response is None:
            return {"status": "FAILURE"}

        status = "SUCCESS" if response.status_code < 400 else "FAILURE"
        return {"status": status}

    @staticmethod
    def _cap_header(value: str) -> str:
        """Truncate a header-sourced string to the hard cap."""
        if len(value) > _MAX_HEADER_VALUE_LENGTH:
            return value[:_MAX_HEADER_VALUE_LENGTH] + "..."
        return value

    def _build_correlation(self, request: Request) -> dict[str, Any] | None:
        """
        Build correlation block from safe request headers only.

        PHI Safety: Only extracts from allowlisted correlation headers.
        Never reads Authorization, Cookie, or other sensitive headers.
        All values are length-capped to prevent event inflation.
        """
        correlation: dict[str, Any] = {}

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

    def _build_metadata(self, request: Request, response: Response | None) -> dict[str, Any] | None:
        """
        Build metadata block with strict allowlist filtering.

        PHI Safety:
        - Metadata is opt-in (empty allowlist by default)
        - Only keys in allowlist are included
        - Non-scalar values (dict, list, tuple, etc.) are dropped
        - Long strings are truncated to max_metadata_value_length
        """
        if not self.config.get_metadata or not self.config.metadata_allowlist:
            return None

        if response is None:
            return None

        try:
            raw_metadata = self.config.get_metadata(request, response)
        except Exception as exc:
            self._stats.emit_failures_total += 1
            self._failure_log.warning(
                "get_metadata callback failed, skipping metadata: %s", exc,
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
