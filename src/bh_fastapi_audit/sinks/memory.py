"""
In-memory sink for testing and development.
"""

from typing import Any


class MemorySink:
    """
    In-memory audit sink that stores events in a list.

    Useful for testing and development. Not intended for production use.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        """
        Store an audit event in memory.

        Args:
            event: The audit event dictionary.
        """
        self.events.append(event)

    def clear(self) -> None:
        """Clear all stored events."""
        self.events.clear()

    def __len__(self) -> int:
        """Return the number of stored events."""
        return len(self.events)
