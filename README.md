# bh-fastapi-audit

A FastAPI middleware for emitting PHI-safe audit events for behavioral healthcare systems, designed for teams building modern healthcare APIs.

This project emits audit events conforming to the **bh-audit-schema** standard (currently v1.0):  
https://github.com/bh-healthcare/bh-audit-schema

## Why

Behavioral health systems handle highly sensitive regulated data. Audit logging is often implemented inconsistently across services, making access review and incident investigation unnecessarily difficult.

The goal of this library is to make consistent, structured audit trails easy to adopt in FastAPI services without logging raw PHI.

## Status

This project is an implementation layer that turns the bh-audit-schema standard into working FastAPI middleware.

**Current version: v0.1 (unreleased)** — Core audit middleware with PHI-safe defaults.

### v0.1 (current)
- FastAPI middleware that emits events conforming to bh-audit-schema v1.0
- PHI-safe defaults (no bodies, safe headers only, error sanitization)
- Captures: service, actor, action, resource, outcome, correlation
- In-memory sink for testing (`MemorySink`)
- Redaction utilities for error message sanitization

### Planned
- Pluggable sinks (JSONL file, SQL databases)
- Schema validation for emitted events

The bh-audit-schema v1.0 JSON schema is vendored into this package for offline validation.

## Quickstart

```python
from fastapi import FastAPI
from bh_fastapi_audit import AuditMiddleware, AuditConfig, MemorySink

app = FastAPI()

# For testing/development - use MemorySink
sink = MemorySink()
config = AuditConfig(
    service_name="example-bh-api",
    service_environment="dev",
)

app.add_middleware(AuditMiddleware, sink=sink, config=config)

@app.get("/patients/{patient_id}")
def get_patient(patient_id: str):
    return {"patient_id": patient_id}
```

Each request emits an audit event like:

```json
{
  "schema_version": "1.0",
  "event_id": "c1d2e3f4-1111-2222-3333-444455556666",
  "timestamp": "2026-01-14T22:00:00Z",
  "service": { "name": "example-bh-api", "environment": "dev" },
  "actor": { "subject_id": "unknown", "subject_type": "service" },
  "action": { "type": "READ", "data_classification": "UNKNOWN" },
  "resource": { "type": "get_patient" },
  "http": { "method": "GET", "route_template": "/patients/{patient_id}", "status_code": 200 },
  "outcome": { "status": "SUCCESS" }
}
```

## Configuration

`AuditConfig` supports:

| Option | Default | Description |
|--------|---------|-------------|
| `service_name` | (required) | Name of the service emitting events |
| `service_environment` | `"unknown"` | Environment (prod, staging, dev) |
| `service_version` | `None` | Service version string |
| `default_actor_id` | `"unknown"` | Default actor when no auth context |
| `default_actor_type` | `"service"` | Default actor type (`"human"` or `"service"`) |
| `get_actor` | `None` | Callback `(Request) -> dict` for custom actor extraction |
| `get_resource` | `None` | Callback `(Request, Response) -> dict` for custom resource extraction |
| `get_metadata` | `None` | Callback `(Request, Response) -> dict` for custom metadata |
| `metadata_allowlist` | `set()` | Set of allowed metadata keys (empty = no metadata) |
| `excluded_paths` | `{"/health", "/healthz", "/ready"}` | Paths to skip auditing |

## PHI-safe defaults

This library is designed to be safe by default:

- **No bodies**: Never reads or logs request/response bodies
- **Route templates**: Uses `/patients/{id}` not `/patients/12345`
- **Safe headers only**: Only extracts correlation headers (no Authorization, Cookie)
- **Error sanitization**: Exception messages are stripped of SSN/email/phone patterns and truncated

PHI safety is enforced by tests that assert synthetic PHI tokens never appear in emitted events.

### Error message sanitization

When exceptions occur, error messages are automatically sanitized:

```python
from bh_fastapi_audit import sanitize_error_message

# Patterns like SSNs, emails, phone numbers are redacted
sanitize_error_message("Patient SSN 123-45-6789 invalid")
# → "Patient SSN [REDACTED-SSN] invalid"

# Long messages are truncated (default 200 chars)
sanitize_error_message("x" * 500)
# → "xxxx...xxx..."
```

### Metadata allowlist

Metadata is opt-in and strictly filtered:

```python
config = AuditConfig(
    service_name="my-api",
    get_metadata=lambda req, res: {
        "content_length": req.headers.get("content-length"),
        "status_family": f"{res.status_code // 100}xx",
        "notes": "sensitive",
    },
    metadata_allowlist={"content_length", "status_family"},  # Only these keys appear
)
```

## Scope and non-goals

**In scope:**

- Structured audit events designed for compliance and operational monitoring
- Correlation support (request_id / trace_id) to connect events across services

**Out of scope:**

- Legal compliance guarantees
- Storing raw PHI or clinical content in logs
- Opinionated IAM or authentication frameworks

## Installation

```bash
# From source (development)
git clone https://github.com/bh-healthcare/bh-fastapi-audit
cd bh-fastapi-audit
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

PyPI publication planned for v0.2 when sinks are production-ready.

## License

Apache 2.0
