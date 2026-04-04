"""
Audit event sinks for bh-fastapi-audit.

Sinks are responsible for persisting or forwarding audit events.
"""

from bh_fastapi_audit.sinks.base import AuditSink
from bh_fastapi_audit.sinks.jsonl import JsonlFileSink
from bh_fastapi_audit.sinks.ledger import LedgerSink
from bh_fastapi_audit.sinks.logging_sink import LoggingSink
from bh_fastapi_audit.sinks.memory import MemorySink

try:
    from bh_fastapi_audit.sinks.sqlalchemy import SQLAlchemySink
except ImportError:
    pass

try:
    from bh_fastapi_audit.sinks.dynamodb import DynamoDBSink
except ImportError:
    pass

_OPTIONAL_SINK_HINTS: dict[str, str] = {
    "DynamoDBSink": "pip install bh-fastapi-audit[dynamodb]",
    "SQLAlchemySink": "pip install bh-fastapi-audit[sqlalchemy]",
}


def __getattr__(name: str) -> object:
    if name in _OPTIONAL_SINK_HINTS:
        raise ImportError(
            f"{name} requires an optional dependency. Install with: {_OPTIONAL_SINK_HINTS[name]}"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AuditSink",
    "DynamoDBSink",
    "JsonlFileSink",
    "LedgerSink",
    "LoggingSink",
    "MemorySink",
    "SQLAlchemySink",
]
