from datetime import datetime, timedelta, timezone

import pytest

from app.cases.service import (
    CaseCoordinationService,
    InvalidCaseTransitionError,
    ProviderBoundaryError,
)
from app.ingestion.canonical_event import ProviderID
from app.schemas.case import (
    CasePrincipal,
    CaseResolutionCode,
    CaseStatus,
    NoteVisibility,
)
from app.schemas.incident import (
    IncidentPriority,
    IncidentStatus,
    IncidentType,
    OperationalIncident,
    RoutingRole,
)


DHAKA_TIME = timezone(timedelta(hours=6))

START = datetime(
    2026,
    6,
    20,
    14,
    0,
    tzinfo=DHAKA_TIME,
)


def build_incident(
    *,
    strong_recommendation_allowed: bool = True,
) -> OperationalIncident:
    return OperationalIncident(
        incident_id="INC-TEST-001",
        incident_key=(
            "AG003|COMBINED_PRIORITY|NAGAD"
        ),
        agent_id="AG003",
        area="Zindabazar",
        provider_scope=[ProviderID.NAGAD],
        incident_type=IncidentType.COMBINED_PRIORITY,
        priority=IncidentPriority.P1,
        status=IncidentStatus.ACTIVE,
        title=(
            "Liquidity pressure with unusual activity"
        ),
        summary=(
            "Liquidity pressure and unusual activity "
            "require human review."
        ),
        confidence=0.82,
        receiver_role=RoutingRole.FIELD_OFFICER,
        responsible_stakeholder=(
            RoutingRole.PROVIDER_OPERATIONS
        ),
        human_review_required=True,
        strong_recommendation_allowed=(
            strong_recommendation_allowed
        ),
        recommended_next_step=(
            "Contact the agent, verify demand and review "
            "the listed evidence."
        ),
        evidence=[],
        uncertainty=[
            "The pattern may have a legitimate explanation."
        ],
        alternative_explanations=[
            "Eid-related customer demand."
        ],
        created_at=START,
        updated_at=START,
        occurrences=1,
    )


def field_officer() -> CasePrincipal:
    return CasePrincipal(
        actor_id="USR-FO-001",
        display_name="Farhan Ahmed",
        role=RoutingRole.FIELD_OFFICER,
    )


def nagad_operations_owner() -> CasePrincipal:
    return CasePrincipal(
        actor_id="USR-OPS-001",
        display_name="Nusrat Jahan",
        role=RoutingRole.PROVIDER_OPERATIONS,
        provider_id=ProviderID.NAGAD,
    )


def risk_reviewer() -> CasePrincipal:
    return CasePrincipal(
        actor_id="USR-RISK-001",
        display_name="Rahim Uddin",
        role=RoutingRole.RISK_REVIEWER,
        provider_id=ProviderID.NAGAD,
    )


def test_case_is_created_from_incident() -> None:
    service = CaseCoordinationService()

    case = service.ensure_case_for_incident(
        incident=build_incident(),
        as_of=START,
    )

    assert case.status == CaseStatus.OPEN

    assert (
        case.responsible_stakeholder
        == RoutingRole.PROVIDER_OPERATIONS
    )

    assert case.case_owner is None

    assert (
        case.automated_financial_action_allowed
        is False
    )

    assert len(case.history) == 1


def test_acknowledgement_and_owner_are_separate() -> None:
    service = CaseCoordinationService()

    case = service.ensure_case_for_incident(
        incident=build_incident(),
        as_of=START,
    )

    acknowledged = service.acknowledge_case(
        case_id=case.case_id,
        actor=field_officer(),
        acknowledged_at=START + timedelta(minutes=2),
        note="Agent contact initiated.",
    )

    assert (
        acknowledged.status
        == CaseStatus.ACKNOWLEDGED
    )

    assert acknowledged.acknowledged_by is not None
    assert acknowledged.case_owner is None

    assigned = service.assign_case(
        case_id=case.case_id,
        assigned_by=field_officer(),
        owner=nagad_operations_owner(),
        assigned_at=START + timedelta(minutes=4),
    )

    assert assigned.status == CaseStatus.ASSIGNED
    assert assigned.case_owner is not None

    assert (
        assigned.case_owner.actor_id
        == "USR-OPS-001"
    )

    assert (
        assigned.acknowledged_by.actor_id
        == "USR-FO-001"
    )


