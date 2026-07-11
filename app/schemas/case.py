from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import ProviderID
from app.schemas.incident import (
    IncidentPriority,
    IncidentStatus,
    RoutingRole,
)


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ASSIGNED = "ASSIGNED"
    IN_REVIEW = "IN_REVIEW"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class CaseActionType(StrEnum):
    CASE_CREATED = "CASE_CREATED"
    INCIDENT_SYNCED = "INCIDENT_SYNCED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ASSIGNED = "ASSIGNED"
    REVIEW_STARTED = "REVIEW_STARTED"
    NOTE_ADDED = "NOTE_ADDED"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class CaseResolutionCode(StrEnum):
    LIQUIDITY_COORDINATED = "LIQUIDITY_COORDINATED"
    DEMAND_SPIKE_CONFIRMED = "DEMAND_SPIKE_CONFIRMED"
    DATA_FEED_RESTORED = "DATA_FEED_RESTORED"
    BALANCE_VERIFIED = "BALANCE_VERIFIED"
    ESCALATED_OUTSIDE_SYSTEM = "ESCALATED_OUTSIDE_SYSTEM"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
    OTHER = "OTHER"


class NoteVisibility(StrEnum):
    INTERNAL = "INTERNAL"
    PROVIDER_SCOPED = "PROVIDER_SCOPED"
    CROSS_PROVIDER_REDACTED = "CROSS_PROVIDER_REDACTED"


class CaseSchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class CasePrincipal(CaseSchema):
    actor_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)

    role: RoutingRole

    provider_id: ProviderID | None = None


class CaseNote(CaseSchema):
    note_id: str

    author: CasePrincipal
    body: str = Field(min_length=1)

    visibility: NoteVisibility

    created_at: datetime


class CaseResolution(CaseSchema):
    code: CaseResolutionCode

    summary: str = Field(min_length=1)

    resolved_by: CasePrincipal
    resolved_at: datetime


class CaseAuditEntry(CaseSchema):
    audit_id: str
    case_id: str

    action: CaseActionType

    actor: CasePrincipal | None = None

    from_status: CaseStatus | None = None
    to_status: CaseStatus | None = None

    note: str | None = None

    details: dict[str, str] = Field(
        default_factory=dict
    )

    occurred_at: datetime


class CoordinationCase(CaseSchema):
    case_id: str
    incident_id: str

    agent_id: str
    area: str

    provider_scope: list[ProviderID]

    source_incident_status: IncidentStatus
    priority: IncidentPriority

    status: CaseStatus

    receiver_role: RoutingRole
    responsible_stakeholder: RoutingRole

    case_owner: CasePrincipal | None = None

    acknowledged_by: CasePrincipal | None = None
    acknowledged_at: datetime | None = None

    escalation_target_role: RoutingRole | None = None
    escalated_at: datetime | None = None

    resolution: CaseResolution | None = None

    recommended_next_step: str

    human_review_required: bool

    safe_fallback_active: bool
    safe_fallback_reason: str | None = None

    advisory_only: bool = True

    automated_financial_action_allowed: bool = False

    provider_boundary_notice: str = (
        "Provider balances, evidence and operational authority "
        "must remain logically separate."
    )

    notes: list[CaseNote] = Field(
        default_factory=list
    )

    history: list[CaseAuditEntry] = Field(
        default_factory=list
    )

    created_at: datetime
    updated_at: datetime


class CaseSummary(CaseSchema):
    as_of: datetime

    total_cases: int

    open: int
    acknowledged: int
    assigned: int
    in_review: int
    escalated: int
    resolved: int
    closed: int

    safe_fallback_cases: int

    cases: list[CoordinationCase]


class AcknowledgeCaseRequest(CaseSchema):
    actor: CasePrincipal
    note: str | None = Field(
        default=None,
        max_length=1000,
    )


class AssignCaseRequest(CaseSchema):
    assigned_by: CasePrincipal
    owner: CasePrincipal

    note: str | None = Field(
        default=None,
        max_length=1000,
    )


class StartReviewRequest(CaseSchema):
    actor: CasePrincipal

    note: str | None = Field(
        default=None,
        max_length=1000,
    )


class AddCaseNoteRequest(CaseSchema):
    author: CasePrincipal

    body: str = Field(
        min_length=1,
        max_length=3000,
    )

    visibility: NoteVisibility = (
        NoteVisibility.INTERNAL
    )


class EscalateCaseRequest(CaseSchema):
    actor: CasePrincipal

    target_role: RoutingRole

    reason: str = Field(
        min_length=1,
        max_length=2000,
    )


class ResolveCaseRequest(CaseSchema):
    actor: CasePrincipal

    resolution_code: CaseResolutionCode

    summary: str = Field(
        min_length=1,
        max_length=3000,
    )


class CloseCaseRequest(CaseSchema):
    actor: CasePrincipal

    note: str | None = Field(
        default=None,
        max_length=1000,
    )