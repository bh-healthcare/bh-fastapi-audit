"""
Vendored bh-audit-schema v1.0 for offline validation.

The JSON schema is included in this package to enable validation
without network access.
"""

from pathlib import Path

SCHEMA_VERSION = "1.0"
SCHEMA_PATH = Path(__file__).parent / "audit_event.schema.json"


def get_schema_path() -> Path:
    """Return the path to the vendored audit event schema."""
    return SCHEMA_PATH


def load_schema() -> dict:
    """Load and return the audit event schema as a dictionary."""
    import json

    with open(SCHEMA_PATH) as f:
        return json.load(f)

