from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.data_quality.trust_score import FeedHealthEngine
from app.ingestion.canonical_event import (
    OpeningBalance,
    ProviderID,
    ResourceType,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)
from app.ledger.balance_engine import BalanceEngine
from app.schemas.liquidity import LiquidityStatus


DHAKA_TIME = timezone(timedelta(hours=6))

START = datetime(
    2026,
    6,
    20,
    9,
    0,
    tzinfo=DHAKA_TIME,
)


def build_system(
    *,
    stale_minutes: int = 100,
    missing_minutes: int = 200,
) -> tuple[
    BalanceEngine,
    FeedHealthEngine,
    LiquidityForecastEngine,
]:
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
        OpeningBalance(
            agent_id="AG001",
            provider_id=ProviderID.NAGAD,
            resource_type=ResourceType.PROVIDER_EMONEY,
            opening_balance=Decimal("70000"),
            timestamp=START,
        ),
    ]

    balance_engine = BalanceEngine(opening_balances)

    feed_engine = FeedHealthEngine(
        stale_minutes=stale_minutes,
        missing_minutes=missing_minutes,
    )

    feed_engine.initialize(opening_balances)

    liquidity_engine = LiquidityForecastEngine(
        balance_engine=balance_engine,
        feed_health_engine=feed_engine,
        lookback_minutes=30,
        safety_buffer_percent=10,
        watch_minutes=30,
        critical_minutes=15,
        min_successful_transactions=3,
    )

    liquidity_engine.initialize(opening_balances)

    return (
        balance_engine,
        feed_engine,
        liquidity_engine,
    )


def apply_transaction(
    *,
    balance_engine: BalanceEngine,
    liquidity_engine: LiquidityForecastEngine,
    transaction_id: str,
    minute: int,
    provider_id: ProviderID,
    transaction_type: TransactionType,
    amount: str,
) -> None:
    transaction = TransactionEvent(
        transaction_id=transaction_id,
        timestamp=START + timedelta(minutes=minute),
        agent_id="AG001",
        provider_id=provider_id,
        account_id=f"ACC-{transaction_id}",
        transaction_type=transaction_type,
        amount=Decimal(amount),
        status=TransactionStatus.SUCCESS,
        channel="AGENT",
    )

    balance_engine.apply_transaction(transaction)
    liquidity_engine.record_transaction(transaction)


def test_provider_emoney_pressure_is_detected() -> None:
    balance_engine, _, liquidity_engine = build_system()

    for index, minute in enumerate([5, 10, 15], start=1):
        apply_transaction(
            balance_engine=balance_engine,
            liquidity_engine=liquidity_engine,
            transaction_id=f"TXN-{index}",
            minute=minute,
            provider_id=ProviderID.BKASH,
            transaction_type=TransactionType.CASH_IN,
            amount="10000",
        )

    forecast = liquidity_engine.get_provider_forecast(
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        as_of=START + timedelta(minutes=15),
    )

    assert forecast.status == LiquidityStatus.CRITICAL
    assert forecast.current_balance == Decimal("20000")
    assert forecast.minutes_to_depletion is not None
    assert forecast.net_depletion_per_minute > 0


def test_shared_cash_pressure_is_detected() -> None:
    balance_engine, _, liquidity_engine = build_system()

    for index, minute in enumerate([5, 10, 15], start=1):
        apply_transaction(
            balance_engine=balance_engine,
            liquidity_engine=liquidity_engine,
            transaction_id=f"TXN-{index}",
            minute=minute,
            provider_id=ProviderID.NAGAD,
            transaction_type=TransactionType.CASH_OUT,
            amount="20000",
        )

    forecast = liquidity_engine.get_shared_cash_forecast(
        agent_id="AG001",
        as_of=START + timedelta(minutes=15),
    )

    assert forecast.status == LiquidityStatus.CRITICAL
    assert forecast.current_balance == Decimal("40000")
    assert forecast.driver_provider_id == ProviderID.NAGAD


def test_hidden_provider_shortage_is_detected() -> None:
    balance_engine, _, liquidity_engine = build_system()

    for index, minute in enumerate([5, 10, 15], start=1):
        apply_transaction(
            balance_engine=balance_engine,
            liquidity_engine=liquidity_engine,
            transaction_id=f"TXN-{index}",
            minute=minute,
            provider_id=ProviderID.BKASH,
            transaction_type=TransactionType.CASH_IN,
            amount="10000",
        )

    result = liquidity_engine.get_agent_forecast(
        agent_id="AG001",
        as_of=START + timedelta(minutes=15),
    )

    assert result.shared_cash.status == LiquidityStatus.SAFE
    assert result.hidden_provider_shortage is True
    assert result.most_urgent_provider == ProviderID.BKASH


def test_missing_feed_returns_insufficient_data() -> None:
    (
        balance_engine,
        _,
        liquidity_engine,
    ) = build_system(
        stale_minutes=15,
        missing_minutes=30,
    )

    for index, minute in enumerate([5, 10, 15], start=1):
        apply_transaction(
            balance_engine=balance_engine,
            liquidity_engine=liquidity_engine,
            transaction_id=f"TXN-{index}",
            minute=minute,
            provider_id=ProviderID.BKASH,
            transaction_type=TransactionType.CASH_IN,
            amount="5000",
        )

    forecast = liquidity_engine.get_provider_forecast(
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        as_of=START + timedelta(minutes=40),
    )

    assert forecast.status == LiquidityStatus.INSUFFICIENT_DATA
    assert forecast.forecast_available is False
    assert forecast.can_issue_strong_recommendation is False


def test_non_depleting_shared_cash_is_safe() -> None:
    balance_engine, _, liquidity_engine = build_system()

    transactions = [
        (
            "TXN-1",
            5,
            TransactionType.CASH_OUT,
            "10000",
        ),
        (
            "TXN-2",
            10,
            TransactionType.CASH_IN,
            "15000",
        ),
        (
            "TXN-3",
            15,
            TransactionType.CASH_OUT,
            "5000",
        ),
    ]

    for transaction_id, minute, transaction_type, amount in transactions:
        apply_transaction(
            balance_engine=balance_engine,
            liquidity_engine=liquidity_engine,
            transaction_id=transaction_id,
            minute=minute,
            provider_id=ProviderID.BKASH,
            transaction_type=transaction_type,
            amount=amount,
        )

    forecast = liquidity_engine.get_shared_cash_forecast(
        agent_id="AG001",
        as_of=START + timedelta(minutes=15),
    )

    assert forecast.status == LiquidityStatus.SAFE
    assert forecast.net_depletion_per_minute == Decimal("0")
    assert forecast.minutes_to_depletion is None