"""
bh-fastapi-audit: PHI-safe audit logging middleware for FastAPI.

This package provides middleware for emitting structured audit events
conforming to the bh-audit-schema standard for behavioral healthcare systems.
"""

__version__ = "0.1.0"

from bh_fastapi_audit.middleware import AuditConfig, AuditMiddleware
from bh_fastapi_audit.redaction import (
    contains_phi_tokens,
    redact_tokens,
    sanitize_error_message,
)
from bh_fastapi_audit.sinks import AuditSink, MemorySink

__all__ = [
    "__version__",
    "AuditConfig",
    "AuditMiddleware",
    "AuditSink",
    "MemorySink",
    # Redaction utilities
    "contains_phi_tokens",
    "redact_tokens",
    "sanitize_error_message",
]
