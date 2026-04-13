"""Tests for bh_fastapi_audit._telemetry."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from bh_fastapi_audit._telemetry import (
    _COUNTER_STATE_FILE,
    _STATE_SCHEMA_VERSION,
    TelemetryCounters,
    TelemetryEmitter,
)


def _mock_urlopen_ok():
    """Return a patch for urlopen that simulates HTTP 200."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return patch("bh_fastapi_audit._telemetry.urlopen", return_value=mock_resp)


def _make_event(
    action_type: str = "READ",
    outcome: str = "SUCCESS",
    classification: str = "PHI",
    with_integrity: bool = False,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "action": {"type": action_type, "data_classification": classification},
        "outcome": {"status": outcome},
    }
    if with_integrity:
        event["integrity"] = {"event_hash": "abc123", "hash_alg": "sha256"}
    return event


def _make_emitter(tmpdir: str, **overrides: Any) -> TelemetryEmitter:
    defaults = {
        "endpoint": "https://example.com/telemetry",
        "deployment_id_path": tmpdir,
        "service_name": "test-svc",
        "environment": "test",
        "package_version": "1.0.0",
    }
    defaults.update(overrides)
    return TelemetryEmitter(**defaults)


# ======================================================================
# TelemetryCounters
# ======================================================================


class TestTelemetryCounters:
    def test_increment_tallies_by_action_type(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event(action_type="READ"))
        c.increment(_make_event(action_type="READ"))
        c.increment(_make_event(action_type="CREATE"))
        assert c.by_action_type == {"READ": 2, "CREATE": 1}
        assert c.events_emitted == 3

    def test_increment_returns_post_count(self) -> None:
        c = TelemetryCounters()
        assert c.increment(_make_event()) == 1
        assert c.increment(_make_event()) == 2
        assert c.increment(_make_event()) == 3

    def test_increment_tallies_by_outcome(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event(outcome="SUCCESS"))
        c.increment(_make_event(outcome="FAILURE"))
        assert c.by_outcome == {"SUCCESS": 1, "FAILURE": 1}

    def test_increment_tallies_by_classification(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event(classification="PHI"))
        c.increment(_make_event(classification="RESTRICTED"))
        assert c.by_data_classification == {"PHI": 1, "RESTRICTED": 1}

    def test_integrity_events_counted(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event(with_integrity=True))
        c.increment(_make_event(with_integrity=False))
        assert c.integrity_events == 1

    def test_increment_failure(self) -> None:
        c = TelemetryCounters()
        c.increment_failure()
        c.increment_failure()
        assert c.emit_failures == 2

    def test_increment_chain_gap(self) -> None:
        c = TelemetryCounters()
        c.increment_chain_gap()
        assert c.chain_gaps == 1

    def test_reset_zeros_all(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event())
        c.increment_failure()
        c.increment_chain_gap()
        c.reset()
        assert c.events_emitted == 0
        assert c.by_action_type == {}
        assert c.emit_failures == 0
        assert c.chain_gaps == 0

    def test_to_report_structure(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event())
        report = c.to_report("deploy-id", "svc", "staging", "1.0.0")

        assert report["schema_version"] == "1.0"
        assert report["deployment_id"] == "deploy-id"
        assert report["service_name"] == "svc"
        assert report["environment"] == "staging"
        assert report["package"] == "bh-fastapi-audit"
        assert report["package_version"] == "1.0.0"
        assert "period_start" not in report
        assert "period_end" not in report
        assert "counters" in report
        counters = report["counters"]
        assert counters["events_emitted"] == 1
        assert "by_action_type" in counters

    def test_to_report_no_pii(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event())
        report = c.to_report("id", "svc", "prod", "1.0.0")
        serialized = json.dumps(report)
        for keyword in ["patient", "user-1", "ssn", "email", "phone"]:
            assert keyword not in serialized.lower()

    def test_thread_safety(self) -> None:
        c = TelemetryCounters()
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(100):
                    c.increment(_make_event())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert c.events_emitted == 1000

    def test_to_dict_round_trips(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event(action_type="READ"))
        c.increment(_make_event(action_type="CREATE", outcome="FAILURE"))
        c.increment_failure()
        c.increment_chain_gap()
        d = c.to_dict()

        c2 = TelemetryCounters.from_dict(d)
        assert c2.events_emitted == 2
        assert c2.by_action_type == {"READ": 1, "CREATE": 1}
        assert c2.by_outcome == {"SUCCESS": 1, "FAILURE": 1}
        assert c2.emit_failures == 1
        assert c2.chain_gaps == 1

    def test_merge_adds_values(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event(action_type="READ"))
        c.merge({"events_emitted": 5, "by_action_type": {"READ": 3, "CREATE": 2}})
        assert c.events_emitted == 6
        assert c.by_action_type == {"READ": 4, "CREATE": 2}


