from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import ProviderID


class IncidentType(StrEnum):
    LIQUIDITY_PRESSURE = "LIQUIDITY_PRESSURE"
    UNUSUAL_ACTIVITY = "UNUSUAL_ACTIVITY"
    COMBINED_PRIORITY = "COMBINED_PRIORITY"
    DATA_QUALITY = "DATA_QUALITY"


class IncidentPriority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class IncidentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLEARED = "CLEARED"


class RoutingRole(StrEnum):
    AGENT = "AGENT"
    FIELD_OFFICER = "FIELD_OFFICER"
    PROVIDER_OPERATIONS = "PROVIDER_OPERATIONS"
    RISK_REVIEWER = "RISK_REVIEWER"


class EvidenceSource(StrEnum):
    LIQUIDITY = "LIQUIDITY"
    ANOMALY = "ANOMALY"
    DATA_QUALITY = "DATA_QUALITY"
    CONTEXT = "CONTEXT"


class IncidentSchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class IncidentEvidence(IncidentSchema):
    source: EvidenceSource
    code: str
    message: str

    value: str | None = None
    points: int | None = None

    transaction_ids: list[str] = Field(default_factory=list)


class OperationalIncident(IncidentSchema):
    incident_id: str
    incident_key: str

    agent_id: str
    area: str

    provider_scope: list[ProviderID]

    incident_type: IncidentType
    priority: IncidentPriority
    status: IncidentStatus

    title: str
    summary: str

    confidence: float = Field(ge=0, le=1)

    receiver_role: RoutingRole
    responsible_stakeholder: RoutingRole

    human_review_required: bool
    strong_recommendation_allowed: bool

    recommended_next_step: str

    evidence: list[IncidentEvidence] = Field(
        default_factory=list
    )

    uncertainty: list[str] = Field(default_factory=list)

    alternative_explanations: list[str] = Field(
        default_factory=list
    )

    created_at: datetime
    updated_at: datetime
    cleared_at: datetime | None = None

    occurrences: int = Field(default=1, ge=1)


class IncidentSummary(IncidentSchema):
    as_of: datetime

    total_incidents: int
    active_incidents: int
    cleared_incidents: int

    p1: int
    p2: int
    p3: int
    p4: int

    liquidity_incidents: int
    unusual_activity_incidents: int
    combined_incidents: int
    data_quality_incidents: int

    incidents: list[OperationalIncident]