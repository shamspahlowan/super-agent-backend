from __future__ import annotations

from datetime import datetime, timedelta
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from app.ingestion.canonical_event import (
    FeedEventType,
    ProviderID,
    TransactionType,
)
from app.simulation.config import ScenarioConfig
from app.simulation.scenarios.base import (
    ScenarioPlugin,
    TransactionCandidate,
)


def round_amount(
    value: Decimal,
    round_to: Decimal = Decimal("50"),
) -> Decimal:
    units = (
        value / round_to
    ).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )

    return max(
        round_to,
        units * round_to,
    )


class HiddenProviderShortageScenario(
    ScenarioPlugin
):
    def candidates_for_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> list[TransactionCandidate]:
        if not self.is_active(minute):
            return []

        provider_id = self.config.provider_id

        if provider_id is None:
            return []

        interval = self.parameter_int(
            "interval_minutes",
            2,
        )

        elapsed = self.elapsed_minutes(minute)

        if elapsed % interval != 0:
            return []

        rng = runtime.seeds.stream(
            f"scenario:{self.scenario_id}"
        )

        minimum = self.parameter_decimal(
            "amount_min",
            "8000",
        )

        maximum = self.parameter_decimal(
            "amount_max",
            "11000",
        )

        pool_size = self.parameter_int(
            "account_pool_size",
            18,
        )

        raw_amount = Decimal(
            str(
                rng.uniform(
                    float(minimum),
                    float(maximum),
                )
            )
        )

        amount = round_amount(
            raw_amount,
            Decimal("100"),
        )

        account_number = rng.randrange(
            1,
            pool_size + 1,
        )

        return [
            TransactionCandidate(
                timestamp=minute
                + timedelta(
                    seconds=rng.randrange(5, 55)
                ),
                agent_id=self.config.agent_id,
                provider_id=provider_id,
                account_id=(
                    f"S2-{self.config.agent_id}-"
                    f"{account_number:03d}"
                ),
                transaction_type=(
                    TransactionType.CASH_IN
                ),
                amount=amount,
                source=self.scenario_id,
                scenario_id=self.scenario_id,
                scenario_label=(
                    "HIDDEN_PROVIDER_SHORTAGE"
                ),
                allow_network_failure=False,
            )
        ]


class RepeatedCashOutClusterScenario(
    ScenarioPlugin
):
    def candidates_for_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> list[TransactionCandidate]:
        if not self.is_active(minute):
            return []

        provider_id = self.config.provider_id

        if provider_id is None:
            return []

        rng = runtime.seeds.stream(
            f"scenario:{self.scenario_id}"
        )

        count = self.parameter_int(
            "transactions_per_minute",
            2,
        )

        account_count = self.parameter_int(
            "account_count",
            3,
        )

        base_amount = self.parameter_decimal(
            "base_amount",
            "10000",
        )

        jitter_percent = Decimal(
            str(
                self.parameter_float(
                    "amount_jitter_percent",
                    1.5,
                )
            )
        ) / Decimal("100")

        candidates: list[
            TransactionCandidate
        ] = []

        for index in range(count):
            jitter = Decimal(
                str(
                    rng.uniform(
                        -float(jitter_percent),
                        float(jitter_percent),
                    )
                )
            )

            amount = round_amount(
                base_amount
                * (Decimal("1") + jitter),
                Decimal("10"),
            )

            account_number = (
                self.elapsed_minutes(minute)
                * count
                + index
            ) % account_count

            candidates.append(
                TransactionCandidate(
                    timestamp=minute
                    + timedelta(
                        seconds=5
                        + index
                        * max(
                            1,
                            45 // count,
                        )
                    ),
                    agent_id=self.config.agent_id,
                    provider_id=provider_id,
                    account_id=(
                        f"S3-CLUSTER-"
                        f"{account_number + 1}"
                    ),
                    transaction_type=(
                        TransactionType.CASH_OUT
                    ),
                    amount=amount,
                    source=self.scenario_id,
                    scenario_id=self.scenario_id,
                    scenario_label=(
                        "REPEATED_AMOUNT_CLUSTER"
                    ),
                    allow_network_failure=False,
                )
            )

        return candidates


