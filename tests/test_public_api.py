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
        AuditValidationError,
        EmitQueue,
        JsonlFileSink,
        LoggingSink,
        MemorySink,
        contains_phi_tokens,
        redact_tokens,
        sanitize_error_message,
        validate_event,
    )

    assert AuditMiddleware is not None
    assert AuditConfig is not None
    assert AuditSink is not None
    assert AuditValidationError is not None
    assert MemorySink is not None
    assert JsonlFileSink is not None
    assert LoggingSink is not None
    assert EmitQueue is not None
    assert callable(sanitize_error_message)
    assert callable(contains_phi_tokens)
    assert callable(redact_tokens)
    assert callable(validate_event)


def test_typed_event_blocks_importable():
    """TypedDict event blocks should be importable from the top-level package."""
    from bh_fastapi_audit import (
        ActionBlock,
        ActionType,
        ActorBlock,
        ActorType,
        AuditEvent,
        CorrelationBlock,
        DataClassification,
        HttpBlock,
        OutcomeBlock,
        OutcomeStatus,
        ResourceBlock,
        ServiceBlock,
    )

    assert ActionBlock is not None
    assert ActionType is not None
    assert ActorBlock is not None
    assert ActorType is not None
    assert AuditEvent is not None
    assert CorrelationBlock is not None
    assert DataClassification is not None
    assert HttpBlock is not None
    assert OutcomeBlock is not None
    assert OutcomeStatus is not None
    assert ResourceBlock is not None
    assert ServiceBlock is not None


def test_sqlalchemy_sink_import():
    """SQLAlchemySink should be importable (may be None if sqlalchemy not installed)."""
    from bh_fastapi_audit import SQLAlchemySink

    try:
        import sqlalchemy  # noqa: F401

        assert SQLAlchemySink is not None
        assert hasattr(SQLAlchemySink, "emit")
    except ImportError:
        assert SQLAlchemySink is None


def test_dynamodb_sink_import():
    """DynamoDBSink should be importable (may be None if boto3 not installed)."""
    from bh_fastapi_audit import DynamoDBSink

    try:
        import boto3  # noqa: F401

        assert DynamoDBSink is not None
        assert hasattr(DynamoDBSink, "emit")
    except ImportError:
        assert DynamoDBSink is None


def test_version_exposed():
    """Package version should be accessible."""
    from bh_fastapi_audit import __version__

    assert isinstance(__version__, str)
    assert __version__ == "0.4.0"


def test_all_exports_defined():
    """__all__ should include all public symbols."""
    import bh_fastapi_audit

    expected_exports = {
        "__version__",
        "AuditConfig",
        "AuditMiddleware",
        "AuditStats",
        "AuditValidationError",
        "EmitQueue",
        # Types
        "ActionBlock",
        "ActionType",
        "ActorBlock",
        "ActorType",
        "AuditEvent",
        "CorrelationBlock",
        "DataClassification",
        "HttpBlock",
        "OutcomeBlock",
        "OutcomeStatus",
        "ResourceBlock",
        "ServiceBlock",
        # Sinks
        "AuditSink",
        "DynamoDBSink",
        "JsonlFileSink",
        "LoggingSink",
        "MemorySink",
        "SQLAlchemySink",
        # Validation
        "validate_event",
        # Redaction
        "contains_phi_tokens",
        "redact_tokens",
        "sanitize_error_message",
    }

    assert set(bh_fastapi_audit.__all__) == expected_exports
