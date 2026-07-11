from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.ingestion.canonical_event import ProviderID


class ExplanationLanguage(StrEnum):
    ENGLISH = "en"
    BANGLA = "bn"
    BANGLISH = "banglish"


class ExplanationAudience(StrEnum):
    AGENT = "AGENT"
    FIELD_OFFICER = "FIELD_OFFICER"
    PROVIDER_OPERATIONS = "PROVIDER_OPERATIONS"
    RISK_REVIEWER = "RISK_REVIEWER"
    MANAGEMENT = "MANAGEMENT"


class ExplanationSource(StrEnum):
    OPENAI = "OPENAI"
    TEMPLATE_FALLBACK = "TEMPLATE_FALLBACK"


class ExplanationSchema(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class GenerateExplanationRequest(ExplanationSchema):
    language: ExplanationLanguage = (
        ExplanationLanguage.ENGLISH
    )

    audience: ExplanationAudience = (
        ExplanationAudience.FIELD_OFFICER
    )

    viewer_provider_id: ProviderID | None = None

    prefer_ai: bool = True


class AIExplanationDraft(ExplanationSchema):
    """
    Exact structure returned by OpenAI Structured Outputs.
    """

    headline: str = Field(
        min_length=1,
        max_length=180,
    )

    situation: str = Field(
        min_length=1,
        max_length=1000,
    )

    evidence: list[str] = Field(
        min_length=1,
        max_length=5,
    )

    uncertainty: str = Field(
        min_length=1,
        max_length=700,
    )

    safe_next_step: str = Field(
        min_length=1,
        max_length=1000,
    )

    provider_boundary_notice: str = Field(
        min_length=1,
        max_length=500,
    )


class ExplanationUsage(ExplanationSchema):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

class ExplanationHealthStatus(StrEnum):
    AI_READY = "AI_READY"
    FALLBACK_ONLY = "FALLBACK_ONLY"
    DISABLED = "DISABLED"


class ExplanationHealth(ExplanationSchema):
    status: ExplanationHealthStatus

    ai_enabled: bool
    ai_configured: bool

    model: str | None = None

    structured_outputs_enabled: bool = True
    deterministic_fallback_available: bool = True

    message: str


class GroundedExplanation(ExplanationSchema):
    incident_id: str

    language: ExplanationLanguage
    audience: ExplanationAudience

    generated_by: ExplanationSource

    model: str | None = None

    headline: str
    situation: str

    evidence: list[str]

    uncertainty: str
    safe_next_step: str
    provider_boundary_notice: str

    full_text: str

    grounded: bool
    safety_validated: bool

    provider_data_redacted: bool

    fallback_reason: str | None = None

    latency_ms: float = Field(ge=0)

    usage: ExplanationUsage = Field(
        default_factory=ExplanationUsage
    )