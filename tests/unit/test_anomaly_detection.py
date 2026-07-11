from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.ingestion.canonical_event import (
    AgentRecord,
    ContextEvent,
    ProviderID,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
)
from app.schemas.anomaly import (
    AnomalyBand,
    AnomalyCategory,
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


def build_engine() -> AnomalyDetectionEngine:
    engine = AnomalyDetectionEngine(
        window_minutes=15,
        baseline_minutes=180,
        minimum_transactions=6,
        amount_tolerance_percent=2,
        medium_threshold=40,
        high_threshold=70,
    )

    engine.initialize(
        agents=[
            AgentRecord(
                agent_id="AG001",
                agent_name="Test Agent",
                area="Zindabazar",
                district="Sylhet",
            )
        ],
        context_events=[
            ContextEvent(
                context_id="CTX-001",
                area="Zindabazar",
                event_type="EID_MARKET_SURGE",
                start_time=START + timedelta(hours=5),
                end_time=START + timedelta(hours=7),
                expected_demand_multiplier=2.5,
                description="Expected Eid demand.",
            )
        ],
    )

    return engine


def record(
    engine: AnomalyDetectionEngine,
    *,
    transaction_id: str,
    minute: int,
    provider_id: ProviderID,
    account_id: str,
    amount: str,
    status: TransactionStatus = TransactionStatus.SUCCESS,
) -> None:
    engine.record_transaction(
        TransactionEvent(
            transaction_id=transaction_id,
            timestamp=START + timedelta(minutes=minute),
            agent_id="AG001",
            provider_id=provider_id,
            account_id=account_id,
            transaction_type=TransactionType.CASH_OUT,
            amount=Decimal(amount),
            status=status,
            channel="AGENT",
        )
    )


def test_repeated_small_account_cluster_requires_review() -> None:
    engine = build_engine()

    amounts = [
        "9900",
        "10000",
        "10050",
        "9950",
        "10000",
        "10100",
        "9980",
        "10020",
        "10000",
    ]

    for index, amount in enumerate(amounts):
        record(
            engine,
            transaction_id=f"TXN-{index}",
            minute=300 + index,
            provider_id=ProviderID.NAGAD,
            account_id=f"ACC-{index % 3}",
            amount=amount,
        )

    result = engine.get_agent_assessment(
        agent_id="AG001",
        as_of=START + timedelta(minutes=314),
    )

    assert result.band == AnomalyBand.HIGH
    assert result.category == AnomalyCategory.REQUIRES_REVIEW
    assert result.requires_human_review is True

    codes = {
        factor.code
        for factor in result.factors
    }

    assert "NEAR_IDENTICAL_AMOUNTS" in codes
    assert "ACCOUNT_CONCENTRATION" in codes


def test_legitimate_eid_spike_is_context_adjusted() -> None:
    engine = build_engine()

    amounts = [
        "1200",
        "2100",
        "3700",
        "5500",
        "7200",
        "2800",
        "9400",
        "4600",
        "6300",
        "1700",
        "8100",
        "3300",
    ]

    for index, amount in enumerate(amounts):
        record(
            engine,
            transaction_id=f"EID-{index}",
            minute=305 + index,
            provider_id=list(ProviderID)[index % 3],
            account_id=f"UNIQUE-{index}",
            amount=amount,
        )

    result = engine.get_agent_assessment(
        agent_id="AG001",
        as_of=START + timedelta(minutes=319),
    )

    assert (
        result.category
        == AnomalyCategory.LEGITIMATE_DEMAND_SPIKE
    )

    assert result.requires_human_review is False
    assert "EID_MARKET_SURGE" in result.active_contexts


def test_cross_provider_pattern_is_detected() -> None:
    engine = build_engine()

    providers = [
        ProviderID.BKASH,
        ProviderID.NAGAD,
        ProviderID.BKASH,
        ProviderID.NAGAD,
        ProviderID.BKASH,
        ProviderID.NAGAD,
    ]

    for index, provider in enumerate(providers):
        record(
            engine,
            transaction_id=f"CROSS-{index}",
            minute=100 + index,
            provider_id=provider,
            account_id="SYNTH-LINK-1",
            amount="15000",
        )

    result = engine.get_agent_assessment(
        agent_id="AG001",
        as_of=START + timedelta(minutes=114),
    )

    assert (
        result.category
        == AnomalyCategory.CROSS_PROVIDER_REVIEW
    )

    assert result.band == AnomalyBand.HIGH

    assert any(
        factor.code == "CROSS_PROVIDER_LINK"
        for factor in result.factors
    )


def test_normal_activity_remains_low() -> None:
    engine = build_engine()

    for index in range(4):
        record(
            engine,
            transaction_id=f"NORMAL-{index}",
            minute=60 + index * 3,
            provider_id=ProviderID.BKASH,
            account_id=f"ACC-{index}",
            amount=str(1000 + index * 700),
        )

    result = engine.get_agent_assessment(
        agent_id="AG001",
        as_of=START + timedelta(minutes=74),
    )

    assert result.band == AnomalyBand.LOW

    assert (
        result.category
        == AnomalyCategory.NORMAL_ACTIVITY
    )

    assert result.requires_human_review is False


def test_abnormal_failure_rate_is_explained() -> None:
    engine = build_engine()

    for index in range(7):
        record(
            engine,
            transaction_id=f"FAIL-{index}",
            minute=200 + index,
            provider_id=ProviderID.ROCKET,
            account_id=f"ACC-{index}",
            amount="2000",
            status=(
                TransactionStatus.FAILED
                if index < 4
                else TransactionStatus.SUCCESS
            ),
        )

    result = engine.get_agent_assessment(
        agent_id="AG001",
        as_of=START + timedelta(minutes=214),
    )

    assert any(
        factor.code == "ABNORMAL_FAILURE_RATE"
        for factor in result.factors
    )

    assert result.failed_transactions == 4