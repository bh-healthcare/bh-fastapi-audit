"""
Opt-in, privacy-first telemetry emitter.

Collects **counter-based aggregate statistics only** -- no PII, no PHI,
no event content, no IP addresses.  Designed for Lambda-friendly
environments (no background threads).

Off by default.  Enable via ``AuditConfig(telemetry_enabled=True)``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.request import Request, urlopen

_log = logging.getLogger("bh.audit.telemetry")

_TELEMETRY_SCHEMA_VERSION = "1.0"
_DEFAULT_PERIOD_DAYS = 7  # Sunday-to-Sunday windows
_HTTP_TIMEOUT_S = 5
_DEPLOYMENT_ID_FILE = ".bh-audit-deployment-id"


class TelemetryCounters:
    """Thread-safe aggregate counters -- no PII, no PHI."""

    __slots__ = (
        "_lock",
        "events_emitted",
        "by_action_type",
        "by_outcome",
        "by_data_classification",
        "integrity_events",
        "chain_gaps",
        "emit_failures",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events_emitted: int = 0
        self.by_action_type: dict[str, int] = {}
        self.by_outcome: dict[str, int] = {}
        self.by_data_classification: dict[str, int] = {}
        self.integrity_events: int = 0
        self.chain_gaps: int = 0
        self.emit_failures: int = 0

    def increment(self, event: dict[str, Any]) -> None:
        """Tally a single event (thread-safe)."""
        action_type = event.get("action", {}).get("type", "UNKNOWN")
        outcome = event.get("outcome", {}).get("status", "UNKNOWN")
        classification = event.get("action", {}).get("data_classification", "UNKNOWN")
        has_integrity = "integrity" in event

        with self._lock:
            self.events_emitted += 1
            self.by_action_type[action_type] = self.by_action_type.get(action_type, 0) + 1
            self.by_outcome[outcome] = self.by_outcome.get(outcome, 0) + 1
            self.by_data_classification[classification] = (
                self.by_data_classification.get(classification, 0) + 1
            )
            if has_integrity:
                self.integrity_events += 1

    def increment_failure(self) -> None:
        """Record a single emit failure (thread-safe)."""
        with self._lock:
            self.emit_failures += 1

    def increment_chain_gap(self) -> None:
        """Record a single chain gap (thread-safe)."""
        with self._lock:
            self.chain_gaps += 1

    def to_report(
        self,
        deployment_id: str,
        service_name: str,
        environment: str,
        package_version: str,
    ) -> dict[str, Any]:
        """Snapshot counters into a telemetry report payload."""
        with self._lock:
            return {
                "schema_version": _TELEMETRY_SCHEMA_VERSION,
                "deployment_id": deployment_id,
                "service_name": service_name,
                "environment": environment,
                "package": "bh-fastapi-audit",
                "package_version": package_version,
                "period_start": None,
                "period_end": None,
                "counters": {
                    "events_emitted": self.events_emitted,
                    "by_action_type": dict(self.by_action_type),
                    "by_outcome": dict(self.by_outcome),
                    "by_data_classification": dict(self.by_data_classification),
                    "integrity_events": self.integrity_events,
                    "chain_gaps": self.chain_gaps,
                    "emit_failures": self.emit_failures,
                },
            }

    def reset(self) -> None:
        """Zero all counters (called after successful emission)."""
        with self._lock:
            self.events_emitted = 0
            self.by_action_type.clear()
            self.by_outcome.clear()
            self.by_data_classification.clear()
            self.integrity_events = 0
            self.chain_gaps = 0
            self.emit_failures = 0


def _next_sunday(dt: datetime) -> datetime:
    """Return the start of the next Sunday (UTC midnight) after *dt*."""
    days_until_sunday = (6 - dt.weekday()) % 7
    target = dt.date() + timedelta(days=days_until_sunday or 7)
    return datetime(target.year, target.month, target.day, tzinfo=UTC)


class TelemetryEmitter:
    """Counter-based period-check emitter.

    On each ``record()`` call, counters are incremented and the period
    boundary is checked.  If the period has elapsed, the report is
    POSTed via urllib and counters are reset.  No background threads.
    """

    def __init__(
        self,
        endpoint: str,
        deployment_id_path: str,
        service_name: str,
        environment: str,
        package_version: str,
    ) -> None:
        self._endpoint = endpoint
        self._deployment_id_path = deployment_id_path
        self._service_name = service_name
        self._environment = environment
        self._package_version = package_version
        self._counters = TelemetryCounters()
        self._deployment_id: str | None = None
        now = datetime.now(UTC)
        self._period_start = now
        self._period_end = _next_sunday(now)

    @property
    def counters(self) -> TelemetryCounters:
        return self._counters

    def record(self, event: dict[str, Any]) -> None:
        """Increment counters and emit if period boundary crossed."""
        self._counters.increment(event)
        now = datetime.now(UTC)
        if now >= self._period_end:
            self._emit_report(now)

    def record_failure(self) -> None:
        """Record an emit failure."""
        self._counters.increment_failure()

    def record_chain_gap(self) -> None:
        """Record a chain gap."""
        self._counters.increment_chain_gap()

    def _emit_report(self, now: datetime) -> None:
        """POST the telemetry report. Failures are silently swallowed."""
        try:
            deployment_id = self._get_or_create_deployment_id()
            report = self._counters.to_report(
                deployment_id=deployment_id,
                service_name=self._service_name,
                environment=self._environment,
                package_version=self._package_version,
            )
            report["period_start"] = self._period_start.isoformat()
            report["period_end"] = self._period_end.isoformat()

            body = json.dumps(report).encode("utf-8")
            req = Request(
                self._endpoint,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urlopen(req, timeout=_HTTP_TIMEOUT_S)  # noqa: S310
            self._counters.reset()
            self._period_start = now
        except Exception:
            _log.debug("Telemetry emission failed (silently swallowed)", exc_info=True)
        finally:
            self._period_end = _next_sunday(now)

    def _get_or_create_deployment_id(self) -> str:
        """Read or create an anonymous deployment UUID."""
        if self._deployment_id is not None:
            return self._deployment_id

        id_dir = self._deployment_id_path
        id_file = os.path.join(id_dir, _DEPLOYMENT_ID_FILE)

        try:
            with open(id_file, encoding="utf-8") as fh:
                cached = fh.read().strip()
                if cached:
                    self._deployment_id = cached
                    return cached
        except FileNotFoundError:
            pass

        new_id = str(uuid.uuid4())
        try:
            os.makedirs(id_dir, exist_ok=True)
            with open(id_file, "w", encoding="utf-8") as fh:
                fh.write(new_id)
        except OSError:
            _log.debug("Could not persist deployment ID to %s", id_file)

        self._deployment_id = new_id
        return new_id
