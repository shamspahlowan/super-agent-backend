from collections import Counter
from pathlib import Path

from app.ingestion.canonical_event import (
    FeedEventType,
    ProviderID,
    TransactionStatus,
    TransactionType,
)
from app.simulation.config import (
    load_simulation_config,
)
from app.simulation.generator import (
    BaselineSimulationGenerator,
)

from decimal import Decimal


ROOT_DIR = Path(__file__).resolve().parents[2]

CONFIG_PATH = (
    ROOT_DIR
    / "configs"
    / "simulations"
    / "demo_day.yaml"
)


def generate_run():
    config = load_simulation_config(
        CONFIG_PATH
    )

    return BaselineSimulationGenerator(
        config
    ).generate()


def test_scenario_ground_truth_does_not_leak() -> None:
    run = generate_run()

    assert run.scenario_labels

    for transaction in run.transactions:
        payload = transaction.model_dump()

        assert "scenario_id" not in payload
        assert "scenario_label" not in payload


def test_hidden_provider_shortage_transactions_exist() -> None:
    run = generate_run()

    transaction_ids = {
        label.transaction_id
        for label in run.scenario_labels
        if label.scenario_id == "S2"
        and label.transaction_id is not None
    }

    transactions = [
        transaction
        for transaction in run.transactions
        if transaction.transaction_id
        in transaction_ids
    ]

    assert len(transactions) >= 10

    assert all(
        transaction.agent_id == "AG002"
        and transaction.provider_id
        == ProviderID.BKASH
        and transaction.transaction_type
        == TransactionType.CASH_IN
        for transaction in transactions
    )


def test_repeated_cluster_contains_expected_pattern() -> None:
    run = generate_run()

    ids = {
        label.transaction_id
        for label in run.scenario_labels
        if label.scenario_id == "S3"
        and label.transaction_id is not None
    }

    transactions = [
        transaction
        for transaction in run.transactions
        if transaction.transaction_id in ids
        and transaction.status
        == TransactionStatus.SUCCESS
    ]

    assert len(transactions) >= 20

    accounts = {
        transaction.account_id
        for transaction in transactions
    }

    assert len(accounts) <= 3

    amounts = [
        transaction.amount
        for transaction in transactions
    ]

    assert (
        max(amounts) - min(amounts)
        <= max(amounts) * Decimal("0.04")
    )


def test_legitimate_demand_has_high_diversity() -> None:
    run = generate_run()

    ids = {
        label.transaction_id
        for label in run.scenario_labels
        if label.scenario_id == "S5"
        and label.transaction_id is not None
    }

    transactions = [
        transaction
        for transaction in run.transactions
        if transaction.transaction_id in ids
    ]

    assert len(transactions) >= 60

    unique_accounts = {
        transaction.account_id
        for transaction in transactions
    }

    providers = {
        transaction.provider_id
        for transaction in transactions
    }

    assert (
        len(unique_accounts)
        == len(transactions)
    )

    assert len(providers) >= 2

    assert any(
        context.event_type
        == "EID_MARKET_SURGE"
        for context in run.context_events
    )


def test_feed_delay_suppresses_heartbeats() -> None:
    run = generate_run()

    rocket_events = [
        event
        for event in run.feed_events
        if event.agent_id == "AG005"
        and event.provider_id
        == ProviderID.ROCKET
    ]

    event_types = {
        event.event_type
        for event in rocket_events
    }

    assert FeedEventType.FEED_DELAY in event_types

    assert (
        FeedEventType.FEED_RECOVERED
        in event_types
    )

    delayed_heartbeats = [
        event
        for event in rocket_events
        if event.event_type
        == FeedEventType.HEARTBEAT
        and event.timestamp.hour == 13
        and 0 <= event.timestamp.minute < 40
    ]

    assert delayed_heartbeats == []


def test_balance_conflict_is_generated() -> None:
    run = generate_run()

    conflicts = [
        event
        for event in run.feed_events
        if event.agent_id == "AG006"
        and event.provider_id
        == ProviderID.BKASH
        and event.event_type
        == FeedEventType.BALANCE_CONFLICT
    ]

    assert len(conflicts) == 1

    assert (
        conflicts[0].reported_balance
        is not None
    )


def test_cross_provider_link_uses_same_account() -> None:
    run = generate_run()

    ids = {
        label.transaction_id
        for label in run.scenario_labels
        if label.scenario_id == "S7"
        and label.transaction_id is not None
    }

    transactions = [
        transaction
        for transaction in run.transactions
        if transaction.transaction_id in ids
    ]

    accounts = {
        transaction.account_id
        for transaction in transactions
    }

    providers = {
        transaction.provider_id
        for transaction in transactions
    }

    assert accounts == {
        "SYNTH-LINK-AG001"
    }

    assert providers == {
        ProviderID.BKASH,
        ProviderID.NAGAD,
    }


def test_normal_heartbeats_exist_for_all_feeds() -> None:
    run = generate_run()

    heartbeat_counts = Counter(
        (
            event.agent_id,
            event.provider_id,
        )
        for event in run.feed_events
        if event.event_type
        == FeedEventType.HEARTBEAT
    )

    assert heartbeat_counts

    assert heartbeat_counts[
        (
            "AG001",
            ProviderID.BKASH,
        )
    ] > 20