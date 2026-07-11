from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.ingestion.canonical_event import (
    ProviderID,
    TransactionType,
)
from app.simulation.config import ScenarioConfig


class ScenarioGroundTruthLabel(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    scenario_id: str
    agent_id: str

    provider_id: ProviderID | None = None

    label: str

    start_time: datetime
    end_time: datetime

    expected_detection_after: datetime

    transaction_id: str | None = None


@dataclass(frozen=True)
class TransactionCandidate:
    timestamp: datetime

    agent_id: str
    provider_id: ProviderID
    account_id: str

    transaction_type: TransactionType
    amount: Decimal

    channel: str = "AGENT"

    source: str = "BASELINE"

    scenario_id: str | None = None
    scenario_label: str | None = None

    allow_network_failure: bool = True


class ScenarioPlugin(ABC):
    def __init__(
        self,
        config: ScenarioConfig,
    ) -> None:
        self.config = config

    @property
    def scenario_id(self) -> str:
        return self.config.scenario_id

    def on_start(
        self,
        runtime,
    ) -> None:
        return None

    def before_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> None:
        return None

    def candidates_for_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> list[TransactionCandidate]:
        return []

    def after_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> None:
        return None

    def on_end(
        self,
        runtime,
    ) -> None:
        return None

    def suppress_heartbeat(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
        timestamp: datetime,
    ) -> bool:
        return False

    def is_active(
        self,
        minute: datetime,
    ) -> bool:
        return (
            self.config.enabled
            and self.config.start_time
            <= minute
            < self.config.end_time
        )

    def elapsed_minutes(
        self,
        minute: datetime,
    ) -> int:
        return int(
            (
                minute - self.config.start_time
            ).total_seconds()
            // 60
        )

    def expected_detection_after(
        self,
    ) -> datetime:
        return (
            self.config.start_time
            + timedelta(
                minutes=(
                    self.config
                    .expected_detection_delay_minutes
                )
            )
        )

    def parameter_int(
        self,
        key: str,
        default: int,
    ) -> int:
        return int(
            self.config.parameters.get(
                key,
                default,
            )
        )

    def parameter_float(
        self,
        key: str,
        default: float,
    ) -> float:
        return float(
            self.config.parameters.get(
                key,
                default,
            )
        )

    def parameter_decimal(
        self,
        key: str,
        default: str,
    ) -> Decimal:
        return Decimal(
            str(
                self.config.parameters.get(
                    key,
                    default,
                )
            )
        )

    def ground_truth_label(
        self,
    ) -> ScenarioGroundTruthLabel:
        return ScenarioGroundTruthLabel(
            scenario_id=self.config.scenario_id,
            agent_id=self.config.agent_id,
            provider_id=self.config.provider_id,
            label=self.config.kind.value,
            start_time=self.config.start_time,
            end_time=self.config.end_time,
            expected_detection_after=(
                self.expected_detection_after()
            ),
            transaction_id=None,
        )