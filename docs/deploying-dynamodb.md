# Deploying DynamoDBSink to AWS

This guide covers everything you need to run `DynamoDBSink` against a real AWS DynamoDB table in dev, staging, and production.

## Quick Start (Local Development)

For local development, use DynamoDB Local with `create_table=True`:

```bash
# Start DynamoDB Local
docker run -d -p 8000:8000 amazon/dynamodb-local

# Or use the docker-compose.yml in the examples repo:
cd bh-fastapi-examples/dynamodb_audit_app
docker compose up -d
```

```python
from bh_fastapi_audit.sinks.dynamodb import DynamoDBSink

sink = DynamoDBSink(
    table_name="bh_audit_events",
    region="us-east-1",
    create_table=True,   # Creates table + GSIs automatically
)
```

**Never use `create_table=True` in production.** The table should be provisioned via IaC (Terraform, CloudFormation, CDK).

---

## Production Setup

### Step 1: Provision the DynamoDB Table

Use the Terraform module in `bh-fastapi-examples/dynamodb_audit_app/terraform/`:

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars for your environment

terraform init
terraform plan
terraform apply
```

This creates:

| Resource | Purpose |
|---|---|
| `aws_dynamodb_table.audit_events` | The audit events table with 3 GSIs |
| `aws_iam_policy.audit_writer` | Minimum IAM policy for the application |

The table is created with:

- **PAY_PER_REQUEST** billing (on-demand, no capacity planning needed)
- **Server-side encryption** (AWS-owned key)
- **Point-in-time recovery** enabled
- **TTL** enabled on the `ttl` attribute
- **3 GSIs** for compliance queries

If you don't use Terraform, create the table manually or via CloudFormation with the schema described in the [Table Design](#table-design) section below.

### Step 2: Attach the IAM Policy

Attach the `bh-audit-dynamodb-writer-{env}` policy to your application's IAM role (ECS task role, Lambda execution role, EC2 instance profile, etc.):

```bash
# Get the policy ARN from Terraform output
terraform output writer_policy_arn

# Attach to your application role
aws iam attach-role-policy \
  --role-name your-app-role \
  --policy-arn arn:aws:iam::123456789012:policy/bh-audit-dynamodb-writer-prod
```

### Step 3: Configure the Application

The sink needs two pieces of information at runtime: the **table name** and the **AWS region**. Pass them via environment variables or constructor arguments.

**Environment variables (recommended for containers/Lambda):**

```bash
export BH_AUDIT_TABLE=bh_audit_events
export AWS_DEFAULT_REGION=us-east-1

# AWS credentials come from the IAM role automatically in ECS/Lambda/EC2.
# For local development with real AWS:
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
```

**In your FastAPI app:**

```python
import os
from bh_fastapi_audit import AuditConfig, AuditMiddleware
from bh_fastapi_audit.sinks.dynamodb import DynamoDBSink

sink = DynamoDBSink(
    table_name=os.environ.get("BH_AUDIT_TABLE", "bh_audit_events"),
    region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    ttl_days=2190,          # ~6 years, HIPAA retention
    create_table=False,     # Table provisioned via Terraform
)

config = AuditConfig(
    service_name="your-service",
    service_environment=os.environ.get("ENVIRONMENT", "dev"),
    emit_failure_mode="log",   # Never break requests on DynamoDB failures
    # ... other config
)

app.add_middleware(AuditMiddleware, sink=sink, config=config)
```

---

## IAM Permissions

The minimum IAM policy for the application:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AuditEventWrite",
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:DescribeTable"
      ],
      "Resource": "arn:aws:dynamodb:REGION:ACCOUNT:table/bh_audit_events"
    },
    {
      "Sid": "AuditEventQuery",
      "Effect": "Allow",
      "Action": [
        "dynamodb:Query"
      ],
      "Resource": [
        "arn:aws:dynamodb:REGION:ACCOUNT:table/bh_audit_events",
        "arn:aws:dynamodb:REGION:ACCOUNT:table/bh_audit_events/index/*"
      ]
    }
  ]
}
```

Replace `REGION` and `ACCOUNT` with your values. If you only write events (no query endpoints), you can drop the `AuditEventQuery` statement entirely.

**Do not grant `dynamodb:CreateTable` or `dynamodb:DeleteTable` to production roles.**

---

## Table Design

The table uses a single-table design optimized for healthcare compliance queries.

### Primary Key

| Key | Attribute | Type | Format | Example |
|---|---|---|---|---|
| Partition Key | `service_date` | String | `{service_name}#{date}` | `intake-api#2026-04-15` |
| Sort Key | `ts_event` | String | `{timestamp}#{event_id}` | `2026-04-15T14:32:07.123Z#c1d2e3f4-...` |

**Why `service#date`?** All events for a service on a given day are co-located, making time-range queries within a day efficient. Cross-day queries require multiple partition reads, which is acceptable for infrequent compliance queries.

### Global Secondary Indexes

| GSI | PK | SK | Use Case | HIPAA Reference |
|---|---|---|---|---|
| `patient_id-index` | `patient_id` | `timestamp` | All access to patient X | §164.312(b) |
| `actor-index` | `actor_subject_id` | `timestamp` | All actions by user Y | §164.308(a)(1)(ii)(D) |
| `outcome-index` | `outcome_status` | `timestamp` | All DENIED/FAILED attempts | §164.308(a)(5)(ii)(C) |

### TTL

The `ttl` attribute is a Unix epoch timestamp set to `event_timestamp + ttl_days * 86400`. DynamoDB automatically deletes expired items at no cost. The default is 2190 days (~6 years) to satisfy HIPAA retention requirements.

To disable TTL, pass `ttl_days=None` to the sink constructor.

---

## Configuration Reference

| Parameter | Constructor arg | Env var | Default | Description |
|---|---|---|---|---|
| Table name | `table_name` | `BH_AUDIT_TABLE` | `bh_audit_events` | DynamoDB table name |
| Region | `region` | `AWS_DEFAULT_REGION` | boto3 default | AWS region |
| TTL days | `ttl_days` | — | `2190` (~6 years) | Days until auto-deletion. `None` to disable. |
| Create table | `create_table` | — | `False` | Create table on init. **Dev/test only.** |

AWS credentials are resolved by the standard boto3 credential chain:

1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`)
2. Shared credentials file (`~/.aws/credentials`)
3. AWS config file (`~/.aws/config`)
4. IAM role for Amazon EC2 / ECS / Lambda (automatic in AWS compute)

---

## Failure Handling

DynamoDB writes can fail (throttling, network issues, service outage). The `DynamoDBSink` is designed to work with the middleware's `emit_failure_mode` setting:

| Mode | Behavior on DynamoDB failure |
|---|---|
| `"log"` (default) | Log a warning, increment `emit_failures_total`, continue serving |
| `"silent"` | Silently drop, increment counter |
| `"raise"` | Raise the exception (not recommended in production) |

The middleware wraps all sink calls in `_safe_emit()`, so a DynamoDB outage **never crashes your application**. Monitor `stats.snapshot()["emit_failures_total"]` to detect sink issues.

---

## Cost Estimates

For a typical BH Healthcare deployment (100-500 patients):

| Metric | Estimate |
|---|---|
| Daily events | 500 - 2,000 |
| Average event size | ~1.5 KB |
| Monthly storage | ~30 - 90 MB |
| 6-year retention | ~2 - 6.5 GB |
| Monthly cost (on-demand) | < $2/month |

This is well within DynamoDB free-tier territory for the first year.
