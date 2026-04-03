"""
DynamoDB sink for audit events.

Stores audit events in a single-table DynamoDB design optimized for
healthcare compliance query patterns. Requires ``boto3`` (install via
``pip install bh-fastapi-audit[dynamodb]``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_DEFAULT_TABLE_NAME = "bh_audit_events"
_DEFAULT_TTL_DAYS = 2190  # ~6 years (HIPAA retention)
_SECONDS_PER_DAY = 86_400


def _iso_to_epoch(iso_ts: str) -> int:
    """Convert an ISO 8601 UTC timestamp to Unix epoch seconds."""
    cleaned = iso_ts.replace("Z", "+00:00")
    from datetime import datetime

    dt = datetime.fromisoformat(cleaned)
    return int(dt.timestamp())


class DynamoDBSink:
    """Audit sink that writes events to DynamoDB.

    Single-table design with three GSIs for compliance queries:

    * **patient_id-index** — all access to a given patient
    * **actor-index** — all actions by a given user
    * **outcome-index** — all DENIED / FAILED outcomes

    Args:
        table_name: DynamoDB table name.
        region: AWS region (default: from environment / boto3 default).
        ttl_days: Days until TTL expiration (default: 2190 ≈ 6 years).
                  Set to ``None`` to disable TTL.
        create_table: If True, create the table on first use (dev/test only).

    Example::

        sink = DynamoDBSink(table_name="bh_audit_events", create_table=True)
        sink.emit(event)
    """

    def __init__(
        self,
        table_name: str = _DEFAULT_TABLE_NAME,
        region: str | None = None,
        ttl_days: int | None = _DEFAULT_TTL_DAYS,
        create_table: bool = False,
    ) -> None:
        self._table_name = table_name
        self._ttl_days = ttl_days

        kwargs: dict[str, Any] = {"service_name": "dynamodb"}
        if region is not None:
            kwargs["region_name"] = region
        self._resource = boto3.resource(**kwargs)
        self._table = self._resource.Table(table_name)

        if create_table:
            self._create_table()

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------

    def emit(self, event: dict[str, Any]) -> None:
        """Write an audit event to DynamoDB.

        * Flattens key fields into top-level attributes for GSI queries.
        * Stores full event JSON in ``event_json`` attribute.
        * Sets TTL if configured.
        * Uses condition expression on ``event_id`` to prevent overwrites.
        """
        item = self._flatten_event(event)

        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(event_id)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.debug(
                    "Duplicate event_id %s — skipping",
                    event.get("event_id"),
                )
                return
            raise

    # ------------------------------------------------------------------
    # GSI queries
    # ------------------------------------------------------------------

    def query_by_patient(
        self,
        patient_id: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query GSI1 (patient_id-index) for patient access history."""
        from boto3.dynamodb.conditions import Key

        key_cond = Key("patient_id").eq(patient_id)
        if start and end:
            key_cond &= Key("timestamp").between(start, end)
        elif start:
            key_cond &= Key("timestamp").gte(start)
        elif end:
            key_cond &= Key("timestamp").lte(end)

        resp = self._table.query(
            IndexName="patient_id-index",
            KeyConditionExpression=key_cond,
        )
        return resp.get("Items", [])

    def query_by_actor(
        self,
        actor_id: str,
        start: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query GSI2 (actor-index) for user activity audit."""
        from boto3.dynamodb.conditions import Key

        key_cond = Key("actor_subject_id").eq(actor_id)
        if start:
            key_cond &= Key("timestamp").gte(start)

        resp = self._table.query(
            IndexName="actor-index",
            KeyConditionExpression=key_cond,
        )
        return resp.get("Items", [])

    def query_denials(
        self,
        start: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query GSI3 (outcome-index) for all DENIED outcomes."""
        from boto3.dynamodb.conditions import Key

        key_cond = Key("outcome_status").eq("DENIED")
        if start:
            key_cond &= Key("timestamp").gte(start)

        resp = self._table.query(
            IndexName="outcome-index",
            KeyConditionExpression=key_cond,
        )
        return resp.get("Items", [])

    # ------------------------------------------------------------------
    # Table creation (dev / test only)
    # ------------------------------------------------------------------

    def _create_table(self) -> None:
        """Create the DynamoDB table with GSIs. Idempotent."""
        try:
            self._resource.create_table(
                TableName=self._table_name,
                KeySchema=[
                    {"AttributeName": "service_date", "KeyType": "HASH"},
                    {"AttributeName": "ts_event", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "service_date", "AttributeType": "S"},
                    {"AttributeName": "ts_event", "AttributeType": "S"},
                    {"AttributeName": "patient_id", "AttributeType": "S"},
                    {"AttributeName": "timestamp", "AttributeType": "S"},
                    {"AttributeName": "actor_subject_id", "AttributeType": "S"},
                    {"AttributeName": "outcome_status", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "patient_id-index",
                        "KeySchema": [
                            {"AttributeName": "patient_id", "KeyType": "HASH"},
                            {"AttributeName": "timestamp", "KeyType": "RANGE"},
                        ],
                        "Projection": {
                            "ProjectionType": "INCLUDE",
                            "NonKeyAttributes": [
                                "event_id",
                                "action_type",
                                "actor_subject_id",
                                "outcome_status",
                                "data_classification",
                                "http_route_template",
                            ],
                        },
                        "ProvisionedThroughput": {
                            "ReadCapacityUnits": 5,
                            "WriteCapacityUnits": 5,
                        },
                    },
                    {
                        "IndexName": "actor-index",
                        "KeySchema": [
                            {"AttributeName": "actor_subject_id", "KeyType": "HASH"},
                            {"AttributeName": "timestamp", "KeyType": "RANGE"},
                        ],
                        "Projection": {
                            "ProjectionType": "INCLUDE",
                            "NonKeyAttributes": [
                                "event_id",
                                "action_type",
                                "resource_type",
                                "patient_id",
                                "outcome_status",
                                "http_route_template",
                            ],
                        },
                        "ProvisionedThroughput": {
                            "ReadCapacityUnits": 5,
                            "WriteCapacityUnits": 5,
                        },
                    },
                    {
                        "IndexName": "outcome-index",
                        "KeySchema": [
                            {"AttributeName": "outcome_status", "KeyType": "HASH"},
                            {"AttributeName": "timestamp", "KeyType": "RANGE"},
                        ],
                        "Projection": {
                            "ProjectionType": "INCLUDE",
                            "NonKeyAttributes": [
                                "event_id",
                                "actor_subject_id",
                                "action_type",
                                "resource_type",
                                "patient_id",
                                "error_type",
                            ],
                        },
                        "ProvisionedThroughput": {
                            "ReadCapacityUnits": 5,
                            "WriteCapacityUnits": 5,
                        },
                    },
                ],
                BillingMode="PROVISIONED",
                ProvisionedThroughput={
                    "ReadCapacityUnits": 5,
                    "WriteCapacityUnits": 5,
                },
            )
            self._table.wait_until_exists()
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceInUseException":
                return
            raise

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _flatten_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Extract top-level DynamoDB attributes from the nested event structure."""
        service = event.get("service", {})
        actor = event.get("actor", {})
        action = event.get("action", {})
        resource = event.get("resource", {})
        outcome = event.get("outcome", {})
        http = event.get("http", {})
        integrity = event.get("integrity", {})

        service_name = service.get("name", "unknown")
        ts = event.get("timestamp", "")
        date_part = ts[:10]
        event_id = event.get("event_id", "")

        item: dict[str, Any] = {
            "service_date": f"{service_name}#{date_part}",
            "ts_event": f"{ts}#{event_id}",
            "event_id": event_id,
            "timestamp": ts,
            "service_name": service_name,
            "environment": service.get("environment", "unknown"),
            "actor_subject_id": actor.get("subject_id", "unknown"),
            "actor_subject_type": actor.get("subject_type", "unknown"),
            "action_type": action.get("type", "OTHER"),
            "action_phi_touched": action.get("phi_touched", False),
            "data_classification": action.get("data_classification", "UNKNOWN"),
            "resource_type": resource.get("type", "Unknown"),
            "outcome_status": outcome.get("status", "SUCCESS"),
            "http_method": http.get("method"),
            "http_route_template": http.get("route_template"),
            "http_status_code": http.get("status_code"),
            "event_json": json.dumps(event, separators=(",", ":"), ensure_ascii=False),
        }

        if resource.get("id"):
            item["resource_id"] = resource["id"]
        if resource.get("patient_id"):
            item["patient_id"] = resource["patient_id"]
        if actor.get("org_id"):
            item["actor_org_id"] = actor["org_id"]
        if actor.get("owner_org_id"):
            item["actor_owner_org_id"] = actor["owner_org_id"]
        if outcome.get("error_type"):
            item["error_type"] = outcome["error_type"]

        if integrity.get("event_hash"):
            item["chain_hash"] = integrity["event_hash"]
        if integrity.get("prev_event_hash"):
            item["prev_chain_hash"] = integrity["prev_event_hash"]

        if self._ttl_days is not None and ts:
            item["ttl"] = _iso_to_epoch(ts) + (self._ttl_days * _SECONDS_PER_DAY)

        # Remove None values — DynamoDB does not accept them
        return {k: v for k, v in item.items() if v is not None}

    @property
    def table_name(self) -> str:
        """Return the configured table name."""
        return self._table_name
