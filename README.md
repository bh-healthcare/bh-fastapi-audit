# bh-fastapi-audit

A FastAPI middleware for emitting PHI-safe audit events for behavioral healthcare systems, designed for teams building modern healthcare APIs.

This project emits audit events conforming to the **bh-audit-schema** standard (currently v1.0):  
https://github.com/bh-healthcare/bh-audit-schema

## Why

Behavioral health systems handle highly sensitive regulated data. Audit logging is often implemented inconsistently across services, making access review and incident investigation unnecessarily difficult.

The goal of this library is to make consistent, structured audit trails easy to adopt in FastAPI services without logging raw PHI.

## Status

This project is an implementation layer that turns the bh-audit-schema standard into working FastAPI middleware.

This repository is under active development. Initial release scope is focused on:

- FastAPI middleware that emits events conforming to bh-audit-schema v1.0
- PHI-safe defaults (no request/response bodies)
- Pluggable sinks (starting with JSONL and SQL databases)

The bh-audit-schema v1.0 JSON schema is vendored into this package for offline validation.

## Quickstart (planned API)

```python
from fastapi import FastAPI
from bh_fastapi_audit import AuditMiddleware
from bh_fastapi_audit.sinks import JsonlFileSink

app = FastAPI()

app.add_middleware(
    AuditMiddleware,
    service_name="example-bh-api",
    sink=JsonlFileSink(path="./audit.jsonl"),
)
```

## PHI-safe defaults

This library is designed to be safe by default:

- Does not log request or response bodies
- Uses route templates instead of raw paths when possible
- Sanitizes error messages before emitting audit events
- Allows only explicitly safe metadata

## Scope and non-goals

**In scope:**

- Structured audit events designed for compliance and operational monitoring
- Correlation support (request_id / trace_id) to connect events across services

**Out of scope:**

- Legal compliance guarantees
- Storing raw PHI or clinical content in logs
- Opinionated IAM or authentication frameworks

## Installation (planned)

The package will be published to PyPI once the initial v0.1 middleware and sinks are complete.

```bash
pip install bh-fastapi-audit
```

## License

Apache 2.0
