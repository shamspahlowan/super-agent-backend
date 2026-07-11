from __future__ import annotations

import hashlib
from datetime import datetime
from threading import RLock

from app.ingestion.canonical_event import ProviderID
from app.schemas.case import (
    CaseActionType,
    CaseAuditEntry,
    CaseNote,
    CasePrincipal,
    CaseResolution,
    CaseResolutionCode,
    CaseStatus,
    CaseSummary,
    CoordinationCase,
    NoteVisibility,
)
from app.schemas.incident import (
    IncidentStatus,
    OperationalIncident,
    RoutingRole,
)


class CaseCoordinationError(RuntimeError):
    """Base exception for case coordination."""


class UnknownCaseError(CaseCoordinationError):
    """Raised when a case cannot be found."""


class InvalidCaseTransitionError(CaseCoordinationError):
    """Raised when a case status transition is invalid."""


class ProviderBoundaryError(CaseCoordinationError):
    """Raised when an actor crosses a provider boundary."""


class CaseCoordinationService:
    def __init__(self) -> None:
        self._cases: dict[str, CoordinationCase] = {}

        self._incident_to_case: dict[str, str] = {}

        self._audit_counter = 0
        self._note_counter = 0

        self._lock = RLock()

    def reset(self) -> None:
        with self._lock:
            self._cases.clear()
            self._incident_to_case.clear()

            self._audit_counter = 0
            self._note_counter = 0

    def sync_incidents(
        self,
        *,
        incidents: list[OperationalIncident],
        as_of: datetime,
    ) -> list[CoordinationCase]:
        """
        Synchronize decision-fusion incidents with coordination cases.

        Active incidents create or update cases.
        Cleared incidents update an existing case but do not
        automatically resolve or close it.
        """

        self._validate_timestamp(as_of)

        synchronized: list[CoordinationCase] = []

        for incident in incidents:
            if incident.status == IncidentStatus.ACTIVE:
                synchronized.append(
                    self.ensure_case_for_incident(
                        incident=incident,
                        as_of=as_of,
                    )
                )
                continue

            with self._lock:
                existing_case_id = self._incident_to_case.get(
                    incident.incident_id
                )

                if existing_case_id is None:
                    continue

                synchronized.append(
                    self._sync_existing_case(
                        case_id=existing_case_id,
                        incident=incident,
                        as_of=as_of,
                    )
                )

        return sorted(
            synchronized,
            key=lambda case: (
                self._priority_rank(case),
                -case.updated_at.timestamp(),
            ),
        )

    def ensure_case_for_incident(
        self,
        *,
        incident: OperationalIncident,
        as_of: datetime,
    ) -> CoordinationCase:
        """
        Create one deterministic coordination case for an incident.

        Repeated calls update the source incident snapshot without
        destroying acknowledgement, assignment, notes or history.
        """

        self._validate_timestamp(as_of)

        with self._lock:
            existing_case_id = self._incident_to_case.get(
                incident.incident_id
            )

            if existing_case_id is not None:
                return self._sync_existing_case(
                    case_id=existing_case_id,
                    incident=incident,
                    as_of=as_of,
                )

            case_id = self._case_id(
                incident.incident_id
            )

            safe_fallback_active = (
                not incident.strong_recommendation_allowed
            )

            safe_fallback_reason = (
                self._safe_fallback_reason(incident)
                if safe_fallback_active
                else None
            )

            creation_audit = self._new_audit(
                case_id=case_id,
                action=CaseActionType.CASE_CREATED,
                occurred_at=as_of,
                from_status=None,
                to_status=CaseStatus.OPEN,
                note=(
                    "Case created automatically from an "
                    "operational incident."
                ),
                details={
                    "incident_id": incident.incident_id,
                    "incident_type": (
                        incident.incident_type.value
                    ),
                    "priority": incident.priority.value,
                },
            )

            case = CoordinationCase(
                case_id=case_id,
                incident_id=incident.incident_id,
                agent_id=incident.agent_id,
                area=incident.area,
                provider_scope=incident.provider_scope,
                source_incident_status=incident.status,
                priority=incident.priority,
                status=CaseStatus.OPEN,
                receiver_role=incident.receiver_role,
                responsible_stakeholder=(
                    incident.responsible_stakeholder
                ),
                recommended_next_step=(
                    incident.recommended_next_step
                ),
                human_review_required=(
                    incident.human_review_required
                ),
                safe_fallback_active=(
                    safe_fallback_active
                ),
                safe_fallback_reason=(
                    safe_fallback_reason
                ),
                advisory_only=True,
                automated_financial_action_allowed=False,
                notes=[],
                history=[creation_audit],
                created_at=as_of,
                updated_at=as_of,
            )

            self._cases[case_id] = case

            self._incident_to_case[
                incident.incident_id
            ] = case_id

            return case

    def acknowledge_case(
        self,
        *,
        case_id: str,
        actor: CasePrincipal,
        acknowledged_at: datetime,
        note: str | None = None,
    ) -> CoordinationCase:
        self._validate_timestamp(acknowledged_at)

        with self._lock:
            case = self._get_case(case_id)

            self._validate_actor_scope(
                case=case,
                actor=actor,
            )

            if case.status != CaseStatus.OPEN:
                raise InvalidCaseTransitionError(
                    "Only an OPEN case can be acknowledged. "
                    f"Current status: {case.status.value}."
                )

            audit = self._new_audit(
                case_id=case.case_id,
                action=CaseActionType.ACKNOWLEDGED,
                actor=actor,
                occurred_at=acknowledged_at,
                from_status=case.status,
                to_status=CaseStatus.ACKNOWLEDGED,
                note=note,
            )

            updated = case.model_copy(
                update={
                    "status": CaseStatus.ACKNOWLEDGED,
                    "acknowledged_by": actor,
                    "acknowledged_at": acknowledged_at,
                    "updated_at": acknowledged_at,
                    "history": [
                        *case.history,
                        audit,
                    ],
                }
            )

            self._cases[case_id] = updated

            return updated

    def assign_case(
        self,
        *,
        case_id: str,
        assigned_by: CasePrincipal,
        owner: CasePrincipal,
        assigned_at: datetime,
        note: str | None = None,
    ) -> CoordinationCase:
        self._validate_timestamp(assigned_at)

        with self._lock:
            case = self._get_case(case_id)

            self._validate_actor_scope(
                case=case,
                actor=assigned_by,
            )

            self._validate_actor_scope(
                case=case,
                actor=owner,
            )

            allowed_statuses = {
                CaseStatus.ACKNOWLEDGED,
                CaseStatus.ASSIGNED,
                CaseStatus.ESCALATED,
            }

            if case.status not in allowed_statuses:
                raise InvalidCaseTransitionError(
                    "Case assignment requires ACKNOWLEDGED, "
                    "ASSIGNED or ESCALATED status. "
                    f"Current status: {case.status.value}."
                )

            audit = self._new_audit(
                case_id=case.case_id,
                action=CaseActionType.ASSIGNED,
                actor=assigned_by,
                occurred_at=assigned_at,
                from_status=case.status,
                to_status=CaseStatus.ASSIGNED,
                note=note,
                details={
                    "owner_id": owner.actor_id,
                    "owner_name": owner.display_name,
                    "owner_role": owner.role.value,
                    "owner_provider": (
                        owner.provider_id.value
                        if owner.provider_id
                        else ""
                    ),
                },
            )

            updated = case.model_copy(
                update={
                    "status": CaseStatus.ASSIGNED,
                    "case_owner": owner,
                    "updated_at": assigned_at,
                    "history": [
                        *case.history,
                        audit,
                    ],
                }
            )

            self._cases[case_id] = updated

            return updated

    def start_review(
        self,
        *,
        case_id: str,
        actor: CasePrincipal,
        started_at: datetime,
        note: str | None = None,
    ) -> CoordinationCase:
        self._validate_timestamp(started_at)

        with self._lock:
            case = self._get_case(case_id)

            self._validate_actor_scope(
                case=case,
                actor=actor,
            )

            allowed_statuses = {
                CaseStatus.ASSIGNED,
                CaseStatus.ESCALATED,
            }

            if case.status not in allowed_statuses:
                raise InvalidCaseTransitionError(
                    "Human review can start only from ASSIGNED "
                    "or ESCALATED status. "
                    f"Current status: {case.status.value}."
                )

            audit = self._new_audit(
                case_id=case.case_id,
                action=CaseActionType.REVIEW_STARTED,
                actor=actor,
                occurred_at=started_at,
                from_status=case.status,
                to_status=CaseStatus.IN_REVIEW,
                note=note,
            )

            updated = case.model_copy(
                update={
                    "status": CaseStatus.IN_REVIEW,
                    "updated_at": started_at,
                    "history": [
                        *case.history,
                        audit,
                    ],
                }
            )

            self._cases[case_id] = updated

            return updated

    def add_note(
        self,
        *,
        case_id: str,
        author: CasePrincipal,
        body: str,
        visibility: NoteVisibility,
        created_at: datetime,
    ) -> CoordinationCase:
        self._validate_timestamp(created_at)

        cleaned_body = body.strip()

        if not cleaned_body:
            raise CaseCoordinationError(
                "Case note body cannot be empty."
            )

        with self._lock:
            case = self._get_case(case_id)

            self._validate_actor_scope(
                case=case,
                actor=author,
            )

            if case.status == CaseStatus.CLOSED:
                raise InvalidCaseTransitionError(
                    "Notes cannot be added to a CLOSED case."
                )

            if (
                visibility
                == NoteVisibility.PROVIDER_SCOPED
                and author.provider_id is None
            ):
                raise ProviderBoundaryError(
                    "A provider-scoped note requires the author "
                    "to have a provider identity."
                )

            self._note_counter += 1

            note = CaseNote(
                note_id=f"NOTE-{self._note_counter:06d}",
                author=author,
                body=cleaned_body,
                visibility=visibility,
                created_at=created_at,
            )

            audit = self._new_audit(
                case_id=case.case_id,
                action=CaseActionType.NOTE_ADDED,
                actor=author,
                occurred_at=created_at,
                from_status=case.status,
                to_status=case.status,
                note="Case note added.",
                details={
                    "note_id": note.note_id,
                    "visibility": visibility.value,
                },
            )

            updated = case.model_copy(
                update={
                    "notes": [
                        *case.notes,
                        note,
                    ],
                    "history": [
                        *case.history,
                        audit,
                    ],
                    "updated_at": created_at,
                }
            )

            self._cases[case_id] = updated

            return updated

    def escalate_case(
        self,
        *,
        case_id: str,
        actor: CasePrincipal,
        target_role: RoutingRole,
        escalated_at: datetime,
        reason: str,
    ) -> CoordinationCase:
        self._validate_timestamp(escalated_at)

        cleaned_reason = reason.strip()

        if not cleaned_reason:
            raise CaseCoordinationError(
                "Escalation reason cannot be empty."
            )

        if target_role == RoutingRole.AGENT:
            raise InvalidCaseTransitionError(
                "A case cannot be escalated to the AGENT role."
            )

        with self._lock:
            case = self._get_case(case_id)

            self._validate_actor_scope(
                case=case,
                actor=actor,
            )

            allowed_statuses = {
                CaseStatus.ACKNOWLEDGED,
                CaseStatus.ASSIGNED,
                CaseStatus.IN_REVIEW,
            }

            if case.status not in allowed_statuses:
                raise InvalidCaseTransitionError(
                    "Escalation requires ACKNOWLEDGED, ASSIGNED "
                    "or IN_REVIEW status. "
                    f"Current status: {case.status.value}."
                )

            audit = self._new_audit(
                case_id=case.case_id,
                action=CaseActionType.ESCALATED,
                actor=actor,
                occurred_at=escalated_at,
                from_status=case.status,
                to_status=CaseStatus.ESCALATED,
                note=cleaned_reason,
                details={
                    "target_role": target_role.value,
                },
            )

            updated = case.model_copy(
                update={
                    "status": CaseStatus.ESCALATED,
                    "escalation_target_role": target_role,
                    "escalated_at": escalated_at,
                    "updated_at": escalated_at,
                    "history": [
                        *case.history,
                        audit,
                    ],
                }
            )

            self._cases[case_id] = updated

            return updated

    def resolve_case(
        self,
        *,
        case_id: str,
        actor: CasePrincipal,
        resolution_code: CaseResolutionCode,
        summary: str,
        resolved_at: datetime,
    ) -> CoordinationCase:
        self._validate_timestamp(resolved_at)

        cleaned_summary = summary.strip()

        if not cleaned_summary:
            raise CaseCoordinationError(
                "Resolution summary cannot be empty."
            )

        with self._lock:
            case = self._get_case(case_id)

            self._validate_actor_scope(
                case=case,
                actor=actor,
            )

            allowed_statuses = {
                CaseStatus.ASSIGNED,
                CaseStatus.IN_REVIEW,
                CaseStatus.ESCALATED,
            }

            if case.status not in allowed_statuses:
                raise InvalidCaseTransitionError(
                    "Resolution requires ASSIGNED, IN_REVIEW "
                    "or ESCALATED status. "
                    f"Current status: {case.status.value}."
                )

            resolution = CaseResolution(
                code=resolution_code,
                summary=cleaned_summary,
                resolved_by=actor,
                resolved_at=resolved_at,
            )

            audit = self._new_audit(
                case_id=case.case_id,
                action=CaseActionType.RESOLVED,
                actor=actor,
                occurred_at=resolved_at,
                from_status=case.status,
                to_status=CaseStatus.RESOLVED,
                note=cleaned_summary,
                details={
                    "resolution_code": (
                        resolution_code.value
                    ),
                },
            )

            updated = case.model_copy(
                update={
                    "status": CaseStatus.RESOLVED,
                    "resolution": resolution,
                    "updated_at": resolved_at,
                    "history": [
                        *case.history,
                        audit,
                    ],
                }
            )

            self._cases[case_id] = updated

            return updated

    def close_case(
        self,
        *,
        case_id: str,
        actor: CasePrincipal,
        closed_at: datetime,
        note: str | None = None,
    ) -> CoordinationCase:
        self._validate_timestamp(closed_at)

        with self._lock:
            case = self._get_case(case_id)

            self._validate_actor_scope(
                case=case,
                actor=actor,
            )

            if case.status != CaseStatus.RESOLVED:
                raise InvalidCaseTransitionError(
                    "Only a RESOLVED case can be closed. "
                    f"Current status: {case.status.value}."
                )

            audit = self._new_audit(
                case_id=case.case_id,
                action=CaseActionType.CLOSED,
                actor=actor,
                occurred_at=closed_at,
                from_status=case.status,
                to_status=CaseStatus.CLOSED,
                note=note,
            )

            updated = case.model_copy(
                update={
                    "status": CaseStatus.CLOSED,
                    "updated_at": closed_at,
                    "history": [
                        *case.history,
                        audit,
                    ],
                }
            )

            self._cases[case_id] = updated

            return updated

    def get_case(
        self,
        case_id: str,
    ) -> CoordinationCase:
        with self._lock:
            return self._get_case(case_id)

    def get_case_for_incident(
        self,
        incident_id: str,
    ) -> CoordinationCase:
        with self._lock:
            case_id = self._incident_to_case.get(
                incident_id
            )

            if case_id is None:
                raise UnknownCaseError(
                    "No coordination case exists for incident "
                    f"{incident_id}."
                )

            return self._get_case(case_id)

    def list_cases(
        self,
        *,
        status: CaseStatus | None = None,
        agent_id: str | None = None,
        provider_id: ProviderID | None = None,
    ) -> list[CoordinationCase]:
        with self._lock:
            cases = list(self._cases.values())

        if status is not None:
            cases = [
                case
                for case in cases
                if case.status == status
            ]

        if agent_id is not None:
            cases = [
                case
                for case in cases
                if case.agent_id == agent_id
            ]

        if provider_id is not None:
            cases = [
                case
                for case in cases
                if provider_id in case.provider_scope
            ]

        return sorted(
            cases,
            key=lambda case: (
                self._priority_rank(case),
                -case.updated_at.timestamp(),
            ),
        )

    def get_summary(
        self,
        *,
        as_of: datetime,
    ) -> CaseSummary:
        self._validate_timestamp(as_of)

        cases = self.list_cases()

        return CaseSummary(
            as_of=as_of,
            total_cases=len(cases),
            open=sum(
                case.status == CaseStatus.OPEN
                for case in cases
            ),
            acknowledged=sum(
                case.status == CaseStatus.ACKNOWLEDGED
                for case in cases
            ),
            assigned=sum(
                case.status == CaseStatus.ASSIGNED
                for case in cases
            ),
            in_review=sum(
                case.status == CaseStatus.IN_REVIEW
                for case in cases
            ),
            escalated=sum(
                case.status == CaseStatus.ESCALATED
                for case in cases
            ),
            resolved=sum(
                case.status == CaseStatus.RESOLVED
                for case in cases
            ),
            closed=sum(
                case.status == CaseStatus.CLOSED
                for case in cases
            ),
            safe_fallback_cases=sum(
                case.safe_fallback_active
                for case in cases
            ),
            cases=cases,
        )

    def _sync_existing_case(
        self,
        *,
        case_id: str,
        incident: OperationalIncident,
        as_of: datetime,
    ) -> CoordinationCase:
        case = self._get_case(case_id)

        safe_fallback_active = (
            not incident.strong_recommendation_allowed
        )

        safe_fallback_reason = (
            self._safe_fallback_reason(incident)
            if safe_fallback_active
            else None
        )

        changed = any(
            [
                case.source_incident_status
                != incident.status,
                case.priority != incident.priority,
                case.provider_scope
                != incident.provider_scope,
                case.recommended_next_step
                != incident.recommended_next_step,
                case.safe_fallback_active
                != safe_fallback_active,
            ]
        )

        if not changed:
            return case

        audit = self._new_audit(
            case_id=case.case_id,
            action=CaseActionType.INCIDENT_SYNCED,
            occurred_at=as_of,
            from_status=case.status,
            to_status=case.status,
            note=(
                "Source incident snapshot updated."
            ),
            details={
                "incident_status": incident.status.value,
                "priority": incident.priority.value,
                "safe_fallback_active": str(
                    safe_fallback_active
                ).lower(),
            },
        )

        updated = case.model_copy(
            update={
                "source_incident_status": incident.status,
                "priority": incident.priority,
                "provider_scope": incident.provider_scope,
                "receiver_role": incident.receiver_role,
                "responsible_stakeholder": (
                    incident.responsible_stakeholder
                ),
                "recommended_next_step": (
                    incident.recommended_next_step
                ),
                "human_review_required": (
                    incident.human_review_required
                ),
                "safe_fallback_active": (
                    safe_fallback_active
                ),
                "safe_fallback_reason": (
                    safe_fallback_reason
                ),
                "updated_at": as_of,
                "history": [
                    *case.history,
                    audit,
                ],
            }
        )

        self._cases[case_id] = updated

        return updated

    def _new_audit(
        self,
        *,
        case_id: str,
        action: CaseActionType,
        occurred_at: datetime,
        actor: CasePrincipal | None = None,
        from_status: CaseStatus | None = None,
        to_status: CaseStatus | None = None,
        note: str | None = None,
        details: dict[str, str] | None = None,
    ) -> CaseAuditEntry:
        self._audit_counter += 1

        return CaseAuditEntry(
            audit_id=f"AUD-{self._audit_counter:06d}",
            case_id=case_id,
            action=action,
            actor=actor,
            from_status=from_status,
            to_status=to_status,
            note=note,
            details=details or {},
            occurred_at=occurred_at,
        )

    def _get_case(
        self,
        case_id: str,
    ) -> CoordinationCase:
        case = self._cases.get(case_id)

        if case is None:
            raise UnknownCaseError(
                f"Unknown coordination case: {case_id}"
            )

        return case

    @staticmethod
    def _validate_actor_scope(
        *,
        case: CoordinationCase,
        actor: CasePrincipal,
    ) -> None:
        if (
            case.provider_scope
            and actor.provider_id is not None
            and actor.provider_id
            not in case.provider_scope
        ):
            raise ProviderBoundaryError(
                "Actor provider is outside the provider scope "
                f"of case {case.case_id}."
            )

    @staticmethod
    def _safe_fallback_reason(
        incident: OperationalIncident,
    ) -> str:
        if incident.status == IncidentStatus.CLEARED:
            return (
                "The source incident is cleared, but the case "
                "still requires an explicit human resolution."
            )

        return (
            "The source incident does not permit a strong "
            "recommendation because data trust, confidence or "
            "available evidence is insufficient. Verify the "
            "relevant inputs before operational action."
        )

    @staticmethod
    def _case_id(
        incident_id: str,
    ) -> str:
        digest = hashlib.sha1(
            incident_id.encode("utf-8")
        ).hexdigest()[:12].upper()

        return f"CASE-{digest}"

    @staticmethod
    def _priority_rank(
        case: CoordinationCase,
    ) -> int:
        priorities = {
            "P1": 1,
            "P2": 2,
            "P3": 3,
            "P4": 4,
        }

        return priorities[case.priority.value]

    @staticmethod
    def _validate_timestamp(
        value: datetime,
    ) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise CaseCoordinationError(
                "Case timestamps must include timezone information."
            )