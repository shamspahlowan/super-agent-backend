from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import ProviderID


class ValidationStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


class EvaluationSchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ValidationCheck(EvaluationSchema):
    name: str
    status: ValidationStatus

    expected: str
    observed: str

    message: str


class ScenarioValidationResult(EvaluationSchema):
    scenario_id: str
    label: str

    agent_id: str
    provider_id: ProviderID | None = None

    expected_detection_after: datetime

    first_detected_at: datetime | None = None
    detection_delay_minutes: float | None = None

    status: ValidationStatus
    passed: bool

    checks: list[ValidationCheck] = Field(
        default_factory=list
    )

    observations: dict[str, Any] = Field(
        default_factory=dict
    )


class ValidationMetric(EvaluationSchema):
    name: str

    value: float
    unit: str

    target: str

    status: ValidationStatus
    passed: bool

    description: str


class SimulationValidationReport(EvaluationSchema):
    run_id: str
    seed: int

    generated_at: datetime

    synthetic_directory: str
    ground_truth_directory: str

    public_invariants: list[ValidationCheck]

    scenario_results: list[
        ScenarioValidationResult
    ]

    metrics: list[ValidationMetric]

    total_replay_events: int
    replay_duration_seconds: float

    overall_status: ValidationStatus
    overall_passed: bool

    notes: list[str] = Field(
        default_factory=list
    )