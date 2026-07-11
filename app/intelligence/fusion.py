from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock

from app.data_quality.trust_score import FeedHealthEngine
from app.ingestion.canonical_event import (
    AgentRecord,
    ProviderID,
)
from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
)
from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)
from app.schemas.anomaly import (
    AnomalyAssessment,
    AnomalyBand,
)
from app.schemas.data_quality import (
    FeedHealthStatus,
    FeedHealthView,
)
from app.schemas.incident import (
    EvidenceSource,
    IncidentEvidence,
    IncidentPriority,
    IncidentStatus,
    IncidentSummary,
    IncidentType,
    OperationalIncident,
    RoutingRole,
)
from app.schemas.liquidity import (
    AgentLiquidityForecast,
    LiquidityStatus,
    ResourceLiquidityForecast,
)


class DecisionFusionError(RuntimeError):
    """Base exception for decision fusion."""


class UnknownIncidentError(DecisionFusionError):
    """Raised when an incident does not exist."""


class UnknownFusionAgentError(DecisionFusionError):
    """Raised when an agent does not exist."""


@dataclass
class IncidentCandidate:
    key: str
    agent_id: str
    area: str

    provider_scope: list[ProviderID]

    incident_type: IncidentType
    priority: IncidentPriority

    title: str
    summary: str

    confidence: float

    receiver_role: RoutingRole
    responsible_stakeholder: RoutingRole

    human_review_required: bool
    strong_recommendation_allowed: bool

    recommended_next_step: str

    evidence: list[IncidentEvidence] = field(
        default_factory=list
    )

    uncertainty: list[str] = field(default_factory=list)

    alternative_explanations: list[str] = field(
        default_factory=list
    )


