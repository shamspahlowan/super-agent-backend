from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.simulation.generator import SimulationRun


class SimulationExportError(RuntimeError):
    """Raised when generated simulation data cannot be exported."""


def serialize_value(value: Any) -> Any:
    """
    Convert Python, Pydantic and domain values into CSV/JSON-safe values.
    """

    if value is None:
        return ""

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, BaseModel):
        return serialize_value(
            value.model_dump(mode="python")
        )

    if isinstance(value, dict):
        return {
            str(key): serialize_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            serialize_value(item)
            for item in value
        ]

    return value


def model_to_row(
    model: BaseModel,
) -> dict[str, Any]:
    """
    Convert a Pydantic model into a flat CSV-compatible dictionary.
    """

    payload = model.model_dump(
        mode="python"
    )

    return {
        key: serialize_value(value)
        for key, value in payload.items()
    }


def write_csv(
    *,
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    """
    Write one CSV file with a stable column order.

    A header is written even when rows is empty.
    """

    if not fieldnames:
        raise SimulationExportError(
            f"No CSV field names supplied for {path}."
        )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    try:
        with temporary_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=fieldnames,
                extrasaction="raise",
            )

            writer.writeheader()

            for row in rows:
                writer.writerow(
                    {
                        field: row.get(field, "")
                        for field in fieldnames
                    }
                )

        temporary_path.replace(path)

    except Exception as exc:
        if temporary_path.exists():
            temporary_path.unlink()

        raise SimulationExportError(
            f"Failed to write CSV file {path}: {exc}"
        ) from exc


