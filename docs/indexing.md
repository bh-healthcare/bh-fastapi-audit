# Database Indexing Recommendations

This document provides recommended database indexes for the `bh_audit_events` table when using the SQLAlchemy sink with PostgreSQL or other relational databases.

## Table Schema

The `SQLAlchemySink` creates a table with the following columns:

| Column | Type | Purpose |
|--------|------|---------|
| `event_id` | TEXT | Primary key |
| `timestamp` | TIMESTAMP WITH TIME ZONE | When the event occurred |
| `service_name` | TEXT | Service that emitted the event |
| `environment` | TEXT | prod/staging/dev |
| `actor_subject_id` | TEXT | Who performed the action |
| `actor_subject_type` | TEXT | human/service |
| `action_type` | TEXT | READ/CREATE/UPDATE/DELETE/etc |
| `resource_type` | TEXT | Type of resource accessed |
| `resource_id` | TEXT | Resource identifier |
| `patient_id` | TEXT | Patient identifier (nullable) |
| `outcome_status` | TEXT | SUCCESS/FAILURE |
| `http_status_code` | INTEGER | HTTP response code |
| `trace_id` | TEXT | Distributed trace ID |
| `request_id` | TEXT | Request correlation ID |
| `event_json` | TEXT/JSONB | Complete event payload |

## Recommended Indexes

### Essential Indexes (Create These)

```sql
-- Timestamp index: Required for time-range queries and retention policies
CREATE INDEX idx_audit_timestamp ON bh_audit_events (timestamp);

-- Patient access audit: HIPAA compliance queries
-- Partial index excludes null patient_ids for efficiency
CREATE INDEX idx_audit_patient_id ON bh_audit_events (patient_id)
  WHERE patient_id IS NOT NULL;

-- User activity audit: "What did user X access?"
CREATE INDEX idx_audit_actor_subject_id ON bh_audit_events (actor_subject_id);

-- Action type filtering: Find all DELETE or EXPORT events
CREATE INDEX idx_audit_action_type ON bh_audit_events (action_type);

-- Outcome filtering: Find all failures
CREATE INDEX idx_audit_outcome_status ON bh_audit_events (outcome_status);
```

### Optional Indexes (Add Based on Query Patterns)

```sql
-- Trace correlation: Link events across services
CREATE INDEX idx_audit_trace_id ON bh_audit_events (trace_id)
  WHERE trace_id IS NOT NULL;

-- Service filtering: Filter events by originating service
CREATE INDEX idx_audit_service_name ON bh_audit_events (service_name);

-- Environment filtering: Useful if multiple environments share one database
CREATE INDEX idx_audit_environment ON bh_audit_events (environment);

-- Resource type filtering: Find all Patient record accesses
CREATE INDEX idx_audit_resource_type ON bh_audit_events (resource_type);
```

### Composite Indexes for Common Queries

```sql
-- Patient access by time range (common compliance query)
CREATE INDEX idx_audit_patient_time ON bh_audit_events (patient_id, timestamp)
  WHERE patient_id IS NOT NULL;

-- User activity by time range
CREATE INDEX idx_audit_actor_time ON bh_audit_events (actor_subject_id, timestamp);

-- Failed accesses (security monitoring)
CREATE INDEX idx_audit_failures ON bh_audit_events (timestamp, action_type)
  WHERE outcome_status = 'FAILURE';
```

## PostgreSQL-Specific Recommendations

### JSONB Instead of TEXT

For PostgreSQL, consider storing `event_json` as JSONB for queryable JSON:

```sql
ALTER TABLE bh_audit_events 
  ALTER COLUMN event_json TYPE JSONB USING event_json::JSONB;
```

This enables queries like:

```sql
SELECT * FROM bh_audit_events 
WHERE event_json->'action'->>'phi_touched' = 'true';
```

### Partitioning (High Volume)

For high-volume deployments (>1M events/month), partition by timestamp:

```sql
CREATE TABLE bh_audit_events (
  -- ... columns ...
) PARTITION BY RANGE (timestamp);

-- Create monthly partitions
CREATE TABLE bh_audit_events_2026_01 
  PARTITION OF bh_audit_events 
  FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
```

Benefits:
- Faster queries on recent data
- Efficient retention (drop old partitions)
- Smaller indexes per partition

## Retention Policies

Audit logs often have retention requirements:

- **HIPAA**: Minimum 6 years
- **SOC 2**: Typically 1 year minimum
- **Internal**: Often 90 days to 2 years

### Simple Retention with Partitions

```sql
-- Drop data older than 7 years
DROP TABLE bh_audit_events_2019_01;
```

### Retention Without Partitions

```sql
-- Delete old events (run during low-traffic periods)
DELETE FROM bh_audit_events 
WHERE timestamp < NOW() - INTERVAL '7 years';

-- Afterward, reclaim space
VACUUM ANALYZE bh_audit_events;
```

## Query Examples

### Patient Access Report (HIPAA)

```sql
SELECT 
  timestamp,
  actor_subject_id,
  action_type,
  outcome_status
FROM bh_audit_events
WHERE patient_id = 'patient_123'
  AND timestamp >= '2026-01-01'
  AND timestamp < '2026-02-01'
ORDER BY timestamp;
```

### User Activity Report

```sql
SELECT 
  timestamp,
  action_type,
  resource_type,
  resource_id,
  outcome_status
FROM bh_audit_events
WHERE actor_subject_id = 'user_456'
  AND timestamp >= NOW() - INTERVAL '30 days'
ORDER BY timestamp DESC;
```

### Failed Access Summary

```sql
SELECT 
  DATE(timestamp) as day,
  action_type,
  COUNT(*) as failure_count
FROM bh_audit_events
WHERE outcome_status = 'FAILURE'
  AND timestamp >= NOW() - INTERVAL '7 days'
GROUP BY DATE(timestamp), action_type
ORDER BY day, failure_count DESC;
```

## SQLite Notes

SQLite (used for local development/testing) has limited index capabilities:

- No partial indexes
- No JSONB type
- No table partitioning

For SQLite, use simple indexes:

```sql
CREATE INDEX idx_audit_timestamp ON bh_audit_events (timestamp);
CREATE INDEX idx_audit_patient_id ON bh_audit_events (patient_id);
CREATE INDEX idx_audit_actor_subject_id ON bh_audit_events (actor_subject_id);
```

## Summary

Start with the essential indexes. Monitor query performance with `EXPLAIN ANALYZE` and add composite or optional indexes based on actual query patterns. For production PostgreSQL deployments with high volume, implement partitioning from the start.
