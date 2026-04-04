"""Tests for bh_fastapi_audit._telemetry."""

from __future__ import annotations

import json
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

from bh_fastapi_audit._telemetry import (
    TelemetryCounters,
    TelemetryEmitter,
    _next_sunday,
)


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


class TestTelemetryCounters:
    def test_increment_tallies_by_action_type(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event(action_type="READ"))
        c.increment(_make_event(action_type="READ"))
        c.increment(_make_event(action_type="CREATE"))
        assert c.by_action_type == {"READ": 2, "CREATE": 1}
        assert c.events_emitted == 3

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
        report = c.to_report("deploy-id", "svc", "staging", "0.5.0")

        assert report["schema_version"] == "1.0"
        assert report["deployment_id"] == "deploy-id"
        assert report["service_name"] == "svc"
        assert report["environment"] == "staging"
        assert report["package"] == "bh-fastapi-audit"
        assert report["package_version"] == "0.5.0"
        assert "counters" in report
        counters = report["counters"]
        assert counters["events_emitted"] == 1
        assert "by_action_type" in counters

    def test_to_report_no_pii(self) -> None:
        c = TelemetryCounters()
        c.increment(_make_event())
        report = c.to_report("id", "svc", "prod", "0.5.0")
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


class TestNextSunday:
    def test_monday_to_sunday(self) -> None:
        monday = datetime(2026, 3, 30, 10, 0, tzinfo=UTC)
        sunday = _next_sunday(monday)
        assert sunday.weekday() == 6
        assert sunday > monday

    def test_sunday_midnight_advances_week(self) -> None:
        sunday = datetime(2026, 4, 5, 0, 0, tzinfo=UTC)
        next_sun = _next_sunday(sunday)
        assert next_sun > sunday
        assert next_sun.weekday() == 6


class TestTelemetryEmitter:
    def test_record_does_not_emit_mid_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = TelemetryEmitter(
                endpoint="https://example.com/telemetry",
                deployment_id_path=tmpdir,
                service_name="test-svc",
                environment="test",
                package_version="0.5.0",
            )
            with patch("bh_fastapi_audit._telemetry.urlopen") as mock_urlopen:
                emitter.record(_make_event())
                emitter.record(_make_event())
                mock_urlopen.assert_not_called()

    def test_period_rollover_triggers_emission(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = TelemetryEmitter(
                endpoint="https://example.com/telemetry",
                deployment_id_path=tmpdir,
                service_name="test-svc",
                environment="test",
                package_version="0.5.0",
            )
            emitter._period_end = datetime.now(UTC) - timedelta(seconds=1)

            with patch("bh_fastapi_audit._telemetry.urlopen") as mock_urlopen:
                emitter.record(_make_event())
                mock_urlopen.assert_called_once()

    def test_emission_failure_silently_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = TelemetryEmitter(
                endpoint="https://example.com/telemetry",
                deployment_id_path=tmpdir,
                service_name="test-svc",
                environment="test",
                package_version="0.5.0",
            )
            emitter._period_end = datetime.now(UTC) - timedelta(seconds=1)

            with patch(
                "bh_fastapi_audit._telemetry.urlopen", side_effect=Exception("network error")
            ):
                emitter.record(_make_event())

    def test_deployment_id_created_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            emitter = TelemetryEmitter(
                endpoint="https://example.com/telemetry",
                deployment_id_path=tmpdir,
                service_name="test-svc",
                environment="test",
                package_version="0.5.0",
            )
            id1 = emitter._get_or_create_deployment_id()
            id2 = emitter._get_or_create_deployment_id()
            assert id1 == id2
            assert len(id1) == 36

            emitter2 = TelemetryEmitter(
                endpoint="https://example.com/telemetry",
                deployment_id_path=tmpdir,
                service_name="test-svc",
                environment="test",
                package_version="0.5.0",
            )
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
