"""
Vendored bh-audit-schema v1.0 and v1.1 for offline validation.

The JSON schemas are included in this package to enable validation
without network access.  Use ``get_schema_path(version)`` to resolve
the correct file or ``load_schema(version)`` to get the parsed dict.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.1"
VERSIONS_DIR = Path(__file__).parent / "versions"
SCHEMA_PATH = Path(__file__).parent / "audit_event.schema.json"


def get_schema_path(version: str = "1.1") -> Path:
    """Return the path to the vendored audit event schema for *version*.

    Raises ``FileNotFoundError`` if the requested version is not vendored.
    """
    path = VERSIONS_DIR / version / "audit_event.schema.json"
    if not path.exists():
        raise FileNotFoundError(f"No vendored schema for version {version!r} at {path}")
    return path


@lru_cache(maxsize=4)
def load_schema(version: str = "1.1") -> dict[str, Any]:
    """Load and return the audit event schema for *version* as a dictionary.

    The result is cached per version to avoid repeated disk reads.
    """
    schema_path = get_schema_path(version)
    with open(schema_path) as f:
        return json.load(f)
