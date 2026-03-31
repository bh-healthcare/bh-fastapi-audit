# Migrating from Schema v1.0 to v1.1

This guide covers the changes between bh-audit-schema v1.0 and v1.1 as they
affect consumers of `bh-fastapi-audit`.

## What Changed in Schema v1.1

| Area | v1.0 | v1.1 |
|------|------|------|
| `outcome.status` | `SUCCESS`, `FAILURE` | `SUCCESS`, `FAILURE`, **`DENIED`** |
| `outcome` on FAILURE | `error_type` optional | `error_type` **and** `error_message` required |
| `outcome` on DENIED | n/a | `error_type` required, `error_message` optional |
| `actor.owner_org_id` | not present | new optional field for cross-org access detection |
| String fields | no bounds | `minLength` / `maxLength` constraints on all strings |
| `metadata` values | any type | scalar-only (`string`, `integer`, `number`, `boolean`, `null`) |
| `event_id` | `minLength: 16` | `format: uuid`, fixed `minLength: 36`, `maxLength: 36` |
| `correlation` | any subset allowed | `minProperties: 1` when present |
| `http.method` | free string | `enum` restricted to standard HTTP methods |
| `http.status_code` | integer | `minimum: 100`, `maximum: 599` |
| `http.client_ip` | free string | `format: ipv4` or `format: ipv6` |

## Gradual Migration with `target_schema_version`

`bh-fastapi-audit` v0.4.0 vendors **both** schema versions and supports a
gradual migration via the `target_schema_version` config field.

### Step 1: Start with v1.0 compatibility

If your downstream consumers are not yet ready for v1.1 events (e.g. a SIEM
pipeline that rejects unknown `outcome.status` values), pin to v1.0:

```python
config = AuditConfig(
    service_name="my-api",
    target_schema_version="1.0",
)
```

With `target_schema_version="1.0"`:

- `schema_version` in emitted events is `"1.0"`
- HTTP 401/403 produce `FAILURE` (not `DENIED`) with `error_type` and `error_message`
- Events pass v1.0 schema validation

### Step 2: Switch to v1.1

When your downstream pipeline supports DENIED:

```python
config = AuditConfig(
    service_name="my-api",
    target_schema_version="1.1",   # default
)
```

With `target_schema_version="1.1"`:

- `schema_version` in emitted events is `"1.1"`
- HTTP 401/403 produce `DENIED` with `error_type`
- Events pass v1.1 schema validation (stricter bounds)

### Step 3: Enable runtime validation

For confidence during migration, turn on runtime validation to catch any
events that would violate the target schema:

```python
config = AuditConfig(
    service_name="my-api",
    target_schema_version="1.1",
    validate_events=True,
    validation_failure_mode="log_and_emit",  # log but still emit
)
```

Once stable, switch to `"drop"` (default) to silently discard bad events,
or disable validation entirely for production throughput.

## DENIED Outcome and Denial Reason Callback

v1.1 introduces `DENIED` to distinguish "the system correctly refused access"
from "something broke" (`FAILURE`).  By default, HTTP 401 and 403 produce
`DENIED` with a generic `error_type` like `"HTTP403"`.

To provide richer denial categories for compliance teams, configure a callback:

```python
from fastapi import Request

def denial_reason(request: Request, exc_info):
    if exc_info:
        error_type = exc_info[0]
        if "consent" in error_type.lower():
            return "ConsentRequired"
        if "role" in error_type.lower():
            return "RoleDenied"
    return None  # fall back to default

config = AuditConfig(
    service_name="my-api",
    get_denial_reason=denial_reason,
)
```

## Configurable Denied Status Codes

By default, only 401 and 403 produce DENIED.  To add custom status codes:

```python
config = AuditConfig(
    service_name="my-api",
    denied_status_codes=frozenset({401, 403, 451}),
)
```

## `owner_org_id` for Cross-Org Access Detection

v1.1 adds `actor.owner_org_id` to the actor block.  Use it in your `get_actor`
callback to flag cross-organization access:

```python
def get_actor(request: Request):
    return {
        "subject_id": request.state.user_id,
        "subject_type": "human",
        "org_id": request.state.user_org,
        "owner_org_id": request.state.resource_org,
    }
```

This enables queries like:

```sql
SELECT * FROM audit_events
WHERE actor->>'org_id' != actor->>'owner_org_id';
```
