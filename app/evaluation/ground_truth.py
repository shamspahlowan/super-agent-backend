from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import ProviderID


class GroundTruthError(RuntimeError):
    """Raised when hidden ground truth is invalid."""


class GroundTruthScenario(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    scenario_id: str
    label: str

    agent_id: str
    provider_id: ProviderID | None = None

    start_time: datetime
    end_time: datetime

    expected_detection_after: datetime

    transaction_ids: list[str] = Field(
        default_factory=list
    )


class GroundTruthBundle(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )

    manifest: dict

    scenarios: list[GroundTruthScenario]


def parse_datetime(
    value: str,
    *,
    field_name: str,
) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value
        )

    except ValueError as exc:
        raise GroundTruthError(
            f"Invalid datetime for {field_name}: {value}"
        ) from exc

    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
    ):
        raise GroundTruthError(
            f"{field_name} must include timezone information."
        )

    return parsed


def load_ground_truth(
    ground_truth_dir: str | Path,
) -> GroundTruthBundle:
    directory = Path(ground_truth_dir)

    labels_path = (
        directory / "scenario_labels.csv"
    )

    manifest_path = (
        directory / "scenario_manifest.json"
    )

    if not labels_path.exists():
        raise GroundTruthError(
            f"Scenario labels not found: {labels_path}"
        )

    if not manifest_path.exists():
        raise GroundTruthError(
            f"Scenario manifest not found: {manifest_path}"
        )

    with manifest_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        manifest = json.load(file)

    grouped_rows: dict[
        str,
        list[dict[str, str]],
    ] = defaultdict(list)

    with labels_path.open(
        "r",
        newline="",
        encoding="utf-8",
    ) as file:
        reader = csv.DictReader(file)

        required_fields = {
            "scenario_id",
            "agent_id",
            "provider_id",
            "label",
            "start_time",
            "end_time",
            "expected_detection_after",
            "transaction_id",
        }

        if reader.fieldnames is None:
            raise GroundTruthError(
                "scenario_labels.csv has no header."
            )

        missing = required_fields.difference(
            reader.fieldnames
        )

        if missing:
            raise GroundTruthError(
                "scenario_labels.csv is missing fields: "
                + ", ".join(sorted(missing))
            )

        for row in reader:
            scenario_id = (
                row["scenario_id"].strip()
            )

            if not scenario_id:
                raise GroundTruthError(
                    "Ground-truth row has no scenario_id."
                )

            grouped_rows[
                scenario_id
            ].append(row)

    scenarios: list[
        GroundTruthScenario
    ] = []

    for scenario_id, rows in sorted(
        grouped_rows.items()
    ):
        scenario_level_rows = [
            row
            for row in rows
            if not row[
                "transaction_id"
            ].strip()
        ]

        if len(scenario_level_rows) != 1:
            raise GroundTruthError(
                f"Scenario {scenario_id} must have exactly one "
                "scenario-level ground-truth row."
            )

        main_row = scenario_level_rows[0]

        provider_text = (
            main_row["provider_id"].strip()
        )

        provider_id = (
            ProviderID(provider_text)
            if provider_text
            else None
        )

        transaction_ids = sorted(
            {
                row["transaction_id"].strip()
                for row in rows
                if row[
                    "transaction_id"
                ].strip()
            }
        )

        scenarios.append(
            GroundTruthScenario(
                scenario_id=scenario_id,
                label=main_row["label"],
                agent_id=(
                    main_row["agent_id"]
                ),
                provider_id=provider_id,
                start_time=parse_datetime(
                    main_row["start_time"],
                    field_name=(
                        f"{scenario_id}.start_time"
                    ),
                ),
                end_time=parse_datetime(
                    main_row["end_time"],
                    field_name=(
                        f"{scenario_id}.end_time"
                    ),
                ),
                expected_detection_after=(
                    parse_datetime(
                        main_row[
                            "expected_detection_after"
                        ],
                        field_name=(
                            f"{scenario_id}."
                            "expected_detection_after"
                        ),
                    )
                ),
                transaction_ids=transaction_ids,
            )
        )

    if not scenarios:
        raise GroundTruthError(
            "No scenarios were found in ground truth."
        )

    return GroundTruthBundle(
        manifest=manifest,
        scenarios=scenarios,
    )