from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)

from app.api.dependencies import (
    get_fusion_engine,
    get_replay_controller,
)
from app.intelligence.fusion import (
    DecisionFusionEngine,
    UnknownFusionAgentError,
    UnknownIncidentError,
)
from app.replay.controller import ReplayController
from app.schemas.incident import (
    IncidentSummary,
    OperationalIncident,
)


router = APIRouter(
    prefix="/incidents",
    tags=["Incidents"],
)


FusionEngineDependency = Annotated[
    DecisionFusionEngine,
    Depends(get_fusion_engine),
]

ReplayControllerDependency = Annotated[
    ReplayController,
    Depends(get_replay_controller),
]


@router.get(
    "",
    response_model=IncidentSummary,
)
def get_incident_summary(
    fusion_engine: FusionEngineDependency,
    replay: ReplayControllerDependency,
) -> IncidentSummary:
    simulation_time = replay.get_state().simulation_time

    return fusion_engine.get_summary(
        as_of=simulation_time,
    )


@router.get(
    "/active",
    response_model=list[OperationalIncident],
)
def list_active_incidents(
    fusion_engine: FusionEngineDependency,
) -> list[OperationalIncident]:
    return fusion_engine.get_active_incidents()


@router.get(
    "/agents/{agent_id}",
    response_model=list[OperationalIncident],
)
def list_agent_incidents(
    agent_id: str,
    fusion_engine: FusionEngineDependency,
    include_cleared: Annotated[
        bool,
        Query(
            description=(
                "Include incidents that were automatically cleared."
            )
        ),
    ] = True,
) -> list[OperationalIncident]:
    try:
        return fusion_engine.get_agent_incidents(
            agent_id,
            include_cleared=include_cleared,
        )

    except UnknownFusionAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/{incident_id}",
    response_model=OperationalIncident,
)
def get_incident(
    incident_id: str,
    fusion_engine: FusionEngineDependency,
) -> OperationalIncident:
    try:
        return fusion_engine.get_incident(
            incident_id
        )

    except UnknownIncidentError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc