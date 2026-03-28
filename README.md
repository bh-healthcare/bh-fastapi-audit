# bh-fastapi-audit

Pure ASGI middleware for emitting PHI-safe audit events for behavioral healthcare systems, designed for teams building modern healthcare APIs.

This project emits audit events conforming to the **bh-audit-schema** standard (v1.1):  
https://github.com/bh-healthcare/bh-audit-schema

## Why

Behavioral health systems handle highly sensitive regulated data. Audit logging is often implemented inconsistently across services, making access review and incident investigation unnecessarily difficult.

The goal of this library is to make consistent, structured audit trails easy to adopt in FastAPI services without logging raw PHI.

## Status

This project is an implementation layer that turns the bh-audit-schema standard into working FastAPI middleware.

**Current version: v0.3.0** — Pure ASGI middleware, non-blocking async emission, typed event blocks, schema v1.1 with HIPAA/SOC compliance rules.

### v0.3 (current)
- **Pure ASGI middleware** — no BaseHTTPMiddleware, supports streaming responses
- **Non-blocking async emission** — bounded `asyncio.Queue` (default 10k events) with background drain task
- **Typed event blocks** — `TypedDict` definitions for all event sub-blocks (`AuditEvent`, `ActorBlock`, etc.)
- **Frozen config** — `AuditConfig` is immutable after creation
- **Schema v1.1** — vendored bh-audit-schema v1.1 with HIPAA/SOC compliance rules, DENIED status, conditional FAILURE validation
- **Schema validation in CI** — emitted events validated against the vendored JSON schema
- PyPI distribution — `pip install bh-fastapi-audit`
- PHI-safe defaults (no bodies, safe headers only, error sanitization)
- Captures: service, actor, action, resource, outcome, correlation, metadata
- Pluggable sinks:
  - `MemorySink` — in-memory for testing (bounded optional)
  - `JsonlFileSink` — JSON Lines file for local dev and demos
  - `LoggingSink` — Python logging for cloud platforms (CloudWatch, Cloud Logging, Azure Monitor, Kubernetes)
  - `SQLAlchemySink` — relational database storage (Postgres, SQLite, etc., via SQLAlchemy Core)
- Redaction utilities for error message sanitization

The bh-audit-schema v1.1 JSON schema is vendored into this package to enable offline validation.

## Quickstart

```python
from fastapi import FastAPI
from bh_fastapi_audit import AuditMiddleware, AuditConfig, MemorySink

app = FastAPI()

sink = MemorySink()
config = AuditConfig(
    service_name="example-bh-api",
    service_environment="dev",
    emit_mode="sync",  # use "queue" (default) for non-blocking in production
)

app.add_middleware(AuditMiddleware, sink=sink, config=config)

@app.get("/patients/{patient_id}")
def get_patient(patient_id: str):
    return {"patient_id": patient_id}
```

Each request emits an audit event like:

```json
{
  "schema_version": "1.1",
  "event_id": "c1d2e3f4-1111-2222-3333-444455556666",
  "timestamp": "2026-03-28T12:00:00.000Z",
  "service": { "name": "example-bh-api", "environment": "dev" },
  "actor": { "subject_id": "unknown", "subject_type": "service" },
  "action": { "type": "READ", "data_classification": "UNKNOWN" },
  "resource": { "type": "get_patient" },
  "http": { "method": "GET", "route_template": "/patients/{patient_id}", "status_code": 200 },
  "outcome": { "status": "SUCCESS" }
}
```

## Production Example: Container Logging (CloudWatch / GCP / K8s)

```python
from fastapi import FastAPI
from bh_fastapi_audit import AuditMiddleware, AuditConfig, LoggingSink

app = FastAPI()

app.add_middleware(
    AuditMiddleware,
    sink=LoggingSink(logger_name="audit"),
    config=AuditConfig(service_name="my-api", service_environment="prod"),
)
```

When deployed in containers, audit events are emitted as structured JSON logs to stdout and collected by your platform logging system (CloudWatch, Cloud Logging, Azure Monitor, Fluentd, etc.). No SDK dependencies required.

## Non-blocking async emission

v0.3 defaults to `emit_mode="queue"` — events are enqueued without blocking the request path, then emitted by a background task:

```python
config = AuditConfig(
    service_name="my-api",
    emit_mode="queue",       # default — non-blocking
    queue_size=10_000,       # default — bounded to prevent unbounded memory growth
    queue_drain_timeout=5.0, # seconds to wait on shutdown
)
```

When the queue is full, events are dropped and `events_dropped_total` is incremented. Call `await middleware.shutdown()` on app shutdown to drain remaining events.

Use `emit_mode="sync"` for testing or when you need deterministic ordering.

## Production hardening

### Sink failure isolation

By default, sink failures are logged but never break your request handling:

