from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.ingestion.canonical_event import (
    ProviderID,
    TransactionStatus,
    TransactionType,
)
from app.simulation.config import (
    AgentSimulationProfile,
    AmountBand,
    ProviderOpening,
    ProviderProfile,
    SimulationConfig,
    TimeBand,
)
from app.simulation.generator import (
    BaselineSimulationGenerator,
)
from app.simulation.state import SimulationState


DHAKA_TIME = timezone(
    timedelta(hours=6)
)

START = datetime(
    2026,
    6,
    20,
    9,
    0,
    tzinfo=DHAKA_TIME,
)


def build_config(
    seed: int = 100,
) -> SimulationConfig:
    return SimulationConfig(
        run_id="TEST-RUN",
        seed=seed,
        start_time=START,
        end_time=START + timedelta(hours=1),
        network_failure_rate=0.01,
        repeat_account_probability=0.10,
        providers=[
            ProviderProfile(
                provider_id=ProviderID.BKASH,
                weight=0.7,
                cash_out_probability=0.6,
            ),
            ProviderProfile(
                provider_id=ProviderID.NAGAD,
                weight=0.3,
                cash_out_probability=0.55,
            ),
        ],
        time_bands=[
            TimeBand(
                start="09:00:00",
                end="10:00:00",
                multiplier=1.0,
            )
        ],
        amount_bands=[
            AmountBand(
                min_amount=Decimal("100"),
                max_amount=Decimal("5000"),
                weight=1.0,
                round_to=Decimal("100"),
            )
        ],
        agents=[
            AgentSimulationProfile(
                agent_id="AG001",
                agent_name="Test Agent",
                area="Zindabazar",
                district="Sylhet",
                base_arrivals_per_hour=30,
                account_pool_size=50,
                opening_shared_cash=Decimal(
                    "100000"
                ),
                provider_openings=[
                    ProviderOpening(
                        provider_id=(
                            ProviderID.BKASH
                        ),
                        balance=Decimal(
                            "80000"
                        ),
                    ),
                    ProviderOpening(
                        provider_id=(
                            ProviderID.NAGAD
                        ),
                        balance=Decimal(
                            "60000"
                        ),
                    ),
                ],
            )
        ],
    )


def test_same_seed_produces_same_transactions() -> None:
    first = BaselineSimulationGenerator(
        build_config(seed=101)
    ).generate()

    second = BaselineSimulationGenerator(
        build_config(seed=101)
    ).generate()

    first_rows = [
        transaction.model_dump(
            mode="json"
        )
        for transaction in first.transactions
    ]

    second_rows = [
        transaction.model_dump(
            mode="json"
        )
        for transaction in second.transactions
    ]

    assert first_rows == second_rows


def test_different_seed_changes_transaction_stream() -> None:
    first = BaselineSimulationGenerator(
        build_config(seed=101)
    ).generate()

    second = BaselineSimulationGenerator(
        build_config(seed=202)
    ).generate()

    first_rows = [
        transaction.model_dump(
            mode="json"
        )
        for transaction in first.transactions
    ]

    second_rows = [
        transaction.model_dump(
            mode="json"
        )
        for transaction in second.transactions
    ]

    assert first_rows != second_rows


def test_state_prevents_negative_shared_cash() -> None:
    state = SimulationState(
        shared_cash={
            "AG001": Decimal("1000")
        },
        provider_emoney={
            (
                "AG001",
                ProviderID.BKASH,
            ): Decimal("5000")
        },
    )

    result = state.apply_transaction(
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        transaction_type=(
            TransactionType.CASH_OUT
        ),
        amount=Decimal("2000"),
    )

    assert (
        result.status
        == TransactionStatus.FAILED
    )

    assert (
        result.failure_reason
        == "INSUFFICIENT_SHARED_CASH"
    )

    state.assert_invariants()


def test_public_transactions_do_not_contain_scenario_id() -> None:
    run = BaselineSimulationGenerator(
        build_config(seed=101)
    ).generate()

    assert run.transactions

    for transaction in run.transactions:
        assert (
            "scenario_id"
            not in transaction.model_dump()
        )