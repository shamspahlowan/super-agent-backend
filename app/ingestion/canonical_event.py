from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class ProviderID(StrEnum):
    BKASH = "BKASH"
    NAGAD = "NAGAD"
    ROCKET = "ROCKET"


class TransactionType(StrEnum):
    CASH_IN = "CASH_IN"
    CASH_OUT = "CASH_OUT"


class TransactionStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ResourceType(StrEnum):
    SHARED_CASH = "SHARED_CASH"
    PROVIDER_EMONEY = "PROVIDER_EMONEY"


class FeedEventType(StrEnum):
    HEARTBEAT = "HEARTBEAT"
    FEED_DELAY = "FEED_DELAY"
    FEED_RECOVERED = "FEED_RECOVERED"
    BALANCE_CONFLICT = "BALANCE_CONFLICT"


class ReplayEventType(StrEnum):
    TRANSACTION = "TRANSACTION"
    FEED_EVENT = "FEED_EVENT"


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


def validate_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include timezone information.")

    return value


class AgentRecord(StrictModel):
    agent_id: str = Field(min_length=1, max_length=50)
    agent_name: str = Field(min_length=1, max_length=150)
    area: str = Field(min_length=1, max_length=100)
    district: str = Field(min_length=1, max_length=100)


class OpeningBalance(StrictModel):
    agent_id: str
    provider_id: ProviderID | None = None
    resource_type: ResourceType
    opening_balance: Decimal = Field(ge=0)
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_have_timezone(
        cls,
        value: datetime,
    ) -> datetime:
        return validate_timezone(value)

    @model_validator(mode="after")
    def validate_resource_scope(self) -> "OpeningBalance":
        if (
            self.resource_type == ResourceType.SHARED_CASH
            and self.provider_id is not None
        ):
            raise ValueError(
                "Shared cash must not have a provider_id."
            )

        if (
            self.resource_type == ResourceType.PROVIDER_EMONEY
            and self.provider_id is None
        ):
            raise ValueError(
                "Provider e-money requires a provider_id."
            )

        return self


class TransactionEvent(StrictModel):
    transaction_id: str = Field(min_length=1, max_length=100)
    timestamp: datetime

    agent_id: str = Field(min_length=1, max_length=50)
    provider_id: ProviderID
    account_id: str = Field(min_length=1, max_length=100)

    transaction_type: TransactionType
    amount: Decimal = Field(gt=0)
    status: TransactionStatus
    channel: str = Field(default="AGENT", min_length=1, max_length=50)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_have_timezone(
        cls,
        value: datetime,
    ) -> datetime:
        return validate_timezone(value)


class FeedEvent(StrictModel):
    feed_event_id: str = Field(min_length=1, max_length=100)
    timestamp: datetime

    agent_id: str = Field(min_length=1, max_length=50)
    provider_id: ProviderID
    event_type: FeedEventType

    delay_minutes: int = Field(default=0, ge=0)
    reported_balance: Decimal | None = Field(default=None, ge=0)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_have_timezone(
        cls,
        value: datetime,
    ) -> datetime:
        return validate_timezone(value)

    @model_validator(mode="after")
    def validate_event_details(self) -> "FeedEvent":
        if (
            self.event_type == FeedEventType.FEED_DELAY
            and self.delay_minutes <= 0
        ):
            raise ValueError(
                "FEED_DELAY must include delay_minutes greater than zero."
            )

        if (
            self.event_type == FeedEventType.BALANCE_CONFLICT
            and self.reported_balance is None
        ):
            raise ValueError(
                "BALANCE_CONFLICT requires reported_balance."
            )

        return self


class ContextEvent(StrictModel):
    context_id: str
    area: str
    event_type: str

    start_time: datetime
    end_time: datetime

    expected_demand_multiplier: float = Field(gt=0)
    description: str

    @field_validator("start_time", "end_time")
    @classmethod
    def timestamps_must_have_timezone(
        cls,
        value: datetime,
    ) -> datetime:
        return validate_timezone(value)

    @model_validator(mode="after")
    def validate_time_range(self) -> "ContextEvent":
        if self.end_time <= self.start_time:
            raise ValueError(
                "Context event end_time must be after start_time."
            )

        return self


class ReplayEvent(StrictModel):
    event_id: str
    event_type: ReplayEventType
    timestamp: datetime

    agent_id: str
    provider_id: ProviderID

    payload: TransactionEvent | FeedEvent

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_have_timezone(
        cls,
        value: datetime,
    ) -> datetime:
        return validate_timezone(value)

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude_none=True,
        )