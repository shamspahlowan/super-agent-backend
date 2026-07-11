from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_replay_controller
from app.replay.controller import ReplayController
from app.schemas.replay import (
    ProcessedReplayEvent,
    ReplayAdvanceRequest,
    ReplayBatchResult,
    ReplayState,
    ReplayStepRequest,
)


router = APIRouter(
    prefix="/replay",
    tags=["Replay"],
)

ReplayControllerDependency = Annotated[
    ReplayController,
    Depends(get_replay_controller),
]


@router.get(
    "/status",
    response_model=ReplayState,
)
def get_replay_status(
    controller: ReplayControllerDependency,
) -> ReplayState:
    return controller.get_state()


@router.post(
    "/reset",
    response_model=ReplayState,
)
def reset_replay(
    controller: ReplayControllerDependency,
) -> ReplayState:
    return controller.reset()


@router.post(
    "/step",
    response_model=ReplayBatchResult,
)
def step_replay(
    payload: ReplayStepRequest,
    controller: ReplayControllerDependency,
) -> ReplayBatchResult:
    return controller.step(payload.event_count)


@router.post(
    "/advance",
    response_model=ReplayBatchResult,
)
def advance_replay(
    payload: ReplayAdvanceRequest,
    controller: ReplayControllerDependency,
) -> ReplayBatchResult:
    return controller.advance(payload.minutes)


@router.get(
    "/recent-events",
    response_model=list[ProcessedReplayEvent],
)
def get_recent_events(
    controller: ReplayControllerDependency,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ProcessedReplayEvent]:
    return controller.get_recent_events(limit)