# ======================================================================
# TelemetryEmitter — basic
# ======================================================================


class TestTelemetryEmitter:
    def test_record_does_not_emit_mid_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            with _mock_urlopen_ok() as mock_urlopen:
                emitter.record(_make_event())
                emitter.record(_make_event())
                mock_urlopen.assert_not_called()

    def test_deployment_id_created_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            id1 = emitter._get_or_create_deployment_id()
            id2 = emitter._get_or_create_deployment_id()
            assert id1 == id2
            assert len(id1) == 36

            emitter2 = _make_emitter(tmpdir)
            assert emitter2._get_or_create_deployment_id() == id1

    def test_telemetry_disabled_no_emitter(self) -> None:
        from bh_fastapi_audit.middleware import AuditConfig, AuditMiddleware
        from bh_fastapi_audit.sinks.base import AuditSink

        class DummySink(AuditSink):
            def emit(self, event: dict[str, Any]) -> None:
                pass

        config = AuditConfig(service_name="test", telemetry_enabled=False)
        mw = AuditMiddleware(app=None, sink=DummySink(), config=config)
        assert mw._telemetry is None


# ======================================================================
# flush()
# ======================================================================


class TestFlush:
    def test_flush_sends_mid_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            emitter._counters.increment(_make_event())
            with _mock_urlopen_ok() as mock_urlopen:
                emitter.flush()
                mock_urlopen.assert_called_once()

    def test_flush_noop_on_empty_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            with _mock_urlopen_ok() as mock_urlopen:
                emitter.flush()
                mock_urlopen.assert_not_called()

    def test_emission_failure_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            emitter._counters.increment(_make_event())
            with patch(
                "bh_fastapi_audit._telemetry.urlopen",
                side_effect=OSError("network error"),
            ):
                emitter.flush()

    def test_flush_waits_for_inflight_async(self) -> None:
        """flush() acquires _flush_lock, so it waits for any in-flight async flush."""
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            emitter._counters.increment(_make_event())
            call_order: list[str] = []

            original_emit = emitter._emit_report

            def slow_emit(now: datetime) -> None:
                call_order.append("async_start")
                original_emit(now)
                call_order.append("async_end")

            emitter._emit_report = slow_emit  # type: ignore[assignment]

            with _mock_urlopen_ok():
                emitter._fire_async(datetime.now(UTC))
                emitter._flush_lock.acquire()
                emitter._flush_lock.release()

            assert "async_start" in call_order
            assert "async_end" in call_order


# ======================================================================
# Dual-trigger: event threshold and interval
# ======================================================================


class TestDualTrigger:
    def test_event_threshold_triggers_async_emit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir, event_flush_threshold=5)
            with patch("bh_fastapi_audit._telemetry.threading.Thread") as mock_thread_cls:
                mock_thread = MagicMock()
                mock_thread_cls.return_value = mock_thread
                for _ in range(4):
                    emitter._counters.increment(_make_event())
                emitter.record(_make_event())
                mock_thread_cls.assert_called_once()
                _, kwargs = mock_thread_cls.call_args
                assert kwargs["daemon"] is True
                mock_thread.start.assert_called_once()

    def test_interval_triggers_async_emit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir, flush_interval_seconds=1.0)
            emitter._period_end = datetime.now(UTC) - timedelta(seconds=1)
            with patch("bh_fastapi_audit._telemetry.threading.Thread") as mock_thread_cls:
                mock_thread = MagicMock()
                mock_thread_cls.return_value = mock_thread
                emitter.record(_make_event())
                mock_thread_cls.assert_called_once()
                _, kwargs = mock_thread_cls.call_args
                assert kwargs["daemon"] is True

    def test_flush_interval_respected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir, flush_interval_seconds=10.0)
            with patch("bh_fastapi_audit._telemetry.threading.Thread") as mock_thread_cls:
                emitter.record(_make_event())
                mock_thread_cls.assert_not_called()


