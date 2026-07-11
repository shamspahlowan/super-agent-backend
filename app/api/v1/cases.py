from typing import Annotated, NoReturn

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)

from app.api.dependencies import (
    get_case_service,
    get_replay_controller,
)
from app.cases.service import (
    CaseCoordinationError,
    CaseCoordinationService,
    InvalidCaseTransitionError,
    ProviderBoundaryError,
    UnknownCaseError,
)
from app.ingestion.canonical_event import ProviderID
from app.replay.controller import ReplayController
from app.schemas.case import (
    AcknowledgeCaseRequest,
    AddCaseNoteRequest,
    AssignCaseRequest,
    CaseAuditEntry,
    CaseStatus,
    CaseSummary,
    CloseCaseRequest,
    CoordinationCase,
    EscalateCaseRequest,
    ResolveCaseRequest,
    StartReviewRequest,
)


router = APIRouter(
    prefix="/cases",
    tags=["Cases"],
)


CaseServiceDependency = Annotated[
    CaseCoordinationService,
    Depends(get_case_service),
]

ReplayDependency = Annotated[
    ReplayController,
    Depends(get_replay_controller),
]


def raise_case_http_error(
    exc: CaseCoordinationError,
) -> NoReturn:
    if isinstance(exc, UnknownCaseError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(exc, ProviderBoundaryError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    if isinstance(exc, InvalidCaseTransitionError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    ) from exc


@router.get(
    "",
    response_model=CaseSummary,
)
def get_case_summary(
    case_service: CaseServiceDependency,
    replay: ReplayDependency,
) -> CaseSummary:
    simulation_time = (
        replay.get_state().simulation_time
    )

    return case_service.get_summary(
        as_of=simulation_time,
    )


@router.get(
    "/queue",
    response_model=list[CoordinationCase],
)
def list_case_queue(
    case_service: CaseServiceDependency,
    case_status: Annotated[
        CaseStatus | None,
        Query(alias="status"),
    ] = None,
    agent_id: str | None = None,
    provider_id: ProviderID | None = None,
) -> list[CoordinationCase]:
    return case_service.list_cases(
        status=case_status,
        agent_id=agent_id,
        provider_id=provider_id,
    )


@router.get(
    "/by-incident/{incident_id}",
    response_model=CoordinationCase,
)
def get_case_by_incident(
    incident_id: str,
    case_service: CaseServiceDependency,
) -> CoordinationCase:
    try:
        return case_service.get_case_for_incident(
            incident_id
        )

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)


@router.get(
    "/{case_id}",
    response_model=CoordinationCase,
)
def get_case(
    case_id: str,
    case_service: CaseServiceDependency,
) -> CoordinationCase:
    try:
        return case_service.get_case(case_id)

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)


@router.get(
    "/{case_id}/history",
    response_model=list[CaseAuditEntry],
)
def get_case_history(
    case_id: str,
    case_service: CaseServiceDependency,
) -> list[CaseAuditEntry]:
    try:
        case = case_service.get_case(case_id)
        return case.history

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)


@router.post(
    "/{case_id}/acknowledge",
    response_model=CoordinationCase,
)
def acknowledge_case(
    case_id: str,
    request: AcknowledgeCaseRequest,
    case_service: CaseServiceDependency,
    replay: ReplayDependency,
) -> CoordinationCase:
    try:
        return case_service.acknowledge_case(
            case_id=case_id,
            actor=request.actor,
            acknowledged_at=(
                replay.get_state().simulation_time
            ),
            note=request.note,
        )

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)


@router.post(
    "/{case_id}/assign",
    response_model=CoordinationCase,
)
def assign_case(
    case_id: str,
    request: AssignCaseRequest,
    case_service: CaseServiceDependency,
    replay: ReplayDependency,
) -> CoordinationCase:
    try:
        return case_service.assign_case(
            case_id=case_id,
            assigned_by=request.assigned_by,
            owner=request.owner,
            assigned_at=(
                replay.get_state().simulation_time
            ),
            note=request.note,
        )

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)


@router.post(
    "/{case_id}/start-review",
    response_model=CoordinationCase,
)
def start_case_review(
    case_id: str,
    request: StartReviewRequest,
    case_service: CaseServiceDependency,
    replay: ReplayDependency,
) -> CoordinationCase:
    try:
        return case_service.start_review(
            case_id=case_id,
            actor=request.actor,
            started_at=(
                replay.get_state().simulation_time
            ),
            note=request.note,
        )

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)


@router.post(
    "/{case_id}/notes",
    response_model=CoordinationCase,
)
def add_case_note(
    case_id: str,
    request: AddCaseNoteRequest,
    case_service: CaseServiceDependency,
    replay: ReplayDependency,
) -> CoordinationCase:
    try:
        return case_service.add_note(
            case_id=case_id,
            author=request.author,
            body=request.body,
            visibility=request.visibility,
            created_at=(
                replay.get_state().simulation_time
            ),
        )

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)


@router.post(
    "/{case_id}/escalate",
    response_model=CoordinationCase,
)
def escalate_case(
    case_id: str,
    request: EscalateCaseRequest,
    case_service: CaseServiceDependency,
    replay: ReplayDependency,
) -> CoordinationCase:
    try:
        return case_service.escalate_case(
            case_id=case_id,
            actor=request.actor,
            target_role=request.target_role,
            escalated_at=(
                replay.get_state().simulation_time
            ),
            reason=request.reason,
        )

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)


@router.post(
    "/{case_id}/resolve",
    response_model=CoordinationCase,
)
def resolve_case(
    case_id: str,
    request: ResolveCaseRequest,
    case_service: CaseServiceDependency,
    replay: ReplayDependency,
) -> CoordinationCase:
    try:
        return case_service.resolve_case(
            case_id=case_id,
            actor=request.actor,
            resolution_code=(
                request.resolution_code
            ),
            summary=request.summary,
            resolved_at=(
                replay.get_state().simulation_time
            ),
        )

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)


@router.post(
    "/{case_id}/close",
    response_model=CoordinationCase,
)
def close_case(
    case_id: str,
    request: CloseCaseRequest,
    case_service: CaseServiceDependency,
    replay: ReplayDependency,
) -> CoordinationCase:
    try:
        return case_service.close_case(
            case_id=case_id,
            actor=request.actor,
            closed_at=(
                replay.get_state().simulation_time
            ),
            note=request.note,
        )

    except CaseCoordinationError as exc:
        raise_case_http_error(exc)