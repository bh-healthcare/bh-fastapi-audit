"""
Audit event sinks for bh-fastapi-audit.

Sinks are responsible for persisting or forwarding audit events.
"""

from bh_fastapi_audit.sinks.base import AuditSink
from bh_fastapi_audit.sinks.memory import MemorySink

__all__ = [
    "AuditSink",
    "MemorySink",
]
