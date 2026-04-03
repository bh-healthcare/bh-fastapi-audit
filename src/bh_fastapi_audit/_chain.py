"""
Pure functions for canonical event serialization and chain hash computation.

These are intentionally stateless — state management lives in ``_chain_state``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_SUPPORTED_ALGORITHMS = frozenset({"sha256", "sha384", "sha512"})


def canonical_serialize(event: dict[str, Any]) -> bytes:
    """Deterministic JSON serialization of an audit event.

    The ``integrity`` key is excluded before serialization to avoid a
    circular dependency (the integrity block contains the hash of the
    serialized event).

    Returns UTF-8 encoded bytes suitable for hashing.
    """
    hashable = {k: v for k, v in event.items() if k != "integrity"}
    return json.dumps(hashable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def compute_chain_hash(
    event: dict[str, Any],
    prev_hash: str | None = None,
    algorithm: str = "sha256",
) -> dict[str, str]:
    """Compute an integrity dict for an audit event.

    Returns a dict with keys ``event_hash``, ``hash_alg``, and
    optionally ``prev_event_hash`` (omitted for the first event in a
    chain).

    The hash input is ``prev_hash_bytes + canonical_bytes`` when a
    previous hash is provided, binding each event to its predecessor.
    """
    if algorithm not in _SUPPORTED_ALGORITHMS:
        raise ValueError(
            f"Unsupported hash algorithm {algorithm!r}; choose from {sorted(_SUPPORTED_ALGORITHMS)}"
        )

    canonical = canonical_serialize(event)

    h = hashlib.new(algorithm)
    if prev_hash is not None:
        h.update(prev_hash.encode("utf-8"))
    h.update(canonical)

    integrity: dict[str, str] = {
        "event_hash": h.hexdigest(),
        "hash_alg": algorithm,
    }
    if prev_hash is not None:
        integrity["prev_event_hash"] = prev_hash

    return integrity
