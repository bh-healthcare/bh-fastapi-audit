# Storage Patterns

Recommended storage layouts and query patterns for bh-fastapi-audit sinks.

## DynamoDB Table Design

### Primary table: `bh_audit_events`

| Attribute       | Type   | Role              |
|-----------------|--------|-------------------|
| `pk`            | String | Partition key     |
| `sk`            | String | Sort key          |
| `event_id`      | String | Unique event UUID |
| `event_json`    | String | Serialised event  |
| `actor_id`      | String | GSI partition key  |
| `ttl`           | Number | TTL epoch (optional) |

**PK/SK rationale**: `pk = SERVICE#<service_name>`, `sk = <ISO timestamp>#<event_id>`.
This gives per-service partitioning with time-ordered sort keys for efficient range queries.

### Global Secondary Indexes

| GSI Name         | PK           | SK              | Projection |
|------------------|--------------|-----------------|------------|
| `actor-index`    | `actor_id`   | `sk` (timestamp)| ALL        |

The actor GSI enables "show me all events for user X" queries required by
HIPAA accounting-of-disclosures.

### Billing mode

`PAY_PER_REQUEST` (on-demand) is recommended for audit workloads, which tend
to be spiky around business hours and near-zero overnight. Provisioned capacity
is harder to size correctly and risks throttling during peak audit volume.

### Capacity considerations

- Average event size: ~1-2 KB (JSON)
- Write throughput: one WCU per event (< 1 KB) or two WCUs (1-2 KB)
- Read throughput: `bh-audit verify` performs a full table scan; consider
  scheduling during off-peak hours

## JSONL File Layout (LedgerSink)

```
/var/log/audit/
├── events.jsonl          # Active ledger file
├── events.2026-03-29.jsonl  # Rotated daily
└── events.2026-03-22.jsonl
```

Each line is a complete JSON event. The `LedgerSink` automatically computes
chain hashes as events are appended. File rotation is left to the operator
(logrotate, cron, etc.).

### Verification

```bash
bh-audit verify --source file --path /var/log/audit/events.jsonl
```

For rotated files, concatenate in chronological order:

```bash
cat events.2026-03-22.jsonl events.2026-03-29.jsonl events.jsonl | \
    bh-audit verify --source file --path /dev/stdin
```

## S3 Archive Layout (Future)

For long-term immutable retention:

```
s3://bh-audit-archive/
├── service=intake-api/
│   ├── year=2026/
│   │   ├── month=03/
│   │   │   ├── day=29/
│   │   │   │   └── events-20260329T000000-20260329T235959.jsonl.gz
│   │   │   └── day=30/
│   │   │       └── events-20260330T000000-20260330T235959.jsonl.gz
```

### Recommended S3 settings

- **Object Lock**: Governance or Compliance mode with retention matching
  your regulatory requirement (HIPAA: 6 years, 42 CFR Part 2: 6 years)
- **Storage class**: S3 Glacier Instant Retrieval for archives > 90 days
- **Lifecycle rules**: Transition to Glacier after 90 days, expire after
  retention period

## Retention Automation

### DynamoDB TTL

Set a `ttl` attribute on each item (epoch seconds). DynamoDB automatically
deletes expired items within ~48 hours. Suitable for operational retention
(e.g. 90 days) when events are also archived to S3.

```python
import time
TTL_DAYS = 90
event_item["ttl"] = int(time.time()) + (TTL_DAYS * 86400)
```

### S3 Lifecycle

```json
{
  "Rules": [
    {
      "ID": "audit-archive-lifecycle",
      "Status": "Enabled",
      "Transitions": [
        {"Days": 90, "StorageClass": "GLACIER_IR"}
      ],
      "Expiration": {"Days": 2190}
    }
  ]
}
```

## Common Query Patterns

| Scenario                          | Source   | Query                                    |
|-----------------------------------|----------|------------------------------------------|
| All events for a patient          | DynamoDB | GSI `actor-index`, PK = patient_id       |
| Events in a time window           | DynamoDB | Query PK = service, SK between timestamps |
| Full chain verification           | File     | `bh-audit verify --source file --path …` |
| Full chain verification           | DynamoDB | `bh-audit verify --source dynamodb --table …` |
| Compliance audit (accounting of disclosures) | DynamoDB | GSI query + filter on action.type = READ |
