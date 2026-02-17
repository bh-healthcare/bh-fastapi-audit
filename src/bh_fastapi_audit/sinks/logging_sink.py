"""
Logging sink for audit events.

Emits one compact JSON audit event per request using Python logging.
Works with any platform that captures application stdout, including
AWS CloudWatch, GCP Cloud Logging, Azure Monitor, and Kubernetes-based
logging pipelines.
"""

from __future__ import annotations

import json
import logging
from typing import Any


class LoggingSink:
    """
    Audit sink that emits events via Python logging.

    Each event is emitted as a single compact JSON line, suitable for
    log aggregation systems that capture stdout/stderr.

    Args:
        logger_name: Name for the logger (default "bh.audit").
        level: Log level as string or int (default "INFO").

    Example:
        sink = LoggingSink(logger_name="bh.audit", level="INFO")
        sink.emit(event)
    """

    def __init__(
        self,
        logger_name: str = "bh.audit",
        level: str | int = "INFO",
    ) -> None:
        self._logger = logging.getLogger(logger_name)
        self._level = self._resolve_level(level)

    @staticmethod
    def _resolve_level(level: str | int) -> int:
        """Convert level string to int if needed."""
        if isinstance(level, int):
            return level
        return getattr(logging, level.upper(), logging.INFO)

    def emit(self, event: dict[str, Any]) -> None:
        """
        Emit an audit event as a single JSON log line.

        Args:
            event: The audit event dictionary conforming to bh-audit-schema.
        """
        # Compact JSON: no extra whitespace, single line
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        self._logger.log(self._level, line, extra={"audit": True})

    @property
    def logger_name(self) -> str:
        """Return the logger name."""
        return self._logger.name

    @property
    def level(self) -> int:
        """Return the logging level."""
        return self._level
