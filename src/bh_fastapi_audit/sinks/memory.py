"""
In-memory sink for testing and development.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class MemorySink:
    """In-memory audit sink that stores events in a bounded deque.

    Useful for testing and development. Not intended for production use.

    Args:
        maxlen: Maximum number of events to retain.  ``None`` (the default)
            means unlimited — suitable for tests but not long-running
            processes.  Set an explicit cap (e.g. 10 000) in production
            demos to prevent unbounded memory growth.
    """

    def __init__(self, *, maxlen: int | None = None) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    @property
    def events(self) -> list[dict[str, Any]]:
        """Return a snapshot list of stored events."""
        with self._lock:
            return list(self._events)

    def emit(self, event: dict[str, Any]) -> None:
        """Store an audit event in memory.

        Args:
            event: The audit event dictionary.
        """
        with self._lock:
            self._events.append(event)

    def clear(self) -> None:
        """Clear all stored events."""
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        """Return the number of stored events."""
        with self._lock:
            return len(self._events)