def test_cannot_resolve_open_case() -> None:
    service = CaseCoordinationService()

    case = service.ensure_case_for_incident(
        incident=build_incident(),
        as_of=START,
    )

    with pytest.raises(
        InvalidCaseTransitionError
    ):
        service.resolve_case(
            case_id=case.case_id,
            actor=risk_reviewer(),
            resolution_code=(
                CaseResolutionCode.NO_ACTION_REQUIRED
            ),
            summary="No action was required.",
            resolved_at=START + timedelta(minutes=5),
        )


def test_case_note_creates_audit_entry() -> None:
    service = CaseCoordinationService()

    case = service.ensure_case_for_incident(
        incident=build_incident(),
        as_of=START,
    )

    updated = service.add_note(
        case_id=case.case_id,
        author=nagad_operations_owner(),
        body=(
            "Agent confirmed unusually high customer demand."
        ),
        visibility=NoteVisibility.PROVIDER_SCOPED,
        created_at=START + timedelta(minutes=3),
    )

    assert len(updated.notes) == 1
    assert len(updated.history) == 2

    assert (
        updated.notes[0].visibility
        == NoteVisibility.PROVIDER_SCOPED
    )


def test_escalation_resolution_and_closure() -> None:
    service = CaseCoordinationService()

    case = service.ensure_case_for_incident(
        incident=build_incident(),
        as_of=START,
    )

    service.acknowledge_case(
        case_id=case.case_id,
        actor=field_officer(),
        acknowledged_at=START + timedelta(minutes=1),
    )

    service.assign_case(
        case_id=case.case_id,
        assigned_by=field_officer(),
        owner=nagad_operations_owner(),
        assigned_at=START + timedelta(minutes=2),
    )

    service.start_review(
        case_id=case.case_id,
        actor=nagad_operations_owner(),
        started_at=START + timedelta(minutes=3),
    )

    escalated = service.escalate_case(
        case_id=case.case_id,
        actor=nagad_operations_owner(),
        target_role=RoutingRole.RISK_REVIEWER,
        escalated_at=START + timedelta(minutes=5),
        reason=(
            "Repeated near-identical transactions "
            "require specialist review."
        ),
    )

    assert escalated.status == CaseStatus.ESCALATED

    resolved = service.resolve_case(
        case_id=case.case_id,
        actor=risk_reviewer(),
        resolution_code=(
            CaseResolutionCode.ESCALATED_OUTSIDE_SYSTEM
        ),
        summary=(
            "Evidence was forwarded for external compliance review. "
            "No fraud determination was made by this prototype."
        ),
        resolved_at=START + timedelta(minutes=15),
    )

    assert resolved.status == CaseStatus.RESOLVED
    assert resolved.resolution is not None

    closed = service.close_case(
        case_id=case.case_id,
        actor=risk_reviewer(),
        closed_at=START + timedelta(minutes=20),
        note="Coordination workflow completed.",
    )

    assert closed.status == CaseStatus.CLOSED
    assert len(closed.history) == 7


def test_provider_boundary_is_enforced() -> None:
    service = CaseCoordinationService()

    case = service.ensure_case_for_incident(
        incident=build_incident(),
        as_of=START,
    )

    rocket_actor = CasePrincipal(
        actor_id="USR-ROCKET-001",
        display_name="Rocket Operations User",
        role=RoutingRole.PROVIDER_OPERATIONS,
        provider_id=ProviderID.ROCKET,
    )

    with pytest.raises(ProviderBoundaryError):
        service.acknowledge_case(
            case_id=case.case_id,
            actor=rocket_actor,
            acknowledged_at=START + timedelta(minutes=1),
        )


def test_low_confidence_incident_activates_safe_fallback() -> None:
    service = CaseCoordinationService()

    case = service.ensure_case_for_incident(
        incident=build_incident(
            strong_recommendation_allowed=False
        ),
        as_of=START,
    )

    assert case.safe_fallback_active is True
    assert case.safe_fallback_reason is not None

    assert (
        case.automated_financial_action_allowed
        is False
    )