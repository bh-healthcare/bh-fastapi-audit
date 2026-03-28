"""
Tests for the async EmitQueue — the default production emission path.

Covers: enqueue, queue-full drop, shutdown drain, drain timeout,
sink failure during background drain, and processing order.

asyncio_mode = "auto" in pyproject.toml handles async test discovery.
"""

from __future__ import annotations

from typing import Any

from bh_fastapi_audit._queue import EmitQueue
from bh_fastapi_audit._stats import AuditStats
from bh_fastapi_audit.sinks.memory import MemorySink


def _make_event(event_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "event_id": event_id,
        "timestamp": "2026-03-28T12:00:00.000Z",
        "service": {"name": "queue-test"},
        "actor": {"subject_id": "u1", "subject_type": "service"},
        "action": {"type": "READ"},
        "resource": {"type": "Patient"},
        "outcome": {"status": "SUCCESS"},
    }


class TestEmitQueueBasic:
    """Basic enqueue / drain behaviour."""

    async def test_enqueued_events_reach_sink(self) -> None:
        sink = MemorySink()
        stats = AuditStats()
        q = EmitQueue(sink, stats, maxsize=100)

        q.enqueue(_make_event("aaa"))
        q.enqueue(_make_event("bbb"))
        await q.shutdown(timeout=2.0)

        assert len(sink) == 2
        assert stats.events_emitted_total == 2

    async def test_processing_preserves_order(self) -> None:
        sink = MemorySink()
        stats = AuditStats()
        q = EmitQueue(sink, stats, maxsize=100)

        ids = [f"id-{i:04d}" for i in range(20)]
        for eid in ids:
            q.enqueue(_make_event(eid))
        await q.shutdown(timeout=2.0)

        emitted_ids = [e["event_id"] for e in sink.events]
        assert emitted_ids == ids


class TestQueueFullDrop:
    """When the queue is full, events must be dropped with counter increment."""

    async def test_drop_when_full(self) -> None:
        sink = MemorySink()
        stats = AuditStats()
        q = EmitQueue(sink, stats, maxsize=2)

        q.start()
        q._queue.put_nowait(_make_event("e1"))
        q._queue.put_nowait(_make_event("e2"))

        ok = q.enqueue(_make_event("e3"))
        assert ok is False
        assert stats.events_dropped_total == 1

        await q.shutdown(timeout=2.0)

    async def test_enqueue_returns_true_when_space(self) -> None:
        sink = MemorySink()
        stats = AuditStats()
        q = EmitQueue(sink, stats, maxsize=100)

        ok = q.enqueue(_make_event())
        assert ok is True

        await q.shutdown(timeout=2.0)


class TestSinkFailureDuringDrain:
    """Sink exceptions in the background task must be isolated."""

    async def test_sink_failure_increments_counter(self) -> None:
        class _FailSink:
            def emit(self, event: dict[str, Any]) -> None:
                raise RuntimeError("disk full")

        stats = AuditStats()
        q = EmitQueue(_FailSink(), stats, maxsize=100, emit_failure_mode="log")

        q.enqueue(_make_event())
        await q.shutdown(timeout=2.0)

        assert stats.emit_failures_total == 1
        assert stats.events_emitted_total == 0

    async def test_sink_failure_does_not_stop_drain(self) -> None:
        """After a sink failure, subsequent events should still be attempted."""
        call_count = 0

        class _FailOnceSink:
            def emit(self, event: dict[str, Any]) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("transient failure")

        stats = AuditStats()
        q = EmitQueue(_FailOnceSink(), stats, maxsize=100, emit_failure_mode="log")

        q.enqueue(_make_event("e1"))
        q.enqueue(_make_event("e2"))
        await q.shutdown(timeout=2.0)

        assert stats.emit_failures_total == 1
        assert stats.events_emitted_total == 1


class TestShutdownDrain:
    """Graceful shutdown must drain pending events."""

    async def test_shutdown_drains_remaining(self) -> None:
        sink = MemorySink()
        stats = AuditStats()
        q = EmitQueue(sink, stats, maxsize=100)

        for i in range(10):
            q.enqueue(_make_event(f"id-{i}"))
        await q.shutdown(timeout=5.0)

        assert len(sink) == 10

    async def test_shutdown_idempotent(self) -> None:
        sink = MemorySink()
        stats = AuditStats()
        q = EmitQueue(sink, stats, maxsize=100)

        q.enqueue(_make_event())
        await q.shutdown(timeout=2.0)
        await q.shutdown(timeout=2.0)

        assert len(sink) == 1

    async def test_pending_property(self) -> None:
        sink = MemorySink()
        stats = AuditStats()
        q = EmitQueue(sink, stats, maxsize=100)

        assert q.pending == 0
        q._queue.put_nowait(_make_event())
        assert q.pending == 1
