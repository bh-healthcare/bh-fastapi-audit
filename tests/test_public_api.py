"""
Test that the public API surface is correctly exported.

This test ensures that users can import the documented public API
and prevents accidental breaking changes to exports.
"""

import pytest


def test_public_api_imports():
    """All documented public symbols should be importable from the top-level package."""
    from bh_fastapi_audit import (
        AuditConfig,
        AuditMiddleware,
        AuditSink,
        AuditValidationError,
        ChainState,
        EmitQueue,
        JsonlFileSink,
        LedgerSink,
        LoggingSink,
        MemorySink,
        VerifyFailure,
        VerifyResult,
        canonical_serialize,
        compute_chain_hash,
        contains_phi_tokens,
        redact_tokens,
        sanitize_error_message,
        validate_event,
        verify_chain,
    )

    assert AuditMiddleware is not None
    assert AuditConfig is not None
    assert AuditSink is not None
    assert AuditValidationError is not None
    assert MemorySink is not None
    assert JsonlFileSink is not None
    assert LedgerSink is not None
    assert LoggingSink is not None
    assert EmitQueue is not None
    assert ChainState is not None
    assert callable(canonical_serialize)
    assert callable(compute_chain_hash)
    assert callable(sanitize_error_message)
    assert callable(contains_phi_tokens)
    assert callable(redact_tokens)
    assert callable(validate_event)
    assert callable(verify_chain)
    assert VerifyResult is not None
    assert VerifyFailure is not None


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
        EmitFailureMode,
        HashAlgorithm,
        HttpBlock,
        IntegrityBlock,
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
    assert EmitFailureMode is not None
    assert HashAlgorithm is not None
    assert HttpBlock is not None
    assert IntegrityBlock is not None
    assert OutcomeBlock is not None
    assert OutcomeStatus is not None
    assert ResourceBlock is not None
    assert ServiceBlock is not None


def test_dynamodb_sink_import():
    """DynamoDBSink should be importable when boto3 is installed."""
    from bh_fastapi_audit import DynamoDBSink

    assert DynamoDBSink is not None
    assert hasattr(DynamoDBSink, "emit")


def test_dynamodb_chain_state_import():
    """DynamoDBChainState should be importable when boto3 is installed."""
    from bh_fastapi_audit import DynamoDBChainState

    assert DynamoDBChainState is not None
    assert hasattr(DynamoDBChainState, "advance")


def test_optional_dep_getattr_raises_import_error():
    """__getattr__ should give a helpful ImportError for missing optional deps."""
    import bh_fastapi_audit

    for name in ("DynamoDBSink", "DynamoDBChainState", "SQLAlchemySink"):
        if not hasattr(bh_fastapi_audit, name):
            with pytest.raises(ImportError, match="optional dependency"):
                getattr(bh_fastapi_audit, name)


def test_unknown_attr_raises_attribute_error():
    """Accessing a truly nonexistent attribute should raise AttributeError."""
    import bh_fastapi_audit

    with pytest.raises(AttributeError, match="has no attribute"):
        bh_fastapi_audit.NoSuchThing  # noqa: B018


def test_version_exposed():
    """Package version should be accessible."""
    from bh_fastapi_audit import __version__

    assert isinstance(__version__, str)
    assert __version__ == "1.1.1"


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
        # Chain hashing
        "canonical_serialize",
        "compute_chain_hash",
        "ChainState",
        "DynamoDBChainState",
        # Types
        "ActionBlock",
        "ActionType",
        "ActorBlock",
        "ActorType",
        "AuditEvent",
        "CorrelationBlock",
        "DataClassification",
        "EmitFailureMode",
        "HashAlgorithm",
        "HttpBlock",
        "IntegrityBlock",
        "OutcomeBlock",
        "OutcomeStatus",
        "ResourceBlock",
        "ServiceBlock",
        # Sinks
        "AuditSink",
        "DynamoDBSink",
        "JsonlFileSink",
        "LedgerSink",
        "LoggingSink",
        "MemorySink",
        "SQLAlchemySink",
        # Verifier
        "VerifyFailure",
        "VerifyResult",
        "verify_chain",
        # Validation
        "validate_event",
        # Redaction
        "contains_phi_tokens",
        "redact_tokens",
        "sanitize_error_message",
    }

    assert set(bh_fastapi_audit.__all__) == expected_exports
