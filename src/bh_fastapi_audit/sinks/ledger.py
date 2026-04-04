"""
LedgerSink — JSONL file sink with built-in SHA-256 chain hashing.

A convenience sink for teams that want tamper-evident audit logs without
configuring middleware-level integrity or DynamoDB.  Each line includes an
``integrity`` block containing the event hash, algorithm, and a link to
the previous event's hash.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from bh_fastapi_audit._chain import compute_chain_hash
from bh_fastapi_audit._chain_state import ChainState
from bh_fastapi_audit.sinks.jsonl import JsonlFileSink

_log = logging.getLogger("bh.audit.chain")


class LedgerSink:
    """JSONL sink with built-in chain hashing.

    Each line includes an ``integrity`` block.  Suitable for local dev and
    small deployments that want tamper-evident audit logs without DynamoDB.

    Args:
        path: Path to the output JSONL file.
        flush: Whether to flush after each write.
        algorithm: Hash algorithm (``sha256``, ``sha384``, ``sha512``).
    """

    def __init__(
        self,
        path: str | Path,
        *,
        flush: bool = True,
        algorithm: str = "sha256",
    ) -> None:
        self._jsonl = JsonlFileSink(path, flush=flush)
        self._chain = ChainState()
        self._algorithm = algorithm
        self._lock = threading.Lock()

    def emit(self, event: dict[str, Any]) -> None:
        """Hash, chain, and write *event* as a single JSONL line."""
        if "integrity" in event:
            _log.warning(
                "LedgerSink: event already has integrity block (possible double-hashing); "
                "overwriting with LedgerSink's own chain hash"
            )
        with self._lock:
            integrity = compute_chain_hash(event, self._chain.last_hash, self._algorithm)
            event = {**event, "integrity": integrity}
            self._chain.advance(integrity["event_hash"])
            self._jsonl.emit(event)

    def close(self) -> None:
        """Close the underlying file."""
        self._jsonl.close()

    @property
    def path(self) -> Path:
        """Return the path to the output file."""
        return self._jsonl.path

    def __enter__(self) -> LedgerSink:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.close()
