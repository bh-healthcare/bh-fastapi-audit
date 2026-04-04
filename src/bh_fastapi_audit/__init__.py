"""
bh-fastapi-audit: PHI-safe audit logging middleware for FastAPI.

This package provides pure ASGI middleware for emitting structured audit events
conforming to the bh-audit-schema v1.1 standard for behavioral healthcare systems.
"""

__version__ = "0.5.0"

from bh_fastapi_audit._chain import canonical_serialize, compute_chain_hash
from bh_fastapi_audit._chain_state import ChainState
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
    EmitFailureMode,
    HashAlgorithm,
    HttpBlock,
    IntegrityBlock,
    OutcomeBlock,
    OutcomeStatus,
    ResourceBlock,
    ServiceBlock,
)
from bh_fastapi_audit._validation import AuditValidationError, validate_event
from bh_fastapi_audit._verifier import VerifyFailure, VerifyResult, verify_chain
from bh_fastapi_audit.middleware import AuditConfig, AuditMiddleware
from bh_fastapi_audit.redaction import (
    contains_phi_tokens,
    redact_tokens,
    sanitize_error_message,
)
from bh_fastapi_audit.sinks import (
    AuditSink,
    JsonlFileSink,
    LedgerSink,
    LoggingSink,
    MemorySink,
)

try:
    from bh_fastapi_audit._chain_state import DynamoDBChainState
except ImportError:
    pass

try:
    from bh_fastapi_audit.sinks.dynamodb import DynamoDBSink
except ImportError:
    pass

try:
    from bh_fastapi_audit.sinks.sqlalchemy import SQLAlchemySink
except ImportError:
    pass

_OPTIONAL_DEP_HINTS: dict[str, str] = {
    "DynamoDBSink": "pip install bh-fastapi-audit[dynamodb]",
    "DynamoDBChainState": "pip install bh-fastapi-audit[dynamodb]",
    "SQLAlchemySink": "pip install bh-fastapi-audit[sqlalchemy]",
}


def __getattr__(name: str) -> object:
    if name in _OPTIONAL_DEP_HINTS:
        raise ImportError(
            f"{name} requires an optional dependency. Install with: {_OPTIONAL_DEP_HINTS[name]}"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "__version__",
    "AuditConfig",
    "AuditMiddleware",
    "AuditStats",
    "AuditValidationError",
    "EmitQueue",
    # Chain hashing
    "canonical_serialize",
    "compute_chain_hash",
    "ChainState",
    "DynamoDBChainState",
    # Type definitions
    "ActionBlock",
    "ActionType",
    "ActorBlock",
    "ActorType",
    "AuditEvent",
    "CorrelationBlock",
    "DataClassification",
    "EmitFailureMode",
    "HashAlgorithm",
    "HttpBlock",
    "IntegrityBlock",
    "OutcomeBlock",
    "OutcomeStatus",
    "ResourceBlock",
    "ServiceBlock",
    # Sinks
    "AuditSink",
    "DynamoDBSink",
    "JsonlFileSink",
    "LedgerSink",
    "LoggingSink",
    "MemorySink",
    "SQLAlchemySink",
    # Verifier
    "VerifyFailure",
    "VerifyResult",
    "verify_chain",
    # Validation
    "validate_event",
    # Redaction utilities
    "contains_phi_tokens",
    "redact_tokens",
    "sanitize_error_message",
]
