"""
Runtime JSON-schema validation for audit events.

Provides lazy-loaded, version-aware validators so the cost of compiling
the schema is paid at most once per schema version.

``jsonschema`` is an **optional** dependency — import it lazily so the
core package remains usable without it.  When a caller enables
``validate_events=True`` without installing jsonschema, a clear
``ImportError`` is raised at validator-construction time.
"""

from __future__ import annotations

import threading
from typing import Any

_VALIDATORS: dict[str, Any] = {}
_VALIDATORS_LOCK = threading.Lock()


class AuditValidationError(Exception):
    """Raised when an audit event fails schema validation."""

    def __init__(self, event_id: str | None, errors: list[str]) -> None:
        self.event_id = event_id
        self.errors = errors
        summary = "; ".join(errors[:3])
        super().__init__(f"Validation failed for event {event_id}: {summary}")


def _get_validator(schema_version: str) -> Any:
    """Return a compiled validator for *schema_version*, caching the result."""
    if schema_version in _VALIDATORS:
        return _VALIDATORS[schema_version]
    with _VALIDATORS_LOCK:
        if schema_version not in _VALIDATORS:
            try:
                import jsonschema
                import jsonschema.validators
            except ImportError:
                raise ImportError(
                    "jsonschema is required for event validation. "
                    "Install with: pip install bh-fastapi-audit[jsonschema]"
                ) from None

            from bh_fastapi_audit.schema import load_schema

            schema = load_schema(schema_version)
            validator_cls = jsonschema.validators.validator_for(schema)
            validator_cls.check_schema(schema)
            _VALIDATORS[schema_version] = validator_cls(
                schema,
                format_checker=jsonschema.FormatChecker(),
            )
    return _VALIDATORS[schema_version]


def validate_event(
    event: dict[str, Any],
    schema_version: str = "1.1",
) -> list[str]:
    """Validate *event* against the given *schema_version*.

    Returns a list of human-readable error messages (empty on success).
    """
    validator = _get_validator(schema_version)
    errors: list[str] = []
    for err in validator.iter_errors(event):
        path = ".".join(str(p) for p in err.absolute_path)
        errors.append(f"{path}: {err.message}" if path else err.message)
    return errors
