from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.data_quality.trust_score import FeedHealthEngine
from app.ingestion.canonical_event import (
    AgentRecord,
    FeedEvent,
    FeedEventType,
    OpeningBalance,
    ProviderID,
    ResourceType,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
)
from app.intelligence.fusion import (
    DecisionFusionEngine,
)
from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)
from app.ledger.balance_engine import BalanceEngine
from app.schemas.incident import (
    IncidentPriority,
    IncidentStatus,
    IncidentType,
)


DHAKA_TIME = timezone(timedelta(hours=6))

START = datetime(
    2026,
    6,
    20,
    9,
    0,
    tzinfo=DHAKA_TIME,
)


def build_system():
    agents = [
        AgentRecord(
            agent_id="AG001",
            agent_name="Test Agent",
            area="Zindabazar",
            district="Sylhet",
        )
    ]

    opening_balances = [
        OpeningBalance(
            agent_id="AG001",
            provider_id=None,
            resource_type=ResourceType.SHARED_CASH,
            opening_balance=Decimal("100000"),
            timestamp=START,
        ),
        OpeningBalance(
            agent_id="AG001",
            provider_id=ProviderID.BKASH,
            resource_type=ResourceType.PROVIDER_EMONEY,
            opening_balance=Decimal("50000"),
            timestamp=START,
        ),
    ]

    balance_engine = BalanceEngine(opening_balances)

    feed_engine = FeedHealthEngine(
        stale_minutes=500,
        missing_minutes=1000,
    )
    feed_engine.initialize(opening_balances)

    liquidity_engine = LiquidityForecastEngine(
        balance_engine=balance_engine,
        feed_health_engine=feed_engine,
        lookback_minutes=30,
        safety_buffer_percent=10,
        watch_minutes=30,
        critical_minutes=15,
        min_successful_transactions=3,
    )
    liquidity_engine.initialize(opening_balances)

    anomaly_engine = AnomalyDetectionEngine(
        window_minutes=15,
        baseline_minutes=180,
        minimum_transactions=6,
        amount_tolerance_percent=2,
        medium_threshold=40,
        high_threshold=70,
    )
    anomaly_engine.initialize(
        agents=agents,
        context_events=[],
    )

    fusion_engine = DecisionFusionEngine(
        liquidity_engine=liquidity_engine,
        anomaly_engine=anomaly_engine,
        feed_health_engine=feed_engine,
    )
    fusion_engine.initialize(agents)

    return (
        balance_engine,
        feed_engine,
        liquidity_engine,
        anomaly_engine,
        fusion_engine,
    )


def apply_transaction(
    *,
    balance_engine,
    liquidity_engine,
    anomaly_engine,
    transaction_id: str,
    minute: int,
    account_id: str,
    amount: str,
) -> None:
    transaction = TransactionEvent(
        transaction_id=transaction_id,
        timestamp=START + timedelta(minutes=minute),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        account_id=account_id,
        transaction_type=TransactionType.CASH_OUT,
        amount=Decimal(amount),
        status=TransactionStatus.SUCCESS,
        channel="AGENT",
    )

    balance_engine.apply_transaction(transaction)
    liquidity_engine.record_transaction(transaction)
    anomaly_engine.record_transaction(transaction)


def test_combined_priority_incident_is_created() -> None:
    (
        balance_engine,
        _,
        liquidity_engine,
        anomaly_engine,
        fusion_engine,
    ) = build_system()

    amounts = [
    "9900",
    "10000",
    "10050",
    "9950",
    "10000",
    "10100",
    "9980",
    "10020",
    ]

    for index, amount in enumerate(amounts):
        apply_transaction(
            balance_engine=balance_engine,
            liquidity_engine=liquidity_engine,
            anomaly_engine=anomaly_engine,
            transaction_id=f"TXN-{index}",
            minute=5 + index,
            account_id=f"ACC-{index % 2}",
            amount=amount,
        )

    incidents = fusion_engine.refresh_agent(
        agent_id="AG001",
        as_of=START + timedelta(minutes=15),
    )

    combined = [
        incident
        for incident in incidents
        if incident.incident_type
        == IncidentType.COMBINED_PRIORITY
    ]

    assert len(combined) == 1
    assert combined[0].priority == IncidentPriority.P1
    assert combined[0].human_review_required is True


def test_data_quality_incident_is_created() -> None:
    (
        _,
        feed_engine,
        _,
        _,
        fusion_engine,
    ) = build_system()

    conflict = FeedEvent(
        feed_event_id="FEED-001",
        timestamp=START + timedelta(minutes=10),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        event_type=FeedEventType.BALANCE_CONFLICT,
        delay_minutes=0,
        reported_balance=Decimal("90000"),
    )

    feed_engine.record_event(
        conflict,
        calculated_balance=Decimal("50000"),
    )

    incidents = fusion_engine.refresh_agent(
        agent_id="AG001",
        as_of=START + timedelta(minutes=10),
    )

    assert any(
        incident.incident_type
        == IncidentType.DATA_QUALITY
        for incident in incidents
    )


def test_resolved_data_issue_clears_incident() -> None:
    (
        _,
        feed_engine,
        _,
        _,
        fusion_engine,
    ) = build_system()

    conflict = FeedEvent(
        feed_event_id="FEED-001",
        timestamp=START + timedelta(minutes=10),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        event_type=FeedEventType.BALANCE_CONFLICT,
        delay_minutes=0,
        reported_balance=Decimal("90000"),
    )

    feed_engine.record_event(
        conflict,
        calculated_balance=Decimal("50000"),
    )

    fusion_engine.refresh_agent(
        agent_id="AG001",
        as_of=START + timedelta(minutes=10),
    )

    feed_engine.resolve_conflict(
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        resolved_at=START + timedelta(minutes=15),
        calculated_balance=Decimal("50000"),
    )

    fusion_engine.refresh_agent(
        agent_id="AG001",
        as_of=START + timedelta(minutes=15),
    )

    incidents = fusion_engine.get_agent_incidents(
        "AG001"
    )

    assert incidents[0].status == IncidentStatus.CLEARED