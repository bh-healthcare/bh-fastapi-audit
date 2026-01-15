# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- Schema validation for emitted events
- Non-blocking / async sink variants (optional)

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

[Unreleased]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/bh-healthcare/bh-fastapi-audit/releases/tag/v0.0.1
