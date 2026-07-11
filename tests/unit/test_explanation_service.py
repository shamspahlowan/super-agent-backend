from datetime import datetime, timedelta, timezone

from app.explanations.service import (
    ExplanationGeneratorResult,
    GroundedExplanationService,
)
from app.ingestion.canonical_event import ProviderID
from app.schemas.explanation import (
    AIExplanationDraft,
    ExplanationAudience,
    ExplanationLanguage,
    ExplanationSource,
    ExplanationUsage,
    GenerateExplanationRequest,
)
from app.schemas.incident import (
    EvidenceSource,
    IncidentEvidence,
    IncidentPriority,
    IncidentStatus,
    IncidentType,
    OperationalIncident,
    RoutingRole,
)


DHAKA_TIME = timezone(timedelta(hours=6))

NOW = datetime(
    2026,
    6,
    20,
    14,
    0,
    tzinfo=DHAKA_TIME,
)


def build_incident(
    *,
    provider_scope: list[ProviderID] | None = None,
) -> OperationalIncident:
    return OperationalIncident(
        incident_id="INC-TEST-001",
        incident_key="AG003|COMBINED|NAGAD",
        agent_id="AG003",
        area="Bondor Bazar",
        provider_scope=(
            provider_scope
            or [ProviderID.NAGAD]
        ),
        incident_type=(
            IncidentType.COMBINED_PRIORITY
        ),
        priority=IncidentPriority.P1,
        status=IncidentStatus.ACTIVE,
        title=(
            "Liquidity pressure with unusual activity"
        ),
        summary=(
            "Provider liquidity is falling while repeated "
            "transaction indicators require review."
        ),
        confidence=0.82,
        receiver_role=RoutingRole.FIELD_OFFICER,
        responsible_stakeholder=(
            RoutingRole.PROVIDER_OPERATIONS
        ),
        human_review_required=True,
        strong_recommendation_allowed=True,
        recommended_next_step=(
            "Contact the agent and review the evidence "
            "before coordinating approved support."
        ),
        evidence=[
            IncidentEvidence(
                source=EvidenceSource.ANOMALY,
                code="NEAR_IDENTICAL_AMOUNTS",
                message=(
                    "Recent transactions had similar amounts."
                ),
                value="ratio=0.75",
                points=25,
                transaction_ids=[
                    "TXN-001",
                    "TXN-002",
                ],
            ),
            IncidentEvidence(
                source=EvidenceSource.LIQUIDITY,
                code="CURRENT_BALANCE",
                message="Current provider balance.",
                value="25000",
            ),
        ],
        uncertainty=[
            "The activity may have a legitimate demand explanation."
        ],
        alternative_explanations=[
            "Local Eid-related demand."
        ],
        created_at=NOW,
        updated_at=NOW,
        occurrences=1,
    )


class ValidFakeGenerator:
    def generate(
        self,
        *,
        system_prompt,
        grounding_payload,
    ) -> ExplanationGeneratorResult:
        return ExplanationGeneratorResult(
            model="fake-model",
            usage=ExplanationUsage(
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
            ),
            draft=AIExplanationDraft(
                headline="Review required",
                situation=(
                    "Provider liquidity is under pressure "
                    "and unusual activity may be present."
                ),
                evidence=[
                    "Current balance is 25000.",
                    "Similar amount ratio is 0.75.",
                ],
                uncertainty=(
                    "This may have a legitimate explanation "
                    "and requires human review."
                ),
                safe_next_step=(
                    "Contact the agent and review the evidence."
                ),
                provider_boundary_notice=(
                    "Keep provider data and authority separate."
                ),
            ),
        )


