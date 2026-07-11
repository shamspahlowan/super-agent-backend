from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.dependencies import (
    get_feed_health_engine,
    get_replay_controller,
)
from app.data_quality.trust_score import (
    FeedHealthEngine,
    UnknownFeedError,
)
from app.ingestion.canonical_event import ProviderID
from app.replay.controller import ReplayController
from app.schemas.data_quality import (
    FeedHealthSummary,
    FeedHealthView,
)


router = APIRouter(
    prefix="/data-quality",
    tags=["Data Quality"],
)


FeedHealthDependency = Annotated[
    FeedHealthEngine,
    Depends(get_feed_health_engine),
]

ReplayDependency = Annotated[
    ReplayController,
    Depends(get_replay_controller),
]


@router.get("")
def get_data_quality_summary(
    feed_engine: FeedHealthDependency,
    replay: ReplayDependency,
):
    simulation_time = replay.get_state().simulation_time
    return feed_engine.get_summary(as_of=simulation_time)


@router.get(
    "/feeds",
    response_model=list[FeedHealthView],
)
def list_feed_health(
    feed_engine: FeedHealthDependency,
    replay: ReplayDependency,
) -> list[FeedHealthView]:
    simulation_time = replay.get_state().simulation_time

    return feed_engine.get_all_health(
        as_of=simulation_time,
    )


@router.get(
    "/agents/{agent_id}",
    response_model=list[FeedHealthView],
)
def get_agent_feed_health(
    agent_id: str,
    feed_engine: FeedHealthDependency,
    replay: ReplayDependency,
) -> list[FeedHealthView]:
    simulation_time = replay.get_state().simulation_time

    try:
        return feed_engine.get_agent_health(
            agent_id=agent_id,
            as_of=simulation_time,
        )

    except UnknownFeedError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/agents/{agent_id}/providers/{provider_id}",
    response_model=FeedHealthView,
)
def get_single_feed_health(
    agent_id: str,
    provider_id: ProviderID,
    feed_engine: FeedHealthDependency,
    replay: ReplayDependency,
) -> FeedHealthView:
    simulation_time = replay.get_state().simulation_time

    try:
        return feed_engine.get_feed_health(
            agent_id=agent_id,
            provider_id=provider_id,
            as_of=simulation_time,
        )

    except UnknownFeedError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc