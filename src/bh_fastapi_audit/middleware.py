"""
FastAPI audit middleware for emitting structured audit events.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[type-arg]
        """Process request and emit audit event."""
        # Skip excluded paths
        if request.url.path in self.config.excluded_paths:
            return await call_next(request)

        # Capture request start time
        start_time = datetime.now(UTC)

        # Track exception info for audit event
        exc_info: tuple[str, str] | None = None

        try:
            # Process request
            response: Response = await call_next(request)
        except Exception as exc:
            # Capture exception details for audit (sanitized)
            exc_info = (
                exc.__class__.__name__,
                sanitize_error_message(str(exc)),
            )
            # Re-raise to let FastAPI handle the error response
            raise
        else:
            # Build and emit audit event for successful processing
            event = self._build_event(request, response, start_time, exc_info=None)
            self.sink.emit(event)
            return response
        finally:
            # If we caught an exception, emit the audit event before propagating
            # Note: We can't access response in exception case, so we use a 500 placeholder
            if exc_info is not None:
                # Create a minimal response stand-in for the audit event
                event = self._build_event(
                    request,
                    response=None,
                    timestamp=start_time,
                    exc_info=exc_info,
                )
                self.sink.emit(event)

    def _build_event(
        self,
        request: Request,
        response: Response | None,
        timestamp: datetime,
        exc_info: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Build an audit event from request/response data.

        Args:
            request: The incoming request.
            response: The response being sent (None if exception occurred).
            timestamp: When the request started.
            exc_info: Optional tuple of (error_type, sanitized_message) if exception.

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
            "http": self._build_http(request, response),
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
        """
        if self.config.get_actor:
            custom_actor = self.config.get_actor(request)
            if custom_actor:
                return custom_actor

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
        """
        if self.config.get_resource and response is not None:
            custom_resource = self.config.get_resource(request, response)
            if custom_resource:
                return custom_resource

        # Default: derive resource type from route or use "Unknown"
        route = getattr(request, "scope", {}).get("route")
        if route and hasattr(route, "name") and route.name:
            return {"type": route.name}

        return {"type": "Unknown"}

    def _build_http(self, request: Request, response: Response | None) -> dict[str, Any]:
        """
        Build the HTTP context block.

        PHI Safety: Only includes safe fields. Never includes:
        - Request/response bodies
        - Query string parameters
        - Authorization or Cookie headers
        - Raw URL path (uses route template instead)
        """
        http: dict[str, Any] = {
            "method": request.method,
            "status_code": response.status_code if response else 500,
        }

        # Get route template if available (NOT the raw path with IDs)
        route = getattr(request, "scope", {}).get("route")
        if route and hasattr(route, "path"):
            http["route_template"] = route.path

        # Add client IP if available
        client = request.client
        if client and client.host:
            http["client_ip"] = client.host

        # Only log safe headers
        user_agent = request.headers.get("user-agent")
        if user_agent:
            http["user_agent"] = user_agent

        return http

    def _build_outcome(
        self,
        response: Response | None,
        exc_info: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Build the outcome block based on response status code or exception.

        If an exception occurred, includes sanitized error information.
        """
        if exc_info is not None:
            error_type, error_message = exc_info
            return {
                "status": "FAILURE",
                "error_type": error_type,
                "error_message": error_message,
            }

        if response is None:
            return {"status": "FAILURE"}

        status = "SUCCESS" if response.status_code < 400 else "FAILURE"
        return {"status": status}

    def _build_correlation(self, request: Request) -> dict[str, Any] | None:
        """
        Build correlation block from safe request headers only.

        PHI Safety: Only extracts from allowlisted correlation headers.
        Never reads Authorization, Cookie, or other sensitive headers.
        """
        correlation: dict[str, Any] = {}

        # Request ID - only from safe headers
        request_id = request.headers.get("x-request-id")
        if request_id:
            correlation["request_id"] = request_id

        # Trace ID (OpenTelemetry traceparent or custom header)
        trace_id = request.headers.get("x-trace-id")
        if not trace_id:
            # Try to extract from traceparent header (format: version-trace_id-parent_id-flags)
            traceparent = request.headers.get("traceparent")
            if traceparent:
                parts = traceparent.split("-")
                if len(parts) >= 2:
                    trace_id = parts[1]
        if trace_id:
            correlation["trace_id"] = trace_id

        # Session ID
        session_id = request.headers.get("x-session-id")
        if session_id:
            correlation["session_id"] = session_id

        return correlation if correlation else None

    def _build_metadata(self, request: Request, response: Response | None) -> dict[str, Any] | None:
        """
        Build metadata block with strict allowlist filtering.

        PHI Safety:
        - Metadata is opt-in (empty allowlist by default)
        - Only keys in allowlist are included
        - All other keys are silently dropped
        """
        if not self.config.get_metadata or not self.config.metadata_allowlist:
            return None

        if response is None:
            return None

        raw_metadata = self.config.get_metadata(request, response)
        if not raw_metadata:
            return None

        # Filter to only allowlisted keys
        filtered = {
            key: value
            for key, value in raw_metadata.items()
            if key in self.config.metadata_allowlist
        }

        return filtered if filtered else None
