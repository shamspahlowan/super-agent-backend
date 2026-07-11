from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.dependencies import (
    get_anomaly_engine,
    get_replay_controller,
)
from app.ingestion.canonical_event import ProviderID
from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
    UnknownAnomalyAgentError,
)
from app.replay.controller import ReplayController
from app.schemas.anomaly import (
    AnomalyAssessment,
    AnomalySummary,
)


router = APIRouter(
    prefix="/anomalies",
    tags=["Anomalies"],
)


AnomalyEngineDependency = Annotated[
    AnomalyDetectionEngine,
    Depends(get_anomaly_engine),
]

ReplayControllerDependency = Annotated[
    ReplayController,
    Depends(get_replay_controller),
]


@router.get(
    "",
    response_model=AnomalySummary,
)
def get_anomaly_summary(
    anomaly_engine: AnomalyEngineDependency,
    replay: ReplayControllerDependency,
) -> AnomalySummary:
    simulation_time = replay.get_state().simulation_time

    return anomaly_engine.get_summary(
        as_of=simulation_time,
    )


@router.get(
    "/agents/{agent_id}",
    response_model=AnomalyAssessment,
)
def get_agent_anomaly_assessment(
    agent_id: str,
    anomaly_engine: AnomalyEngineDependency,
    replay: ReplayControllerDependency,
) -> AnomalyAssessment:
    simulation_time = replay.get_state().simulation_time

    try:
        return anomaly_engine.get_agent_assessment(
            agent_id=agent_id,
            as_of=simulation_time,
        )

    except UnknownAnomalyAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/agents/{agent_id}/providers/{provider_id}",
    response_model=AnomalyAssessment,
)
def get_provider_anomaly_assessment(
    agent_id: str,
    provider_id: ProviderID,
    anomaly_engine: AnomalyEngineDependency,
    replay: ReplayControllerDependency,
) -> AnomalyAssessment:
    simulation_time = replay.get_state().simulation_time

    try:
        return anomaly_engine.get_agent_assessment(
            agent_id=agent_id,
            provider_id=provider_id,
            as_of=simulation_time,
        )

    except UnknownAnomalyAgentError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc