"""
Internal counters for audit event emission diagnostics.
"""

from __future__ import annotations

import threading
from typing import Literal

_CounterName = Literal[
    "events_emitted_total",
    "emit_failures_total",
    "callback_failures_total",
    "events_dropped_total",
    "validation_failures_total",
]


class AuditStats:
    """
    Lightweight, thread-safe counters tracking audit emission health.

    All fields are monotonically increasing integers.  Use ``snapshot()``
    to obtain a read-only dict copy suitable for health-check endpoints
    or operational dashboards.
    """

    __slots__ = (
        "_lock",
        "events_emitted_total",
        "emit_failures_total",
        "callback_failures_total",
        "events_dropped_total",
        "validation_failures_total",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events_emitted_total: int = 0
        self.emit_failures_total: int = 0
        self.callback_failures_total: int = 0
        self.events_dropped_total: int = 0
        self.validation_failures_total: int = 0

    def increment(self, name: _CounterName) -> None:
        """Atomically increment a named counter by 1."""
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)

    def snapshot(self) -> dict[str, int]:
        """Return a plain-dict copy of the current counter values."""
        with self._lock:
            return {
                "events_emitted_total": self.events_emitted_total,
                "emit_failures_total": self.emit_failures_total,
                "callback_failures_total": self.callback_failures_total,
                "events_dropped_total": self.events_dropped_total,
                "validation_failures_total": self.validation_failures_total,
            }
