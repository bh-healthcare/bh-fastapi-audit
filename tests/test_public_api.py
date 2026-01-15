"""
Test that the public API surface is correctly exported.

This test ensures that users can import the documented public API
and prevents accidental breaking changes to exports.
"""


def test_public_api_imports():
    """All documented public symbols should be importable from the top-level package."""
    from bh_fastapi_audit import (
        AuditConfig,
        AuditMiddleware,
        AuditSink,
        JsonlFileSink,
        MemorySink,
        contains_phi_tokens,
        redact_tokens,
        sanitize_error_message,
    )

    # Verify these are the actual classes/functions, not None or placeholders
    assert AuditMiddleware is not None
    assert AuditConfig is not None
    assert AuditSink is not None
    assert MemorySink is not None
    assert JsonlFileSink is not None
    assert callable(sanitize_error_message)
    assert callable(contains_phi_tokens)
    assert callable(redact_tokens)


def test_sqlalchemy_sink_import():
    """SQLAlchemySink should be importable (may be None if sqlalchemy not installed)."""
    from bh_fastapi_audit import SQLAlchemySink

    # SQLAlchemySink may be None if sqlalchemy is not installed,
    # but the import itself should not fail
    # When sqlalchemy IS installed, it should be the actual class
    try:
        import sqlalchemy  # noqa: F401

        assert SQLAlchemySink is not None
        assert hasattr(SQLAlchemySink, "emit")
    except ImportError:
        # sqlalchemy not installed, SQLAlchemySink should be None
        assert SQLAlchemySink is None


def test_version_exposed():
    """Package version should be accessible."""
    from bh_fastapi_audit import __version__

    assert isinstance(__version__, str)
    assert __version__ == "0.1.0"


def test_all_exports_defined():
    """__all__ should include all public symbols."""
    import bh_fastapi_audit

    expected_exports = {
        "__version__",
        "AuditConfig",
        "AuditMiddleware",
        "AuditSink",
        "JsonlFileSink",
        "MemorySink",
        "SQLAlchemySink",
        "contains_phi_tokens",
        "redact_tokens",
        "sanitize_error_message",
    }

    assert set(bh_fastapi_audit.__all__) == expected_exports
