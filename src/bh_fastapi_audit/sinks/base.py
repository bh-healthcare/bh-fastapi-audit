"""
Base sink interface for audit events.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AuditSink(Protocol):
    """
    Protocol for audit event sinks.

    Implementations are responsible for persisting or forwarding audit events.
    Examples: in-memory storage, file logging, message queue, database.
    """

    def emit(self, event: dict[str, Any]) -> None:
        """
        Emit an audit event to the sink.

        Args:
            event: The audit event as a dictionary conforming to bh-audit-schema.
        """
        ...
