from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import (
    FeedEventType,
    ProviderID,
)


class FeedHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"


class DataQualitySchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class FeedHealthView(DataQualitySchema):
    agent_id: str
    provider_id: ProviderID

    status: FeedHealthStatus

    confidence: float = Field(ge=0, le=1)

    last_signal_at: datetime
    last_event_at: datetime

    last_event_type: FeedEventType | None = None

    data_age_minutes: float = Field(ge=0)
    explicit_delay_minutes: int = Field(default=0, ge=0)

    reported_balance: Decimal | None = None
    calculated_balance: Decimal | None = None
    balance_difference: Decimal | None = None

    reasons: list[str] = Field(default_factory=list)

    safe_fallback: str

    can_issue_strong_recommendation: bool


class FeedHealthSummary(DataQualitySchema):
    as_of: datetime

    total_feeds: int

    healthy: int
    stale: int
    missing: int
    conflicting: int

    average_confidence: float = Field(ge=0, le=1)

    fallback_required: bool

    feeds: list[FeedHealthView]