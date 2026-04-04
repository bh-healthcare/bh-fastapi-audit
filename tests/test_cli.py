"""Tests for bh_fastapi_audit.cli (bh-audit verify command)."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from typer.testing import CliRunner

from bh_fastapi_audit._chain import compute_chain_hash
from bh_fastapi_audit.cli import app

runner = CliRunner()


def _make_event(
    event_id: str = "evt-001",
    timestamp: str = "2026-01-01T00:00:00.000Z",
) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "event_id": event_id,
        "timestamp": timestamp,
        "service": {"name": "test-svc", "environment": "test"},
        "actor": {"subject_id": "user-1", "subject_type": "human"},
        "action": {"type": "READ", "data_classification": "PHI"},
        "resource": {"type": "Patient"},
        "outcome": {"status": "SUCCESS"},
    }


def _build_chain(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    prev_hash: str | None = None
    for evt in events:
        integrity = compute_chain_hash(evt, prev_hash)
        chained = {**evt, "integrity": integrity}
        prev_hash = integrity["event_hash"]
        chain.append(chained)
    return chain


def _write_jsonl(events: list[dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for evt in events:
            fh.write(json.dumps(evt) + "\n")


class TestCliFileSourcePass:
    def test_intact_chain_exit_0(self) -> None:
        events = _build_chain(
            [
                _make_event("e1", "2026-01-01T00:00:00.000Z"),
                _make_event("e2", "2026-01-01T00:01:00.000Z"),
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(events, f.name)
            path = f.name
        try:
            result = runner.invoke(app, ["--source", "file", "--path", path])
            assert result.exit_code == 0
            assert "PASS" in result.stdout
        finally:
            os.unlink(path)

    def test_human_format_contains_chain_info(self) -> None:
        events = _build_chain([_make_event()])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(events, f.name)
            path = f.name
        try:
            result = runner.invoke(
                app,
                [
                    "--source",
                    "file",
                    "--path",
                    path,
                    "--format",
                    "human",
                ],
            )
            assert result.exit_code == 0
            assert "Chain length:" in result.stdout
            assert "Chain gaps:" in result.stdout
        finally:
            os.unlink(path)


class TestCliFileSourceFail:
    def test_broken_chain_exit_1(self) -> None:
        events = _build_chain(
            [
                _make_event("e1", "2026-01-01T00:00:00.000Z"),
                _make_event("e2", "2026-01-01T00:01:00.000Z"),
            ]
        )
        events[1]["action"]["type"] = "DELETE"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(events, f.name)
            path = f.name
        try:
            result = runner.invoke(app, ["--source", "file", "--path", path])
            assert result.exit_code == 1
            assert "FAIL" in result.stdout
        finally:
            os.unlink(path)


class TestCliJsonFormat:
    def test_json_output_valid(self) -> None:
        events = _build_chain([_make_event()])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(events, f.name)
            path = f.name
        try:
            result = runner.invoke(
                app,
                [
                    "--source",
                    "file",
                    "--path",
                    path,
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 0
            payload = json.loads(result.stdout)
            assert payload["result"] == "PASS"
            assert "events_scanned" in payload
            assert "failures" in payload
            assert isinstance(payload["failures"], list)
        finally:
            os.unlink(path)

    def test_json_format_failure_details(self) -> None:
        events = _build_chain(
            [
                _make_event("e1", "2026-01-01T00:00:00.000Z"),
                _make_event("e2", "2026-01-01T00:01:00.000Z"),
            ]
        )
        events[1]["service"]["name"] = "tampered"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            _write_jsonl(events, f.name)
            path = f.name
        try:
            result = runner.invoke(
                app,
                [
                    "--source",
                    "file",
                    "--path",
                    path,
                    "--format",
                    "json",
                ],
            )
            assert result.exit_code == 1
            payload = json.loads(result.stdout)
            assert payload["result"] == "FAIL"
            assert len(payload["failures"]) >= 1
            assert payload["failures"][0]["failure_type"] == "hash_mismatch"
        finally:
            os.unlink(path)


class TestCliErrors:
    def test_missing_path_exit_2(self) -> None:
        result = runner.invoke(app, ["--source", "file"])
        assert result.exit_code == 2

    def test_nonexistent_file_exit_2(self) -> None:
        result = runner.invoke(
            app,
            [
                "--source",
                "file",
                "--path",
                "/tmp/nonexistent_audit.jsonl",
            ],
        )
        assert result.exit_code == 2

    def test_invalid_source_type_exit_2(self) -> None:
        result = runner.invoke(app, ["--source", "s3"])
        assert result.exit_code == 2

    def test_dynamodb_missing_table_exit_2(self) -> None:
        result = runner.invoke(app, ["--source", "dynamodb"])
        assert result.exit_code == 2

    def test_invalid_json_exit_2(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("not valid json\n")
            path = f.name
        try:
            result = runner.invoke(app, ["--source", "file", "--path", path])
            assert result.exit_code == 2
        finally:
            os.unlink(path)

    def test_empty_file_pass(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            result = runner.invoke(app, ["--source", "file", "--path", path])
            assert result.exit_code == 0
            assert "PASS" in result.stdout
        finally:
            os.unlink(path)
