"""
JSONL file sink for audit events.

Writes one event per line as compact JSON, suitable for local use, demos, and log aggregation.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, TextIO


class JsonlFileSink:
    """
    Audit sink that writes events to a JSONL (JSON Lines) file.

    Each event is written as a single line of compact JSON, followed by a newline.
    Thread-safe for concurrent writes from multiple threads.

    Args:
        path: Path to the output file. Parent directories will be created if missing.
        flush: Whether to flush after each write (default True for durability).

    Example:
        sink = JsonlFileSink("/var/log/audit/events.jsonl")
        sink.emit(event)
    """

    def __init__(self, path: str | Path, *, flush: bool = True) -> None:
        self._path = Path(path)
        self._flush = flush
        self._lock = threading.Lock()
        self._file: TextIO | None = None

        # Create parent directories if they don't exist
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_open(self) -> TextIO:
        """Ensure the file is open for writing."""
        if self._file is None or self._file.closed:
            self._file = open(self._path, mode="a", encoding="utf-8")
        return self._file

    def emit(self, event: dict[str, Any]) -> None:
        """
        Write an audit event to the JSONL file.

        Args:
            event: The audit event dictionary conforming to bh-audit-schema.
        """
        # Compact JSON: no extra whitespace
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False)

        with self._lock:
            f = self._ensure_open()
            f.write(line)
            f.write("\n")
            if self._flush:
                f.flush()

    def close(self) -> None:
        """Close the underlying file."""
        with self._lock:
            if self._file is not None and not self._file.closed:
                self._file.close()
                self._file = None

    @property
    def path(self) -> Path:
        """Return the path to the output file."""
        return self._path

    def __enter__(self) -> "JsonlFileSink":
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Context manager exit - close the file."""
        self.close()
