# Telemetry

bh-fastapi-audit includes an **opt-in, privacy-first** telemetry system that reports
aggregate usage statistics to help the maintainers understand how the library is used
in the wild and prioritise improvements.

## Off by default

Telemetry is **disabled** unless you explicitly enable it:

```python
config = AuditConfig(
    service_name="intake-api",
    telemetry_enabled=True,               # opt-in
    telemetry_endpoint="https://abt0rxi196.execute-api.us-east-1.amazonaws.com/v1/report",
    telemetry_deployment_id_path="/tmp/bh-audit/",
)
```

## What is sent

A single JSON report per deployment, per flush interval:

```json
{
  "schema_version": "1.0",
  "deployment_id": "a1b2c3d4-...",
  "service_name": "intake-api",
  "environment": "production",
  "package": "bh-fastapi-audit",
  "package_version": "1.0.0",
  "period_start": "2026-04-13T00:00:00+00:00",
  "period_end": "2026-04-13T00:05:00+00:00",
  "counters": {
    "events_emitted": 12847,
    "by_action_type": {"READ": 8021, "CREATE": 3102, "UPDATE": 1724},
    "by_outcome": {"SUCCESS": 12500, "FAILURE": 347},
    "by_data_classification": {"PHI": 9000, "RESTRICTED": 3847},
    "integrity_events": 12847,
    "chain_gaps": 0,
    "emit_failures": 2
  }
}
```

## What is NOT sent

- No event content, payloads, or field values
- No patient identifiers, actor IDs, or session tokens
- No IP addresses, hostnames, or network information
- No request/response bodies or headers
- No file paths, database connection strings, or credentials
- No error messages or stack traces

## How it works

1. On each request, after the audit event is emitted, counters are incremented
   in-memory (thread-safe).
2. A flush triggers when **either** `telemetry_flush_interval_seconds` (default 300s)
   has elapsed **or** `telemetry_event_flush_threshold` (default 500) events have
   accumulated — whichever comes first.
3. Mid-request flushes use a **fire-and-forget daemon thread** so `record()` adds
   zero latency to the request path.  Only one flush runs at a time (protected by a
   lock; concurrent triggers are skipped).
4. Counters are checkpointed to disk every `max(50, threshold // 10)` events so data
   survives process restarts.
5. On success, counters are reset and the disk state is cleared. On failure, the
   snapshot is persisted to disk for recovery on the next cold start.
6. `AuditMiddleware.shutdown()` calls `flush()` (blocking, bounded by
   `telemetry_http_timeout_s`) to guarantee a delivery attempt before the process exits.

## Configuration fields

| Field | Type | Default | Description |
|---|---|---|---|
| `telemetry_enabled` | `bool` | `False` | Master switch — nothing is sent or written when `False` |
| `telemetry_endpoint` | `str` | `"https://…/v1/report"` | Telemetry receiver URL |
| `telemetry_deployment_id_path` | `str` | `"/tmp/bh-audit/"` | Directory for deployment ID and state files |
| `telemetry_flush_interval_seconds` | `float` | `300.0` | Flush after this many seconds elapsed |
| `telemetry_event_flush_threshold` | `int` | `500` | Also flush when this many events accumulate |
| `telemetry_log_level` | `int` | `logging.WARNING` | Log level for emission failures |
| `telemetry_http_timeout_s` | `float` | `1.5` | Max seconds for the HTTP POST |
| `telemetry_flush_stale_on_init` | `bool` | `True` | Flush stale disk state on cold start (see below) |

## Lambda / serverless configuration

The default settings work on Lambda and ephemeral infrastructure.  The dual-trigger
flush (time + event count) and disk-backed persistence solve the fundamental problem
of short-lived processes that never survive a weekly boundary.

### Recommended Lambda setup

```python
config = AuditConfig(
    service_name="intake-api",
    telemetry_enabled=True,
    # Defaults are Lambda-friendly:
    # telemetry_flush_interval_seconds=300.0,  # 5 minutes
    # telemetry_event_flush_threshold=500,
    # telemetry_http_timeout_s=1.5,
    # telemetry_flush_stale_on_init=True,
)
```

### Cold-start latency penalty

When `telemetry_flush_stale_on_init=True` (default), a cold start that finds a
stale state file from a previous container will perform a **blocking** HTTP POST
(bounded by `telemetry_http_timeout_s`, default 1.5s) in `__init__`.  This recovers
data from the previous container's counters.

If cold-start latency is more important than recovering stale telemetry, set
`telemetry_flush_stale_on_init=False` — the stale data will be discarded.

### Shutdown handler

Wire `AuditMiddleware.shutdown()` into your ASGI lifespan for a final flush:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await audit_middleware.shutdown()  # drains queue + flushes telemetry
```

## Disk state

Two files are written to `telemetry_deployment_id_path` (default `/tmp/bh-audit/`):

- `.bh-audit-deployment-id` — anonymous UUID, persisted across restarts
- `.bh-audit-telemetry-state.json` — counter checkpoint with `state_schema_version: 1`

Both are created only when `telemetry_enabled=True`.  When `telemetry_enabled=False`,
no files are read or written.

## Deployment ID

An anonymous UUID is generated on first run and persisted to
`<telemetry_deployment_id_path>/.bh-audit-deployment-id`. This allows correlating
reports from the same deployment without identifying the operator.

## Failure behaviour

If the HTTP POST fails (network error, timeout, non-200 response), the failure is
logged at `telemetry_log_level` (default `WARNING`).  The counter snapshot is
persisted to disk for recovery on the next cold start.

Telemetry never raises exceptions and never affects application behaviour.

## Disabling telemetry

Set `telemetry_enabled=False` (the default) or simply omit the setting:

```python
config = AuditConfig(service_name="my-service")
# telemetry_enabled defaults to False -- nothing is sent
```
