from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_balance_engine
from app.ledger.balance_engine import (
    BalanceEngine,
    UnknownAgentError,
)
from app.schemas.balance import AgentBalanceView


router = APIRouter(
    prefix="/balances",
    tags=["Balances"],
)

BalanceEngineDependency = Annotated[
    BalanceEngine,
    Depends(get_balance_engine),
]


@router.get(
    "",
    response_model=list[AgentBalanceView],
)
def list_agent_balances(
    ledger: BalanceEngineDependency,
) -> list[AgentBalanceView]:
    return ledger.get_all_agent_balances()


@router.get(
    "/{agent_id}",
    response_model=AgentBalanceView,
)
def get_agent_balance(
    agent_id: str,
    ledger: BalanceEngineDependency,
) -> AgentBalanceView:
    try:
        return ledger.get_agent_balance(agent_id)

    except UnknownAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc