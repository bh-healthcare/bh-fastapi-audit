"""
``bh-audit`` CLI -- audit infrastructure tools.

Requires the ``[cli]`` extra: ``pip install bh-fastapi-audit[cli]``

Usage::

    bh-audit verify --source file --path /var/log/audit/events.jsonl
    bh-audit verify --source dynamodb --table bh_audit_events --service intake-api
    bh-audit verify --source file --path events.jsonl --format json
"""

from __future__ import annotations

import json
from typing import Any

try:
    import typer
except ImportError as _exc:
    raise ImportError(
        "The bh-audit CLI requires typer. Install with: pip install bh-fastapi-audit[cli]"
    ) from _exc

from bh_fastapi_audit._verifier import VerifyResult, verify_chain

app = typer.Typer(name="bh-audit", help="BH Audit infrastructure tools")

_EXIT_PASS = 0
_EXIT_FAIL = 1
_EXIT_ERROR = 2


def _load_events_from_file(path: str) -> list[dict[str, Any]]:
    """Read audit events from a JSONL file."""
    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                typer.echo(f"Error: invalid JSON on line {lineno}: {exc}", err=True)
                raise typer.Exit(code=_EXIT_ERROR) from exc
    return events


def _load_events_from_dynamodb(
    table: str,
    service: str | None,
    start: str | None,
    end: str | None,
    region: str | None,
) -> list[dict[str, Any]]:
    """Query audit events from a DynamoDB table."""
    try:
        from bh_fastapi_audit.sinks.dynamodb import DynamoDBSink
    except ImportError as exc:
        typer.echo(
            "Error: DynamoDB source requires boto3. "
            "Install with: pip install bh-fastapi-audit[dynamodb]",
            err=True,
        )
        raise typer.Exit(code=_EXIT_ERROR) from exc

    kwargs: dict[str, Any] = {"table_name": table}
    if region:
        kwargs["region"] = region
    sink = DynamoDBSink(**kwargs)

    if service:
        return sink.query_by_actor(service, start=start, end=end)
    return sink.query_by_patient("*", start=start, end=end)


def _format_human(result: VerifyResult, source_label: str) -> str:
    """Render a human-readable verification report."""
    lines = [
        "bh-audit verify",
        "",
        f"Source: {source_label}",
        f"Events scanned: {result.events_scanned:,}",
    ]
    if result.time_range_start and result.time_range_end:
        lines.append(f"Time range: {result.time_range_start} to {result.time_range_end}")

    lines.append("")
    lines.append("Chain verification:")
    lines.append(f"  Chain length:      {result.chain_length:,}")
    lines.append(f"  Chain gaps:        {result.chain_gaps}")
    lines.append(f"  Hash mismatches:   {result.hash_mismatches}")
    if result.unchained_events > 0:
        lines.append(f"  Unchained events:  {result.unchained_events}")

    for failure in result.failures:
        lines.append("")
        lines.append(f"FAILURE at event #{failure.event_index}:")
        lines.append(f"  event_id:       {failure.event_id}")
        lines.append(f"  timestamp:      {failure.timestamp}")
        lines.append(f"  type:           {failure.failure_type}")
        if failure.expected is not None:
            lines.append(f"  expected_hash:  {failure.expected}")
        if failure.actual is not None:
            lines.append(f"  actual_hash:    {failure.actual}")
        lines.append(f"  detail:         {failure.message}")

    lines.append("")
    if result.result == "PASS":
        lines.append("Result: PASS - audit chain is intact")
        lines.append("")
        lines.append("Compliance note:")
        lines.append(f"  All {result.chain_length:,} chained events have valid integrity hashes.")
        lines.append("  No evidence of tampering or silent deletion detected.")
    else:
        lines.append("Result: FAIL - integrity violation detected")
        lines.append("")
        lines.append("Recommended action:")
        lines.append("  Investigate the flagged events and surrounding context.")
        lines.append("  Preserve original audit logs for forensic review.")

    return "\n".join(lines)


def _format_json(result: VerifyResult, source_label: str) -> str:
    """Render a machine-readable JSON verification report."""
    payload: dict[str, Any] = {
        "source": source_label,
        "events_scanned": result.events_scanned,
        "time_range": {
            "start": result.time_range_start,
            "end": result.time_range_end,
        },
        "chain_length": result.chain_length,
        "chain_gaps": result.chain_gaps,
        "hash_mismatches": result.hash_mismatches,
        "unchained_events": result.unchained_events,
        "result": result.result,
        "failures": [
            {
                "event_index": f.event_index,
                "event_id": f.event_id,
                "timestamp": f.timestamp,
                "failure_type": f.failure_type,
                "expected": f.expected,
                "actual": f.actual,
                "message": f.message,
            }
            for f in result.failures
        ],
    }
    return json.dumps(payload, indent=2)


@app.command()
def verify(
    source: str = typer.Option(..., help="Source type: 'file' or 'dynamodb'"),
    path: str | None = typer.Option(None, help="Path to JSONL file (for --source file)"),
    table: str | None = typer.Option(None, help="DynamoDB table name (for --source dynamodb)"),
    service: str | None = typer.Option(None, help="Service name filter (for --source dynamodb)"),
    start: str | None = typer.Option(None, help="Start date (ISO 8601)"),
    end: str | None = typer.Option(None, help="End date (ISO 8601)"),
    region: str | None = typer.Option(None, help="AWS region (for --source dynamodb)"),
    output_format: str = typer.Option("human", "--format", help="Output format: 'human' or 'json'"),
) -> None:
    """Verify integrity of an audit event chain."""
    if source == "file":
        if not path:
            typer.echo("Error: --path is required when --source is 'file'", err=True)
            raise typer.Exit(code=_EXIT_ERROR)
        try:
            events = _load_events_from_file(path)
        except FileNotFoundError as exc:
            typer.echo(f"Error: file not found: {path}", err=True)
            raise typer.Exit(code=_EXIT_ERROR) from exc
        source_label = path
    elif source == "dynamodb":
        if not table:
            typer.echo("Error: --table is required when --source is 'dynamodb'", err=True)
            raise typer.Exit(code=_EXIT_ERROR)
        events = _load_events_from_dynamodb(table, service, start, end, region)
        source_label = f"dynamodb://{table}"
    else:
        typer.echo(f"Error: unknown source type '{source}'. Use 'file' or 'dynamodb'.", err=True)
        raise typer.Exit(code=_EXIT_ERROR)

    result = verify_chain(events)

    if output_format == "json":
        typer.echo(_format_json(result, source_label))
    else:
        typer.echo(_format_human(result, source_label))

    raise typer.Exit(code=_EXIT_PASS if result.result == "PASS" else _EXIT_FAIL)


if __name__ == "__main__":
    app()
