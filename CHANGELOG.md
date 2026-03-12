# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- Schema validation for emitted events (optional, v0.4)
- Non-blocking / async sink variants (v0.3)

## [0.2.2] - 2026-03-11

### Added

- **Sink failure isolation** — new `emit_failure_mode` config (`"silent"`, `"log"`, `"raise"`)
  controls what happens when a sink raises during emission. Default `"log"` ensures audit
  failures never break request handling while still surfacing diagnostics.
- **Internal counters** — `AuditStats` dataclass with `events_emitted_total`,
  `emit_failures_total`, `events_dropped_total`, `validation_failures_total`.
  Access via `middleware.stats.snapshot()`.
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

[Unreleased]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/bh-healthcare/bh-fastapi-audit/releases/tag/v0.0.1
