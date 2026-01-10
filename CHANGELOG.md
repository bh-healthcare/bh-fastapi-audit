# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- FastAPI middleware for automatic audit event emission
- JSONL file sink for audit event persistence
- SQL database sink support
- PHI-safe defaults (no request/response body logging)
- Route template extraction (not raw paths)
- Error message sanitization
- Correlation ID support (request_id, trace_id)
- Vendored bh-audit-schema v1.0 for offline validation

## [0.0.1] - 2026-01-09

### Added

- Initial repository structure
- README with planned API documentation
- Apache 2.0 license
- Vendored bh-audit-schema v1.0 JSON schema

[Unreleased]: https://github.com/bh-healthcare/bh-fastapi-audit/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/bh-healthcare/bh-fastapi-audit/releases/tag/v0.0.1

