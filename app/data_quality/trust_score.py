from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from threading import RLock

from app.ingestion.canonical_event import (
    FeedEvent,
    FeedEventType,
    OpeningBalance,
    ProviderID,
    ResourceType,
)
from app.schemas.data_quality import (
    FeedHealthStatus,
    FeedHealthSummary,
    FeedHealthView,
)


class FeedHealthError(RuntimeError):
    """Base exception for feed-health operations."""


class UnknownFeedError(FeedHealthError):
    """Raised when an agent/provider feed is unknown."""


class InvalidFeedConfigurationError(FeedHealthError):
    """Raised when feed thresholds are invalid."""


@dataclass
class FeedState:
    agent_id: str
    provider_id: ProviderID

    last_signal_at: datetime
    last_event_at: datetime

    last_event_type: FeedEventType | None = None

    explicit_delay_minutes: int = 0

    conflict_active: bool = False

    reported_balance: Decimal | None = None
    calculated_balance: Decimal | None = None


class FeedHealthEngine:
    def __init__(
        self,
        *,
        stale_minutes: int = 15,
        missing_minutes: int = 30,
    ) -> None:
        if stale_minutes <= 0:
            raise InvalidFeedConfigurationError(
                "stale_minutes must be greater than zero."
            )

        if missing_minutes <= stale_minutes:
            raise InvalidFeedConfigurationError(
                "missing_minutes must be greater than stale_minutes."
            )

        self.stale_minutes = stale_minutes
        self.missing_minutes = missing_minutes

        self._states: dict[
            tuple[str, ProviderID],
            FeedState,
        ] = {}

        self._lock = RLock()

    def initialize(
        self,
        opening_balances: list[OpeningBalance],
    ) -> None:
        """
        Initialize one feed state for every agent/provider pair.

        Opening provider balances are treated as the first trusted
        provider data point at simulation start.
        """

        with self._lock:
            self._states.clear()

            for balance in opening_balances:
                if (
                    balance.resource_type
                    != ResourceType.PROVIDER_EMONEY
                ):
                    continue

                if balance.provider_id is None:
                    continue

                key = (
                    balance.agent_id,
                    balance.provider_id,
                )

                if key in self._states:
                    raise FeedHealthError(
                        "Duplicate feed initialization for "
                        f"agent={balance.agent_id}, "
                        f"provider={balance.provider_id.value}."
                    )

                self._states[key] = FeedState(
                    agent_id=balance.agent_id,
                    provider_id=balance.provider_id,
                    last_signal_at=balance.timestamp,
                    last_event_at=balance.timestamp,
                    calculated_balance=balance.opening_balance,
                )

        if not self._states:
            raise FeedHealthError(
                "No provider feeds could be initialized."
            )

    def record_event(
        self,
        event: FeedEvent,
        *,
        calculated_balance: Decimal | None = None,
    ) -> FeedHealthView:
        """
        Update feed state from a provider feed event.
        """

        with self._lock:
            state = self._get_state(
                event.agent_id,
                event.provider_id,
            )

            state.last_event_at = event.timestamp
            state.last_event_type = event.event_type

            if calculated_balance is not None:
                state.calculated_balance = calculated_balance

            if event.event_type == FeedEventType.HEARTBEAT:
                state.last_signal_at = event.timestamp
                state.explicit_delay_minutes = 0

            elif event.event_type == FeedEventType.FEED_DELAY:
                state.explicit_delay_minutes = max(
                    state.explicit_delay_minutes,
                    event.delay_minutes,
                )

            elif event.event_type == FeedEventType.FEED_RECOVERED:
                state.last_signal_at = event.timestamp
                state.explicit_delay_minutes = 0

            elif event.event_type == FeedEventType.BALANCE_CONFLICT:
                state.last_signal_at = event.timestamp
                state.conflict_active = True
                state.reported_balance = event.reported_balance

            return self._evaluate_state(
                state=state,
                as_of=event.timestamp,
            )

    def resolve_conflict(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
        resolved_at: datetime,
        calculated_balance: Decimal | None = None,
    ) -> FeedHealthView:
        """
        Manually clear a balance-conflict state after human or
        provider-side verification.
        """

        with self._lock:
            state = self._get_state(
                agent_id,
                provider_id,
            )

            state.conflict_active = False
            state.reported_balance = None
            state.last_signal_at = resolved_at
            state.last_event_at = resolved_at
            state.last_event_type = FeedEventType.FEED_RECOVERED

            if calculated_balance is not None:
                state.calculated_balance = calculated_balance

            return self._evaluate_state(
                state=state,
                as_of=resolved_at,
            )

    def get_feed_health(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
        as_of: datetime,
    ) -> FeedHealthView:
        with self._lock:
            state = self._get_state(
                agent_id,
                provider_id,
            )

            return self._evaluate_state(
                state=state,
                as_of=as_of,
            )

    def get_agent_health(
        self,
        *,
        agent_id: str,
        as_of: datetime,
    ) -> list[FeedHealthView]:
        with self._lock:
            matching_states = [
                state
                for state in self._states.values()
                if state.agent_id == agent_id
            ]

            if not matching_states:
                raise UnknownFeedError(
                    f"No provider feeds found for agent {agent_id}."
                )

            results = [
                self._evaluate_state(
                    state=state,
                    as_of=as_of,
                )
                for state in matching_states
            ]

        return sorted(
            results,
            key=lambda item: item.provider_id.value,
        )

    def get_all_health(
        self,
        *,
        as_of: datetime,
    ) -> list[FeedHealthView]:
        with self._lock:
            results = [
                self._evaluate_state(
                    state=state,
                    as_of=as_of,
                )
                for state in self._states.values()
            ]

        return sorted(
            results,
            key=lambda item: (
                item.agent_id,
                item.provider_id.value,
            ),
        )

    def get_summary(
        self,
        *,
        as_of: datetime,
    ) -> FeedHealthSummary:
        feeds = self.get_all_health(as_of=as_of)

        healthy = sum(
            feed.status == FeedHealthStatus.HEALTHY
            for feed in feeds
        )

        stale = sum(
            feed.status == FeedHealthStatus.STALE
            for feed in feeds
        )

        missing = sum(
            feed.status == FeedHealthStatus.MISSING
            for feed in feeds
        )

        conflicting = sum(
            feed.status == FeedHealthStatus.CONFLICTING
            for feed in feeds
        )

        average_confidence = (
            sum(feed.confidence for feed in feeds)
            / len(feeds)
            if feeds
            else 0
        )

        return FeedHealthSummary(
            as_of=as_of,
            total_feeds=len(feeds),
            healthy=healthy,
            stale=stale,
            missing=missing,
            conflicting=conflicting,
            average_confidence=round(
                average_confidence,
                3,
            ),
            fallback_required=any(
                feed.status != FeedHealthStatus.HEALTHY
                for feed in feeds
            ),
            feeds=feeds,
        )

    def _evaluate_state(
        self,
        *,
        state: FeedState,
        as_of: datetime,
    ) -> FeedHealthView:
        if as_of.tzinfo is None:
            raise FeedHealthError(
                "as_of must include timezone information."
            )

        elapsed_seconds = (
            as_of - state.last_signal_at
        ).total_seconds()

        data_age_minutes = max(
            0.0,
            elapsed_seconds / 60,
        )

        effective_age = max(
            data_age_minutes,
            float(state.explicit_delay_minutes),
        )

        status = self._determine_status(
            state=state,
            effective_age=effective_age,
        )

        confidence = self._calculate_confidence(
            status=status,
            effective_age=effective_age,
        )

        balance_difference = self._balance_difference(state)

        reasons = self._build_reasons(
            state=state,
            status=status,
            effective_age=effective_age,
            balance_difference=balance_difference,
        )

        fallback = self._safe_fallback(status)

        return FeedHealthView(
            agent_id=state.agent_id,
            provider_id=state.provider_id,
            status=status,
            confidence=confidence,
            last_signal_at=state.last_signal_at,
            last_event_at=state.last_event_at,
            last_event_type=state.last_event_type,
            data_age_minutes=round(data_age_minutes, 2),
            explicit_delay_minutes=(
                state.explicit_delay_minutes
            ),
            reported_balance=state.reported_balance,
            calculated_balance=state.calculated_balance,
            balance_difference=balance_difference,
            reasons=reasons,
            safe_fallback=fallback,
            can_issue_strong_recommendation=(
                status == FeedHealthStatus.HEALTHY
            ),
        )

    def _determine_status(
        self,
        *,
        state: FeedState,
        effective_age: float,
    ) -> FeedHealthStatus:
        if state.conflict_active:
            return FeedHealthStatus.CONFLICTING

        if effective_age >= self.missing_minutes:
            return FeedHealthStatus.MISSING

        if effective_age >= self.stale_minutes:
            return FeedHealthStatus.STALE

        return FeedHealthStatus.HEALTHY

    def _calculate_confidence(
        self,
        *,
        status: FeedHealthStatus,
        effective_age: float,
    ) -> float:
        if status == FeedHealthStatus.CONFLICTING:
            return 0.30

        if status == FeedHealthStatus.MISSING:
            return 0.20

        if status == FeedHealthStatus.STALE:
            stale_range = (
                self.missing_minutes
                - self.stale_minutes
            )

            stale_progress = (
                effective_age - self.stale_minutes
            ) / stale_range

            confidence = 0.65 - (
                min(max(stale_progress, 0), 1) * 0.25
            )

            return round(confidence, 3)

        freshness_ratio = min(
            effective_age / self.stale_minutes,
            1,
        )

        confidence = 1.0 - (
            freshness_ratio * 0.20
        )

        return round(confidence, 3)

    @staticmethod
    def _balance_difference(
        state: FeedState,
    ) -> Decimal | None:
        if (
            state.reported_balance is None
            or state.calculated_balance is None
        ):
            return None

        return abs(
            state.reported_balance
            - state.calculated_balance
        )

    @staticmethod
    def _build_reasons(
        *,
        state: FeedState,
        status: FeedHealthStatus,
        effective_age: float,
        balance_difference: Decimal | None,
    ) -> list[str]:
        reasons: list[str] = []

        if state.explicit_delay_minutes > 0:
            reasons.append(
                "Provider reported a feed delay of "
                f"{state.explicit_delay_minutes} minutes."
            )

        if status == FeedHealthStatus.HEALTHY:
            reasons.append(
                "Provider data is recent and no active "
                "consistency problem is recorded."
            )

        elif status == FeedHealthStatus.STALE:
            reasons.append(
                "The latest trusted provider signal is "
                f"{effective_age:.1f} minutes old."
            )

        elif status == FeedHealthStatus.MISSING:
            reasons.append(
                "No sufficiently recent trusted provider data "
                f"is available. Effective age is "
                f"{effective_age:.1f} minutes."
            )

        elif status == FeedHealthStatus.CONFLICTING:
            reasons.append(
                "The provider-reported balance conflicts with "
                "the balance calculated from transaction events."
            )

            if balance_difference is not None:
                reasons.append(
                    "Absolute balance difference is "
                    f"{balance_difference}."
                )

        return reasons

    @staticmethod
    def _safe_fallback(
        status: FeedHealthStatus,
    ) -> str:
        if status == FeedHealthStatus.HEALTHY:
            return (
                "Normal analytical processing is allowed."
            )

        if status == FeedHealthStatus.STALE:
            return (
                "Show reduced confidence and avoid precise "
                "shortage timing until fresher data arrives."
            )

        if status == FeedHealthStatus.MISSING:
            return (
                "Return insufficient-data status and do not issue "
                "a strong liquidity recommendation."
            )

        return (
            "Do not trust the affected provider balance until "
            "the discrepancy is verified by an authorized human."
        )

    def _get_state(
        self,
        agent_id: str,
        provider_id: ProviderID,
    ) -> FeedState:
        key = (
            agent_id,
            provider_id,
        )

        state = self._states.get(key)

        if state is None:
            raise UnknownFeedError(
                "Unknown provider feed: "
                f"agent={agent_id}, "
                f"provider={provider_id.value}."
            )

        return state