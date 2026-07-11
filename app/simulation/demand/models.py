from __future__ import annotations

import math
from datetime import datetime
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from app.ingestion.canonical_event import (
    ProviderID,
    TransactionType,
)
from app.simulation.config import (
    AgentSimulationProfile,
    SimulationConfig,
)
from app.simulation.seed_manager import SeedManager


class ArrivalModel:
    def __init__(
        self,
        *,
        config: SimulationConfig,
        seeds: SeedManager,
    ) -> None:
        self.config = config
        self.seeds = seeds

    def sample_arrivals(
        self,
        *,
        agent: AgentSimulationProfile,
        minute: datetime,
    ) -> int:
        multiplier = (
            self.config.demand_multiplier_at(
                minute
            )
        )

        expected_per_minute = (
            agent.base_arrivals_per_hour
            * multiplier
            / 60
        )

        rng = self.seeds.stream(
            f"arrivals:{agent.agent_id}"
        )

        return self._sample_poisson(
            rng=rng,
            expected_value=expected_per_minute,
        )

    @staticmethod
    def _sample_poisson(
        *,
        rng,
        expected_value: float,
    ) -> int:
        if expected_value <= 0:
            return 0

        if expected_value < 30:
            threshold = math.exp(
                -expected_value
            )

            product = 1.0
            count = 0

            while product > threshold:
                count += 1
                product *= rng.random()

            return count - 1

        sampled = round(
            rng.gauss(
                expected_value,
                math.sqrt(expected_value),
            )
        )

        return max(0, sampled)


class ProviderModel:
    def __init__(
        self,
        *,
        config: SimulationConfig,
        seeds: SeedManager,
    ) -> None:
        self.config = config
        self.seeds = seeds

    def choose_provider(
        self,
        *,
        agent_id: str,
    ) -> ProviderID:
        return self.seeds.weighted_choice(
            namespace=f"provider:{agent_id}",
            values=[
                profile.provider_id
                for profile in self.config.providers
            ],
            weights=[
                profile.weight
                for profile in self.config.providers
            ],
        )


class TransactionTypeModel:
    def __init__(
        self,
        *,
        config: SimulationConfig,
        seeds: SeedManager,
    ) -> None:
        self.config = config
        self.seeds = seeds

    def choose_type(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
    ) -> TransactionType:
        provider = self.config.provider_profile(
            provider_id
        )

        rng = self.seeds.stream(
            "transaction-type:"
            f"{agent_id}:{provider_id.value}"
        )

        if (
            rng.random()
            < provider.cash_out_probability
        ):
            return TransactionType.CASH_OUT

        return TransactionType.CASH_IN


class AmountModel:
    def __init__(
        self,
        *,
        config: SimulationConfig,
        seeds: SeedManager,
    ) -> None:
        self.config = config
        self.seeds = seeds

    def sample_amount(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
    ) -> Decimal:
        band = self.seeds.weighted_choice(
            namespace=(
                f"amount-band:{agent_id}:"
                f"{provider_id.value}"
            ),
            values=self.config.amount_bands,
            weights=[
                item.weight
                for item in self.config.amount_bands
            ],
        )

        rng = self.seeds.stream(
            f"amount-value:{agent_id}:"
            f"{provider_id.value}"
        )

        raw_amount = Decimal(
            str(
                rng.uniform(
                    float(band.min_amount),
                    float(band.max_amount),
                )
            )
        )

        units = (
            raw_amount / band.round_to
        ).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )

        rounded = units * band.round_to

        return max(
            band.min_amount,
            min(
                rounded,
                band.max_amount,
            ),
        )


class AccountModel:
    def __init__(
        self,
        *,
        repeat_probability: float,
        seeds: SeedManager,
    ) -> None:
        self.repeat_probability = (
            repeat_probability
        )

        self.seeds = seeds

        self._last_account: dict[
            tuple[str, ProviderID],
            str,
        ] = {}

    def choose_account(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
        pool_size: int,
    ) -> str:
        key = (
            agent_id,
            provider_id,
        )

        rng = self.seeds.stream(
            f"account:{agent_id}:"
            f"{provider_id.value}"
        )

        previous = self._last_account.get(key)

        if (
            previous is not None
            and rng.random()
            < self.repeat_probability
        ):
            return previous

        account_number = rng.randrange(
            1,
            pool_size + 1,
        )

        account_id = (
            f"ACC-{agent_id}-"
            f"{provider_id.value}-"
            f"{account_number:04d}"
        )

        self._last_account[key] = account_id

        return account_id