# ======================================================================
# Flush lock
# ======================================================================


class TestFlushLock:
    def test_flush_lock_skip_when_already_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            emitter._flush_lock.acquire()
            try:
                with patch("bh_fastapi_audit._telemetry.threading.Thread") as mock_thread_cls:
                    emitter._fire_async(datetime.now(UTC))
                    mock_thread_cls.assert_not_called()
            finally:
                emitter._flush_lock.release()

    def test_concurrent_triggers_only_one_flush(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir, event_flush_threshold=10)
            call_count = 0
            original_emit = emitter._emit_report

            def counting_emit(now: datetime) -> None:
                nonlocal call_count
                call_count += 1
                original_emit(now)

            emitter._emit_report = counting_emit  # type: ignore[assignment]
            barrier = threading.Barrier(5)

            def fire() -> None:
                barrier.wait()
                emitter._fire_async(datetime.now(UTC))

            with _mock_urlopen_ok():
                threads = [threading.Thread(target=fire) for _ in range(5)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=5)

            emitter._flush_lock.acquire()
            emitter._flush_lock.release()

            assert call_count == 1


# ======================================================================
# Snapshot-first emit ordering
# ======================================================================


class TestSnapshotOrdering:
    def test_snapshot_taken_before_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            emitter._counters.increment(_make_event(action_type="READ"))
            emitter._counters.increment(_make_event(action_type="CREATE"))

            with patch(
                "bh_fastapi_audit._telemetry.urlopen",
                side_effect=OSError("fail"),
            ):
                emitter.flush()

            state_path = os.path.join(tmpdir, _COUNTER_STATE_FILE)
            assert os.path.exists(state_path)
            with open(state_path) as f:
                state = json.load(f)
            assert state["counters"]["events_emitted"] == 2
            assert state["counters"]["by_action_type"] == {"READ": 1, "CREATE": 1}


# ======================================================================
# Disk persistence
# ======================================================================


