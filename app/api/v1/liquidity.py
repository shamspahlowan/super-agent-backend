from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.dependencies import (
    get_liquidity_engine,
    get_replay_controller,
)
from app.ingestion.canonical_event import ProviderID
from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
    UnknownLiquidityResourceError,
)
from app.replay.controller import ReplayController
from app.schemas.liquidity import (
    AgentLiquidityForecast,
    LiquiditySummary,
    ResourceLiquidityForecast,
)


router = APIRouter(
    prefix="/liquidity",
    tags=["Liquidity"],
)


LiquidityEngineDependency = Annotated[
    LiquidityForecastEngine,
    Depends(get_liquidity_engine),
]

ReplayControllerDependency = Annotated[
    ReplayController,
    Depends(get_replay_controller),
]


@router.get(
    "",
    response_model=LiquiditySummary,
)
def get_liquidity_summary(
    liquidity_engine: LiquidityEngineDependency,
    replay: ReplayControllerDependency,
) -> LiquiditySummary:
    simulation_time = replay.get_state().simulation_time

    return liquidity_engine.get_summary(
        as_of=simulation_time,
    )


@router.get(
    "/agents/{agent_id}",
    response_model=AgentLiquidityForecast,
)
def get_agent_liquidity(
    agent_id: str,
    liquidity_engine: LiquidityEngineDependency,
    replay: ReplayControllerDependency,
) -> AgentLiquidityForecast:
    simulation_time = replay.get_state().simulation_time

    try:
        return liquidity_engine.get_agent_forecast(
            agent_id=agent_id,
            as_of=simulation_time,
        )

    except UnknownLiquidityResourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/agents/{agent_id}/shared-cash",
    response_model=ResourceLiquidityForecast,
)
def get_shared_cash_forecast(
    agent_id: str,
    liquidity_engine: LiquidityEngineDependency,
    replay: ReplayControllerDependency,
) -> ResourceLiquidityForecast:
    simulation_time = replay.get_state().simulation_time

    try:
        return liquidity_engine.get_shared_cash_forecast(
            agent_id=agent_id,
            as_of=simulation_time,
        )

    except UnknownLiquidityResourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/agents/{agent_id}/providers/{provider_id}",
    response_model=ResourceLiquidityForecast,
)
def get_provider_liquidity_forecast(
    agent_id: str,
    provider_id: ProviderID,
    liquidity_engine: LiquidityEngineDependency,
    replay: ReplayControllerDependency,
) -> ResourceLiquidityForecast:
    simulation_time = replay.get_state().simulation_time

    try:
        return liquidity_engine.get_provider_forecast(
            agent_id=agent_id,
            provider_id=provider_id,
            as_of=simulation_time,
        )

    except UnknownLiquidityResourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc