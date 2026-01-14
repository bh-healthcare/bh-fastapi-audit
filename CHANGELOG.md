# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- JSONL file sink for audit event persistence
- SQL database sink support
- Schema validation for emitted events
- PHI redaction utilities

## [0.1.0] - 2026-01-14

### Added

- `AuditMiddleware` - FastAPI middleware for automatic audit event emission
- `AuditConfig` - Configuration dataclass for middleware settings
- `AuditSink` - Protocol for pluggable audit event sinks
- `MemorySink` - In-memory sink for testing and development
- PHI-safe defaults (no request/response body logging)
- Route template extraction (not raw paths)
- HTTP method to action type mapping (GET→READ, POST→CREATE, etc.)
- Correlation ID extraction from headers (X-Request-ID, X-Trace-ID, traceparent, X-Session-ID)
- Configurable excluded paths (defaults: /health, /healthz, /ready)
- Custom actor extraction via `get_actor` callback
- Custom resource extraction via `get_resource` callback
- 18 tests covering event emission and structure

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
