from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.ingestion.canonical_event import (
    AgentRecord,
    ContextEvent,
    FeedEvent,
    FeedEventType,
    OpeningBalance,
    ResourceType,
)
from app.simulation.config import SimulationConfig
from app.simulation.demand.models import (
    AccountModel,
    AmountModel,
    ArrivalModel,
    ProviderModel,
    TransactionTypeModel,
)
from app.simulation.runtime import SimulationRuntime
from app.simulation.scenarios.base import (
    ScenarioGroundTruthLabel,
    TransactionCandidate,
)
from app.simulation.scenarios.orchestrator import (
    ScenarioOrchestrator,
    build_scenario_plugins,
)
from app.simulation.seed_manager import SeedManager
from app.simulation.state import SimulationState


@dataclass(frozen=True)
class SimulationRun:
    run_id: str
    seed: int

    started_at: datetime
    ended_at: datetime

    agents: list[AgentRecord]
    opening_balances: list[OpeningBalance]

    transactions: list
    feed_events: list[FeedEvent]
    context_events: list[ContextEvent]

    scenario_labels: list[
        ScenarioGroundTruthLabel
    ]

    failure_reasons: dict[str, str]

    final_state: dict[str, object]
    manifest: dict[str, object]


class BaselineSimulationGenerator:
    def __init__(
        self,
        config: SimulationConfig,
    ) -> None:
        self.config = config

        self.seeds = SeedManager(
            config.seed
        )

        self.state = SimulationState.from_config(
            config
        )

        self.runtime = SimulationRuntime(
            config=config,
            state=self.state,
            seeds=self.seeds,
        )

        self.arrival_model = ArrivalModel(
            config=config,
            seeds=self.seeds,
        )

        self.provider_model = ProviderModel(
            config=config,
            seeds=self.seeds,
        )

        self.type_model = TransactionTypeModel(
            config=config,
            seeds=self.seeds,
        )

        self.amount_model = AmountModel(
            config=config,
            seeds=self.seeds,
        )

        self.account_model = AccountModel(
            repeat_probability=(
                config.repeat_account_probability
            ),
            seeds=self.seeds,
        )

        self.timestamp_rng = self.seeds.stream(
            "timestamp-offsets"
        )

        self.orchestrator = ScenarioOrchestrator(
            build_scenario_plugins(
                config.scenarios
            )
        )

    def generate(self) -> SimulationRun:
        agents = self._build_agents()

        opening_balances = (
            self._build_opening_balances()
        )

        self.orchestrator.on_start(
            self.runtime
        )

        minute = self.config.start_time

        while minute < self.config.end_time:
            self.orchestrator.before_minute(
                runtime=self.runtime,
                minute=minute,
            )

            candidates = (
                self._baseline_candidates(
                    minute
                )
            )

            candidates.extend(
                self.orchestrator
                .candidates_for_minute(
                    runtime=self.runtime,
                    minute=minute,
                )
            )

            self.runtime.commit_candidates(
                candidates
            )

            self._emit_heartbeats(minute)

            self.orchestrator.after_minute(
                runtime=self.runtime,
                minute=minute,
            )

            minute += timedelta(minutes=1)

        self.orchestrator.on_end(
            self.runtime
        )

        self.runtime.transactions.sort(
            key=lambda item: (
                item.timestamp,
                item.transaction_id,
            )
        )

        self.runtime.feed_events.sort(
            key=lambda item: (
                item.timestamp,
                item.feed_event_id,
            )
        )

        self.runtime.context_events.sort(
            key=lambda item: (
                item.start_time,
                item.context_id,
            )
        )

        self.state.assert_invariants()

        manifest = self._build_manifest(
            agents
        )

        return SimulationRun(
            run_id=self.config.run_id,
            seed=self.config.seed,
            started_at=self.config.start_time,
            ended_at=self.config.end_time,
            agents=agents,
            opening_balances=opening_balances,
            transactions=(
                self.runtime.transactions
            ),
            feed_events=(
                self.runtime.feed_events
            ),
            context_events=(
                self.runtime.context_events
            ),
            scenario_labels=(
                self.runtime.scenario_labels
            ),
            failure_reasons=(
                self.runtime.failure_reasons
            ),
            final_state=self.state.snapshot(),
            manifest=manifest,
        )

    def _baseline_candidates(
        self,
        minute: datetime,
    ) -> list[TransactionCandidate]:
        candidates: list[
            TransactionCandidate
        ] = []

        for agent in self.config.agents:
            arrivals = (
                self.arrival_model.sample_arrivals(
                    agent=agent,
                    minute=minute,
                )
            )

            for _ in range(arrivals):
                provider_id = (
                    self.provider_model
                    .choose_provider(
                        agent_id=agent.agent_id
                    )
                )

                transaction_type = (
                    self.type_model.choose_type(
                        agent_id=agent.agent_id,
                        provider_id=provider_id,
                    )
                )

                amount = (
                    self.amount_model.sample_amount(
                        agent_id=agent.agent_id,
                        provider_id=provider_id,
                    )
                )

                account_id = (
                    self.account_model
                    .choose_account(
                        agent_id=agent.agent_id,
                        provider_id=provider_id,
                        pool_size=(
                            agent.account_pool_size
                        ),
                    )
                )

                candidates.append(
                    TransactionCandidate(
                        timestamp=minute
                        + timedelta(
                            seconds=(
                                self.timestamp_rng
                                .randrange(0, 60)
                            )
                        ),
                        agent_id=agent.agent_id,
                        provider_id=provider_id,
                        account_id=account_id,
                        transaction_type=(
                            transaction_type
                        ),
                        amount=amount,
                        source="BASELINE",
                        allow_network_failure=True,
                    )
                )

        return candidates

    def _emit_heartbeats(
        self,
        minute: datetime,
    ) -> None:
        elapsed = int(
            (
                minute
                - self.config.start_time
            ).total_seconds()
            // 60
        )

        if (
            elapsed
            % self.config.heartbeat_interval_minutes
            != 0
        ):
            return

        heartbeat_at = minute + timedelta(
            seconds=59
        )

        for agent in self.config.agents:
            for provider in self.config.providers:
                if (
                    self.orchestrator
                    .suppress_heartbeat(
                        agent_id=agent.agent_id,
                        provider_id=(
                            provider.provider_id
                        ),
                        timestamp=minute,
                    )
                ):
                    continue

                self.runtime.emit_feed_event(
                    timestamp=heartbeat_at,
                    agent_id=agent.agent_id,
                    provider_id=(
                        provider.provider_id
                    ),
                    event_type=(
                        FeedEventType.HEARTBEAT
                    ),
                )

    def _build_manifest(
        self,
        agents: list[AgentRecord],
    ) -> dict[str, object]:
        status_counts = Counter(
            transaction.status.value
            for transaction
            in self.runtime.transactions
        )

        provider_counts = Counter(
            transaction.provider_id.value
            for transaction
            in self.runtime.transactions
        )

        type_counts = Counter(
            transaction
            .transaction_type.value
            for transaction
            in self.runtime.transactions
        )

        failure_counts = Counter(
            self.runtime
            .failure_reasons.values()
        )

        feed_event_counts = Counter(
            event.event_type.value
            for event
            in self.runtime.feed_events
        )

        return {
            "run_id": self.config.run_id,
            "seed": self.config.seed,
            "start_time": (
                self.config.start_time.isoformat()
            ),
            "end_time": (
                self.config.end_time.isoformat()
            ),
            "agents": len(agents),
            "providers": len(
                self.config.providers
            ),
            "transactions": len(
                self.runtime.transactions
            ),
            "feed_events": len(
                self.runtime.feed_events
            ),
            "context_events": len(
                self.runtime.context_events
            ),
            "status_counts": dict(
                status_counts
            ),
            "provider_counts": dict(
                provider_counts
            ),
            "transaction_type_counts": dict(
                type_counts
            ),
            "failure_reason_counts": dict(
                failure_counts
            ),
            "feed_event_counts": dict(
                feed_event_counts
            ),
            "ground_truth_labels": len(
                self.runtime.scenario_labels
            ),
            "ground_truth_leaked_to_transactions": False,
            "financial_invariants_passed": True,
            "scenario_plugins": [
                scenario.scenario_id
                for scenario
                in self.config.scenarios
                if scenario.enabled
            ],
        }

    def _build_agents(
        self,
    ) -> list[AgentRecord]:
        return [
            AgentRecord(
                agent_id=agent.agent_id,
                agent_name=agent.agent_name,
                area=agent.area,
                district=agent.district,
            )
            for agent in self.config.agents
        ]

    def _build_opening_balances(
        self,
    ) -> list[OpeningBalance]:
        balances: list[OpeningBalance] = []

        for agent in self.config.agents:
            balances.append(
                OpeningBalance(
                    agent_id=agent.agent_id,
                    provider_id=None,
                    resource_type=(
                        ResourceType.SHARED_CASH
                    ),
                    opening_balance=(
                        agent.opening_shared_cash
                    ),
                    timestamp=(
                        self.config.start_time
                    ),
                )
            )

            for opening in agent.provider_openings:
                balances.append(
                    OpeningBalance(
                        agent_id=agent.agent_id,
                        provider_id=(
                            opening.provider_id
                        ),
                        resource_type=(
                            ResourceType
                            .PROVIDER_EMONEY
                        ),
                        opening_balance=(
                            opening.balance
                        ),
                        timestamp=(
                            self.config.start_time
                        ),
                    )
                )

        return balances