class LegitimateDemandSpikeScenario(
    ScenarioPlugin
):
    def __init__(
        self,
        config: ScenarioConfig,
    ) -> None:
        super().__init__(config)

        self._sequence = 0

    def on_start(
        self,
        runtime,
    ) -> None:
        agent = next(
            agent
            for agent in runtime.config.agents
            if agent.agent_id
            == self.config.agent_id
        )

        runtime.emit_context_event(
            area=agent.area,
            event_type=str(
                self.config.parameters.get(
                    "context_event_type",
                    "EID_MARKET_SURGE",
                )
            ),
            start_time=self.config.start_time,
            end_time=self.config.end_time,
            expected_demand_multiplier=(
                self.parameter_float(
                    "expected_demand_multiplier",
                    2.8,
                )
            ),
            description=(
                "Known local Eid market demand event. "
                "Activity should remain broadly distributed."
            ),
        )

    def candidates_for_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> list[TransactionCandidate]:
        if not self.is_active(minute):
            return []

        count = self.parameter_int(
            "transactions_per_minute",
            3,
        )

        minimum = self.parameter_decimal(
            "amount_min",
            "800",
        )

        maximum = self.parameter_decimal(
            "amount_max",
            "18000",
        )

        cash_out_probability = (
            self.parameter_float(
                "cash_out_probability",
                0.68,
            )
        )

        rng = runtime.seeds.stream(
            f"scenario:{self.scenario_id}"
        )

        provider_profiles = (
            runtime.config.providers
        )

        candidates: list[
            TransactionCandidate
        ] = []

        for index in range(count):
            self._sequence += 1

            provider = runtime.seeds.weighted_choice(
                namespace=(
                    f"scenario-provider:"
                    f"{self.scenario_id}"
                ),
                values=[
                    item.provider_id
                    for item in provider_profiles
                ],
                weights=[
                    item.weight
                    for item in provider_profiles
                ],
            )

            transaction_type = (
                TransactionType.CASH_OUT
                if rng.random()
                < cash_out_probability
                else TransactionType.CASH_IN
            )

            raw_amount = Decimal(
                str(
                    rng.uniform(
                        float(minimum),
                        float(maximum),
                    )
                )
            )

            amount = round_amount(
                raw_amount,
                Decimal("50"),
            )

            candidates.append(
                TransactionCandidate(
                    timestamp=minute
                    + timedelta(
                        seconds=3
                        + index
                        * max(
                            1,
                            50 // count,
                        )
                    ),
                    agent_id=self.config.agent_id,
                    provider_id=provider,
                    account_id=(
                        f"EID-UNIQUE-"
                        f"{self._sequence:05d}"
                    ),
                    transaction_type=(
                        transaction_type
                    ),
                    amount=amount,
                    source=self.scenario_id,
                    scenario_id=self.scenario_id,
                    scenario_label=(
                        "LEGITIMATE_DEMAND_SPIKE"
                    ),
                    allow_network_failure=False,
                )
            )

        return candidates