class TestDiskPersistence:
    def test_disk_state_saved_on_flush_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            emitter._counters.increment(_make_event())
            with patch(
                "bh_fastapi_audit._telemetry.urlopen",
                side_effect=OSError("fail"),
            ):
                emitter.flush()
            assert os.path.exists(os.path.join(tmpdir, _COUNTER_STATE_FILE))

    def test_disk_state_restored_on_reinit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            for _ in range(3):
                emitter._counters.increment(_make_event())
            emitter._save_state()

            emitter2 = _make_emitter(tmpdir)
            assert emitter2._counters.events_emitted == 3

    def test_disk_state_schema_version_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            emitter._counters.increment(_make_event())
            emitter._save_state()
            state_path = os.path.join(tmpdir, _COUNTER_STATE_FILE)
            with open(state_path) as f:
                state = json.load(f)
            assert state["state_schema_version"] == _STATE_SCHEMA_VERSION

    def test_checkpoint_cadence_scales_with_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            e1 = _make_emitter(tmpdir, event_flush_threshold=500)
            assert e1._checkpoint_every == 50

            e2 = _make_emitter(tmpdir, event_flush_threshold=50)
            assert e2._checkpoint_every == 50

            e3 = _make_emitter(tmpdir, event_flush_threshold=5000)
            assert e3._checkpoint_every == 500

    def test_checkpoint_written_periodically(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir, event_flush_threshold=10000)
            state_path = os.path.join(tmpdir, _COUNTER_STATE_FILE)

            with _mock_urlopen_ok():
                for _i in range(1, emitter._checkpoint_every + 1):
                    emitter.record(_make_event())

            assert os.path.exists(state_path)

    def test_clear_state_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir)
            emitter._counters.increment(_make_event())
            emitter._save_state()
            state_path = os.path.join(tmpdir, _COUNTER_STATE_FILE)
            assert os.path.exists(state_path)
            emitter._clear_state()
            assert not os.path.exists(state_path)

    def test_malformed_state_file_handled(self) -> None:
        """M4: valid JSON with wrong types should not crash __init__."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, _COUNTER_STATE_FILE)
            os.makedirs(tmpdir, exist_ok=True)
            with open(state_path, "w") as f:
                json.dump({"counters": "garbage", "state_schema_version": 1}, f)
            emitter = _make_emitter(tmpdir)
            assert emitter._counters.events_emitted == 0


# ======================================================================
# Stale-period recovery on init
# ======================================================================


class TestStalePeriodRecovery:
    def _write_stale_state(self, tmpdir: str, events: int = 10) -> None:
        state = {
            "state_schema_version": _STATE_SCHEMA_VERSION,
            "period_start": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            "period_end": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "counters": {
                "events_emitted": events,
                "by_action_type": {"READ": events},
                "by_outcome": {"SUCCESS": events},
                "by_data_classification": {"PHI": events},
                "integrity_events": 0,
                "chain_gaps": 0,
                "emit_failures": 0,
            },
        }
        os.makedirs(tmpdir, exist_ok=True)
        with open(os.path.join(tmpdir, _COUNTER_STATE_FILE), "w") as f:
            json.dump(state, f)

    def test_stale_period_flushed_on_init_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_stale_state(tmpdir)
            with _mock_urlopen_ok() as mock_urlopen:
                _make_emitter(tmpdir, flush_stale_on_init=True)
                mock_urlopen.assert_called_once()

    def test_stale_period_not_flushed_on_init_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_stale_state(tmpdir)
            with _mock_urlopen_ok() as mock_urlopen:
                emitter = _make_emitter(tmpdir, flush_stale_on_init=False)
                mock_urlopen.assert_not_called()
            assert emitter._counters.events_emitted == 0

    def test_telemetry_disabled_no_disk_read(self) -> None:
        from bh_fastapi_audit.middleware import AuditConfig, AuditMiddleware
        from bh_fastapi_audit.sinks.base import AuditSink

        class DummySink(AuditSink):
            def emit(self, event: dict[str, Any]) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_stale_state(tmpdir)
            config = AuditConfig(
                service_name="test",
                telemetry_enabled=False,
                telemetry_deployment_id_path=tmpdir,
            )
            mw = AuditMiddleware(app=None, sink=DummySink(), config=config)
            assert mw._telemetry is None


# ======================================================================
# Log level configuration
# ======================================================================


class TestLogLevel:
    def test_failure_logs_at_configured_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir, log_level=logging.ERROR)
            emitter._counters.increment(_make_event())
            with (
                patch(
                    "bh_fastapi_audit._telemetry.urlopen",
                    side_effect=OSError("fail"),
                ),
                patch("bh_fastapi_audit._telemetry._log") as mock_log,
            ):
                emitter.flush()
            mock_log.log.assert_called()
            assert mock_log.log.call_args_list[0][0][0] == logging.ERROR

    def test_failure_log_does_not_include_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir, log_level=logging.WARNING)
            emitter._counters.increment(_make_event())
            with (
                patch(
                    "bh_fastapi_audit._telemetry.urlopen",
                    side_effect=OSError("fail"),
                ),
                patch("bh_fastapi_audit._telemetry._log") as mock_log,
            ):
                emitter.flush()
            _, kwargs = mock_log.log.call_args
            assert kwargs.get("exc_info") is not True


# ======================================================================
# HTTP timeout configuration
# ======================================================================


class TestHttpTimeout:
    def test_http_timeout_passed_to_urlopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = _make_emitter(tmpdir, http_timeout_s=2.5)
            emitter._counters.increment(_make_event())
            with _mock_urlopen_ok() as mock_urlopen:
                emitter.flush()
                args, kwargs = mock_urlopen.call_args
                timeout = kwargs.get("timeout", args[1] if len(args) > 1 else None)
                assert timeout == 2.5


# ======================================================================
# Shutdown integration
# ======================================================================


class TestShutdownIntegration:
    def test_shutdown_calls_telemetry_flush_blocking(self) -> None:
        import asyncio

        from bh_fastapi_audit.middleware import AuditConfig, AuditMiddleware
        from bh_fastapi_audit.sinks.base import AuditSink

        class DummySink(AuditSink):
            def emit(self, event: dict[str, Any]) -> None:
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            config = AuditConfig(
                service_name="test",
                telemetry_enabled=True,
                telemetry_deployment_id_path=tmpdir,
                emit_mode="sync",
            )
            with _mock_urlopen_ok():
                mw = AuditMiddleware(app=None, sink=DummySink(), config=config)

            assert mw._telemetry is not None
            with patch.object(mw._telemetry, "flush") as mock_flush:
                asyncio.get_event_loop().run_until_complete(mw.shutdown())
                mock_flush.assert_called_once()
