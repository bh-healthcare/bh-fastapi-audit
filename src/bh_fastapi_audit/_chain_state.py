"""
Chain state backends for tracking the previous event hash.

Two implementations:

* ``ChainState`` — thread-safe in-memory state for single-process deployments.
* ``DynamoDBChainState`` — for Lambda / multi-process / multi-container
  deployments.  Uses DynamoDB conditional writes for safe concurrency.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

_log = logging.getLogger("bh.audit.chain")

_MAX_RETRIES = 3


class ChainState:
    """Thread-safe, in-memory chain state for single-process deployments.

    Stores the hash of the most recently emitted event so the next event
    can reference it as ``prev_event_hash``.
    """

    __slots__ = ("_lock", "_last_hash")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_hash: str | None = None

    @property
    def last_hash(self) -> str | None:
        """Return the hash of the most recently advanced event, or ``None``."""
        with self._lock:
            return self._last_hash

    def advance(self, event_hash: str) -> str | None:
        """Record *event_hash* as the latest and return the **previous** hash.

        Returns ``None`` for the very first event in the chain.
        """
        with self._lock:
            prev = self._last_hash
            self._last_hash = event_hash
            return prev


class DynamoDBChainState:
    """DynamoDB-backed chain state for multi-process deployments.

    Stores one row per ``service_name`` in a dedicated chain-state table.
    Uses conditional writes (``attribute_not_exists`` for the first event,
    ``expected_hash = :prev`` afterwards) to guarantee ordering even
    across concurrent Lambda invocations or container replicas.

    On conditional-check failure the write is retried up to 3 times
    (re-reading the current hash each time).  If all retries are
    exhausted the event is emitted **without** a ``prev_event_hash``
    (an unchained event is better than a dropped event).

    Args:
        table_name: DynamoDB table for chain state (not the events table).
        service_name: Partition key — typically matches ``AuditConfig.service_name``.
        region: AWS region.  ``None`` falls back to the boto3 default chain.
    """

    def __init__(
        self,
        table_name: str = "bh_audit_chain_state",
        service_name: str = "default",
        region: str | None = None,
        create_table: bool = False,
    ) -> None:
        import boto3

        self._service_name = service_name
        session = boto3.Session(region_name=region)
        dynamodb = session.resource("dynamodb")
        self._table: Any = dynamodb.Table(table_name)

        if create_table:
            self._create_table(session, table_name)

    def _create_table(self, session: Any, table_name: str) -> None:
        client = session.client("dynamodb")
        try:
            client.describe_table(TableName=table_name)
        except client.exceptions.ResourceNotFoundException:
            client.create_table(
                TableName=table_name,
                KeySchema=[{"AttributeName": "service_name", "KeyType": "HASH"}],
                AttributeDefinitions=[
                    {"AttributeName": "service_name", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            waiter = client.get_waiter("table_exists")
            waiter.wait(TableName=table_name)

    @property
    def last_hash(self) -> str | None:
        """Read the current chain head from DynamoDB."""
        resp = self._table.get_item(Key={"service_name": self._service_name})
        item = resp.get("Item")
        if item is None:
            return None
        return item.get("last_hash")  # type: ignore[no-any-return]

    def advance(self, event_hash: str) -> str | None:
        """Atomically advance the chain head via conditional write.

        Returns the **previous** hash, or ``None`` for the first event.
        Falls back to returning ``None`` (unchained) if retries are
        exhausted — the caller should still emit the event.
        """
        from botocore.exceptions import ClientError

        for _attempt in range(_MAX_RETRIES):
            current = self.last_hash

            try:
                if current is None:
                    self._table.put_item(
                        Item={
                            "service_name": self._service_name,
                            "last_hash": event_hash,
                        },
                        ConditionExpression="attribute_not_exists(service_name)",
                    )
                else:
                    self._table.update_item(
                        Key={"service_name": self._service_name},
                        UpdateExpression="SET last_hash = :new_hash",
                        ConditionExpression="last_hash = :expected",
                        ExpressionAttributeValues={
                            ":new_hash": event_hash,
                            ":expected": current,
                        },
                    )
                return current

            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    _log.debug(
                        "Chain state conditional check failed (attempt %d), retrying",
                        _attempt + 1,
                    )
                    continue
                raise

        _log.warning(
            "Chain state advance exhausted %d retries for service=%s; emitting unchained event",
            _MAX_RETRIES,
            self._service_name,
        )
        return None
