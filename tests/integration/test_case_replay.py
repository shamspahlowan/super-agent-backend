from pathlib import Path

from app.cases.service import CaseCoordinationService
from app.data_quality.trust_score import FeedHealthEngine
from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
)
from app.intelligence.fusion import DecisionFusionEngine
from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)
from app.ledger.balance_engine import BalanceEngine
from app.replay.controller import ReplayController
from app.replay.loader import SyntheticDataLoader
from app.schemas.case import (
    CaseActionType,
    CasePrincipal,
    CaseResolutionCode,
    CaseStatus,
)
from app.schemas.incident import RoutingRole


ROOT_DIR = Path(__file__).resolve().parents[2]


def build_system():
    loader = SyntheticDataLoader(
        ROOT_DIR / "data" / "synthetic"
    )

    bundle = loader.load()
    events = loader.build_event_stream(bundle)

    balance_engine = BalanceEngine()

    feed_health_engine = FeedHealthEngine(
        stale_minutes=15,
        missing_minutes=30,
    )

    liquidity_engine = LiquidityForecastEngine(
        balance_engine=balance_engine,
        feed_health_engine=feed_health_engine,
        lookback_minutes=30,
        safety_buffer_percent=15,
        watch_minutes=120,
        critical_minutes=60,
        min_successful_transactions=3,
    )

    anomaly_engine = AnomalyDetectionEngine(
        window_minutes=15,
        baseline_minutes=180,
        minimum_transactions=6,
        amount_tolerance_percent=2,
        medium_threshold=40,
        high_threshold=70,
    )

    fusion_engine = DecisionFusionEngine(
        liquidity_engine=liquidity_engine,
        anomaly_engine=anomaly_engine,
        feed_health_engine=feed_health_engine,
    )

    case_service = CaseCoordinationService()

    replay = ReplayController(
        events=events,
        opening_balances=bundle.opening_balances,
        balance_engine=balance_engine,
        feed_health_engine=feed_health_engine,
        liquidity_engine=liquidity_engine,
        anomaly_engine=anomaly_engine,
        fusion_engine=fusion_engine,
        case_service=case_service,
        agents=bundle.agents,
        context_events=bundle.context_events,
    )

    return replay, fusion_engine, case_service


def test_replay_automatically_creates_cases() -> None:
    replay, fusion_engine, case_service = (
        build_system()
    )

    replay.advance(314)

    incidents = fusion_engine.get_all_incidents()
    cases = case_service.list_cases()

    assert incidents
    assert cases

    active_incident_ids = {
        incident.incident_id
        for incident in incidents
        if incident.status.value == "ACTIVE"
    }

    case_incident_ids = {
        case.incident_id
        for case in cases
    }

    assert active_incident_ids.issubset(
        case_incident_ids
    )


def test_case_workflow_is_traceable() -> None:
    replay, _, case_service = build_system()

    replay.advance(314)

    cases = case_service.list_cases(
        agent_id="AG003"
    )

    assert cases

    case = cases[0]

    provider_id = (
        case.provider_scope[0]
        if case.provider_scope
        else None
    )

    field_officer = CasePrincipal(
        actor_id="USR-FIELD-001",
        display_name="Farhan Ahmed",
        role=RoutingRole.FIELD_OFFICER,
    )

    owner = CasePrincipal(
        actor_id="USR-OPS-001",
        display_name="Nusrat Jahan",
        role=RoutingRole.PROVIDER_OPERATIONS,
        provider_id=provider_id,
    )

    simulation_time = (
        replay.get_state().simulation_time
    )

    case_service.acknowledge_case(
        case_id=case.case_id,
        actor=field_officer,
        acknowledged_at=simulation_time,
        note="Agent contact initiated.",
    )

    case_service.assign_case(
        case_id=case.case_id,
        assigned_by=field_officer,
        owner=owner,
        assigned_at=simulation_time,
        note="Assigned to provider operations.",
    )

    case_service.start_review(
        case_id=case.case_id,
        actor=owner,
        started_at=simulation_time,
        note="Evidence review started.",
    )

    case_service.resolve_case(
        case_id=case.case_id,
        actor=owner,
        resolution_code=(
            CaseResolutionCode.NO_ACTION_REQUIRED
        ),
        summary=(
            "Activity was reviewed. No automatic or "
            "financial action was taken."
        ),
        resolved_at=simulation_time,
    )

    closed = case_service.close_case(
        case_id=case.case_id,
        actor=owner,
        closed_at=simulation_time,
        note="Human coordination completed.",
    )

    assert closed.status == CaseStatus.CLOSED

    actions = {
        entry.action
        for entry in closed.history
    }

    assert CaseActionType.CASE_CREATED in actions
    assert CaseActionType.ACKNOWLEDGED in actions
    assert CaseActionType.ASSIGNED in actions
    assert CaseActionType.REVIEW_STARTED in actions
    assert CaseActionType.RESOLVED in actions
    assert CaseActionType.CLOSED in actions


def test_replay_reset_clears_cases() -> None:
    replay, _, case_service = build_system()

    replay.advance(314)

    assert case_service.list_cases()

    replay.reset()

    assert case_service.list_cases() == []