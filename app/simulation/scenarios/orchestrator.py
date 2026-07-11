from __future__ import annotations

from datetime import datetime

from app.ingestion.canonical_event import (
    ProviderID,
)
from app.simulation.config import (
    ScenarioConfig,
    ScenarioKind,
)
from app.simulation.scenarios.base import (
    ScenarioPlugin,
    TransactionCandidate,
)
from app.simulation.scenarios.plugins import (
    BalanceConflictScenario,
    CrossProviderLinkedActivityScenario,
    FeedDelayRecoveryScenario,
    HiddenProviderShortageScenario,
    LegitimateDemandSpikeScenario,
    RepeatedCashOutClusterScenario,
)


PLUGIN_TYPES: dict[
    ScenarioKind,
    type[ScenarioPlugin],
] = {
    ScenarioKind.HIDDEN_PROVIDER_SHORTAGE: (
        HiddenProviderShortageScenario
    ),
    ScenarioKind.REPEATED_CASH_OUT_CLUSTER: (
        RepeatedCashOutClusterScenario
    ),
    ScenarioKind.LEGITIMATE_DEMAND_SPIKE: (
        LegitimateDemandSpikeScenario
    ),
    ScenarioKind.CROSS_PROVIDER_LINKED_ACTIVITY: (
        CrossProviderLinkedActivityScenario
    ),
    ScenarioKind.FEED_DELAY_RECOVERY: (
        FeedDelayRecoveryScenario
    ),
    ScenarioKind.BALANCE_CONFLICT: (
        BalanceConflictScenario
    ),
}


def build_scenario_plugins(
    scenarios: list[ScenarioConfig],
) -> list[ScenarioPlugin]:
    plugins: list[ScenarioPlugin] = []

    for scenario in scenarios:
        if not scenario.enabled:
            continue

        plugin_type = PLUGIN_TYPES.get(
            scenario.kind
        )

        if plugin_type is None:
            raise ValueError(
                "No scenario plugin registered for "
                f"{scenario.kind.value}."
            )

        plugins.append(
            plugin_type(scenario)
        )

    return plugins


class ScenarioOrchestrator:
    def __init__(
        self,
        plugins: list[ScenarioPlugin],
    ) -> None:
        self.plugins = list(plugins)

    def on_start(
        self,
        runtime,
    ) -> None:
        for plugin in self.plugins:
            runtime.register_scenario(
                plugin.ground_truth_label()
            )

            plugin.on_start(runtime)

    def before_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> None:
        for plugin in self.plugins:
            plugin.before_minute(
                runtime=runtime,
                minute=minute,
            )

    def candidates_for_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> list[TransactionCandidate]:
        candidates: list[
            TransactionCandidate
        ] = []

        for plugin in self.plugins:
            candidates.extend(
                plugin.candidates_for_minute(
                    runtime=runtime,
                    minute=minute,
                )
            )

        return candidates

    def after_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> None:
        for plugin in self.plugins:
            plugin.after_minute(
                runtime=runtime,
                minute=minute,
            )

    def on_end(
        self,
        runtime,
    ) -> None:
        for plugin in self.plugins:
            plugin.on_end(runtime)

    def suppress_heartbeat(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
        timestamp: datetime,
    ) -> bool:
        return any(
            plugin.suppress_heartbeat(
                agent_id=agent_id,
                provider_id=provider_id,
                timestamp=timestamp,
            )
            for plugin in self.plugins
        )