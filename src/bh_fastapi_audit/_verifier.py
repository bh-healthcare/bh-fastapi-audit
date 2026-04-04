"""
Chain integrity verification for tamper-evident audit trails.

Provides a pure-function verifier that walks an ordered sequence of
audit events, recomputes each event's chain hash, and compares it to
the recorded integrity block.  The result is a structured report
suitable for compliance review or CI pipeline assertions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from bh_fastapi_audit._chain import compute_chain_hash


@dataclass
class VerifyFailure:
    """A single integrity violation found during chain verification."""

    event_index: int
    event_id: str
    timestamp: str
    failure_type: Literal["hash_mismatch", "chain_gap"]
    expected: str | None
    actual: str | None
    message: str


@dataclass
class VerifyResult:
    """Outcome of a chain verification run."""

    events_scanned: int
    time_range_start: str | None
    time_range_end: str | None
    chain_length: int
    chain_gaps: int
    hash_mismatches: int
    unchained_events: int
    result: Literal["PASS", "FAIL"]
    failures: list[VerifyFailure] = field(default_factory=list)


def verify_chain(
    events: Iterable[dict[str, Any]],
    algorithm: str = "sha256",
) -> VerifyResult:
    """Walk *events* in order, recompute hashes, and compare to stored integrity.

    Each event is expected to carry an ``integrity`` block with at least
    ``event_hash`` and ``hash_alg``.  Events without an ``integrity``
    block are counted as *unchained* but do not cause a FAIL on their own.

    Args:
        events: Ordered iterable of audit event dicts.
        algorithm: Default hash algorithm when ``integrity.hash_alg`` is
                   absent.  Normally read from each event's integrity block.

    Returns:
        A ``VerifyResult`` summarising the verification.
    """
    failures: list[VerifyFailure] = []
    prev_hash: str | None = None
    chain_length = 0
    chain_gaps = 0
    hash_mismatches = 0
    unchained_events = 0
    first_ts: str | None = None
    last_ts: str | None = None
    scanned = 0

    for idx, event in enumerate(events):
        scanned += 1
        ts = event.get("timestamp", "")
        eid = event.get("event_id", "unknown")

        if first_ts is None:
            first_ts = ts
        last_ts = ts

        integrity = event.get("integrity")
        if integrity is None:
            unchained_events += 1
            prev_hash = None
            continue

        chain_length += 1
        alg = integrity.get("hash_alg", algorithm)
        recorded_hash = integrity.get("event_hash", "")

        recomputed = compute_chain_hash(event, prev_hash, alg)
        recomputed_hash = recomputed["event_hash"]

        if recomputed_hash != recorded_hash:
            hash_mismatches += 1
            failures.append(
                VerifyFailure(
                    event_index=idx,
                    event_id=eid,
                    timestamp=ts,
                    failure_type="hash_mismatch",
                    expected=recomputed_hash,
                    actual=recorded_hash,
                    message=(
                        "Event content does not match its recorded hash. "
                        "Event may have been modified after emission."
                    ),
                )
            )

        recorded_prev = integrity.get("prev_event_hash")
        if chain_length > 1 and recorded_prev is None:
            chain_gaps += 1
            failures.append(
                VerifyFailure(
                    event_index=idx,
                    event_id=eid,
                    timestamp=ts,
                    failure_type="chain_gap",
                    expected=prev_hash,
                    actual=None,
                    message="Missing prev_event_hash mid-chain (possible unchained emission).",
                )
            )
        elif recorded_prev is not None and recorded_prev != prev_hash:
            chain_gaps += 1
            failures.append(
                VerifyFailure(
                    event_index=idx,
                    event_id=eid,
                    timestamp=ts,
                    failure_type="chain_gap",
                    expected=prev_hash,
                    actual=recorded_prev,
                    message=(
                        "prev_event_hash does not match the previous event's hash. "
                        "An event may have been deleted or reordered."
                    ),
                )
            )

        prev_hash = recorded_hash

    ok = hash_mismatches == 0 and chain_gaps == 0

    return VerifyResult(
        events_scanned=scanned,
        time_range_start=first_ts or None,
        time_range_end=last_ts or None,
        chain_length=chain_length,
        chain_gaps=chain_gaps,
        hash_mismatches=hash_mismatches,
        unchained_events=unchained_events,
        result="PASS" if ok else "FAIL",
        failures=failures,
    )
