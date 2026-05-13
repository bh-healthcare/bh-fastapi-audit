# bh-fastapi-audit

[![PyPI Downloads](https://static.pepy.tech/personalized-badge/bh-fastapi-audit?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/bh-fastapi-audit)

Pure ASGI middleware for emitting PHI-safe audit events for behavioral healthcare systems, designed for teams building modern healthcare APIs.

This project emits audit events conforming to the **bh-audit-schema** standard (v1.1):  
https://github.com/bh-healthcare/bh-audit-schema

## Why

Behavioral health systems handle highly sensitive regulated data. Audit logging is often implemented inconsistently across services, making access review and incident investigation unnecessarily difficult.

The goal of this library is to make consistent, structured audit trails easy to adopt in FastAPI services without logging raw PHI.

## Status

This project is an implementation layer that turns the bh-audit-schema standard into working FastAPI middleware.

**Current version: v1.1.1** — Lambda-safe telemetry, Verifier CLI, chain hashing, DynamoDB sink, runtime validation, DENIED outcome with denial callbacks, schema negotiation. Vendored schema uses `$defs` for enum types.

### v1.0 (current)
- **Verifier CLI** — `bh-audit verify` for chain integrity verification (file or DynamoDB source, human or JSON output)
- **Programmatic verifier** — `verify_chain()`, `VerifyResult`, `VerifyFailure` for code-level chain verification
- **Opt-in telemetry** — privacy-first, counter-based weekly aggregate reports (no PII/PHI)
- **Chain hashing** — tamper-evident audit trails via SHA-256 chain hashing with `enable_integrity=True`
- **DynamoDB sink** — production-grade DynamoDB sink with 3 GSIs for HIPAA compliance queries
- **LedgerSink** — JSONL file sink with built-in chain hashing

### v0.4
- **Runtime event validation** — optional `validate_events=True` checks every event against the vendored JSON schema before emission, with configurable failure modes (`drop`, `log_and_emit`, `raise`)
- **DENIED outcome with denial callback** — `get_denial_reason` callback provides rich denial categories (e.g. `RoleDenied`, `ConsentRequired`, `CrossOrgAccessDenied`) for compliance queries
- **Configurable denied status codes** — `denied_status_codes` config (default `{401, 403}`)
- **Schema negotiation** — `target_schema_version` config (`"1.0"` or `"1.1"`) controls which schema version is emitted, enabling gradual migration
- **Vendored dual schemas** — both v1.0 and v1.1 schemas bundled for offline validation
- **Validation timing** — `validation_time_ms_total` counter for monitoring validation overhead

### v0.3
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

The bh-audit-schema v1.0 and v1.1 JSON schemas are vendored into this package to
enable offline validation. The vendored v1.1 schema currently tracks **bh-audit-schema
v1.1.2**, which defines `ActionType`, `OutcomeStatus`, and `DataClassification` as
named `$defs` referenced via `$ref` (accepted values unchanged).

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

## Runtime Event Validation

v0.4 adds optional runtime validation against the vendored JSON schema:

```python
config = AuditConfig(
    service_name="my-api",
    validate_events=True,                       # enable validation
    validation_failure_mode="log_and_emit",      # "drop" (default), "log_and_emit", or "raise"
)
```

- `"drop"` — increment counters and silently discard invalid events
- `"log_and_emit"` — log validation errors but still emit the event
- `"raise"` — raise `AuditValidationError` (use in dev/test)

Validation timing is tracked in `stats.snapshot()["validation_time_ms_total"]`.

## DENIED Outcome and Denial Callbacks

HTTP 401/403 produce `outcome.status: "DENIED"` (v1.1) with an `error_type`.
Provide a callback for richer denial categories:

```python
def denial_reason(request, exc_info):
    if exc_info and "consent" in exc_info[0].lower():
        return "ConsentRequired"
    return None  # fall back to default

config = AuditConfig(
    service_name="my-api",
    get_denial_reason=denial_reason,
    denied_status_codes=frozenset({401, 403, 451}),  # customize
)
```

## Schema Negotiation

Control which schema version emitted events conform to:

```python
config = AuditConfig(
    service_name="my-api",
    target_schema_version="1.0",  # or "1.1" (default)
)
```

With `"1.0"`, DENIED outcomes are downgraded to FAILURE for backward compatibility.
See [docs/migrating-1.0-to-1.1.md](docs/migrating-1.0-to-1.1.md) for a full migration guide.

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
| `get_action` | `None` | Callback `(Request) -> dict` for custom action extraction |
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
| `validate_events` | `False` | Enable runtime JSON-schema validation |
| `validation_failure_mode` | `"drop"` | `"drop"`, `"log_and_emit"`, or `"raise"` |
| `get_denial_reason` | `None` | Callback `(Request, exc_info) -> str\|None` for denial categorization |
| `denied_status_codes` | `frozenset({401, 403})` | Status codes that produce DENIED outcome |
| `target_schema_version` | `"1.1"` | Schema version for emitted events (`"1.0"` or `"1.1"`) |
| `enable_integrity` | `False` | Enable chain hashing on emitted events |
| `chain_state` | `None` | Chain state backend (`ChainState` or `DynamoDBChainState`) |
| `hash_algorithm` | `"sha256"` | Hash algorithm for chain hashing (`"sha256"`, `"sha384"`, `"sha512"`) |
| `telemetry_enabled` | `False` | Enable opt-in anonymous telemetry |
| `telemetry_endpoint` | `"https://…/v1/report"` | Telemetry receiver URL |
| `telemetry_deployment_id_path` | `"/tmp/bh-audit/"` | Directory for deployment ID and state files |
| `telemetry_flush_interval_seconds` | `300.0` | Flush after this many seconds elapsed |
| `telemetry_event_flush_threshold` | `500` | Also flush when this many events accumulate |
| `telemetry_log_level` | `logging.WARNING` | Log level for telemetry emission failures |
| `telemetry_http_timeout_s` | `1.5` | Max seconds for the telemetry HTTP POST |
| `telemetry_flush_stale_on_init` | `True` | Flush stale disk state on cold start |

## PHI-safe defaults

This library is designed to be safe by default:

- **No bodies**: Never reads or logs request/response bodies
- **Route templates**: Uses `/patients/{id}` not `/patients/12345`
- **Safe headers only**: Only extracts correlation headers (no Authorization, Cookie)
- **Error sanitization**: Exception messages are stripped of SSN/email/phone patterns and truncated

PHI safety is enforced by tests that assert synthetic PHI tokens never appear in emitted events.

## Chain hashing (integrity)

v1.0 adds tamper-evident audit trails via SHA-256 chain hashing:

```python
from bh_fastapi_audit import ChainState

config = AuditConfig(
    service_name="my-api",
    enable_integrity=True,
    chain_state=ChainState(),
    hash_algorithm="sha256",
)
```

For DynamoDB-backed multi-process chain state:

```python
from bh_fastapi_audit import DynamoDBChainState

chain_state = DynamoDBChainState(table_name="bh_chain_state", service_name="my-api")
config = AuditConfig(
    service_name="my-api",
    enable_integrity=True,
    chain_state=chain_state,
)
```

## Verifier CLI

v1.0 adds `bh-audit verify` for chain integrity verification:

```bash
pip install bh-fastapi-audit[cli]

# Verify a JSONL ledger file
bh-audit verify --source file --path /var/log/audit/events.jsonl

# Verify from DynamoDB
bh-audit verify --source dynamodb --table bh_audit_events --service my-api

# JSON output for CI pipelines
bh-audit verify --source file --path events.jsonl --format json
```

Programmatic verification:

```python
from bh_fastapi_audit import verify_chain

result = verify_chain(events)
assert result.result == "PASS"
```

## Telemetry

v1.0 adds opt-in, privacy-first telemetry. **Off by default.** No PII, no PHI, no event content -- only aggregate counters.

```python
config = AuditConfig(
    service_name="my-api",
    telemetry_enabled=True,  # explicit opt-in required
)
```

See [docs/telemetry.md](docs/telemetry.md) for the full privacy commitment and payload format.

## Installation

**Requires Python 3.11+**

```bash
pip install bh-fastapi-audit                # core (FastAPI + Pydantic)
pip install bh-fastapi-audit[dynamodb]      # + DynamoDB sink (boto3)
pip install bh-fastapi-audit[cli]           # + bh-audit verify CLI (typer)
pip install bh-fastapi-audit[sqlalchemy]    # + SQLAlchemy sink
pip install bh-fastapi-audit[jsonschema]    # + runtime schema validation
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
