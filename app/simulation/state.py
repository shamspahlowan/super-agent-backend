from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from threading import RLock

from app.ingestion.canonical_event import (
    ProviderID,
    TransactionStatus,
    TransactionType,
)
from app.simulation.config import SimulationConfig


ZERO = Decimal("0")


class SimulationStateError(RuntimeError):
    """Base exception for simulation-state operations."""


class UnknownSimulationAgentError(
    SimulationStateError
):
    """Raised when a simulated agent does not exist."""


class UnknownSimulationProviderError(
    SimulationStateError
):
    """Raised when a simulated provider balance does not exist."""


@dataclass(frozen=True)
class SimulationDecision:
    status: TransactionStatus

    failure_reason: str | None

    shared_cash_before: Decimal
    shared_cash_after: Decimal

    provider_balance_before: Decimal
    provider_balance_after: Decimal


class SimulationState:
    """
    Stateful simulated financial position.

    This simulator never allows successful transactions to create
    unexplained negative balances.
    """

    def __init__(
        self,
        *,
        shared_cash: dict[str, Decimal],
        provider_emoney: dict[
            tuple[str, ProviderID],
            Decimal,
        ],
    ) -> None:
        self._shared_cash = dict(shared_cash)
        self._provider_emoney = dict(
            provider_emoney
        )

        self._lock = RLock()

    @classmethod
    def from_config(
        cls,
        config: SimulationConfig,
    ) -> "SimulationState":
        shared_cash: dict[str, Decimal] = {}

        provider_emoney: dict[
            tuple[str, ProviderID],
            Decimal,
        ] = {}

        for agent in config.agents:
            shared_cash[
                agent.agent_id
            ] = agent.opening_shared_cash

            for opening in agent.provider_openings:
                provider_emoney[
                    (
                        agent.agent_id,
                        opening.provider_id,
                    )
                ] = opening.balance

        return cls(
            shared_cash=shared_cash,
            provider_emoney=provider_emoney,
        )
    

    def get_shared_cash(
        self,
        agent_id: str,
    ) -> Decimal:
        with self._lock:
            return self._get_shared_cash(agent_id)


    def get_provider_balance(
        self,
        agent_id: str,
        provider_id: ProviderID,
    ) -> Decimal:
        with self._lock:
            return self._get_provider_balance(
                agent_id,
                provider_id,
            )

    def apply_transaction(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
        transaction_type: TransactionType,
        amount: Decimal,
    ) -> SimulationDecision:
        if amount <= ZERO:
            raise SimulationStateError(
                "Transaction amount must be positive."
            )

        with self._lock:
            shared_before = self._get_shared_cash(
                agent_id
            )

            provider_before = (
                self._get_provider_balance(
                    agent_id,
                    provider_id,
                )
            )

            if (
                transaction_type
                == TransactionType.CASH_OUT
            ):
                if shared_before < amount:
                    return SimulationDecision(
                        status=TransactionStatus.FAILED,
                        failure_reason=(
                            "INSUFFICIENT_SHARED_CASH"
                        ),
                        shared_cash_before=shared_before,
                        shared_cash_after=shared_before,
                        provider_balance_before=(
                            provider_before
                        ),
                        provider_balance_after=(
                            provider_before
                        ),
                    )

                shared_after = shared_before - amount
                provider_after = (
                    provider_before + amount
                )

            elif (
                transaction_type
                == TransactionType.CASH_IN
            ):
                if provider_before < amount:
                    return SimulationDecision(
                        status=TransactionStatus.FAILED,
                        failure_reason=(
                            "INSUFFICIENT_PROVIDER_EMONEY"
                        ),
                        shared_cash_before=shared_before,
                        shared_cash_after=shared_before,
                        provider_balance_before=(
                            provider_before
                        ),
                        provider_balance_after=(
                            provider_before
                        ),
                    )

                shared_after = shared_before + amount
                provider_after = (
                    provider_before - amount
                )

            else:
                raise SimulationStateError(
                    "Unsupported transaction type: "
                    f"{transaction_type}"
                )

            self._shared_cash[
                agent_id
            ] = shared_after

            self._provider_emoney[
                (
                    agent_id,
                    provider_id,
                )
            ] = provider_after

            return SimulationDecision(
                status=TransactionStatus.SUCCESS,
                failure_reason=None,
                shared_cash_before=shared_before,
                shared_cash_after=shared_after,
                provider_balance_before=(
                    provider_before
                ),
                provider_balance_after=provider_after,
            )

    def reject_transaction(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
        failure_reason: str,
    ) -> SimulationDecision:
        with self._lock:
            shared_cash = self._get_shared_cash(
                agent_id
            )

            provider_balance = (
                self._get_provider_balance(
                    agent_id,
                    provider_id,
                )
            )

            return SimulationDecision(
                status=TransactionStatus.FAILED,
                failure_reason=failure_reason,
                shared_cash_before=shared_cash,
                shared_cash_after=shared_cash,
                provider_balance_before=(
                    provider_balance
                ),
                provider_balance_after=(
                    provider_balance
                ),
            )

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "shared_cash": {
                    agent_id: str(balance)
                    for agent_id, balance
                    in sorted(
                        self._shared_cash.items()
                    )
                },
                "provider_emoney": {
                    (
                        f"{agent_id}:{provider_id.value}"
                    ): str(balance)
                    for (
                        agent_id,
                        provider_id,
                    ), balance
                    in sorted(
                        self._provider_emoney.items(),
                        key=lambda item: (
                            item[0][0],
                            item[0][1].value,
                        ),
                    )
                },
            }

    def assert_invariants(self) -> None:
        with self._lock:
            negative_shared = {
                agent_id: balance
                for agent_id, balance
                in self._shared_cash.items()
                if balance < ZERO
            }

            negative_provider = {
                (
                    agent_id,
                    provider_id.value,
                ): balance
                for (
                    agent_id,
                    provider_id,
                ), balance
                in self._provider_emoney.items()
                if balance < ZERO
            }

        if negative_shared or negative_provider:
            raise SimulationStateError(
                "Simulation produced negative balances. "
                f"shared={negative_shared}, "
                f"provider={negative_provider}"
            )

    def _get_shared_cash(
        self,
        agent_id: str,
    ) -> Decimal:
        if agent_id not in self._shared_cash:
            raise UnknownSimulationAgentError(
                f"Unknown simulation agent: {agent_id}"
            )

        return self._shared_cash[agent_id]

    def _get_provider_balance(
        self,
        agent_id: str,
        provider_id: ProviderID,
    ) -> Decimal:
        key = (
            agent_id,
            provider_id,
        )

        if key not in self._provider_emoney:
            raise UnknownSimulationProviderError(
                "Unknown simulated provider balance: "
                f"agent={agent_id}, "
                f"provider={provider_id.value}"
            )

        return self._provider_emoney[key]