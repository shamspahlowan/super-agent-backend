from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.ingestion.canonical_event import ProviderID

from enum import StrEnum
from typing import Any


class SimulationConfigurationError(RuntimeError):
    """Raised when simulation configuration is invalid."""


class SimulationConfigModel(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )


class ProviderProfile(SimulationConfigModel):
    provider_id: ProviderID

    weight: float = Field(gt=0)

    cash_out_probability: float = Field(
        ge=0,
        le=1,
    )


class TimeBand(SimulationConfigModel):
    start: time
    end: time

    multiplier: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "TimeBand":
        if self.end <= self.start:
            raise ValueError(
                "Time-band end must be later than start."
            )

        return self


class AmountBand(SimulationConfigModel):
    min_amount: Decimal = Field(gt=0)
    max_amount: Decimal = Field(gt=0)

    weight: float = Field(gt=0)

    round_to: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_amount_range(self) -> "AmountBand":
        if self.max_amount <= self.min_amount:
            raise ValueError(
                "Amount-band maximum must exceed minimum."
            )

        return self


class ProviderOpening(SimulationConfigModel):
    provider_id: ProviderID
    balance: Decimal = Field(gt=0)


class AgentSimulationProfile(SimulationConfigModel):
    agent_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)

    area: str = Field(min_length=1)
    district: str = Field(min_length=1)

    base_arrivals_per_hour: float = Field(gt=0)

    account_pool_size: int = Field(
        ge=20,
        le=10000,
    )

    opening_shared_cash: Decimal = Field(gt=0)

    provider_openings: list[ProviderOpening]

class ScenarioKind(StrEnum):
    HIDDEN_PROVIDER_SHORTAGE = (
        "HIDDEN_PROVIDER_SHORTAGE"
    )

    REPEATED_CASH_OUT_CLUSTER = (
        "REPEATED_CASH_OUT_CLUSTER"
    )

    LEGITIMATE_DEMAND_SPIKE = (
        "LEGITIMATE_DEMAND_SPIKE"
    )

    CROSS_PROVIDER_LINKED_ACTIVITY = (
        "CROSS_PROVIDER_LINKED_ACTIVITY"
    )

    FEED_DELAY_RECOVERY = (
        "FEED_DELAY_RECOVERY"
    )

    BALANCE_CONFLICT = (
        "BALANCE_CONFLICT"
    )


class ScenarioConfig(SimulationConfigModel):
    scenario_id: str = Field(min_length=1)

    kind: ScenarioKind
    enabled: bool = True

    agent_id: str = Field(min_length=1)
    provider_id: ProviderID | None = None

    start_time: datetime
    end_time: datetime

    expected_detection_delay_minutes: int = Field(
        default=5,
        ge=0,
    )

    parameters: dict[str, Any] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def validate_scenario(
        self,
    ) -> "ScenarioConfig":
        if (
            self.start_time.tzinfo is None
            or self.start_time.utcoffset() is None
        ):
            raise ValueError(
                "Scenario start_time must include timezone."
            )

        if (
            self.end_time.tzinfo is None
            or self.end_time.utcoffset() is None
        ):
            raise ValueError(
                "Scenario end_time must include timezone."
            )

        if self.end_time <= self.start_time:
            raise ValueError(
                "Scenario end_time must be later than start_time."
            )

        return self


class SimulationConfig(SimulationConfigModel):
    run_id: str = Field(min_length=1)
    seed: int

    heartbeat_interval_minutes: int = Field(
        default=10,
        gt=0,
        le=60,
    )

    scenarios: list[ScenarioConfig] = Field(
        default_factory=list
    )

    start_time: datetime
    end_time: datetime

    network_failure_rate: float = Field(
        ge=0,
        le=0.25,
    )

    repeat_account_probability: float = Field(
        ge=0,
        le=1,
    )

    providers: list[ProviderProfile]
    time_bands: list[TimeBand]
    amount_bands: list[AmountBand]
    agents: list[AgentSimulationProfile]

    @model_validator(mode="after")
    def validate_configuration(
        self,
    ) -> "SimulationConfig":
        if (
            self.start_time.tzinfo is None
            or self.start_time.utcoffset() is None
        ):
            raise ValueError(
                "start_time must include timezone information."
            )

        if (
            self.end_time.tzinfo is None
            or self.end_time.utcoffset() is None
        ):
            raise ValueError(
                "end_time must include timezone information."
            )

        if self.end_time <= self.start_time:
            raise ValueError(
                "end_time must be later than start_time."
            )

        provider_ids = [
            provider.provider_id
            for provider in self.providers
        ]

        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError(
                "Provider IDs must be unique."
            )

        agent_ids = [
            agent.agent_id
            for agent in self.agents
        ]

        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError(
                "Agent IDs must be unique."
            )

        expected_provider_ids = set(provider_ids)

        for agent in self.agents:
            opening_provider_ids = {
                opening.provider_id
                for opening in agent.provider_openings
            }

            if opening_provider_ids != expected_provider_ids:
                raise ValueError(
                    f"Agent {agent.agent_id} must have one "
                    "opening balance for every configured provider."
                )
            

        agent_ids_set = set(agent_ids)

        scenario_ids = [
            scenario.scenario_id
            for scenario in self.scenarios
        ]

        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError(
                "Scenario IDs must be unique."
            )

        for scenario in self.scenarios:
            if scenario.agent_id not in agent_ids_set:
                raise ValueError(
                    f"Scenario {scenario.scenario_id} references "
                    f"unknown agent {scenario.agent_id}."
                )

            if (
                scenario.provider_id is not None
                and scenario.provider_id
                not in expected_provider_ids
            ):
                raise ValueError(
                    f"Scenario {scenario.scenario_id} references "
                    "an unknown provider."
                )

            if (
                scenario.start_time < self.start_time
                or scenario.end_time > self.end_time
            ):
                raise ValueError(
                    f"Scenario {scenario.scenario_id} must remain "
                    "inside the configured simulation period."
                )

        return self

    def provider_profile(
        self,
        provider_id: ProviderID,
    ) -> ProviderProfile:
        for provider in self.providers:
            if provider.provider_id == provider_id:
                return provider

        raise SimulationConfigurationError(
            f"Unknown provider profile: {provider_id.value}"
        )

    def demand_multiplier_at(
        self,
        timestamp: datetime,
    ) -> float:
        local_time = timestamp.timetz().replace(
            tzinfo=None
        )

        for band in self.time_bands:
            if band.start <= local_time < band.end:
                return band.multiplier

        return 1.0


def load_simulation_config(
    path: str | Path,
) -> SimulationConfig:
    config_path = Path(path)

    if not config_path.exists():
        raise SimulationConfigurationError(
            f"Simulation configuration not found: {config_path}"
        )

    try:
        with config_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            payload = yaml.safe_load(file)

    except yaml.YAMLError as exc:
        raise SimulationConfigurationError(
            f"Invalid YAML in {config_path}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise SimulationConfigurationError(
            "Simulation configuration must be a YAML object."
        )

    try:
        return SimulationConfig.model_validate(payload)

    except Exception as exc:
        raise SimulationConfigurationError(
            f"Invalid simulation configuration: {exc}"
        ) from exc