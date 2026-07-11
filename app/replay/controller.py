from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from threading import RLock

from app.ingestion.canonical_event import (
    FeedEvent,
    ReplayEvent,
    ReplayEventType,
    TransactionEvent,
)
from app.ledger.balance_engine import BalanceEngine
from app.schemas.replay import (
    ProcessedReplayEvent,
    ReplayBatchResult,
    ReplayState,
    ReplayStatus,
)
from app.data_quality.trust_score import FeedHealthEngine

from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)

from app.ingestion.canonical_event import (
    AgentRecord,
    ContextEvent,
    FeedEvent,
    ReplayEvent,
    ReplayEventType,
    TransactionEvent,
)

from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
)

class ReplayControllerError(RuntimeError):
    """Base replay-controller error."""


class EmptyReplayStreamError(ReplayControllerError):
    """Raised when no replay events are available."""


class ReplayController:
    def __init__(
         self,
        *,
        events: list[ReplayEvent],
        opening_balances,
        balance_engine: BalanceEngine,
        feed_health_engine: FeedHealthEngine | None = None,
        liquidity_engine: LiquidityForecastEngine | None = None,
        anomaly_engine: AnomalyDetectionEngine | None = None,
        agents: list[AgentRecord] | None = None,
        context_events: list[ContextEvent] | None = None,
        recent_event_limit: int = 100,
    ) -> None:
        if not events:
            raise EmptyReplayStreamError(
                "Replay stream must contain at least one event."
            )

        self._lock = RLock()

        self._events = sorted(
            events,
            key=lambda event: (
                event.timestamp,
                event.event_id,
            ),
        )

        self._opening_balances = list(opening_balances)
        self._balance_engine = balance_engine
        self._feed_health_engine = feed_health_engine
        self._liquidity_engine = liquidity_engine

        self._anomaly_engine = anomaly_engine
        self._agents = list(agents or [])
        self._context_events = list(context_events or [])

        if self._anomaly_engine is not None and not self._agents:
            raise ReplayControllerError(
                "Agents are required when anomaly detection is enabled."
            )

        self._recent_events: deque[ProcessedReplayEvent] = deque(
            maxlen=recent_event_limit
        )

        self._simulation_start = min(
            balance.timestamp
            for balance in self._opening_balances
        )

        self._simulation_end = self._events[-1].timestamp

        self._current_index = 0
        self._simulation_time = self._simulation_start
        self._status = ReplayStatus.READY

        self._processed_transactions = 0
        self._processed_feed_events = 0
        self._last_event: ProcessedReplayEvent | None = None

        self.reset()

    def reset(self) -> ReplayState:
        with self._lock:
            self._balance_engine.initialize(
                self._opening_balances
            )
            if self._feed_health_engine is not None:
                self._feed_health_engine.initialize(
                    self._opening_balances
                )
            
            if self._liquidity_engine is not None:
                self._liquidity_engine.initialize(
                    self._opening_balances
                )

            if self._anomaly_engine is not None:
                self._anomaly_engine.initialize(
                    agents=self._agents,
                    context_events=self._context_events,
                )

            self._current_index = 0
            self._simulation_time = self._simulation_start
            self._status = ReplayStatus.READY

            self._processed_transactions = 0
            self._processed_feed_events = 0

            self._recent_events.clear()
            self._last_event = None

            return self.get_state()

    def step(
        self,
        event_count: int = 1,
    ) -> ReplayBatchResult:
        if event_count < 1:
            raise ReplayControllerError(
                "event_count must be at least 1."
            )

        with self._lock:
            if self._is_complete():
                self._status = ReplayStatus.COMPLETED

                return ReplayBatchResult(
                    state=self.get_state(),
                    events=[],
                )

            self._status = ReplayStatus.RUNNING
            processed: list[ProcessedReplayEvent] = []

            for _ in range(event_count):
                if self._is_complete():
                    break

                event = self._events[self._current_index]
                result = self._process_event(event)

                processed.append(result)
                self._current_index += 1
                self._simulation_time = event.timestamp

            self._finish_batch()

            return ReplayBatchResult(
                state=self.get_state(),
                events=processed,
            )

    def advance(
        self,
        minutes: int,
    ) -> ReplayBatchResult:
        if minutes < 1:
            raise ReplayControllerError(
                "minutes must be at least 1."
            )

        with self._lock:
            if self._is_complete():
                self._status = ReplayStatus.COMPLETED

                return ReplayBatchResult(
                    state=self.get_state(),
                    events=[],
                )

            target_time = min(
                self._simulation_time + timedelta(minutes=minutes),
                self._simulation_end,
            )

            self._status = ReplayStatus.RUNNING
            processed: list[ProcessedReplayEvent] = []

            while not self._is_complete():
                next_event = self._events[self._current_index]

                if next_event.timestamp > target_time:
                    break

                result = self._process_event(next_event)

                processed.append(result)
                self._current_index += 1

            self._simulation_time = target_time
            self._finish_batch()

            return ReplayBatchResult(
                state=self.get_state(),
                events=processed,
            )

    def get_state(self) -> ReplayState:
        with self._lock:
            total_events = len(self._events)
            processed_events = self._current_index
            remaining_events = total_events - processed_events

            next_event = (
                self._events[self._current_index]
                if not self._is_complete()
                else None
            )

            percentage = (
                processed_events / total_events
            ) * 100

            return ReplayState(
                status=self._status,
                simulation_start=self._simulation_start,
                simulation_end=self._simulation_end,
                simulation_time=self._simulation_time,
                total_events=total_events,
                processed_events=processed_events,
                remaining_events=remaining_events,
                processed_transactions=(
                    self._processed_transactions
                ),
                processed_feed_events=(
                    self._processed_feed_events
                ),
                completion_percentage=round(percentage, 2),
                next_event_id=(
                    next_event.event_id
                    if next_event
                    else None
                ),
                next_event_time=(
                    next_event.timestamp
                    if next_event
                    else None
                ),
                last_event=self._last_event,
            )

    def get_recent_events(
        self,
        limit: int = 20,
    ) -> list[ProcessedReplayEvent]:
        if limit < 1:
            return []

        with self._lock:
            events = list(self._recent_events)

        return events[-limit:]

    def _process_event(
        self,
        event: ReplayEvent,
    ) -> ProcessedReplayEvent:
        if event.event_type == ReplayEventType.TRANSACTION:
            return self._process_transaction(event)

        if event.event_type == ReplayEventType.FEED_EVENT:
            return self._process_feed_event(event)

        raise ReplayControllerError(
            f"Unsupported replay event type: {event.event_type}"
        )

    def _process_transaction(
        self,
        event: ReplayEvent,
    ) -> ProcessedReplayEvent:
        if not isinstance(event.payload, TransactionEvent):
            raise ReplayControllerError(
                f"Event {event.event_id} has an invalid transaction payload."
            )

        transaction = event.payload

        result = self._balance_engine.apply_transaction(
            transaction
        )

        if self._liquidity_engine is not None:
            self._liquidity_engine.record_transaction(
                transaction
            )

        if self._anomaly_engine is not None:
            self._anomaly_engine.record_transaction(
                transaction
            )

        self._processed_transactions += 1

        details = (
            f"cash_delta={self._format_decimal(result.cash_delta)}, "
            f"provider_delta="
            f"{self._format_decimal(result.provider_emoney_delta)}"
        )

        processed = ProcessedReplayEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            agent_id=event.agent_id,
            provider_id=event.provider_id,
            applied=result.applied,
            action=(
                "TRANSACTION_APPLIED"
                if result.applied
                else "TRANSACTION_IGNORED"
            ),
            details=details,
        )

        self._remember_event(processed)

        return processed

    def _process_feed_event(
        self,
        event: ReplayEvent,
    ) -> ProcessedReplayEvent:
        if not isinstance(event.payload, FeedEvent):
            raise ReplayControllerError(
                f"Event {event.event_id} has an invalid feed payload."
            )

        self._processed_feed_events += 1

        feed_event = event.payload

        details = f"feed_event={feed_event.event_type.value}"

        if feed_event.delay_minutes:
            details += (
                f", delay_minutes={feed_event.delay_minutes}"
            )

        if feed_event.reported_balance is not None:
            details += (
                ", reported_balance="
                f"{self._format_decimal(feed_event.reported_balance)}"
            )

        if self._feed_health_engine is not None:
            calculated_balance = (
                self._balance_engine.get_provider_balance(
                    feed_event.agent_id,
                    feed_event.provider_id,
                )
            )

            health = self._feed_health_engine.record_event(
                feed_event,
                calculated_balance=calculated_balance,
            )

            details += (
                f", health_status={health.status.value}, "
                f"confidence={health.confidence}"
            )

        processed = ProcessedReplayEvent(
            event_id=event.event_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            agent_id=event.agent_id,
            provider_id=event.provider_id,
            applied=True,
            action="FEED_EVENT_RECORDED",
            details=details,
        )

        self._remember_event(processed)

        return processed

    def _remember_event(
        self,
        event: ProcessedReplayEvent,
    ) -> None:
        self._recent_events.append(event)
        self._last_event = event

    def _finish_batch(self) -> None:
        if self._is_complete():
            self._status = ReplayStatus.COMPLETED
            self._simulation_time = self._simulation_end
        else:
            self._status = ReplayStatus.PAUSED

    def _is_complete(self) -> bool:
        return self._current_index >= len(self._events)

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        return format(value, "f")