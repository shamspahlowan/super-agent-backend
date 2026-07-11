from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.ingestion.canonical_event import (
    OpeningBalance,
    ProviderID,
    ResourceType,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from app.ledger.balance_engine import (
    BalanceEngine,
    DuplicateTransactionError,
)


DHAKA_TIME = timezone(timedelta(hours=6))
OPENING_TIME = datetime(
    2026,
    6,
    20,
    9,
    0,
    tzinfo=DHAKA_TIME,
)


@pytest.fixture
def balance_engine() -> BalanceEngine:
    balances = [
        OpeningBalance(
            agent_id="AG001",
            provider_id=None,
            resource_type=ResourceType.SHARED_CASH,
            opening_balance=Decimal("100000"),
            timestamp=OPENING_TIME,
        ),
        OpeningBalance(
            agent_id="AG001",
            provider_id=ProviderID.BKASH,
            resource_type=ResourceType.PROVIDER_EMONEY,
            opening_balance=Decimal("50000"),
            timestamp=OPENING_TIME,
        ),
        OpeningBalance(
            agent_id="AG001",
            provider_id=ProviderID.NAGAD,
            resource_type=ResourceType.PROVIDER_EMONEY,
            opening_balance=Decimal("60000"),
            timestamp=OPENING_TIME,
        ),
    ]

    return BalanceEngine(balances)


def create_transaction(
    *,
    transaction_id: str,
    transaction_type: TransactionType,
    amount: str,
    status: TransactionStatus = TransactionStatus.SUCCESS,
) -> TransactionEvent:
    return TransactionEvent(
        transaction_id=transaction_id,
        timestamp=OPENING_TIME + timedelta(minutes=10),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        account_id="ACC-001",
        transaction_type=transaction_type,
        amount=Decimal(amount),
        status=status,
        channel="AGENT",
    )


def test_cash_in_increases_cash_and_reduces_emoney(
    balance_engine: BalanceEngine,
) -> None:
    transaction = create_transaction(
        transaction_id="TXN-001",
        transaction_type=TransactionType.CASH_IN,
        amount="10000",
    )

    result = balance_engine.apply_transaction(transaction)

    assert result.applied is True
    assert result.after.shared_cash == Decimal("110000")
    assert result.after.provider_emoney == Decimal("40000")


def test_cash_out_reduces_cash_and_increases_emoney(
    balance_engine: BalanceEngine,
) -> None:
    transaction = create_transaction(
        transaction_id="TXN-002",
        transaction_type=TransactionType.CASH_OUT,
        amount="10000",
    )

    result = balance_engine.apply_transaction(transaction)

    assert result.applied is True
    assert result.after.shared_cash == Decimal("90000")
    assert result.after.provider_emoney == Decimal("60000")


def test_failed_transaction_does_not_change_balance(
    balance_engine: BalanceEngine,
) -> None:
    transaction = create_transaction(
        transaction_id="TXN-003",
        transaction_type=TransactionType.CASH_OUT,
        amount="10000",
        status=TransactionStatus.FAILED,
    )

    result = balance_engine.apply_transaction(transaction)

    assert result.applied is False
    assert result.after.shared_cash == Decimal("100000")
    assert result.after.provider_emoney == Decimal("50000")


def test_duplicate_transaction_is_rejected(
    balance_engine: BalanceEngine,
) -> None:
    transaction = create_transaction(
        transaction_id="TXN-004",
        transaction_type=TransactionType.CASH_IN,
        amount="5000",
    )

    balance_engine.apply_transaction(transaction)

    with pytest.raises(DuplicateTransactionError):
        balance_engine.apply_transaction(transaction)


def test_total_operational_value(
    balance_engine: BalanceEngine,
) -> None:
    balance = balance_engine.get_agent_balance("AG001")

    assert balance.shared_cash == Decimal("100000")
    assert balance.total_provider_emoney == Decimal("110000")
    assert balance.total_operational_value == Decimal("210000")