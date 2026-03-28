# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-03-28

### Added

- **Pure ASGI middleware** — complete rewrite from `BaseHTTPMiddleware` to a raw ASGI
  implementation. Enables streaming response support, avoids known Starlette issues,
  and reduces per-request overhead.
- **Non-blocking async emission** — new `EmitQueue` with configurable bounded
  `asyncio.Queue` (default 10 000 events). Events are enqueued without blocking the
  request path; a background task drains the queue and forwards to the sink via
  `run_in_executor()`. When the queue is full, events are dropped and
  `events_dropped_total` is incremented.
- **Typed event blocks** — `TypedDict` definitions for all event sub-blocks
  (`ServiceBlock`, `ActorBlock`, `ActionBlock`, `ResourceBlock`, `HttpBlock`,
  `OutcomeBlock`, `CorrelationBlock`, `IntegrityBlock`, `AuditEvent`) and `Literal`
  type aliases (`ActionType`, `ActorType`, `OutcomeStatus`, `DataClassification`,
  `HttpMethod`). Exported from the top-level package for static checking.
- **Frozen config** — `AuditConfig` is now `@dataclass(frozen=True)` to prevent
  runtime mutation of security settings. `metadata_allowlist` and `excluded_paths`
  use `frozenset` for immutability.
- **Schema validation CI test** — `test_schema_validation.py` validates emitted
  events against the vendored bh-audit-schema v1.1 JSON schema with
  `FormatChecker` enabled (uuid, date-time, ipv4/ipv6 formats enforced).
- **DENIED outcome for 401/403** — HTTP 401 Unauthorized and 403 Forbidden now
  emit `outcome.status: "DENIED"` with `error_type` as required by schema v1.1.
  Other 4xx/5xx remain FAILURE. Enables `WHERE outcome.status = 'DENIED'` queries
  for HIPAA access review.
- **v1.1 FAILURE compliance** — HTTP 4xx/5xx responses now include `error_type`
  and `error_message` in the outcome block as required by schema v1.1.
- **`get_action` callback** — new `AuditConfig.get_action` callback
  `(Request) -> dict | None` enables setting `phi_touched` and
  `data_classification` per-request for HIPAA PHI access monitoring.
- **EmitQueue test suite** — dedicated tests for queue-full drop, shutdown
  drain, sink failure isolation, processing order, and idempotent shutdown.
- `AuditConfig.emit_mode`: `"sync"` (direct call) or `"queue"` (default, non-blocking).
- `AuditConfig.queue_size`: maximum queue depth (default 10 000).
- `AuditConfig.queue_drain_timeout`: seconds to wait on shutdown (default 5.0).
- `AuditMiddleware.shutdown()` coroutine for graceful queue drain on app shutdown.
- `MemorySink` now accepts optional `maxlen` parameter to bound memory growth.
- `default_actor_type` now uses `Literal["human", "service"]` type.
- `get_resource` and `get_metadata` callbacks now receive `(Request, int)` instead
  of `(Request, Response)` — the `int` is the HTTP status code.
- Mutable `set` values for `metadata_allowlist` / `excluded_paths` are now
  automatically coerced to `frozenset` in `__post_init__`.

### Changed

- **Schema version bumped to 1.1** — vendored bh-audit-schema v1.1 with HIPAA/SOC
  compliance rule set, DENIED outcome status, conditional FAILURE validation,
  maxLength/minLength bounds on all string fields, and scalar-only metadata.
- `SCHEMA_VERSION` constant updated from `"1.0"` to `"1.1"`.
- `schema/__init__.py` now uses `@lru_cache` for `load_schema()`.
- `MemorySink` is now thread-safe with internal locking.
- `MemorySink.events` is now a property returning a snapshot (list copy).

### Fixed

- **BaseHTTPMiddleware removed** — eliminates streaming response breakage, buffering
  overhead, and the task-group bugs documented in Starlette issues.
- **Exception masking hardened** — the ASGI `finally` block always swallows audit
  exceptions to prevent `_build_event` or `_safe_emit` failures from crashing
  requests. `emit_failures_total` is incremented on any failure.
- **Header truncation schema-safe** — `_cap_header()` now produces output that
  fits within `maxLength: 256` (was 259 due to appending `"..."` after the cap).
- **SQLAlchemy column widths** — `trace_id` and `request_id` columns widened
  from `String(128)` to `String(256)` to match schema `maxLength: 256`.
- **Timestamp format** — uses `strftime` instead of fragile `.replace("+00:00", "Z")`.
- **Publish workflow** — tag-push builds now run the full test suite before
  publishing to PyPI.

### Compatibility

- Python 3.11+ unchanged
- **Breaking**: `get_resource` and `get_metadata` callback signatures changed from
  `(Request, Response)` to `(Request, int)` (status code).
- **Breaking**: `AuditConfig` is now frozen — attribute assignment after creation raises.
- **Breaking**: `metadata_allowlist` and `excluded_paths` are now `frozenset`.
- `MemorySink.events` is now a property (list copy) instead of a direct attribute.

## [0.2.2] - 2026-03-11

### Added

- **Sink failure isolation** — new `emit_failure_mode` config (`"silent"`, `"log"`, `"raise"`)
  controls what happens when a sink raises during emission. Default `"log"` ensures audit
  failures never break request handling while still surfacing diagnostics.
- **Internal counters** — thread-safe `AuditStats` with `events_emitted_total`,
  `emit_failures_total`, `callback_failures_total`, `events_dropped_total`,
  `validation_failures_total`. Access via `middleware.stats.snapshot()`.
