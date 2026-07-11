from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from threading import RLock

from app.ingestion.canonical_event import (
    ContextEvent,
    FeedEvent,
    FeedEventType,
    TransactionEvent,
)
from app.simulation.config import SimulationConfig
from app.simulation.scenarios.base import (
    ScenarioGroundTruthLabel,
    TransactionCandidate,
)
from app.simulation.seed_manager import SeedManager
from app.simulation.state import SimulationState


class SimulationRuntime:
    def __init__(
        self,
        *,
        config: SimulationConfig,
        state: SimulationState,
        seeds: SeedManager,
    ) -> None:
        self.config = config
        self.state = state
        self.seeds = seeds

        self.transactions: list[
            TransactionEvent
        ] = []

        self.feed_events: list[
            FeedEvent
        ] = []

        self.context_events: list[
            ContextEvent
        ] = []

        self.scenario_labels: list[
            ScenarioGroundTruthLabel
        ] = []

        self.failure_reasons: dict[
            str,
            str,
        ] = {}

        self._transaction_counter = 0
        self._feed_counter = 0
        self._context_counter = 0

        self._failure_rng = seeds.stream(
            "operational-failures"
        )

        self._lock = RLock()

    def register_scenario(
        self,
        label: ScenarioGroundTruthLabel,
    ) -> None:
        self.scenario_labels.append(label)

    def commit_candidates(
        self,
        candidates: list[TransactionCandidate],
    ) -> list[TransactionEvent]:
        ordered = sorted(
            candidates,
            key=lambda item: (
                item.timestamp,
                item.agent_id,
                item.provider_id.value,
                item.account_id,
                item.source,
            ),
        )

        return [
            self.commit_transaction(candidate)
            for candidate in ordered
        ]

    def commit_transaction(
        self,
        candidate: TransactionCandidate,
    ) -> TransactionEvent:
        with self._lock:
            self._transaction_counter += 1

            transaction_id = (
                f"TXN-{self._transaction_counter:08d}"
            )

            should_fail_network = (
                candidate.allow_network_failure
                and self._failure_rng.random()
                < self.config.network_failure_rate
            )

            if should_fail_network:
                decision = self.state.reject_transaction(
                    agent_id=candidate.agent_id,
                    provider_id=candidate.provider_id,
                    failure_reason=(
                        "SIMULATED_NETWORK_ERROR"
                    ),
                )

            else:
                decision = self.state.apply_transaction(
                    agent_id=candidate.agent_id,
                    provider_id=candidate.provider_id,
                    transaction_type=(
                        candidate.transaction_type
                    ),
                    amount=candidate.amount,
                )

            transaction = TransactionEvent(
                transaction_id=transaction_id,
                timestamp=candidate.timestamp,
                agent_id=candidate.agent_id,
                provider_id=candidate.provider_id,
                account_id=candidate.account_id,
                transaction_type=(
                    candidate.transaction_type
                ),
                amount=candidate.amount,
                status=decision.status,
                channel=candidate.channel,
            )

            self.transactions.append(transaction)

            if decision.failure_reason is not None:
                self.failure_reasons[
                    transaction_id
                ] = decision.failure_reason

            if candidate.scenario_id is not None:
                self.scenario_labels.append(
                    ScenarioGroundTruthLabel(
                        scenario_id=(
                            candidate.scenario_id
                        ),
                        agent_id=candidate.agent_id,
                        provider_id=(
                            candidate.provider_id
                        ),
                        label=(
                            candidate.scenario_label
                            or candidate.scenario_id
                        ),
                        start_time=(
                            candidate.timestamp
                        ),
                        end_time=(
                            candidate.timestamp
                        ),
                        expected_detection_after=(
                            candidate.timestamp
                        ),
                        transaction_id=transaction_id,
                    )
                )

            return transaction

    def emit_feed_event(
        self,
        *,
        timestamp: datetime,
        agent_id: str,
        provider_id,
        event_type: FeedEventType,
        delay_minutes: int = 0,
        reported_balance: Decimal | None = None,
    ) -> FeedEvent:
        with self._lock:
            self._feed_counter += 1

            event = FeedEvent(
                feed_event_id=(
                    f"FEED-{self._feed_counter:06d}"
                ),
                timestamp=timestamp,
                agent_id=agent_id,
                provider_id=provider_id,
                event_type=event_type,
                delay_minutes=delay_minutes,
                reported_balance=reported_balance,
            )

            self.feed_events.append(event)

            return event

    def emit_context_event(
        self,
        *,
        area: str,
        event_type: str,
        start_time: datetime,
        end_time: datetime,
        expected_demand_multiplier: float,
        description: str,
    ) -> ContextEvent:
        with self._lock:
            self._context_counter += 1

            event = ContextEvent(
                context_id=(
                    f"CTX-{self._context_counter:06d}"
                ),
                area=area,
                event_type=event_type,
                start_time=start_time,
                end_time=end_time,
                expected_demand_multiplier=(
                    expected_demand_multiplier
                ),
                description=description,
            )

            self.context_events.append(event)

            return event