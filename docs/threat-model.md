# Threat Model

This document describes the security boundaries and threat posture of the
bh-fastapi-audit chain hashing and verification system.

## What the system protects against

### Event tampering (post-emission)

Each event's content is hashed via SHA-256 (or SHA-384/512). If any field is
modified after emission, `bh-audit verify` detects the mismatch.

**Mitigation**: `compute_chain_hash` produces a deterministic hash over the
canonical serialization of the event (sorted keys, compact JSON, UTF-8).
Verification recomputes and compares.

### Silent deletion

Each event's hash includes the previous event's hash (`prev_event_hash`),
forming a hash chain. Deleting an event mid-chain causes a chain gap that
`verify_chain` detects.

**Mitigation**: The `prev_event_hash` field links events together. Any break
in the chain is flagged as a `chain_gap` failure.

### Event reordering / replay

Reordering events breaks `prev_event_hash` linkage. Replaying an event with
a correct hash but wrong position in the chain is detected as a gap.

**Mitigation**: Ordered verification with `verify_chain()` ensures events
are in the expected sequence.

## What the system does NOT protect against

### Compromised event producer

If the service emitting events is compromised, the attacker can emit
well-formed, correctly-hashed fraudulent events. Chain hashing verifies
integrity, not authenticity.

**Accept**: This is a fundamental limitation of symmetric hashing. For
producer authentication, consider asymmetric signing (future work).

### Root / admin access to storage

An attacker with write access to the DynamoDB table or JSONL file can
rewrite the entire chain with valid hashes. The system cannot detect a
complete chain replacement.

**Mitigate**: Use DynamoDB point-in-time recovery, S3 Object Lock, or
immutable storage for the audit trail. Cross-reference with external
systems (SIEM, CloudTrail).

### Clock manipulation

Timestamps are taken from the application host. If the clock is skewed or
manipulated, events may appear out of order but the hash chain remains valid.

**Mitigate**: Use NTP-synchronized hosts. Monitor for timestamp anomalies
in compliance reviews.

### Side-channel attacks

The hashing implementation uses Python's `hashlib` which does not provide
constant-time comparison. This is acceptable because audit hashes are not
secrets -- they are stored alongside the events.

## Trust boundaries

```
┌──────────────────────────────────────┐
│  FastAPI Application Process         │
│  ┌─────────────────┐ ┌───────────┐  │
│  │ AuditMiddleware  │ │ChainState │  │
│  │ (ASGI, producer) │ │(in-memory │  │
│  └───────┬─────────┘ │or DynamoDB)│  │
│          │            └───────────┘  │
│          ▼                           │
│  ┌──────────┐                        │
│  │ Sink     │  (LoggingSink,         │
│  │          │   DynamoDBSink,        │
│  │          │   LedgerSink, etc.)    │
│  └─────┬────┘                        │
└────────┼─────────────────────────────┘
         │  TRUST BOUNDARY
         ▼
┌──────────────────┐
│ Storage Layer    │  (DynamoDB table, JSONL file, S3)
│ (at-rest data)   │
└──────────────────┘
         │  TRUST BOUNDARY
         ▼
┌──────────────────┐
│ Verifier         │  (bh-audit verify CLI or verify_chain())
│ (read-only)      │
└──────────────────┘
```

### Key assumptions

1. The application process is trusted at emission time.
2. Storage is append-only or append-mostly (deletions are detectable).
3. The verifier operates on a faithful copy of the stored data.
4. Hash algorithms (SHA-256/384/512) are collision-resistant.

## Recommendations for production deployments

1. **Enable DynamoDB point-in-time recovery** for the audit table.
2. **Run `bh-audit verify` on a schedule** (daily or weekly) from a
   separate, hardened environment.
3. **Archive audit events to S3 with Object Lock** for immutable retention.
4. **Forward audit events to a SIEM** for independent correlation.
5. **Monitor `chain_gaps_total` and `integrity_events_total`** in your
   observability stack.