- **Metadata safety enforcement** — metadata values are now restricted to scalar
  JSON types (str, int, float, bool, None); dict/list/tuple values are silently
  dropped. Long strings are truncated to `max_metadata_value_length` (default 200).
- **Client IP opt-in** — `include_client_ip` config (default `False`). Client IP
  is no longer included in events unless explicitly enabled.
- New config fields on `AuditConfig`:
  - `emit_failure_mode: Literal["silent", "log", "raise"]` (default `"log"`)
  - `failure_logger_name: str` (default `"bh.audit.internal"`)
  - `max_metadata_value_length: int` (default `200`)
  - `include_client_ip: bool` (default `False`)
- `AuditStats` exported from package top level
- `route_template` now defaults to `"unknown"` when no route is matched
- **Callback failure isolation** — `get_actor`, `get_resource`, and `get_metadata`
  callbacks are now wrapped in try/except. A failing callback falls back to safe
  defaults, increments the failure counter, and logs a compact warning — it never
  crashes the request.
- **Header value length caps** — correlation IDs (`x-request-id`, `x-trace-id`,
  `x-session-id`) and `user_agent` are now capped at 256 characters to prevent
  unbounded user-controlled input from inflating audit events.

### Changed

- `AuditMiddleware.dispatch()` now uses safe emission wrapper instead of calling
  `sink.emit()` directly
- HTTPException status codes are preserved in emitted events (previously all
  exception-path events hardcoded `status_code: 500`)
- Compact internal failure logs include only `event_id`, `service.name`,
  `action.type`, `resource.type` — never the full event payload

### Fixed

- **Exception masking in finally block** — if `_build_event` failed while
  handling an application exception, the original exception was silently replaced.
  The finally block is now wrapped in its own try/except.
- **SQL injection in `SQLAlchemySink.count()`** — table name was f-string
  interpolated into raw SQL. Now uses parameterized `select(func.count())`.
- **`IntegrityError` catch too broad** — previously swallowed all integrity
  violations; now only suppresses duplicate `event_id` conflicts.
- **`service_environment` always-truthy guard** — the `if` check was dead code
  since the default is `"unknown"`. Environment is now always included.
- Removed unused `_SAFE_LOGGED_HEADERS` constant.

### Compatibility

- Python 3.11+ unchanged
- No breaking changes to the public shape of emitted events
- Synchronous emission remains the default in v0.2.x

## [0.2.1] - 2026-02-17

### Added

- `LoggingSink` now attaches `extra={"audit": True}` to log records, enabling
  easy filtering of audit logs from application logs in aggregation systems
- Production container logging example in README (CloudWatch / GCP / K8s)
- Performance note in README documenting synchronous emission in v0.2.x

### Changed

- Explicit `__all__` public API surface confirmed and tested
- README version updated to v0.2.1

### Compatibility

- Python 3.11+ unchanged
- No breaking changes from 0.2.0

## [0.2.0] - 2026-01-21

### Added

- `LoggingSink` - Emits audit events via Python logging as compact JSON lines
  - Works with any platform that captures stdout: AWS CloudWatch, GCP Cloud Logging, Azure Monitor, Kubernetes
  - Configurable logger name and log level
  - No SDK dependencies, no retries, no buffering - simple and reliable
- PyPI distribution - `pip install bh-fastapi-audit` now works
- GitHub Actions workflow for automated PyPI publishing on tags

### Changed

- Package now available on PyPI for easy installation

## [0.1.0] - 2026-01-14

### Added

- `AuditMiddleware` - FastAPI middleware for automatic audit event emission
- `AuditConfig` - Configuration dataclass for middleware settings
- `AuditSink` - Protocol for pluggable audit event sinks
- Pluggable sinks:
  - `MemorySink` - In-memory sink for testing and development
  - `JsonlFileSink` - JSON Lines file sink for local dev and demos (thread-safe)
  - `SQLAlchemySink` - Relational database storage via SQLAlchemy Core (Postgres, SQLite, etc.)
- Correlation ID extraction from headers (X-Request-ID, X-Trace-ID, traceparent, X-Session-ID)
- Configurable excluded paths (defaults: /health, /healthz, /ready)
- Custom actor extraction via `get_actor` callback
- Custom resource extraction via `get_resource` callback
- Database indexing documentation for common compliance queries

### PHI Safety (Issue #2)

- **No bodies logged**: Request/response bodies are never read or logged
- **Route templates only**: Uses route templates, not raw paths with IDs
- **Safe headers only**: Only extracts allowlisted headers (no Authorization, Cookie)
- **Error sanitization**: `sanitize_error_message()` strips patterns (SSN, email, phone) and truncates
- **Metadata allowlist**: `metadata_allowlist` config ensures only safe keys are logged
- Redaction utilities: `sanitize_error_message()`, `contains_phi_tokens()`, `redact_tokens()` (for testing and internal use)
- PHI safety tests with synthetic tokens prove no leakage

### Schema Conformance

- Events conform to bh-audit-schema v1.0
- All required fields populated: schema_version, event_id, timestamp, service, actor, action, resource, outcome

## [0.0.1] - 2026-01-09

### Added

- Initial repository structure
- README with planned API documentation
- Apache 2.0 license
- Vendored bh-audit-schema v1.0 JSON schema

[Unreleased]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/bh-healthcare/bh-fastapi-audit/releases/tag/v0.0.1
