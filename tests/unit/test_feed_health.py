from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.data_quality.trust_score import FeedHealthEngine
from app.ingestion.canonical_event import (
    FeedEvent,
    FeedEventType,
    OpeningBalance,
    ProviderID,
    ResourceType,
)
from app.schemas.data_quality import FeedHealthStatus


DHAKA_TIME = timezone(timedelta(hours=6))

START = datetime(
    2026,
    6,
    20,
    9,
    0,
    tzinfo=DHAKA_TIME,
)


def build_engine() -> FeedHealthEngine:
    engine = FeedHealthEngine(
        stale_minutes=15,
        missing_minutes=30,
    )

    engine.initialize(
        [
            OpeningBalance(
                agent_id="AG001",
                provider_id=ProviderID.BKASH,
                resource_type=ResourceType.PROVIDER_EMONEY,
                opening_balance=Decimal("50000"),
                timestamp=START,
            )
        ]
    )

    return engine


def test_initial_feed_is_healthy() -> None:
    engine = build_engine()

    result = engine.get_feed_health(
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        as_of=START,
    )

    assert result.status == FeedHealthStatus.HEALTHY
    assert result.confidence == 1.0
    assert result.can_issue_strong_recommendation is True


def test_feed_becomes_stale() -> None:
    engine = build_engine()

    result = engine.get_feed_health(
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        as_of=START + timedelta(minutes=20),
    )

    assert result.status == FeedHealthStatus.STALE
    assert result.can_issue_strong_recommendation is False


def test_feed_becomes_missing() -> None:
    engine = build_engine()

    result = engine.get_feed_health(
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        as_of=START + timedelta(minutes=31),
    )

    assert result.status == FeedHealthStatus.MISSING
    assert result.confidence == 0.20


def test_explicit_delay_causes_missing_state() -> None:
    engine = build_engine()

    event = FeedEvent(
        feed_event_id="FEED-001",
        timestamp=START + timedelta(minutes=10),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        event_type=FeedEventType.FEED_DELAY,
        delay_minutes=35,
        reported_balance=None,
    )

    result = engine.record_event(
        event,
        calculated_balance=Decimal("49000"),
    )

    assert result.status == FeedHealthStatus.MISSING
    assert result.explicit_delay_minutes == 35


def test_recovered_feed_becomes_healthy() -> None:
    engine = build_engine()

    delay_event = FeedEvent(
        feed_event_id="FEED-001",
        timestamp=START + timedelta(minutes=10),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        event_type=FeedEventType.FEED_DELAY,
        delay_minutes=35,
        reported_balance=None,
    )

    engine.record_event(
        delay_event,
        calculated_balance=Decimal("49000"),
    )

    recovery_event = FeedEvent(
        feed_event_id="FEED-002",
        timestamp=START + timedelta(minutes=40),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        event_type=FeedEventType.FEED_RECOVERED,
        delay_minutes=0,
        reported_balance=None,
    )

    result = engine.record_event(
        recovery_event,
        calculated_balance=Decimal("49000"),
    )

    assert result.status == FeedHealthStatus.HEALTHY
    assert result.explicit_delay_minutes == 0


def test_balance_conflict_is_detected() -> None:
    engine = build_engine()

    conflict_event = FeedEvent(
        feed_event_id="FEED-003",
        timestamp=START + timedelta(minutes=5),
        agent_id="AG001",
        provider_id=ProviderID.BKASH,
        event_type=FeedEventType.BALANCE_CONFLICT,
        delay_minutes=0,
        reported_balance=Decimal("60000"),
    )

    result = engine.record_event(
        conflict_event,
        calculated_balance=Decimal("50000"),
    )

    assert result.status == FeedHealthStatus.CONFLICTING
    assert result.balance_difference == Decimal("10000")
    assert result.can_issue_strong_recommendation is False