from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import (
    ProviderID,
    ReplayEventType,
)


class ReplaySchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ReplayStatus(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"


class ReplayStepRequest(ReplaySchema):
    event_count: int = Field(default=1, ge=1, le=500)


class ReplayAdvanceRequest(ReplaySchema):
    minutes: int = Field(ge=1, le=600)


class ProcessedReplayEvent(ReplaySchema):
    event_id: str
    event_type: ReplayEventType
    timestamp: datetime

    agent_id: str
    provider_id: ProviderID

    applied: bool
    action: str
    details: str


class ReplayState(ReplaySchema):
    status: ReplayStatus

    simulation_start: datetime
    simulation_end: datetime
    simulation_time: datetime

    total_events: int
    processed_events: int
    remaining_events: int

    processed_transactions: int
    processed_feed_events: int

    completion_percentage: float

    next_event_id: str | None = None
    next_event_time: datetime | None = None

    last_event: ProcessedReplayEvent | None = None


class ReplayBatchResult(ReplaySchema):
    state: ReplayState
    events: list[ProcessedReplayEvent]