class CrossProviderLinkedActivityScenario(
    ScenarioPlugin
):
    def candidates_for_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> list[TransactionCandidate]:
        if not self.is_active(minute):
            return []

        interval = self.parameter_int(
            "interval_minutes",
            2,
        )

        elapsed = self.elapsed_minutes(minute)

        if elapsed % interval != 0:
            return []

        raw_providers = (
            self.config.parameters.get(
                "providers",
                [
                    ProviderID.BKASH.value,
                    ProviderID.NAGAD.value,
                ],
            )
        )

        providers = [
            ProviderID(str(value))
            for value in raw_providers
        ]

        account_id = str(
            self.config.parameters.get(
                "account_id",
                "SYNTH-LINKED-ACCOUNT",
            )
        )

        base_amount = self.parameter_decimal(
            "amount",
            "15000",
        )

        jitter_percent = Decimal(
            str(
                self.parameter_float(
                    "amount_jitter_percent",
                    1.0,
                )
            )
        ) / Decimal("100")

        rng = runtime.seeds.stream(
            f"scenario:{self.scenario_id}"
        )

        candidates: list[
            TransactionCandidate
        ] = []

        for index, provider_id in enumerate(
            providers
        ):
            jitter = Decimal(
                str(
                    rng.uniform(
                        -float(jitter_percent),
                        float(jitter_percent),
                    )
                )
            )

            amount = round_amount(
                base_amount
                * (Decimal("1") + jitter),
                Decimal("50"),
            )

            candidates.append(
                TransactionCandidate(
                    timestamp=minute
                    + timedelta(
                        seconds=10 + index * 20
                    ),
                    agent_id=self.config.agent_id,
                    provider_id=provider_id,
                    account_id=account_id,
                    transaction_type=(
                        TransactionType.CASH_OUT
                    ),
                    amount=amount,
                    source=self.scenario_id,
                    scenario_id=self.scenario_id,
                    scenario_label=(
                        "CROSS_PROVIDER_LINKED_ACTIVITY"
                    ),
                    allow_network_failure=False,
                )
            )

        return candidates


class FeedDelayRecoveryScenario(
    ScenarioPlugin
):
    def __init__(
        self,
        config: ScenarioConfig,
    ) -> None:
        super().__init__(config)

        self._delay_emitted = False
        self._recovery_emitted = False

    def before_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> None:
        provider_id = self.config.provider_id

        if provider_id is None:
            return

        if (
            not self._delay_emitted
            and minute == self.config.start_time
        ):
            declared_delay = self.parameter_int(
                "declared_delay_minutes",
                int(
                    (
                        self.config.end_time
                        - self.config.start_time
                    ).total_seconds()
                    // 60
                ),
            )

            runtime.emit_feed_event(
                timestamp=minute,
                agent_id=self.config.agent_id,
                provider_id=provider_id,
                event_type=(
                    FeedEventType.FEED_DELAY
                ),
                delay_minutes=declared_delay,
            )

            self._delay_emitted = True

        if (
            not self._recovery_emitted
            and minute == self.config.end_time
        ):
            runtime.emit_feed_event(
                timestamp=minute,
                agent_id=self.config.agent_id,
                provider_id=provider_id,
                event_type=(
                    FeedEventType.FEED_RECOVERED
                ),
            )

            self._recovery_emitted = True

    def suppress_heartbeat(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
        timestamp: datetime,
    ) -> bool:
        return (
            self.config.enabled
            and agent_id
            == self.config.agent_id
            and provider_id
            == self.config.provider_id
            and self.config.start_time
            <= timestamp
            < self.config.end_time
        )


class BalanceConflictScenario(
    ScenarioPlugin
):
    def __init__(
        self,
        config: ScenarioConfig,
    ) -> None:
        super().__init__(config)

        self._emitted = False

    def after_minute(
        self,
        *,
        runtime,
        minute: datetime,
    ) -> None:
        provider_id = self.config.provider_id

        if (
            provider_id is None
            or self._emitted
            or minute != self.config.start_time
        ):
            return

        calculated_balance = (
            runtime.state.get_provider_balance(
                self.config.agent_id,
                provider_id,
            )
        )

        offset = self.parameter_decimal(
            "reported_balance_offset",
            "45000",
        )

        runtime.emit_feed_event(
            timestamp=minute
            + timedelta(seconds=58),
            agent_id=self.config.agent_id,
            provider_id=provider_id,
            event_type=(
                FeedEventType.BALANCE_CONFLICT
            ),
            reported_balance=(
                calculated_balance + offset
            ),
        )

        self._emitted = True