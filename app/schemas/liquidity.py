from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import ProviderID
from app.schemas.data_quality import FeedHealthStatus


class LiquidityStatus(StrEnum):
    SAFE = "SAFE"
    WATCH = "WATCH"
    CRITICAL = "CRITICAL"
    DEPLETED = "DEPLETED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class LiquidityResource(StrEnum):
    SHARED_CASH = "SHARED_CASH"
    PROVIDER_EMONEY = "PROVIDER_EMONEY"


class LiquiditySchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class LiquidityEvidence(LiquiditySchema):
    code: str
    message: str
    value: str | None = None


class ResourceLiquidityForecast(LiquiditySchema):
    agent_id: str
    provider_id: ProviderID | None = None

    resource: LiquidityResource
    status: LiquidityStatus

    current_balance: Decimal
    opening_balance: Decimal
    safety_reserve: Decimal

    forecast_available: bool

    net_depletion_per_minute: Decimal = Decimal("0")
    observed_window_minutes: float = Field(ge=0)
    successful_transactions: int = Field(ge=0)

    minutes_to_safety_threshold: float | None = None
    minutes_to_depletion: float | None = None

    safety_threshold_at: datetime | None = None
    projected_depletion_at: datetime | None = None

    confidence: float = Field(ge=0, le=1)

    data_trust_status: FeedHealthStatus
    can_issue_strong_recommendation: bool

    driver_provider_id: ProviderID | None = None

    evidence: list[LiquidityEvidence] = Field(
        default_factory=list
    )

    recommendation: str


class AgentLiquidityForecast(LiquiditySchema):
    agent_id: str
    as_of: datetime

    overall_status: LiquidityStatus
    aggregate_confidence: float = Field(ge=0, le=1)

    shared_cash: ResourceLiquidityForecast
    provider_forecasts: list[ResourceLiquidityForecast]

    hidden_provider_shortage: bool

    most_urgent_resource: LiquidityResource | None = None
    most_urgent_provider: ProviderID | None = None

    headline: str
    warnings: list[str] = Field(default_factory=list)


class LiquiditySummary(LiquiditySchema):
    as_of: datetime
    total_agents: int

    safe: int
    watch: int
    critical: int
    depleted: int
    insufficient_data: int

    hidden_provider_shortages: int

    agents: list[AgentLiquidityForecast]