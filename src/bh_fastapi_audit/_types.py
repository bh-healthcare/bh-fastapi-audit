"""
Typed event block definitions conforming to bh-audit-schema v1.1.

Provides TypedDict classes for all event sub-blocks, enabling static
type checking of event construction and callback return values.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

ActionType = Literal[
    "READ",
    "CREATE",
    "UPDATE",
    "DELETE",
    "EXPORT",
    "LOGIN",
    "LOGOUT",
    "PRINT",
    "OTHER",
]
OutcomeStatus = Literal["SUCCESS", "FAILURE", "DENIED"]
ActorType = Literal["human", "service"]
DataClassification = Literal["PHI", "PII", "NONE", "UNKNOWN"]
HttpMethod = Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
HashAlgorithm = Literal["sha256", "sha384", "sha512"]
EmitFailureMode = Literal["silent", "log", "raise"]


class ServiceBlock(TypedDict, total=False):
    name: str
    environment: str
    version: str


class CorrelationBlock(TypedDict, total=False):
    request_id: str
    trace_id: str
    session_id: str


class ActorBlock(TypedDict, total=False):
    subject_id: str
    subject_type: ActorType
    org_id: str
    owner_org_id: str
    roles: list[str]


class ActionBlock(TypedDict, total=False):
    type: ActionType
    name: str
    phi_touched: bool
    data_classification: DataClassification


class ResourceBlock(TypedDict, total=False):
    type: str
    id: str
    patient_id: str


class HttpBlock(TypedDict, total=False):
    method: HttpMethod
    route_template: str
    status_code: int
    client_ip: str
    user_agent: str


class OutcomeBlock(TypedDict, total=False):
    status: OutcomeStatus
    error_type: str
    error_message: str


class IntegrityBlock(TypedDict, total=False):
    event_hash: str
    prev_event_hash: str
    hash_alg: HashAlgorithm


class AuditEvent(TypedDict, total=False):
    schema_version: str
    event_id: str
    timestamp: str
    service: ServiceBlock
    actor: ActorBlock
    action: ActionBlock
    resource: ResourceBlock
    outcome: OutcomeBlock
    http: HttpBlock
    correlation: CorrelationBlock
    integrity: IntegrityBlock
    metadata: dict[str, str | int | float | bool | None]


class RequiredAuditEvent(TypedDict):
    """Strict variant with all required keys enforced."""

    schema_version: str
    event_id: str
    timestamp: str
    service: ServiceBlock
    actor: ActorBlock
    action: ActionBlock
    resource: ResourceBlock
    outcome: OutcomeBlock
    http: NotRequired[HttpBlock]
    correlation: NotRequired[CorrelationBlock]
    integrity: NotRequired[IntegrityBlock]
    metadata: NotRequired[dict[str, str | int | float | bool | None]]
