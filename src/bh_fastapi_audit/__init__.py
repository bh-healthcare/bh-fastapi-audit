"""
bh-fastapi-audit: PHI-safe audit logging middleware for FastAPI.

This package provides pure ASGI middleware for emitting structured audit events
conforming to the bh-audit-schema v1.1 standard for behavioral healthcare systems.
"""

__version__ = "0.3.0"

from bh_fastapi_audit._queue import EmitQueue
from bh_fastapi_audit._stats import AuditStats
from bh_fastapi_audit._types import (
    ActionBlock,
    ActionType,
    ActorBlock,
    ActorType,
    AuditEvent,
    CorrelationBlock,
    DataClassification,
    HttpBlock,
    OutcomeBlock,
    OutcomeStatus,
    ResourceBlock,
    ServiceBlock,
)
from bh_fastapi_audit.middleware import AuditConfig, AuditMiddleware
from bh_fastapi_audit.redaction import (
    contains_phi_tokens,
    redact_tokens,
    sanitize_error_message,
)
from bh_fastapi_audit.sinks import (
    AuditSink,
    JsonlFileSink,
    LoggingSink,
    MemorySink,
    SQLAlchemySink,
)

__all__ = [
    "__version__",
    "AuditConfig",
    "AuditMiddleware",
    "AuditStats",
    "EmitQueue",
    # Type definitions
    "ActionBlock",
    "ActionType",
    "ActorBlock",
    "ActorType",
    "AuditEvent",
    "CorrelationBlock",
    "DataClassification",
    "HttpBlock",
    "OutcomeBlock",
    "OutcomeStatus",
    "ResourceBlock",
    "ServiceBlock",
    # Sinks
    "AuditSink",
    "JsonlFileSink",
    "LoggingSink",
    "MemorySink",
    "SQLAlchemySink",
    # Redaction utilities
    "contains_phi_tokens",
    "redact_tokens",
    "sanitize_error_message",
]
