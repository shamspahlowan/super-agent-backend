from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.ingestion.canonical_event import (
    AgentRecord,
    ContextEvent,
    FeedEvent,
    OpeningBalance,
    ReplayEvent,
    ReplayEventType,
    ResourceType,
    TransactionEvent,
)


ModelType = TypeVar("ModelType", bound=BaseModel)


class SyntheticDataError(RuntimeError):
    """Raised when synthetic input data is missing or invalid."""


@dataclass(frozen=True)
class SyntheticDataBundle:
    agents: list[AgentRecord]
    opening_balances: list[OpeningBalance]
    transactions: list[TransactionEvent]
    feed_events: list[FeedEvent]
    context_events: list[ContextEvent]

    @property
    def agent_ids(self) -> set[str]:
        return {agent.agent_id for agent in self.agents}

    @property
    def provider_ids(self) -> set[str]:
        providers: set[str] = set()

        for balance in self.opening_balances:
            if balance.provider_id is not None:
                providers.add(balance.provider_id.value)

        return providers


class SyntheticDataLoader:
    def __init__(
        self,
        synthetic_directory: Path | str = "data/synthetic",
    ) -> None:
        self.synthetic_directory = Path(synthetic_directory)

    def load(self) -> SyntheticDataBundle:
        bundle = SyntheticDataBundle(
            agents=self._load_models(
                filename="agents.csv",
                model=AgentRecord,
            ),
            opening_balances=self._load_models(
                filename="opening_balances.csv",
                model=OpeningBalance,
            ),
            transactions=self._load_models(
                filename="transactions.csv",
                model=TransactionEvent,
            ),
            feed_events=self._load_models(
                filename="feed_events.csv",
                model=FeedEvent,
            ),
            context_events=self._load_models(
                filename="context_events.csv",
                model=ContextEvent,
            ),
        )

        self._validate_bundle(bundle)

        return bundle

    def build_event_stream(
        self,
        bundle: SyntheticDataBundle | None = None,
    ) -> list[ReplayEvent]:
        if bundle is None:
            bundle = self.load()

        events: list[ReplayEvent] = []

        for transaction in bundle.transactions:
            events.append(
                ReplayEvent(
                    event_id=transaction.transaction_id,
                    event_type=ReplayEventType.TRANSACTION,
                    timestamp=transaction.timestamp,
                    agent_id=transaction.agent_id,
                    provider_id=transaction.provider_id,
                    payload=transaction,
                )
            )

        for feed_event in bundle.feed_events:
            events.append(
                ReplayEvent(
                    event_id=feed_event.feed_event_id,
                    event_type=ReplayEventType.FEED_EVENT,
                    timestamp=feed_event.timestamp,
                    agent_id=feed_event.agent_id,
                    provider_id=feed_event.provider_id,
                    payload=feed_event,
                )
            )

        event_priority = {
            ReplayEventType.FEED_EVENT: 0,
            ReplayEventType.TRANSACTION: 1,
        }

        events.sort(
            key=lambda event: (
                event.timestamp,
                event_priority[event.event_type],
                event.event_id,
            )
        )

        return events

    def summary(
        self,
        bundle: SyntheticDataBundle,
    ) -> dict[str, Any]:
        event_stream = self.build_event_stream(bundle)

        return {
            "agents": len(bundle.agents),
            "providers": sorted(bundle.provider_ids),
            "opening_balances": len(bundle.opening_balances),
            "transactions": len(bundle.transactions),
            "feed_events": len(bundle.feed_events),
            "context_events": len(bundle.context_events),
            "replay_events": len(event_stream),
            "simulation_start": (
                event_stream[0].timestamp.isoformat()
                if event_stream
                else None
            ),
            "simulation_end": (
                event_stream[-1].timestamp.isoformat()
                if event_stream
                else None
            ),
        }

    def _load_models(
        self,
        *,
        filename: str,
        model: type[ModelType],
    ) -> list[ModelType]:
        path = self.synthetic_directory / filename

        if not path.exists():
            raise SyntheticDataError(
                f"Required synthetic data file does not exist: {path}"
            )

        rows = self._read_csv(path)
        parsed_models: list[ModelType] = []

        for row_number, row in enumerate(rows, start=2):
            normalized_row = self._normalize_row(row)

            try:
                parsed_models.append(
                    model.model_validate(normalized_row)
                )
            except ValidationError as exc:
                raise SyntheticDataError(
                    f"Invalid row in {path} at line {row_number}:\n"
                    f"{exc}"
                ) from exc

        if not parsed_models:
            raise SyntheticDataError(
                f"Synthetic data file contains no records: {path}"
            )

        return parsed_models

    @staticmethod
    def _read_csv(path: Path) -> list[dict[str, str]]:
        try:
            with path.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as file:
                reader = csv.DictReader(file)

                if reader.fieldnames is None:
                    raise SyntheticDataError(
                        f"CSV file has no header: {path}"
                    )

                return list(reader)

        except OSError as exc:
            raise SyntheticDataError(
                f"Unable to read synthetic data file: {path}"
            ) from exc

    @staticmethod
    def _normalize_row(
        row: dict[str, str],
    ) -> dict[str, Any]:
        normalized: dict[str, Any] = {}

        for key, value in row.items():
            if key is None:
                continue

            normalized_key = key.strip()

            if value is None:
                normalized[normalized_key] = None
                continue

            normalized_value = value.strip()

            normalized[normalized_key] = (
                None if normalized_value == "" else normalized_value
            )

        return normalized

    def _validate_bundle(
        self,
        bundle: SyntheticDataBundle,
    ) -> None:
        self._validate_unique_agents(bundle)
        self._validate_event_ids(bundle)
        self._validate_references(bundle)
        self._validate_opening_balances(bundle)
        self._validate_provider_count(bundle)
        self._validate_no_ground_truth_leakage()

    @staticmethod
    def _validate_unique_agents(
        bundle: SyntheticDataBundle,
    ) -> None:
        agent_ids = [
            agent.agent_id
            for agent in bundle.agents
        ]

        duplicates = SyntheticDataLoader._find_duplicates(agent_ids)

        if duplicates:
            raise SyntheticDataError(
                "Duplicate agent IDs found: "
                + ", ".join(sorted(duplicates))
            )

    @staticmethod
    def _validate_event_ids(
        bundle: SyntheticDataBundle,
    ) -> None:
        transaction_ids = [
            transaction.transaction_id
            for transaction in bundle.transactions
        ]

        duplicate_transactions = (
            SyntheticDataLoader._find_duplicates(
                transaction_ids
            )
        )

        if duplicate_transactions:
            raise SyntheticDataError(
                "Duplicate transaction IDs found: "
                + ", ".join(sorted(duplicate_transactions))
            )

        feed_event_ids = [
            event.feed_event_id
            for event in bundle.feed_events
        ]

        duplicate_feed_events = (
            SyntheticDataLoader._find_duplicates(
                feed_event_ids
            )
        )

        if duplicate_feed_events:
            raise SyntheticDataError(
                "Duplicate feed event IDs found: "
                + ", ".join(sorted(duplicate_feed_events))
            )

        overlapping_ids = set(transaction_ids) & set(feed_event_ids)

        if overlapping_ids:
            raise SyntheticDataError(
                "Transaction and feed event IDs overlap: "
                + ", ".join(sorted(overlapping_ids))
            )

    @staticmethod
    def _validate_references(
        bundle: SyntheticDataBundle,
    ) -> None:
        known_agents = bundle.agent_ids

        referenced_agents: set[str] = set()

        referenced_agents.update(
            balance.agent_id
            for balance in bundle.opening_balances
        )

        referenced_agents.update(
            transaction.agent_id
            for transaction in bundle.transactions
        )

        referenced_agents.update(
            event.agent_id
            for event in bundle.feed_events
        )

        unknown_agents = referenced_agents - known_agents

        if unknown_agents:
            raise SyntheticDataError(
                "Records reference unknown agents: "
                + ", ".join(sorted(unknown_agents))
            )

    @staticmethod
    def _validate_opening_balances(
        bundle: SyntheticDataBundle,
    ) -> None:
        shared_cash_count: dict[str, int] = {}
        provider_balance_keys: set[tuple[str, str]] = set()

        for balance in bundle.opening_balances:
            if balance.resource_type == ResourceType.SHARED_CASH:
                shared_cash_count[balance.agent_id] = (
                    shared_cash_count.get(balance.agent_id, 0) + 1
                )
                continue

            assert balance.provider_id is not None

            key = (
                balance.agent_id,
                balance.provider_id.value,
            )

            if key in provider_balance_keys:
                raise SyntheticDataError(
                    "Duplicate provider opening balance for "
                    f"agent={key[0]}, provider={key[1]}"
                )

            provider_balance_keys.add(key)

        invalid_shared_cash_agents = [
            agent_id
            for agent_id in bundle.agent_ids
            if shared_cash_count.get(agent_id, 0) != 1
        ]

        if invalid_shared_cash_agents:
            raise SyntheticDataError(
                "Every agent must have exactly one shared-cash "
                "opening balance. Invalid agents: "
                + ", ".join(sorted(invalid_shared_cash_agents))
            )

        providers = bundle.provider_ids

        for agent_id in bundle.agent_ids:
            missing_providers = [
                provider_id
                for provider_id in providers
                if (
                    agent_id,
                    provider_id,
                ) not in provider_balance_keys
            ]

            if missing_providers:
                raise SyntheticDataError(
                    f"Agent {agent_id} is missing opening balances for: "
                    + ", ".join(sorted(missing_providers))
                )

    @staticmethod
    def _validate_provider_count(
        bundle: SyntheticDataBundle,
    ) -> None:
        if len(bundle.provider_ids) < 2:
            raise SyntheticDataError(
                "The simulation must contain at least two "
                "logically separate providers."
            )

    def _validate_no_ground_truth_leakage(self) -> None:
        forbidden_columns = {
            "scenario_id",
            "expected_category",
            "expected_positive",
            "ground_truth",
            "is_anomaly",
        }

        detection_files = [
            "transactions.csv",
            "opening_balances.csv",
            "feed_events.csv",
            "context_events.csv",
        ]

        for filename in detection_files:
            path = self.synthetic_directory / filename

            with path.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as file:
                reader = csv.DictReader(file)
                headers = set(reader.fieldnames or [])

            leaked_columns = headers & forbidden_columns

            if leaked_columns:
                raise SyntheticDataError(
                    f"Ground-truth leakage found in {path}: "
                    + ", ".join(sorted(leaked_columns))
                )

    @staticmethod
    def _find_duplicates(
        values: list[str],
    ) -> set[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()

        for value in values:
            if value in seen:
                duplicates.add(value)
            else:
                seen.add(value)

        return duplicates