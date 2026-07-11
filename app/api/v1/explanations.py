from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.dependencies import (
    get_case_service,
    get_explanation_service,
    get_fusion_engine,
)
from app.cases.service import (
    CaseCoordinationService,
    UnknownCaseError,
)
from app.explanations.service import (
    ExplanationProviderBoundaryError,
    ExplanationServiceError,
    GroundedExplanationService,
)
from app.intelligence.fusion import (
    DecisionFusionEngine,
    UnknownIncidentError,
)
from app.schemas.explanation import (
    ExplanationHealth,
    ExplanationHealthStatus,
    GenerateExplanationRequest,
    GroundedExplanation,
)


router = APIRouter(
    prefix="/explanations",
    tags=["AI Explanations"],
)


ExplanationServiceDependency = Annotated[
    GroundedExplanationService,
    Depends(get_explanation_service),
]

FusionEngineDependency = Annotated[
    DecisionFusionEngine,
    Depends(get_fusion_engine),
]

CaseServiceDependency = Annotated[
    CaseCoordinationService,
    Depends(get_case_service),
]


def raise_explanation_http_error(
    exc: Exception,
) -> NoReturn:
    if isinstance(
        exc,
        (
            UnknownIncidentError,
            UnknownCaseError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        ExplanationProviderBoundaryError,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    if isinstance(
        exc,
        ExplanationServiceError,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raise HTTPException(
        status_code=(
            status.HTTP_500_INTERNAL_SERVER_ERROR
        ),
        detail="Explanation generation failed.",
    ) from exc


@router.get(
    "/health",
    response_model=ExplanationHealth,
)
def get_explanation_health(
    explanation_service: ExplanationServiceDependency,
) -> ExplanationHealth:
    generator = explanation_service.generator

    ai_configured = (
        explanation_service.enabled
        and generator is not None
    )

    model = getattr(
        generator,
        "model",
        None,
    )

    if not explanation_service.enabled:
        health_status = (
            ExplanationHealthStatus.DISABLED
        )

        message = (
            "AI explanations are disabled. "
            "Deterministic fallback remains available."
        )

    elif ai_configured:
        health_status = (
            ExplanationHealthStatus.AI_READY
        )

        message = (
            "OpenAI structured explanations are configured. "
            "Unsafe or unavailable outputs use deterministic fallback."
        )

    else:
        health_status = (
            ExplanationHealthStatus.FALLBACK_ONLY
        )

        message = (
            "OpenAI is unavailable or not configured. "
            "Deterministic explanations remain operational."
        )

    return ExplanationHealth(
        status=health_status,
        ai_enabled=explanation_service.enabled,
        ai_configured=ai_configured,
        model=model,
        structured_outputs_enabled=True,
        deterministic_fallback_available=True,
        message=message,
    )


@router.post(
    "/incidents/{incident_id}",
    response_model=GroundedExplanation,
)
def explain_incident(
    incident_id: str,
    request: GenerateExplanationRequest,
    explanation_service: ExplanationServiceDependency,
    fusion_engine: FusionEngineDependency,
    case_service: CaseServiceDependency,
) -> GroundedExplanation:
    try:
        incident = fusion_engine.get_incident(
            incident_id
        )

        coordination_case = None

        try:
            coordination_case = (
                case_service.get_case_for_incident(
                    incident_id
                )
            )

        except UnknownCaseError:
            coordination_case = None

        return (
            explanation_service
            .generate_explanation(
                incident=incident,
                request=request,
                coordination_case=(
                    coordination_case
                ),
            )
        )

    except Exception as exc:
        raise_explanation_http_error(exc)


@router.post(
    "/cases/{case_id}",
    response_model=GroundedExplanation,
)
def explain_case(
    case_id: str,
    request: GenerateExplanationRequest,
    explanation_service: ExplanationServiceDependency,
    fusion_engine: FusionEngineDependency,
    case_service: CaseServiceDependency,
) -> GroundedExplanation:
    try:
        coordination_case = (
            case_service.get_case(case_id)
        )

        incident = fusion_engine.get_incident(
            coordination_case.incident_id
        )

        return (
            explanation_service
            .generate_explanation(
                incident=incident,
                request=request,
                coordination_case=(
                    coordination_case
                ),
            )
        )

    except Exception as exc:
        raise_explanation_http_error(exc)