class UnsafeFakeGenerator:
    def generate(
        self,
        *,
        system_prompt,
        grounding_payload,
    ) -> ExplanationGeneratorResult:
        return ExplanationGeneratorResult(
            model="unsafe-model",
            usage=ExplanationUsage(),
            draft=AIExplanationDraft(
                headline="Fraud confirmed",
                situation=(
                    "This account is fraudulent."
                ),
                evidence=[
                    "Current balance is 25000."
                ],
                uncertainty=(
                    "Human review may be useful."
                ),
                safe_next_step=(
                    "Block the account."
                ),
                provider_boundary_notice=(
                    "Provider boundary."
                ),
            ),
        )


class InventedNumberGenerator:
    def generate(
        self,
        *,
        system_prompt,
        grounding_payload,
    ) -> ExplanationGeneratorResult:
        return ExplanationGeneratorResult(
            model="invented-number-model",
            usage=ExplanationUsage(),
            draft=AIExplanationDraft(
                headline="Review required",
                situation=(
                    "The balance may run out in 47 minutes."
                ),
                evidence=[
                    "Current balance is 25000."
                ],
                uncertainty=(
                    "This may change and requires human review."
                ),
                safe_next_step=(
                    "Contact the agent."
                ),
                provider_boundary_notice=(
                    "Keep providers separate."
                ),
            ),
        )


def test_valid_ai_explanation_is_used() -> None:
    service = GroundedExplanationService(
        generator=ValidFakeGenerator(),
        enabled=True,
    )

    result = service.generate_explanation(
        incident=build_incident(),
        request=GenerateExplanationRequest(
            language=ExplanationLanguage.ENGLISH,
            audience=(
                ExplanationAudience.FIELD_OFFICER
            ),
        ),
    )

    assert (
        result.generated_by
        == ExplanationSource.OPENAI
    )

    assert result.grounded is True
    assert result.safety_validated is True

    assert result.usage.total_tokens == 150


def test_unsafe_ai_output_uses_fallback() -> None:
    service = GroundedExplanationService(
        generator=UnsafeFakeGenerator(),
        enabled=True,
    )

    result = service.generate_explanation(
        incident=build_incident(),
        request=GenerateExplanationRequest(),
    )

    assert (
        result.generated_by
        == ExplanationSource.TEMPLATE_FALLBACK
    )

    assert result.fallback_reason is not None
    assert "fraud confirmed" not in (
        result.full_text.casefold()
    )


def test_invented_number_uses_fallback() -> None:
    service = GroundedExplanationService(
        generator=InventedNumberGenerator(),
        enabled=True,
    )

    result = service.generate_explanation(
        incident=build_incident(),
        request=GenerateExplanationRequest(),
    )

    assert (
        result.generated_by
        == ExplanationSource.TEMPLATE_FALLBACK
    )

    assert "47 minutes" not in result.full_text


def test_bangla_template_fallback_is_complete() -> None:
    service = GroundedExplanationService(
        generator=None,
        enabled=False,
    )

    result = service.generate_explanation(
        incident=build_incident(),
        request=GenerateExplanationRequest(
            language=ExplanationLanguage.BANGLA,
            audience=ExplanationAudience.AGENT,
        ),
    )

    assert (
        result.generated_by
        == ExplanationSource.TEMPLATE_FALLBACK
    )

    assert "পরিস্থিতি" in result.full_text
    assert "প্রমাণ" in result.full_text
    assert "অনিশ্চয়তা" in result.full_text

    assert (
        "নিরাপদ পরবর্তী পদক্ষেপ"
        in result.full_text
    )


def test_cross_provider_data_is_redacted() -> None:
    service = GroundedExplanationService(
        generator=None,
        enabled=False,
    )

    result = service.generate_explanation(
        incident=build_incident(
            provider_scope=[
                ProviderID.BKASH,
                ProviderID.NAGAD,
            ]
        ),
        request=GenerateExplanationRequest(
            language=ExplanationLanguage.ENGLISH,
            audience=(
                ExplanationAudience.PROVIDER_OPERATIONS
            ),
            viewer_provider_id=ProviderID.BKASH,
        ),
    )

    assert result.provider_data_redacted is True

    assert "TXN-001" not in result.full_text
    assert "TXN-002" not in result.full_text