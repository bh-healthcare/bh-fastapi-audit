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

A single JSON report per deployment, per week (Sunday-to-Sunday):

```json
{
  "schema_version": "1.0",
  "deployment_id": "a1b2c3d4-...",
  "service_name": "intake-api",
  "environment": "production",
  "package": "bh-fastapi-audit",
  "package_version": "0.5.0",
  "period_start": "2026-03-29T00:00:00+00:00",
  "period_end": "2026-04-05T00:00:00+00:00",
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
2. On each call, the current UTC time is compared to the period boundary.
3. When the period boundary is crossed, the report is POSTed via `urllib.request`
   with a 5-second timeout.
4. Counters are reset after successful emission.
5. **No background threads** -- this works in Lambda and single-process environments.

## Deployment ID

An anonymous UUID is generated on first run and persisted to
`<telemetry_deployment_id_path>/.bh-audit-deployment-id`. This allows correlating
weekly reports from the same deployment without identifying the operator.

## Failure behaviour

If the HTTP POST fails (network error, timeout, non-200 response), the failure is
silently swallowed. A `DEBUG`-level log message is emitted to the `bh.audit.telemetry`
logger. Counters are **not** reset on failure, so the data rolls into the next period.

Telemetry never raises exceptions and never affects application behaviour.

## Disabling telemetry

Set `telemetry_enabled=False` (the default) or simply omit the setting:

```python
config = AuditConfig(service_name="my-service")
# telemetry_enabled defaults to False -- nothing is sent
```
