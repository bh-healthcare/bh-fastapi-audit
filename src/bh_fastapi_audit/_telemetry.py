"""
Opt-in, privacy-first telemetry emitter.

Collects **counter-based aggregate statistics only** -- no PII, no PHI,
no event content, no IP addresses.  Lambda-safe: dual-trigger flush
(interval **or** event threshold), fire-and-forget daemon threads for
mid-request flushes, disk-backed persistence across cold starts, and
bounded-timeout blocking flush on shutdown.

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
from urllib.error import URLError
from urllib.request import Request, urlopen

_log = logging.getLogger("bh.audit.telemetry")

_TELEMETRY_SCHEMA_VERSION = "1.0"
_DEPLOYMENT_ID_FILE = ".bh-audit-deployment-id"
_COUNTER_STATE_FILE = ".bh-audit-telemetry-state.json"
_STATE_SCHEMA_VERSION = 1

_DEFAULT_FLUSH_INTERVAL_S = 300.0
_DEFAULT_EVENT_THRESHOLD = 500
_DEFAULT_HTTP_TIMEOUT_S = 1.5

_NETWORK_ERRORS = (OSError, URLError, TimeoutError, ValueError)


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

    def increment(self, event: dict[str, Any]) -> int:
        """Tally a single event (thread-safe).  Returns post-increment count."""
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
            return self.events_emitted

    def increment_failure(self) -> None:
        """Record a single emit failure (thread-safe)."""
        with self._lock:
            self.emit_failures += 1

    def increment_chain_gap(self) -> None:
        """Record a single chain gap (thread-safe)."""
        with self._lock:
            self.chain_gaps += 1

    def to_dict(self) -> dict[str, Any]:
        """Snapshot counter values to a plain dict (for disk persistence)."""
        with self._lock:
            return {
                "events_emitted": self.events_emitted,
                "by_action_type": dict(self.by_action_type),
                "by_outcome": dict(self.by_outcome),
                "by_data_classification": dict(self.by_data_classification),
                "integrity_events": self.integrity_events,
                "chain_gaps": self.chain_gaps,
                "emit_failures": self.emit_failures,
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryCounters:
        """Restore counters from a serialised dict."""
        c = cls()
        c.events_emitted = data.get("events_emitted", 0)
        c.by_action_type = dict(data.get("by_action_type", {}))
        c.by_outcome = dict(data.get("by_outcome", {}))
        c.by_data_classification = dict(data.get("by_data_classification", {}))
        c.integrity_events = data.get("integrity_events", 0)
        c.chain_gaps = data.get("chain_gaps", 0)
        c.emit_failures = data.get("emit_failures", 0)
        return c

    def merge(self, other_dict: dict[str, Any]) -> None:
        """Add values from *other_dict* into self (thread-safe)."""
        with self._lock:
            self.events_emitted += other_dict.get("events_emitted", 0)
            for k, v in other_dict.get("by_action_type", {}).items():
                self.by_action_type[k] = self.by_action_type.get(k, 0) + v
            for k, v in other_dict.get("by_outcome", {}).items():
                self.by_outcome[k] = self.by_outcome.get(k, 0) + v
            for k, v in other_dict.get("by_data_classification", {}).items():
                self.by_data_classification[k] = self.by_data_classification.get(k, 0) + v
            self.integrity_events += other_dict.get("integrity_events", 0)
            self.chain_gaps += other_dict.get("chain_gaps", 0)
            self.emit_failures += other_dict.get("emit_failures", 0)

    def to_report(
        self,
        deployment_id: str,
        service_name: str,
        environment: str,
        package_version: str,
    ) -> dict[str, Any]:
        """Snapshot counters into a telemetry report dict (for inspection/tests)."""
        with self._lock:
            return {
                "schema_version": _TELEMETRY_SCHEMA_VERSION,
                "deployment_id": deployment_id,
                "service_name": service_name,
                "environment": environment,
                "package": "bh-fastapi-audit",
                "package_version": package_version,
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


class TelemetryEmitter:
    """Lambda-safe telemetry emitter with dual-trigger flush.

    Flushes when *either* ``flush_interval_seconds`` has elapsed *or*
    ``event_flush_threshold`` events have accumulated — whichever comes
    first.

    Mid-request flushes use a fire-and-forget daemon thread so
    ``record()`` never blocks the caller.  ``flush()`` is synchronous
    (bounded by ``http_timeout_s``) and intended for shutdown handlers.
    Only one flush runs at a time via ``_flush_lock``.
    """

    def __init__(
        self,
        endpoint: str,
        deployment_id_path: str,
        service_name: str,
        environment: str,
        package_version: str,
        flush_interval_seconds: float = _DEFAULT_FLUSH_INTERVAL_S,
        event_flush_threshold: int = _DEFAULT_EVENT_THRESHOLD,
        log_level: int = logging.WARNING,
        http_timeout_s: float = _DEFAULT_HTTP_TIMEOUT_S,
        flush_stale_on_init: bool = True,
    ) -> None:
        self._endpoint = endpoint
        self._deployment_id_path = deployment_id_path
        self._service_name = service_name
        self._environment = environment
        self._package_version = package_version
        self._flush_interval_seconds = flush_interval_seconds
        self._event_flush_threshold = event_flush_threshold
        self._log_level = log_level
        self._http_timeout_s = http_timeout_s
        self._flush_stale_on_init = flush_stale_on_init

        self._counters = TelemetryCounters()
        self._deployment_id: str | None = None
        self._flush_lock = threading.Lock()
        self._checkpoint_every = max(50, event_flush_threshold // 10)

        now = datetime.now(UTC)
        self._period_start = now
        self._period_end = now + timedelta(seconds=flush_interval_seconds)

        self._load_state()

    @property
    def counters(self) -> TelemetryCounters:
        return self._counters

    def record(self, event: dict[str, Any]) -> None:
        """Increment counters; fire async flush if trigger condition met."""
        count = self._counters.increment(event)
        threshold_hit = count >= self._event_flush_threshold
        interval_elapsed = datetime.now(UTC) >= self._period_end
        if interval_elapsed or threshold_hit:
            self._fire_async(datetime.now(UTC))
        elif count % self._checkpoint_every == 0:
            self._save_state()

    def record_failure(self) -> None:
        """Record an emit failure."""
        self._counters.increment_failure()

    def record_chain_gap(self) -> None:
        """Record a chain gap."""
        self._counters.increment_chain_gap()

    def flush(self) -> None:
        """Blocking flush bounded by ``http_timeout_s``.

        Guarantees a delivery attempt.  Intended for shutdown handlers
        (``AuditMiddleware.shutdown()``) — **not** for mid-request use.
        """
        with self._flush_lock:
            if self._counters.events_emitted == 0:
                return
            self._emit_report(datetime.now(UTC))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fire_async(self, now: datetime) -> None:
        """Best-effort background flush.  One in-flight flush at a time."""
        if not self._flush_lock.acquire(blocking=False):
            return

        def _run() -> None:
            try:
                self._emit_report(now)
            finally:
                self._flush_lock.release()

        threading.Thread(target=_run, daemon=True).start()

    def _emit_report(self, now: datetime) -> None:
        """POST a telemetry report.  Snapshot-first so failures preserve data."""
        snapshot = self._counters.to_dict()
        try:
            deployment_id = self._get_or_create_deployment_id()
            report = {
                "schema": "bh-telemetry-v1",
                "schema_version": _TELEMETRY_SCHEMA_VERSION,
                "deployment_id": deployment_id,
                "service_name": self._service_name,
                "environment": self._environment,
                "package": "bh-fastapi-audit",
                "package_version": self._package_version,
                "period": {
                    "start": self._period_start.isoformat(),
                    "end": now.isoformat(),
                },
                "counters": snapshot,
            }

            body = json.dumps(report).encode("utf-8")
            req = Request(
                self._endpoint,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=self._http_timeout_s) as resp:  # noqa: S310
                if resp.status < 200 or resp.status >= 300:
                    raise OSError(f"Telemetry endpoint returned HTTP {resp.status}")
            self._counters.reset()
            self._clear_state()
            self._period_start = now
        except _NETWORK_ERRORS as exc:
            self._save_state(snapshot)
            _log.log(
                self._log_level,
                "Telemetry emission failed: %s",
                type(exc).__name__,
            )
        finally:
            self._period_end = now + timedelta(seconds=self._flush_interval_seconds)

    # ------------------------------------------------------------------
    # Disk persistence
    # ------------------------------------------------------------------

    def _state_file_path(self) -> str:
        return os.path.join(self._deployment_id_path, _COUNTER_STATE_FILE)

    def _save_state(self, snapshot: dict[str, Any] | None = None) -> None:
        """Persist counters to disk so the next cold start can recover."""
        with self._flush_lock if snapshot is None else _nullcontext():
            if snapshot is None:
                snapshot = self._counters.to_dict()
            period_start = self._period_start.isoformat()
            period_end = self._period_end.isoformat()
        state = {
            "state_schema_version": _STATE_SCHEMA_VERSION,
            "period_start": period_start,
            "period_end": period_end,
            "counters": snapshot,
        }
        try:
            os.makedirs(self._deployment_id_path, exist_ok=True)
            with open(self._state_file_path(), "w", encoding="utf-8") as fh:
                json.dump(state, fh)
        except OSError:
            _log.log(self._log_level, "Could not save telemetry state to disk")

    def _load_state(self) -> None:
        """Restore counters from disk on cold start; flush stale data if configured."""
        try:
            with open(self._state_file_path(), encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, json.JSONDecodeError, ValueError):
            return

        if not isinstance(state, dict):
            self._clear_state()
            return

        counters_dict = state.get("counters")
        if not isinstance(counters_dict, dict) or counters_dict.get("events_emitted", 0) == 0:
            self._clear_state()
            return

        period_end_str = state.get("period_end")
        period_start_str = state.get("period_start")

        if period_start_str:
            try:
                self._period_start = datetime.fromisoformat(period_start_str)
            except (ValueError, TypeError):
                pass

        stale = False
        if period_end_str:
            try:
                stored_end = datetime.fromisoformat(period_end_str)
                if datetime.now(UTC) >= stored_end:
                    stale = True
            except (ValueError, TypeError):
                pass

        if stale and self._flush_stale_on_init:
            with self._flush_lock:
                try:
                    self._counters.merge(counters_dict)
                except Exception:
                    self._clear_state()
                    return
                if self._counters.events_emitted > 0:
                    self._emit_report(datetime.now(UTC))
        elif stale:
            self._clear_state()
        else:
            try:
                self._counters.merge(counters_dict)
            except Exception:
                self._clear_state()

    def _clear_state(self) -> None:
        """Remove the disk state file."""
        try:
            os.remove(self._state_file_path())
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Deployment ID
    # ------------------------------------------------------------------

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


class _nullcontext:
    """Minimal no-op context manager (avoid importing contextlib)."""

    def __enter__(self) -> None:
        pass

    def __exit__(self, *_: object) -> None:
        pass
