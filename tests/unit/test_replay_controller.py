from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.ingestion.canonical_event import (
    FeedEvent,
    FeedEventType,
    OpeningBalance,
    ProviderID,
    ReplayEvent,
    ReplayEventType,
    ResourceType,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from app.ledger.balance_engine import BalanceEngine
from app.replay.controller import ReplayController
from app.schemas.replay import ReplayStatus


DHAKA_TIME = timezone(timedelta(hours=6))

START = datetime(
    2026,
    6,
    20,
    9,
    0,
    tzinfo=DHAKA_TIME,
)


def build_controller() -> tuple[ReplayController, BalanceEngine]:
    opening_balances = [
        OpeningBalance(
            agent_id="AG001",
            provider_id=None,
            resource_type=ResourceType.SHARED_CASH,
            opening_balance=Decimal("100000"),
            timestamp=START,
        ),
        OpeningBalance(
            agent_id="AG001",
            provider_id=ProviderID.BKASH,
            resource_type=ResourceType.PROVIDER_EMONEY,
            opening_balance=Decimal("50000"),
            timestamp=START,
        ),
    ]

    feed = FeedEvent(
        feed_event_id="FEED-001",
        timestamp=START + timedelta(minutes=5),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        event_type=FeedEventType.HEARTBEAT,
        delay_minutes=0,
        reported_balance=None,
    )

    cash_out = TransactionEvent(
        transaction_id="TXN-001",
        timestamp=START + timedelta(minutes=10),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        account_id="ACC-001",
        transaction_type=TransactionType.CASH_OUT,
        amount=Decimal("10000"),
        status=TransactionStatus.SUCCESS,
        channel="AGENT",
    )

    cash_in = TransactionEvent(
        transaction_id="TXN-002",
        timestamp=START + timedelta(minutes=20),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        account_id="ACC-002",
        transaction_type=TransactionType.CASH_IN,
        amount=Decimal("5000"),
        status=TransactionStatus.SUCCESS,
        channel="AGENT",
    )

    events = [
        ReplayEvent(
            event_id=feed.feed_event_id,
            event_type=ReplayEventType.FEED_EVENT,
            timestamp=feed.timestamp,
            agent_id=feed.agent_id,
            provider_id=feed.provider_id,
            payload=feed,
        ),
        ReplayEvent(
            event_id=cash_out.transaction_id,
            event_type=ReplayEventType.TRANSACTION,
            timestamp=cash_out.timestamp,
            agent_id=cash_out.agent_id,
            provider_id=cash_out.provider_id,
            payload=cash_out,
        ),
        ReplayEvent(
            event_id=cash_in.transaction_id,
            event_type=ReplayEventType.TRANSACTION,
            timestamp=cash_in.timestamp,
            agent_id=cash_in.agent_id,
            provider_id=cash_in.provider_id,
            payload=cash_in,
        ),
    ]

    balance_engine = BalanceEngine()

    controller = ReplayController(
        events=events,
        opening_balances=opening_balances,
        balance_engine=balance_engine,
    )

    return controller, balance_engine


def test_initial_state() -> None:
    controller, _ = build_controller()

    state = controller.get_state()

    assert state.status == ReplayStatus.READY
    assert state.processed_events == 0
    assert state.remaining_events == 3


def test_step_processes_one_event() -> None:
    controller, _ = build_controller()

    result = controller.step(1)

    assert len(result.events) == 1
    assert result.state.processed_events == 1
    assert result.state.processed_feed_events == 1


def test_advance_updates_balance() -> None:
    controller, balance_engine = build_controller()

    controller.advance(15)

    balance = balance_engine.get_agent_balance("AG001")

    assert balance.shared_cash == Decimal("90000")
    assert balance.provider_balances[0].balance == Decimal("60000")


def test_reset_restores_opening_balance() -> None:
    controller, balance_engine = build_controller()

    controller.advance(15)
    controller.reset()

    balance = balance_engine.get_agent_balance("AG001")

    assert balance.shared_cash == Decimal("100000")
    assert balance.provider_balances[0].balance == Decimal("50000")

    state = controller.get_state()

    assert state.processed_events == 0
    assert state.status == ReplayStatus.READY


def test_replay_completes() -> None:
    controller, _ = build_controller()

    result = controller.advance(60)

    assert result.state.status == ReplayStatus.COMPLETED
    assert result.state.remaining_events == 0
    assert result.state.completion_percentage == 100.0