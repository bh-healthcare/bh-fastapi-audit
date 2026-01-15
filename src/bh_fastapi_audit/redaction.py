"""
PHI-safe redaction utilities.

This module provides functions to sanitize text that might contain
sensitive information before including it in audit events.
"""

import re
from typing import Sequence

# Default max length for error messages
DEFAULT_MAX_ERROR_LENGTH = 200

# Common patterns that might indicate sensitive data
# These are intentionally conservative - we err on the side of redaction
_SENSITIVE_PATTERNS = [
    # SSN-like patterns (XXX-XX-XXXX)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[REDACTED-EMAIL]"),
    # Phone numbers (various formats)
    (re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[REDACTED-PHONE]"),
    # Long digit sequences (10+ digits) - could be MRN, account numbers
    (re.compile(r"\b\d{10,}\b"), "[REDACTED-ID]"),
]


def sanitize_error_message(
    message: str,
    max_len: int = DEFAULT_MAX_ERROR_LENGTH,
    redact_patterns: bool = True,
) -> str:
    """
    Sanitize an error message for safe inclusion in audit events.

    Performs the following:
    - Strips leading/trailing whitespace
    - Replaces newlines with spaces
    - Optionally redacts common sensitive patterns (SSN, email, phone)
    - Truncates to max length

    Args:
        message: The error message to sanitize.
        max_len: Maximum length of the output (default 200).
        redact_patterns: Whether to redact common sensitive patterns.

    Returns:
        A sanitized string safe for audit logging.
    """
    if not message:
        return ""

    # Strip and normalize whitespace
    result = message.strip()
    result = re.sub(r"\s+", " ", result)

    # Redact sensitive patterns
    if redact_patterns:
        for pattern, replacement in _SENSITIVE_PATTERNS:
            result = pattern.sub(replacement, result)

    # Truncate with indicator
    if len(result) > max_len:
        result = result[: max_len - 3] + "..."

    return result


def contains_phi_tokens(text: str, tokens: Sequence[str]) -> list[str]:
    """
    Check if text contains any of the given PHI tokens.

    This is primarily a test utility to verify that sensitive tokens
    do not appear in emitted audit events.

    Args:
        text: The text to search.
        tokens: Sequence of tokens to look for.

    Returns:
        List of tokens that were found in the text.
    """
    found = []
    text_lower = text.lower()
    for token in tokens:
        if token.lower() in text_lower:
            found.append(token)
    return found


def redact_tokens(text: str, tokens: Sequence[str], replacement: str = "[REDACTED]") -> str:
    """
    Redact specific tokens from text.

    This allows explicit redaction of known sensitive values,
    useful when you know specific values that must not appear.

    Args:
        text: The text to redact from.
        tokens: Tokens to redact.
        replacement: What to replace tokens with.

    Returns:
        Text with tokens replaced.
    """
    result = text
    for token in tokens:
        # Case-insensitive replacement
        pattern = re.compile(re.escape(token), re.IGNORECASE)
        result = pattern.sub(replacement, result)
    return result
