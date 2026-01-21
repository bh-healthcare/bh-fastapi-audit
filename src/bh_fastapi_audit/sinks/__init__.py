"""
Audit event sinks for bh-fastapi-audit.

Sinks are responsible for persisting or forwarding audit events.
"""

from bh_fastapi_audit.sinks.base import AuditSink
from bh_fastapi_audit.sinks.jsonl import JsonlFileSink
from bh_fastapi_audit.sinks.logging_sink import LoggingSink
from bh_fastapi_audit.sinks.memory import MemorySink

# SQLAlchemy sink is optional - import will fail if sqlalchemy not installed
try:
    from bh_fastapi_audit.sinks.sqlalchemy import SQLAlchemySink
except ImportError:
    SQLAlchemySink = None  # type: ignore[misc, assignment]

__all__ = [
    "AuditSink",
    "JsonlFileSink",
    "LoggingSink",
    "MemorySink",
    "SQLAlchemySink",
]