```python
config = AuditConfig(
    service_name="my-api",
    emit_failure_mode="log",       # "silent", "log" (default), or "raise"
    failure_logger_name="bh.audit.internal",
)
```

- `"silent"` — swallow errors, increment counter only
- `"log"` — log a compact summary (event_id, service, action, resource) without the full payload
- `"raise"` — re-raise the original exception (use in dev/test)

### Client IP opt-in

Client IP is excluded from audit events by default. Enable explicitly:

```python
config = AuditConfig(
    service_name="my-api",
    include_client_ip=True,   # default: False
)
```

### Metadata restrictions

Metadata values are enforced to be scalar JSON types (`str`, `int`, `float`, `bool`, `None`). Dict, list, and tuple values are silently dropped. Long strings are truncated:

```python
config = AuditConfig(
    service_name="my-api",
    metadata_allowlist=frozenset({"content_length", "status_family"}),
    max_metadata_value_length=200,
    get_metadata=lambda req, status: {"content_length": req.headers.get("content-length")},
)
```

### Internal counters

Track emission health via the middleware's stats:

```python
# After app startup, access via the middleware instance:
# middleware.stats.snapshot()
# {"events_emitted_total": 42, "emit_failures_total": 0, ...}
```

## Sinks

Sinks determine where audit events are stored. Choose based on your deployment:

### MemorySink (testing)

```python
from bh_fastapi_audit import MemorySink

sink = MemorySink()           # unbounded — for tests
sink = MemorySink(maxlen=100) # bounded — for dev
# After requests: sink.events contains all emitted events
```

### JsonlFileSink (local dev, demos)

Writes one JSON object per line. Thread-safe, flushes by default.

```python
from bh_fastapi_audit import JsonlFileSink

sink = JsonlFileSink("/var/log/audit/events.jsonl")
```

### LoggingSink (cloud deployments)

Emits one compact JSON audit event per request using Python logging.

```python
from bh_fastapi_audit import LoggingSink

sink = LoggingSink(logger_name="bh.audit", level="INFO")
```

No SDK dependencies, no retries, no buffering. The cloud platform handles collection.

### SQLAlchemySink (production database)

Stores events in a relational database with query-friendly columns plus full JSON.

```python
from bh_fastapi_audit import SQLAlchemySink

sink = SQLAlchemySink("postgresql://user:pass@localhost/mydb")
```

See [docs/indexing.md](docs/indexing.md) for recommended database indexes and query examples.

## Configuration

`AuditConfig` supports (frozen after creation):

| Option | Default | Description |
|--------|---------|-------------|
| `service_name` | (required) | Name of the service emitting events |
| `service_environment` | `"unknown"` | Environment (prod, staging, dev) |
| `service_version` | `None` | Service version string |
| `default_actor_id` | `"unknown"` | Default actor when no auth context |
| `default_actor_type` | `"service"` | Default actor type (`"human"` or `"service"`) |
| `get_actor` | `None` | Callback `(Request) -> dict` for custom actor extraction |
| `get_resource` | `None` | Callback `(Request, int) -> dict` for custom resource extraction |
| `get_metadata` | `None` | Callback `(Request, int) -> dict` for custom metadata |
| `metadata_allowlist` | `frozenset()` | Allowed metadata keys (empty = no metadata) |
| `excluded_paths` | `frozenset({"/health", ...})` | Paths to skip auditing |
| `emit_failure_mode` | `"log"` | How to handle sink failures |
| `failure_logger_name` | `"bh.audit.internal"` | Logger name for internal diagnostics |
| `max_metadata_value_length` | `200` | Max string length for metadata values |
| `include_client_ip` | `False` | Whether to include client IP |
| `emit_mode` | `"queue"` | `"sync"` or `"queue"` (non-blocking) |
| `queue_size` | `10_000` | Maximum pending events in queue |
| `queue_drain_timeout` | `5.0` | Seconds to wait for queue drain on shutdown |

## PHI-safe defaults

This library is designed to be safe by default:

- **No bodies**: Never reads or logs request/response bodies
- **Route templates**: Uses `/patients/{id}` not `/patients/12345`
- **Safe headers only**: Only extracts correlation headers (no Authorization, Cookie)
- **Error sanitization**: Exception messages are stripped of SSN/email/phone patterns and truncated

PHI safety is enforced by tests that assert synthetic PHI tokens never appear in emitted events.

## Installation

**Requires Python 3.11+**

```bash
pip install bh-fastapi-audit
```

### Optional dependencies

```bash
pip install bh-fastapi-audit[sqlalchemy]    # Database sink
pip install bh-fastapi-audit[jsonschema]    # Full JSON schema validation
```

### Development installation

```bash
git clone https://github.com/bh-healthcare/bh-fastapi-audit
cd bh-fastapi-audit
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,sqlalchemy,jsonschema]"
```

## License

Apache 2.0
