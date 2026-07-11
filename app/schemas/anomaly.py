from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import ProviderID


class AnomalyBand(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AnomalyCategory(StrEnum):
    NORMAL_ACTIVITY = "NORMAL_ACTIVITY"
    LEGITIMATE_DEMAND_SPIKE = "LEGITIMATE_DEMAND_SPIKE"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    CROSS_PROVIDER_REVIEW = "CROSS_PROVIDER_REVIEW"


class AnomalySchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class AnomalyFactor(AnomalySchema):
    code: str
    points: int

    description: str
    value: str | None = None

    transaction_ids: list[str] = Field(
        default_factory=list
    )


class AnomalyAssessment(AnomalySchema):
    agent_id: str

    provider_scope: list[ProviderID]

    as_of: datetime
    window_start: datetime

    score: int = Field(ge=0, le=100)
    band: AnomalyBand
    category: AnomalyCategory

    requires_human_review: bool

    transaction_count: int = Field(ge=0)
    successful_transactions: int = Field(ge=0)
    failed_transactions: int = Field(ge=0)

    total_successful_amount: Decimal = Decimal("0")

    unique_accounts: int = Field(ge=0)
    unique_providers: int = Field(ge=0)

    repeated_amount_ratio: float = Field(ge=0, le=1)
    dominant_account_ratio: float = Field(ge=0, le=1)

    confidence: float = Field(ge=0, le=1)

    active_contexts: list[str] = Field(
        default_factory=list
    )

    factors: list[AnomalyFactor] = Field(
        default_factory=list
    )

    evidence_transaction_ids: list[str] = Field(
        default_factory=list
    )

    alternative_explanations: list[str] = Field(
        default_factory=list
    )

    summary: str
    safe_next_step: str


class AnomalySummary(AnomalySchema):
    as_of: datetime

    total_agents: int

    low: int
    medium: int
    high: int

    requires_review: int
    legitimate_demand_spikes: int
    cross_provider_reviews: int

    assessments: list[AnomalyAssessment]