class DecisionFusionEngine:
    def __init__(
        self,
        *,
        liquidity_engine: LiquidityForecastEngine,
        anomaly_engine: AnomalyDetectionEngine,
        feed_health_engine: FeedHealthEngine,
    ) -> None:
        self.liquidity_engine = liquidity_engine
        self.anomaly_engine = anomaly_engine
        self.feed_health_engine = feed_health_engine

        self._agent_areas: dict[str, str] = {}

        self._incidents: dict[
            str,
            OperationalIncident,
        ] = {}

        self._lock = RLock()

    def initialize(
        self,
        agents: list[AgentRecord],
    ) -> None:
        with self._lock:
            self._agent_areas = {
                agent.agent_id: agent.area
                for agent in agents
            }

            self._incidents.clear()

        if not self._agent_areas:
            raise DecisionFusionError(
                "At least one agent is required."
            )

    def refresh_agent(
        self,
        *,
        agent_id: str,
        as_of: datetime,
    ) -> list[OperationalIncident]:
        self._validate_agent(agent_id)

        liquidity = self.liquidity_engine.get_agent_forecast(
            agent_id=agent_id,
            as_of=as_of,
        )

        anomaly = self.anomaly_engine.get_agent_assessment(
            agent_id=agent_id,
            as_of=as_of,
        )

        feed_health = self.feed_health_engine.get_agent_health(
            agent_id=agent_id,
            as_of=as_of,
        )

        candidates: list[IncidentCandidate] = []

        candidates.extend(
            self._build_data_quality_candidates(
                agent_id=agent_id,
                feeds=feed_health,
            )
        )

        signal_candidate = self._build_signal_candidate(
            agent_id=agent_id,
            liquidity=liquidity,
            anomaly=anomaly,
            feeds=feed_health,
        )

        if signal_candidate is not None:
            candidates.append(signal_candidate)

        return self._synchronize_incidents(
            agent_id=agent_id,
            candidates=candidates,
            as_of=as_of,
        )

    def refresh_all(
        self,
        *,
        as_of: datetime,
    ) -> list[OperationalIncident]:
        incidents: list[OperationalIncident] = []

        for agent_id in sorted(self._agent_areas):
            incidents.extend(
                self.refresh_agent(
                    agent_id=agent_id,
                    as_of=as_of,
                )
            )

        return incidents

    def get_incident(
        self,
        incident_id: str,
    ) -> OperationalIncident:
        with self._lock:
            incident = self._incidents.get(incident_id)

        if incident is None:
            raise UnknownIncidentError(
                f"Unknown incident: {incident_id}"
            )

        return incident

    def get_all_incidents(
        self,
    ) -> list[OperationalIncident]:
        with self._lock:
            incidents = list(self._incidents.values())

        return self._sort_incidents(incidents)

    def get_active_incidents(
        self,
    ) -> list[OperationalIncident]:
        with self._lock:
            incidents = [
                incident
                for incident in self._incidents.values()
                if incident.status == IncidentStatus.ACTIVE
            ]

        return self._sort_incidents(incidents)

    def get_agent_incidents(
        self,
        agent_id: str,
        *,
        include_cleared: bool = True,
    ) -> list[OperationalIncident]:
        self._validate_agent(agent_id)

        with self._lock:
            incidents = [
                incident
                for incident in self._incidents.values()
                if incident.agent_id == agent_id
                and (
                    include_cleared
                    or incident.status
                    == IncidentStatus.ACTIVE
                )
            ]

        return self._sort_incidents(incidents)

    def get_summary(
        self,
        *,
        as_of: datetime,
    ) -> IncidentSummary:
        incidents = self.get_all_incidents()

        return IncidentSummary(
            as_of=as_of,
            total_incidents=len(incidents),
            active_incidents=sum(
                incident.status == IncidentStatus.ACTIVE
                for incident in incidents
            ),
            cleared_incidents=sum(
                incident.status == IncidentStatus.CLEARED
                for incident in incidents
            ),
            p1=sum(
                incident.priority == IncidentPriority.P1
                and incident.status == IncidentStatus.ACTIVE
                for incident in incidents
            ),
            p2=sum(
                incident.priority == IncidentPriority.P2
                and incident.status == IncidentStatus.ACTIVE
                for incident in incidents
            ),
            p3=sum(
                incident.priority == IncidentPriority.P3
                and incident.status == IncidentStatus.ACTIVE
                for incident in incidents
            ),
            p4=sum(
                incident.priority == IncidentPriority.P4
                and incident.status == IncidentStatus.ACTIVE
                for incident in incidents
            ),
            liquidity_incidents=sum(
                incident.incident_type
                == IncidentType.LIQUIDITY_PRESSURE
                for incident in incidents
            ),
            unusual_activity_incidents=sum(
                incident.incident_type
                == IncidentType.UNUSUAL_ACTIVITY
                for incident in incidents
            ),
            combined_incidents=sum(
                incident.incident_type
                == IncidentType.COMBINED_PRIORITY
                for incident in incidents
            ),
            data_quality_incidents=sum(
                incident.incident_type
                == IncidentType.DATA_QUALITY
                for incident in incidents
            ),
            incidents=incidents,
        )

    def _build_data_quality_candidates(
        self,
        *,
        agent_id: str,
        feeds: list[FeedHealthView],
    ) -> list[IncidentCandidate]:
        candidates: list[IncidentCandidate] = []

        for feed in feeds:
            if feed.status == FeedHealthStatus.HEALTHY:
                continue

            if feed.status == FeedHealthStatus.CONFLICTING:
                priority = IncidentPriority.P2
                title = (
                    f"{feed.provider_id.value} balance conflict"
                )

            elif feed.status == FeedHealthStatus.MISSING:
                priority = IncidentPriority.P2
                title = (
                    f"{feed.provider_id.value} provider feed missing"
                )

            else:
                priority = IncidentPriority.P3
                title = (
                    f"{feed.provider_id.value} provider feed stale"
                )

            evidence = [
                IncidentEvidence(
                    source=EvidenceSource.DATA_QUALITY,
                    code=f"FEED_{feed.status.value}",
                    message=reason,
                    value=(
                        f"confidence={feed.confidence}, "
                        f"age_minutes={feed.data_age_minutes}"
                    ),
                )
                for reason in feed.reasons
            ]

            key = self._candidate_key(
                agent_id=agent_id,
                incident_type=IncidentType.DATA_QUALITY,
                provider_scope=[feed.provider_id],
            )

            candidates.append(
                IncidentCandidate(
                    key=key,
                    agent_id=agent_id,
                    area=self._agent_areas[agent_id],
                    provider_scope=[feed.provider_id],
                    incident_type=IncidentType.DATA_QUALITY,
                    priority=priority,
                    title=title,
                    summary=(
                        "Provider data cannot currently support "
                        "a fully reliable operational conclusion."
                    ),
                    confidence=feed.confidence,
                    receiver_role=(
                        RoutingRole.PROVIDER_OPERATIONS
                    ),
                    responsible_stakeholder=(
                        RoutingRole.PROVIDER_OPERATIONS
                    ),
                    human_review_required=True,
                    strong_recommendation_allowed=False,
                    recommended_next_step=feed.safe_fallback,
                    evidence=evidence,
                    uncertainty=[
                        "Liquidity timing may be inaccurate until "
                        "provider data is restored or verified."
                    ],
                )
            )

        return candidates

    def _build_signal_candidate(
        self,
        *,
        agent_id: str,
        liquidity: AgentLiquidityForecast,
        anomaly: AnomalyAssessment,
        feeds: list[FeedHealthView],
    ) -> IncidentCandidate | None:
        severe_feed_problem = any(
            feed.status
            in {
                FeedHealthStatus.MISSING,
                FeedHealthStatus.CONFLICTING,
            }
            for feed in feeds
        )

        liquidity_risk = liquidity.overall_status in {
            LiquidityStatus.WATCH,
            LiquidityStatus.CRITICAL,
            LiquidityStatus.DEPLETED,
        }

        anomaly_risk = anomaly.requires_human_review

        if (
            liquidity_risk
            and anomaly_risk
            and not severe_feed_problem
        ):
            return self._build_combined_candidate(
                agent_id=agent_id,
                liquidity=liquidity,
                anomaly=anomaly,
            )

        if liquidity_risk:
            return self._build_liquidity_candidate(
                agent_id=agent_id,
                liquidity=liquidity,
            )

        if anomaly_risk:
            return self._build_anomaly_candidate(
                agent_id=agent_id,
                anomaly=anomaly,
            )

        return None

    def _build_combined_candidate(
        self,
        *,
        agent_id: str,
        liquidity: AgentLiquidityForecast,
        anomaly: AnomalyAssessment,
    ) -> IncidentCandidate:
        priority = (
            IncidentPriority.P1
            if (
                liquidity.overall_status
                in {
                    LiquidityStatus.CRITICAL,
                    LiquidityStatus.DEPLETED,
                }
                and anomaly.band == AnomalyBand.HIGH
            )
            else IncidentPriority.P2
        )

        provider_scope = self._combined_provider_scope(
            liquidity=liquidity,
            anomaly=anomaly,
        )

        urgent_forecast = self._most_urgent_forecast(
            liquidity
        )

        evidence = self._liquidity_evidence(
            urgent_forecast
        )

        evidence.extend(
            self._anomaly_evidence(anomaly)
        )

        key = self._candidate_key(
            agent_id=agent_id,
            incident_type=IncidentType.COMBINED_PRIORITY,
            provider_scope=provider_scope,
        )

        return IncidentCandidate(
            key=key,
            agent_id=agent_id,
            area=self._agent_areas[agent_id],
            provider_scope=provider_scope,
            incident_type=IncidentType.COMBINED_PRIORITY,
            priority=priority,
            title=(
                "Liquidity pressure with unusual activity"
            ),
            summary=(
                "Liquidity is deteriorating while explainable "
                "transaction indicators also require human review."
            ),
            confidence=round(
                min(
                    liquidity.aggregate_confidence,
                    anomaly.confidence,
                ),
                3,
            ),
            receiver_role=RoutingRole.FIELD_OFFICER,
            responsible_stakeholder=(
                RoutingRole.PROVIDER_OPERATIONS
            ),
            human_review_required=True,
            strong_recommendation_allowed=(
                urgent_forecast
                .can_issue_strong_recommendation
            ),
            recommended_next_step=(
                "Contact the agent, verify current demand, review "
                "the listed transaction evidence, and coordinate "
                "approved liquidity support only after human review."
            ),
            evidence=evidence,
            uncertainty=[
                "The recent activity may still have a legitimate "
                "operational explanation.",
                "Forecast timing assumes the recent transaction "
                "rate continues.",
            ],
            alternative_explanations=(
                anomaly.alternative_explanations
            ),
        )

    def _build_liquidity_candidate(
        self,
        *,
        agent_id: str,
        liquidity: AgentLiquidityForecast,
    ) -> IncidentCandidate:
        urgent = self._most_urgent_forecast(liquidity)

        if urgent.status == LiquidityStatus.DEPLETED:
            priority = IncidentPriority.P1

        elif urgent.status == LiquidityStatus.CRITICAL:
            priority = IncidentPriority.P2

        else:
            priority = IncidentPriority.P3

        provider_scope = (
            [urgent.provider_id]
            if urgent.provider_id is not None
            else []
        )

        key = self._candidate_key(
            agent_id=agent_id,
            incident_type=IncidentType.LIQUIDITY_PRESSURE,
            provider_scope=provider_scope,
        )

        return IncidentCandidate(
            key=key,
            agent_id=agent_id,
            area=self._agent_areas[agent_id],
            provider_scope=provider_scope,
            incident_type=IncidentType.LIQUIDITY_PRESSURE,
            priority=priority,
            title="Upcoming liquidity shortage",
            summary=liquidity.headline,
            confidence=liquidity.aggregate_confidence,
            receiver_role=RoutingRole.FIELD_OFFICER,
            responsible_stakeholder=(
                RoutingRole.PROVIDER_OPERATIONS
            ),
            human_review_required=True,
            strong_recommendation_allowed=(
                urgent.can_issue_strong_recommendation
            ),
            recommended_next_step=urgent.recommendation,
            evidence=self._liquidity_evidence(urgent),
            uncertainty=[
                "Projected timing depends on recent transaction "
                "flow continuing at a similar rate."
            ],
        )

    def _build_anomaly_candidate(
        self,
        *,
        agent_id: str,
        anomaly: AnomalyAssessment,
    ) -> IncidentCandidate:
        priority = (
            IncidentPriority.P2
            if anomaly.band == AnomalyBand.HIGH
            else IncidentPriority.P3
        )

        key = self._candidate_key(
            agent_id=agent_id,
            incident_type=IncidentType.UNUSUAL_ACTIVITY,
            provider_scope=anomaly.provider_scope,
        )

        return IncidentCandidate(
            key=key,
            agent_id=agent_id,
            area=self._agent_areas[agent_id],
            provider_scope=anomaly.provider_scope,
            incident_type=IncidentType.UNUSUAL_ACTIVITY,
            priority=priority,
            title="Unusual activity requires review",
            summary=anomaly.summary,
            confidence=anomaly.confidence,
            receiver_role=RoutingRole.PROVIDER_OPERATIONS,
            responsible_stakeholder=(
                RoutingRole.RISK_REVIEWER
            ),
            human_review_required=True,
            strong_recommendation_allowed=False,
            recommended_next_step=anomaly.safe_next_step,
            evidence=self._anomaly_evidence(anomaly),
            uncertainty=[
                "An anomaly score is not proof of fraud."
            ],
            alternative_explanations=(
                anomaly.alternative_explanations
            ),
        )

    @staticmethod
    def _liquidity_evidence(
        forecast: ResourceLiquidityForecast,
    ) -> list[IncidentEvidence]:
        return [
            IncidentEvidence(
                source=EvidenceSource.LIQUIDITY,
                code=item.code,
                message=item.message,
                value=item.value,
            )
            for item in forecast.evidence
        ]

    @staticmethod
    def _anomaly_evidence(
        anomaly: AnomalyAssessment,
    ) -> list[IncidentEvidence]:
        return [
            IncidentEvidence(
                source=EvidenceSource.ANOMALY,
                code=factor.code,
                message=factor.description,
                value=factor.value,
                points=factor.points,
                transaction_ids=factor.transaction_ids,
            )
            for factor in anomaly.factors
        ]

    @staticmethod
    def _most_urgent_forecast(
        liquidity: AgentLiquidityForecast,
    ) -> ResourceLiquidityForecast:
        forecasts = [
            liquidity.shared_cash,
            *liquidity.provider_forecasts,
        ]

        severity = {
            LiquidityStatus.SAFE: 1,
            LiquidityStatus.INSUFFICIENT_DATA: 2,
            LiquidityStatus.WATCH: 3,
            LiquidityStatus.CRITICAL: 4,
            LiquidityStatus.DEPLETED: 5,
        }

        return max(
            forecasts,
            key=lambda item: severity[item.status],
        )

    @staticmethod
    def _combined_provider_scope(
        *,
        liquidity: AgentLiquidityForecast,
        anomaly: AnomalyAssessment,
    ) -> list[ProviderID]:
        providers = set(anomaly.provider_scope)

        if liquidity.most_urgent_provider is not None:
            providers.add(liquidity.most_urgent_provider)

        return sorted(
            providers,
            key=lambda provider: provider.value,
        )

    def _synchronize_incidents(
        self,
        *,
        agent_id: str,
        candidates: list[IncidentCandidate],
        as_of: datetime,
    ) -> list[OperationalIncident]:
        candidate_keys = {
            candidate.key
            for candidate in candidates
        }

        with self._lock:
            for candidate in candidates:
                incident_id = self._incident_id(
                    candidate.key
                )

                existing = self._incidents.get(incident_id)

                if existing is None:
                    self._incidents[incident_id] = (
                        self._create_incident(
                            incident_id=incident_id,
                            candidate=candidate,
                            as_of=as_of,
                        )
                    )

                else:
                    occurrences = existing.occurrences

                    if existing.status == IncidentStatus.CLEARED:
                        occurrences += 1

                    self._incidents[incident_id] = (
                        existing.model_copy(
                            update={
                                "provider_scope": (
                                    candidate.provider_scope
                                ),
                                "incident_type": (
                                    candidate.incident_type
                                ),
                                "priority": candidate.priority,
                                "status": IncidentStatus.ACTIVE,
                                "title": candidate.title,
                                "summary": candidate.summary,
                                "confidence": (
                                    candidate.confidence
                                ),
                                "receiver_role": (
                                    candidate.receiver_role
                                ),
                                "responsible_stakeholder": (
                                    candidate
                                    .responsible_stakeholder
                                ),
                                "human_review_required": (
                                    candidate
                                    .human_review_required
                                ),
                                "strong_recommendation_allowed": (
                                    candidate
                                    .strong_recommendation_allowed
                                ),
                                "recommended_next_step": (
                                    candidate
                                    .recommended_next_step
                                ),
                                "evidence": candidate.evidence,
                                "uncertainty": (
                                    candidate.uncertainty
                                ),
                                "alternative_explanations": (
                                    candidate
                                    .alternative_explanations
                                ),
                                "updated_at": as_of,
                                "cleared_at": None,
                                "occurrences": occurrences,
                            }
                        )
                    )

            for incident_id, incident in list(
                self._incidents.items()
            ):
                if (
                    incident.agent_id == agent_id
                    and incident.status
                    == IncidentStatus.ACTIVE
                    and incident.incident_key
                    not in candidate_keys
                ):
                    self._incidents[incident_id] = (
                        incident.model_copy(
                            update={
                                "status": IncidentStatus.CLEARED,
                                "updated_at": as_of,
                                "cleared_at": as_of,
                            }
                        )
                    )

        return self.get_agent_incidents(
            agent_id,
            include_cleared=False,
        )

    @staticmethod
    def _create_incident(
        *,
        incident_id: str,
        candidate: IncidentCandidate,
        as_of: datetime,
    ) -> OperationalIncident:
        return OperationalIncident(
            incident_id=incident_id,
            incident_key=candidate.key,
            agent_id=candidate.agent_id,
            area=candidate.area,
            provider_scope=candidate.provider_scope,
            incident_type=candidate.incident_type,
            priority=candidate.priority,
            status=IncidentStatus.ACTIVE,
            title=candidate.title,
            summary=candidate.summary,
            confidence=candidate.confidence,
            receiver_role=candidate.receiver_role,
            responsible_stakeholder=(
                candidate.responsible_stakeholder
            ),
            human_review_required=(
                candidate.human_review_required
            ),
            strong_recommendation_allowed=(
                candidate.strong_recommendation_allowed
            ),
            recommended_next_step=(
                candidate.recommended_next_step
            ),
            evidence=candidate.evidence,
            uncertainty=candidate.uncertainty,
            alternative_explanations=(
                candidate.alternative_explanations
            ),
            created_at=as_of,
            updated_at=as_of,
            occurrences=1,
        )

    @staticmethod
    def _candidate_key(
        *,
        agent_id: str,
        incident_type: IncidentType,
        provider_scope: list[ProviderID],
    ) -> str:
        providers = ",".join(
            sorted(
                provider.value
                for provider in provider_scope
            )
        )

        return (
            f"{agent_id}|{incident_type.value}|"
            f"{providers or 'SHARED'}"
        )

    @staticmethod
    def _incident_id(key: str) -> str:
        digest = hashlib.sha1(
            key.encode("utf-8")
        ).hexdigest()[:12].upper()

        return f"INC-{digest}"

    def _validate_agent(
        self,
        agent_id: str,
    ) -> None:
        if agent_id not in self._agent_areas:
            raise UnknownFusionAgentError(
                f"Unknown agent: {agent_id}"
            )

    @staticmethod
    def _sort_incidents(
        incidents: list[OperationalIncident],
    ) -> list[OperationalIncident]:
        priority_rank = {
            IncidentPriority.P1: 1,
            IncidentPriority.P2: 2,
            IncidentPriority.P3: 3,
            IncidentPriority.P4: 4,
        }

        status_rank = {
            IncidentStatus.ACTIVE: 1,
            IncidentStatus.CLEARED: 2,
        }

        return sorted(
            incidents,
            key=lambda item: (
                status_rank[item.status],
                priority_rank[item.priority],
                -item.updated_at.timestamp(),
            ),
        )