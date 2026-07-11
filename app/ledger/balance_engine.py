from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from threading import RLock

from app.ingestion.canonical_event import (
    OpeningBalance,
    ProviderID,
    ResourceType,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from app.schemas.balance import (
    AgentBalanceView,
    ProviderBalanceView,
    TransactionApplicationResult,
    TransactionBalanceState,
)


ZERO = Decimal("0")


class BalanceEngineError(RuntimeError):
    """Base error for balance-ledger operations."""


class DuplicateTransactionError(BalanceEngineError):
    """Raised when the same transaction is processed twice."""


class UnknownAgentError(BalanceEngineError):
    """Raised when an unknown agent is requested."""


class UnknownProviderBalanceError(BalanceEngineError):
    """Raised when an agent has no balance for a provider."""


class InvalidOpeningBalanceError(BalanceEngineError):
    """Raised when opening-balance data is incomplete or duplicated."""


class BalanceEngine:
    """
    In-memory operational balance ledger.

    Transaction effects:

    CASH_IN:
        Shared physical cash increases.
        Provider e-money decreases.

    CASH_OUT:
        Shared physical cash decreases.
        Provider e-money increases.

    Failed transactions do not affect balances.
    """

    def __init__(
        self,
        opening_balances: list[OpeningBalance] | None = None,
    ) -> None:
        self._lock = RLock()

        self._shared_cash: dict[str, Decimal] = {}
        self._provider_emoney: dict[
            tuple[str, ProviderID],
            Decimal,
        ] = {}

        self._last_updated_at: dict[str, datetime] = {}

        self._processed_transaction_ids: set[str] = set()

        self._processed_count: dict[str, int] = {}
        self._ignored_failed_count: dict[str, int] = {}

        if opening_balances:
            self.initialize(opening_balances)

    def initialize(
        self,
        opening_balances: list[OpeningBalance],
    ) -> None:
        """
        Reset and initialize the ledger from opening balances.
        """

        if not opening_balances:
            raise InvalidOpeningBalanceError(
                "At least one opening balance is required."
            )

        with self._lock:
            self.reset()

            for balance in opening_balances:
                agent_id = balance.agent_id

                self._processed_count.setdefault(agent_id, 0)
                self._ignored_failed_count.setdefault(agent_id, 0)

                current_timestamp = self._last_updated_at.get(agent_id)

                if (
                    current_timestamp is None
                    or balance.timestamp > current_timestamp
                ):
                    self._last_updated_at[agent_id] = balance.timestamp

                if balance.resource_type == ResourceType.SHARED_CASH:
                    if agent_id in self._shared_cash:
                        raise InvalidOpeningBalanceError(
                            "Duplicate shared-cash opening balance for "
                            f"agent {agent_id}."
                        )

                    self._shared_cash[agent_id] = (
                        balance.opening_balance
                    )
                    continue

                if balance.provider_id is None:
                    raise InvalidOpeningBalanceError(
                        "Provider e-money balance is missing provider_id."
                    )

                key = (
                    agent_id,
                    balance.provider_id,
                )

                if key in self._provider_emoney:
                    raise InvalidOpeningBalanceError(
                        "Duplicate provider opening balance for "
                        f"agent={agent_id}, "
                        f"provider={balance.provider_id.value}."
                    )

                self._provider_emoney[key] = (
                    balance.opening_balance
                )

            self._validate_initialized_agents()

    def reset(self) -> None:
        with self._lock:
            self._shared_cash.clear()
            self._provider_emoney.clear()
            self._last_updated_at.clear()
            self._processed_transaction_ids.clear()
            self._processed_count.clear()
            self._ignored_failed_count.clear()

    def apply_transaction(
        self,
        transaction: TransactionEvent,
    ) -> TransactionApplicationResult:
        """
        Apply one transaction exactly once.
        """

        with self._lock:
            self._validate_transaction_reference(transaction)

            if (
                transaction.transaction_id
                in self._processed_transaction_ids
            ):
                raise DuplicateTransactionError(
                    "Transaction has already been processed: "
                    f"{transaction.transaction_id}"
                )

            agent_id = transaction.agent_id
            provider_key = (
                transaction.agent_id,
                transaction.provider_id,
            )

            before_cash = self._shared_cash[agent_id]
            before_emoney = self._provider_emoney[provider_key]

            self._processed_transaction_ids.add(
                transaction.transaction_id
            )

            if transaction.status == TransactionStatus.FAILED:
                self._ignored_failed_count[agent_id] += 1
                self._update_timestamp(
                    agent_id,
                    transaction.timestamp,
                )

                return TransactionApplicationResult(
                    transaction_id=transaction.transaction_id,
                    agent_id=transaction.agent_id,
                    provider_id=transaction.provider_id,
                    applied=False,
                    reason="Failed transaction does not affect balances.",
                    cash_delta=ZERO,
                    provider_emoney_delta=ZERO,
                    before=TransactionBalanceState(
                        shared_cash=before_cash,
                        provider_emoney=before_emoney,
                    ),
                    after=TransactionBalanceState(
                        shared_cash=before_cash,
                        provider_emoney=before_emoney,
                    ),
                    processed_at=transaction.timestamp,
                )

            cash_delta, provider_delta = (
                self._calculate_transaction_deltas(transaction)
            )

            after_cash = before_cash + cash_delta
            after_emoney = before_emoney + provider_delta

            self._shared_cash[agent_id] = after_cash
            self._provider_emoney[provider_key] = after_emoney

            self._processed_count[agent_id] += 1

            self._update_timestamp(
                agent_id,
                transaction.timestamp,
            )

            return TransactionApplicationResult(
                transaction_id=transaction.transaction_id,
                agent_id=transaction.agent_id,
                provider_id=transaction.provider_id,
                applied=True,
                reason="Successful transaction applied to ledger.",
                cash_delta=cash_delta,
                provider_emoney_delta=provider_delta,
                before=TransactionBalanceState(
                    shared_cash=before_cash,
                    provider_emoney=before_emoney,
                ),
                after=TransactionBalanceState(
                    shared_cash=after_cash,
                    provider_emoney=after_emoney,
                ),
                processed_at=transaction.timestamp,
            )

    def get_agent_balance(
        self,
        agent_id: str,
    ) -> AgentBalanceView:
        with self._lock:
            self._ensure_agent_exists(agent_id)

            provider_balances = [
                ProviderBalanceView(
                    provider_id=provider_id,
                    balance=balance,
                    is_negative=balance < ZERO,
                )
                for (
                    stored_agent_id,
                    provider_id,
                ), balance in self._provider_emoney.items()
                if stored_agent_id == agent_id
            ]

            provider_balances.sort(
                key=lambda item: item.provider_id.value
            )

            shared_cash = self._shared_cash[agent_id]

            total_provider_emoney = sum(
                (
                    provider.balance
                    for provider in provider_balances
                ),
                ZERO,
            )

            warnings = self._build_balance_warnings(
                agent_id=agent_id,
                shared_cash=shared_cash,
                provider_balances=provider_balances,
            )

            return AgentBalanceView(
                agent_id=agent_id,
                shared_cash=shared_cash,
                provider_balances=provider_balances,
                total_provider_emoney=total_provider_emoney,
                total_operational_value=(
                    shared_cash + total_provider_emoney
                ),
                processed_transactions=self._processed_count[
                    agent_id
                ],
                ignored_failed_transactions=(
                    self._ignored_failed_count[agent_id]
                ),
                last_updated_at=self._last_updated_at[agent_id],
                warnings=warnings,
            )

    def get_all_agent_balances(
        self,
    ) -> list[AgentBalanceView]:
        with self._lock:
            agent_ids = sorted(self._shared_cash.keys())

        return [
            self.get_agent_balance(agent_id)
            for agent_id in agent_ids
        ]

    def has_processed(
        self,
        transaction_id: str,
    ) -> bool:
        with self._lock:
            return (
                transaction_id
                in self._processed_transaction_ids
            )

    @property
    def total_processed_transactions(self) -> int:
        with self._lock:
            return sum(self._processed_count.values())

    @property
    def total_ignored_failed_transactions(self) -> int:
        with self._lock:
            return sum(self._ignored_failed_count.values())

    def _validate_initialized_agents(self) -> None:
        provider_agents = {
            agent_id
            for agent_id, _ in self._provider_emoney
        }

        agents_without_cash = (
            provider_agents - set(self._shared_cash.keys())
        )

        if agents_without_cash:
            raise InvalidOpeningBalanceError(
                "Provider balances exist without shared cash for: "
                + ", ".join(sorted(agents_without_cash))
            )

        agents_without_provider_balance = (
            set(self._shared_cash.keys()) - provider_agents
        )

        if agents_without_provider_balance:
            raise InvalidOpeningBalanceError(
                "Shared cash exists without provider balances for: "
                + ", ".join(
                    sorted(agents_without_provider_balance)
                )
            )

    def _validate_transaction_reference(
        self,
        transaction: TransactionEvent,
    ) -> None:
        self._ensure_agent_exists(transaction.agent_id)

        provider_key = (
            transaction.agent_id,
            transaction.provider_id,
        )

        if provider_key not in self._provider_emoney:
            raise UnknownProviderBalanceError(
                "No provider balance exists for "
                f"agent={transaction.agent_id}, "
                f"provider={transaction.provider_id.value}."
            )

    def _ensure_agent_exists(
        self,
        agent_id: str,
    ) -> None:
        if agent_id not in self._shared_cash:
            raise UnknownAgentError(
                f"Unknown agent: {agent_id}"
            )

    @staticmethod
    def _calculate_transaction_deltas(
        transaction: TransactionEvent,
    ) -> tuple[Decimal, Decimal]:
        amount = transaction.amount

        if transaction.transaction_type == TransactionType.CASH_IN:
            return amount, -amount

        if transaction.transaction_type == TransactionType.CASH_OUT:
            return -amount, amount

        raise BalanceEngineError(
            "Unsupported transaction type: "
            f"{transaction.transaction_type}"
        )

    def _update_timestamp(
        self,
        agent_id: str,
        timestamp: datetime,
    ) -> None:
        current = self._last_updated_at.get(agent_id)

        if current is None or timestamp > current:
            self._last_updated_at[agent_id] = timestamp

    @staticmethod
    def _build_balance_warnings(
        *,
        agent_id: str,
        shared_cash: Decimal,
        provider_balances: list[ProviderBalanceView],
    ) -> list[str]:
        warnings: list[str] = []

        if shared_cash < ZERO:
            warnings.append(
                f"Agent {agent_id} has a negative shared-cash balance."
            )

        for provider in provider_balances:
            if provider.balance < ZERO:
                warnings.append(
                    f"{provider.provider_id.value} e-money balance "
                    "is negative."
                )

        return warnings