def write_json(
    *,
    path: Path,
    payload: dict[str, Any],
) -> None:
    """
    Write formatted JSON using an atomic temporary-file replacement.
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = path.with_suffix(
        path.suffix + ".tmp"
    )

    try:
        with temporary_path.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                serialize_value(payload),
                file,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )

            file.write("\n")

        temporary_path.replace(path)

    except Exception as exc:
        if temporary_path.exists():
            temporary_path.unlink()

        raise SimulationExportError(
            f"Failed to write JSON file {path}: {exc}"
        ) from exc


class SimulationExporter:
    """
    Export one simulation run into public synthetic data and
    separate hidden ground-truth files.

    Public directory:
        agents.csv
        opening_balances.csv
        transactions.csv
        feed_events.csv
        context_events.csv

    Hidden ground-truth directory:
        transaction_failures.csv
        scenario_labels.csv
        scenario_manifest.json
    """

    AGENT_FIELDS = [
        "agent_id",
        "agent_name",
        "area",
        "district",
    ]

    OPENING_BALANCE_FIELDS = [
        "agent_id",
        "provider_id",
        "resource_type",
        "opening_balance",
        "timestamp",
    ]

    TRANSACTION_FIELDS = [
        "transaction_id",
        "timestamp",
        "agent_id",
        "provider_id",
        "account_id",
        "transaction_type",
        "amount",
        "status",
        "channel",
    ]

    FEED_EVENT_FIELDS = [
        "feed_event_id",
        "timestamp",
        "agent_id",
        "provider_id",
        "event_type",
        "delay_minutes",
        "reported_balance",
    ]

    CONTEXT_EVENT_FIELDS = [
        "context_id",
        "area",
        "event_type",
        "start_time",
        "end_time",
        "expected_demand_multiplier",
        "description",
    ]

    TRANSACTION_FAILURE_FIELDS = [
        "transaction_id",
        "failure_reason",
    ]

    SCENARIO_LABEL_FIELDS = [
        "scenario_id",
        "agent_id",
        "provider_id",
        "label",
        "start_time",
        "end_time",
        "expected_detection_after",
        "transaction_id",
    ]

    def __init__(
        self,
        *,
        synthetic_dir: str | Path,
        ground_truth_dir: str | Path,
    ) -> None:
        self.synthetic_dir = Path(
            synthetic_dir
        )

        self.ground_truth_dir = Path(
            ground_truth_dir
        )

    def export(
        self,
        run: SimulationRun,
    ) -> None:
        """
        Export all files belonging to one completed simulation run.
        """

        self._validate_run(run)

        self.synthetic_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.ground_truth_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._export_agents(run)
        self._export_opening_balances(run)
        self._export_transactions(run)
        self._export_feed_events(run)
        self._export_context_events(run)

        self._export_transaction_failures(run)
        self._export_scenario_labels(run)
        self._export_manifest(run)

    def _export_agents(
        self,
        run: SimulationRun,
    ) -> None:
        rows = [
            model_to_row(agent)
            for agent in run.agents
        ]

        write_csv(
            path=self.synthetic_dir / "agents.csv",
            fieldnames=self.AGENT_FIELDS,
            rows=rows,
        )

    def _export_opening_balances(
        self,
        run: SimulationRun,
    ) -> None:
        rows = [
            model_to_row(balance)
            for balance in run.opening_balances
        ]

        write_csv(
            path=(
                self.synthetic_dir
                / "opening_balances.csv"
            ),
            fieldnames=self.OPENING_BALANCE_FIELDS,
            rows=rows,
        )

    def _export_transactions(
        self,
        run: SimulationRun,
    ) -> None:
        rows = [
            model_to_row(transaction)
            for transaction in run.transactions
        ]

        # Public transaction rows intentionally contain no scenario_id.
        for row in rows:
            row.pop("scenario_id", None)
            row.pop("scenario_label", None)

        write_csv(
            path=(
                self.synthetic_dir
                / "transactions.csv"
            ),
            fieldnames=self.TRANSACTION_FIELDS,
            rows=rows,
        )

    def _export_feed_events(
        self,
        run: SimulationRun,
    ) -> None:
        rows = [
            model_to_row(event)
            for event in run.feed_events
        ]

        write_csv(
            path=(
                self.synthetic_dir
                / "feed_events.csv"
            ),
            fieldnames=self.FEED_EVENT_FIELDS,
            rows=rows,
        )

    def _export_context_events(
        self,
        run: SimulationRun,
    ) -> None:
        rows = [
            model_to_row(event)
            for event in run.context_events
        ]

        write_csv(
            path=(
                self.synthetic_dir
                / "context_events.csv"
            ),
            fieldnames=self.CONTEXT_EVENT_FIELDS,
            rows=rows,
        )

    def _export_transaction_failures(
        self,
        run: SimulationRun,
    ) -> None:
        rows = [
            {
                "transaction_id": transaction_id,
                "failure_reason": failure_reason,
            }
            for transaction_id, failure_reason
            in sorted(
                run.failure_reasons.items()
            )
        ]

        write_csv(
            path=(
                self.ground_truth_dir
                / "transaction_failures.csv"
            ),
            fieldnames=(
                self.TRANSACTION_FAILURE_FIELDS
            ),
            rows=rows,
        )

    def _export_scenario_labels(
        self,
        run: SimulationRun,
    ) -> None:
        rows = [
            model_to_row(label)
            for label in run.scenario_labels
        ]

        rows.sort(
            key=lambda row: (
                row.get("scenario_id", ""),
                row.get("start_time", ""),
                row.get("transaction_id", ""),
            )
        )

        write_csv(
            path=(
                self.ground_truth_dir
                / "scenario_labels.csv"
            ),
            fieldnames=self.SCENARIO_LABEL_FIELDS,
            rows=rows,
        )

    def _export_manifest(
        self,
        run: SimulationRun,
    ) -> None:
        manifest = {
            **run.manifest,
            "run_id": run.run_id,
            "seed": run.seed,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "public_data_directory": (
                self.synthetic_dir
            ),
            "ground_truth_directory": (
                self.ground_truth_dir
            ),
            "final_state": run.final_state,
            "exported_files": {
                "public": [
                    "agents.csv",
                    "opening_balances.csv",
                    "transactions.csv",
                    "feed_events.csv",
                    "context_events.csv",
                ],
                "ground_truth": [
                    "transaction_failures.csv",
                    "scenario_labels.csv",
                    "scenario_manifest.json",
                ],
            },
        }

        write_json(
            path=(
                self.ground_truth_dir
                / "scenario_manifest.json"
            ),
            payload=manifest,
        )

    @staticmethod
    def _validate_run(
        run: SimulationRun,
    ) -> None:
        if not run.run_id.strip():
            raise SimulationExportError(
                "Simulation run_id cannot be empty."
            )

        if not run.agents:
            raise SimulationExportError(
                "Simulation run contains no agents."
            )

        if not run.opening_balances:
            raise SimulationExportError(
                "Simulation run contains no opening balances."
            )

        if not run.transactions:
            raise SimulationExportError(
                "Simulation run contains no transactions."
            )

        transaction_ids = [
            transaction.transaction_id
            for transaction in run.transactions
        ]

        if len(transaction_ids) != len(
            set(transaction_ids)
        ):
            raise SimulationExportError(
                "Simulation contains duplicate transaction IDs."
            )

        feed_event_ids = [
            event.feed_event_id
            for event in run.feed_events
        ]

        if len(feed_event_ids) != len(
            set(feed_event_ids)
        ):
            raise SimulationExportError(
                "Simulation contains duplicate feed-event IDs."
            )

        transaction_fields = set(
            SimulationExporter.TRANSACTION_FIELDS
        )

        if "scenario_id" in transaction_fields:
            raise SimulationExportError(
                "scenario_id must never be exported "
                "with public transactions."
            )