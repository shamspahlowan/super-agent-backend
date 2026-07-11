from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import ProviderID


class BalanceSchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        from_attributes=True,
    )


class ProviderBalanceView(BalanceSchema):
    provider_id: ProviderID
    balance: Decimal

    is_negative: bool = False


class AgentBalanceView(BalanceSchema):
    agent_id: str

    shared_cash: Decimal

    provider_balances: list[ProviderBalanceView]

    total_provider_emoney: Decimal
    total_operational_value: Decimal

    processed_transactions: int
    ignored_failed_transactions: int

    last_updated_at: datetime

    warnings: list[str] = Field(default_factory=list)


class TransactionBalanceState(BalanceSchema):
    shared_cash: Decimal
    provider_emoney: Decimal


class TransactionApplicationResult(BalanceSchema):
    transaction_id: str
    agent_id: str
    provider_id: ProviderID

    applied: bool
    reason: str

    cash_delta: Decimal
    provider_emoney_delta: Decimal

    before: TransactionBalanceState
    after: TransactionBalanceState

    processed_at: datetime