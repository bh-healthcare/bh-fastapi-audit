"""
SQLAlchemy sink for audit events.

Stores audit events in a relational database with query-friendly columns
plus the full event JSON for completeness.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


def _create_audit_table(metadata: MetaData, table_name: str) -> Table:
    """
    Create the audit events table definition.

    Uses TEXT for JSON column to ensure SQLite compatibility.
    Postgres users can use JSONB via raw SQL if needed.
    """
    return Table(
        table_name,
        metadata,
        # Primary key - event_id from the audit event
        Column("event_id", String(64), primary_key=True),
        # Timestamp - stored as TIMESTAMP for query efficiency
        Column("timestamp", DateTime(timezone=True), nullable=False, index=True),
        # Service info - flattened for easy filtering
        Column("service_name", String(128), nullable=False),
        Column("environment", String(64), nullable=True),
        # Actor info - flattened for compliance queries
        Column("actor_subject_id", String(256), nullable=False),
        Column("actor_subject_type", String(32), nullable=False),
        # Action info
        Column("action_type", String(32), nullable=False),
        # Resource info
        Column("resource_type", String(128), nullable=False),
        Column("resource_id", String(256), nullable=True),
        Column("patient_id", String(256), nullable=True),
        # Outcome
        Column("outcome_status", String(16), nullable=False),
        # HTTP info (optional)
        Column("http_status_code", Integer, nullable=True),
        # Correlation IDs (optional)
        Column("trace_id", String(256), nullable=True),
        Column("request_id", String(256), nullable=True),
        # Full event JSON for completeness
        Column("event_json", Text, nullable=False),
    )


class SQLAlchemySink:
    """
    Audit sink that stores events in a relational database via SQLAlchemy.

    Extracts key fields into indexed columns for efficient querying while
    storing the complete event JSON for audit completeness.

    Works with any SQLAlchemy-supported database (Postgres, SQLite, MySQL, etc.).

    Args:
        dsn: Database connection string (e.g., "postgresql://user:pass@host/db"
             or "sqlite+pysqlite:///:memory:" for testing).
        table_name: Name of the audit events table (default: "bh_audit_events").
        create_table: If True, create the table on startup if it doesn't exist.

    Example:
        # Postgres
        sink = SQLAlchemySink("postgresql://user:pass@localhost/mydb")

        # SQLite for testing
        sink = SQLAlchemySink("sqlite+pysqlite:///:memory:")

        sink.emit(event)
    """

    def __init__(
        self,
        dsn: str,
        table_name: str = "bh_audit_events",
        *,
        create_table: bool = True,
    ) -> None:
        self._dsn = dsn
        self._table_name = table_name
        self._engine: Engine = create_engine(dsn)
        self._metadata = MetaData()
        self._table = _create_audit_table(self._metadata, table_name)

        if create_table:
            self._metadata.create_all(self._engine)

    def emit(self, event: dict[str, Any]) -> None:
        """
        Store an audit event in the database.

        Extracts key fields into columns and stores the full JSON.
        Duplicate event_ids are silently ignored (append-only semantics).

        Args:
            event: The audit event dictionary conforming to bh-audit-schema.
        """
        row = self._extract_row(event)

        try:
            with self._engine.connect() as conn:
                conn.execute(self._table.insert().values(**row))
                conn.commit()
        except IntegrityError as exc:
            if "event_id" in str(exc).lower() or "primary" in str(exc).lower():
                pass
            else:
                raise

    def _extract_row(self, event: dict[str, Any]) -> dict[str, Any]:
        """Extract a flat row dict from the nested event structure."""
        # Parse timestamp string to datetime
        timestamp_str = event["timestamp"]
        # Handle ISO format timestamps (with or without Z suffix)
        if timestamp_str.endswith("Z"):
            timestamp_str = timestamp_str[:-1] + "+00:00"
        timestamp = datetime.fromisoformat(timestamp_str)

        # Extract correlation IDs if present
        correlation = event.get("correlation", {})
        http = event.get("http", {})
        resource = event.get("resource", {})

        return {
            "event_id": event["event_id"],
            "timestamp": timestamp,
            "service_name": event["service"]["name"],
            "environment": event["service"].get("environment"),
            "actor_subject_id": event["actor"]["subject_id"],
            "actor_subject_type": event["actor"]["subject_type"],
            "action_type": event["action"]["type"],
            "resource_type": resource.get("type", "unknown"),
            "resource_id": resource.get("id"),
            "patient_id": resource.get("patient_id"),
            "outcome_status": event["outcome"]["status"],
            "http_status_code": http.get("status_code"),
            "trace_id": correlation.get("trace_id"),
            "request_id": correlation.get("request_id"),
            "event_json": json.dumps(event, separators=(",", ":"), ensure_ascii=False),
        }

    @property
    def engine(self) -> Engine:
        """Return the SQLAlchemy engine."""
        return self._engine

    @property
    def table(self) -> Table:
        """Return the audit events table definition."""
        return self._table

    def query_by_patient(self, patient_id: str) -> list[dict[str, Any]]:
        """
        Query events by patient ID.

        Convenience method for common compliance query.

        Args:
            patient_id: The patient ID to search for.

        Returns:
            List of event dictionaries.
        """
        with self._engine.connect() as conn:
            result = conn.execute(
                self._table.select().where(self._table.c.patient_id == patient_id)
            )
            return [dict(row._mapping) for row in result]

    def query_by_actor(self, subject_id: str) -> list[dict[str, Any]]:
        """
        Query events by actor subject ID.

        Convenience method for user activity audit.

        Args:
            subject_id: The actor subject ID to search for.

        Returns:
            List of event dictionaries.
        """
        with self._engine.connect() as conn:
            result = conn.execute(
                self._table.select().where(self._table.c.actor_subject_id == subject_id)
            )
            return [dict(row._mapping) for row in result]

    def count(self) -> int:
        """Return the total number of stored events."""
        with self._engine.connect() as conn:
            result = conn.execute(select(func.count()).select_from(self._table))
            row = result.fetchone()
            return row[0] if row else 0

    def close(self) -> None:
        """Dispose of the database engine."""
        self._engine.dispose()
