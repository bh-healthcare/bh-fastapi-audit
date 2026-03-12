"""
Internal counters for audit event emission diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class AuditStats:
    """
    Lightweight counters tracking audit emission health.

    All fields are monotonically increasing integers. Use ``snapshot()``
    to obtain a read-only dict copy suitable for health-check endpoints
    or operational dashboards.
    """

    events_emitted_total: int = 0
    emit_failures_total: int = 0
    events_dropped_total: int = 0
    validation_failures_total: int = 0

    def snapshot(self) -> dict[str, int]:
        """Return a plain-dict copy of the current counter values."""
        return {f.name: getattr(self, f.name) for f in fields(self)}
