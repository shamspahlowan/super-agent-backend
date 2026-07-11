from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from openai import OpenAI, OpenAIError

from app.ingestion.canonical_event import ProviderID
from app.schemas.case import CoordinationCase
from app.schemas.explanation import (
    AIExplanationDraft,
    ExplanationAudience,
    ExplanationLanguage,
    ExplanationSource,
    ExplanationUsage,
    GenerateExplanationRequest,
    GroundedExplanation,
)
from app.schemas.incident import (
    IncidentType,
    OperationalIncident,
)


class ExplanationServiceError(RuntimeError):
    """Base explanation-service exception."""


class AIExplanationValidationError(
    ExplanationServiceError
):
    """Raised when an AI explanation violates grounding rules."""


@dataclass(frozen=True)
class ExplanationGeneratorResult:
    draft: AIExplanationDraft
    model: str
    usage: ExplanationUsage


class ExplanationGenerator(Protocol):
    def generate(
        self,
        *,
        system_prompt: str,
        grounding_payload: dict[str, Any],
    ) -> ExplanationGeneratorResult:
        ...


class OpenAIExplanationGenerator:
    """
    Thin wrapper around OpenAI Responses API Structured Outputs.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.6-terra",
        timeout_seconds: float = 20,
        max_output_tokens: int = 900,
    ) -> None:
        if not api_key.strip():
            raise ExplanationServiceError(
                "OpenAI API key cannot be empty."
            )

        self.model = model
        self.max_output_tokens = max_output_tokens

        self.client = OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=1,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        grounding_payload: dict[str, Any],
    ) -> ExplanationGeneratorResult:
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        grounding_payload,
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            text_format=AIExplanationDraft,
            max_output_tokens=(
                self.max_output_tokens
            ),
            store=False,
        )

        draft = response.output_parsed

        if draft is None:
            raise ExplanationServiceError(
                "OpenAI returned no parsed explanation."
            )

        response_usage = getattr(
            response,
            "usage",
            None,
        )

        input_tokens = int(
            getattr(
                response_usage,
                "input_tokens",
                0,
            )
            or 0
        )

        output_tokens = int(
            getattr(
                response_usage,
                "output_tokens",
                0,
            )
            or 0
        )

        total_tokens = int(
            getattr(
                response_usage,
                "total_tokens",
                input_tokens + output_tokens,
            )
            or input_tokens
            + output_tokens
        )

        return ExplanationGeneratorResult(
            draft=draft,
            model=self.model,
            usage=ExplanationUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
        )


class GroundedExplanationService:
    """
    Generates grounded multilingual incident explanations.

    Important boundaries:
    - AI cannot modify the incident or case.
    - AI cannot declare fraud.
    - AI cannot recommend blocking, freezing or transferring funds.
    - Invalid AI output automatically falls back to deterministic text.
    """

    FORBIDDEN_PHRASES = {
        "fraud confirmed",
        "confirmed fraud",
        "confirmed as fraud",
        "is fraudulent",
        "block the account",
        "freeze the account",
        "automatically transfer",
        "automatic transfer",
        "auto transfer",
        "automatically refill",
        "wallet refill",
        "প্রতারণা নিশ্চিত",
        "অ্যাকাউন্ট ব্লক",
        "অ্যাকাউন্ট ফ্রিজ",
        "স্বয়ংক্রিয়ভাবে টাকা স্থানান্তর",
        "fraud confirm",
        "account block korun",
        "account freeze korun",
        "auto transfer korun",
    }

    BENGALI_DIGIT_TRANSLATION = str.maketrans(
        "০১২৩৪৫৬৭৮৯",
        "0123456789",
    )

    def __init__(
        self,
        *,
        generator: ExplanationGenerator | None,
        enabled: bool = True,
    ) -> None:
        self.generator = generator
        self.enabled = enabled

    def generate_explanation(
        self,
        *,
        incident: OperationalIncident,
        request: GenerateExplanationRequest,
        coordination_case: CoordinationCase | None = None,
    ) -> GroundedExplanation:
        started = time.perf_counter()

        (
            grounding_payload,
            provider_data_redacted,
        ) = self._build_grounding_payload(
            incident=incident,
            request=request,
            coordination_case=coordination_case,
        )

        fallback_reason: str | None = None

        if (
            request.prefer_ai
            and self.enabled
            and self.generator is not None
        ):
            try:
                generated = self.generator.generate(
                    system_prompt=(
                        self._system_prompt(request)
                    ),
                    grounding_payload=(
                        grounding_payload
                    ),
                )

                self._validate_ai_draft(
                    draft=generated.draft,
                    grounding_payload=(
                        grounding_payload
                    ),
                    request=request,
                )

                latency_ms = (
                    time.perf_counter()
                    - started
                ) * 1000

                return self._build_result(
                    incident_id=incident.incident_id,
                    request=request,
                    draft=generated.draft,
                    generated_by=(
                        ExplanationSource.OPENAI
                    ),
                    model=generated.model,
                    grounded=True,
                    safety_validated=True,
                    provider_data_redacted=(
                        provider_data_redacted
                    ),
                    fallback_reason=None,
                    latency_ms=latency_ms,
                    usage=generated.usage,
                )

            except (
                OpenAIError,
                ExplanationServiceError,
                ValueError,
                TypeError,
            ) as exc:
                fallback_reason = (
                    f"{type(exc).__name__}: {exc}"
                )

        else:
            fallback_reason = (
                "AI_DISABLED_UNAVAILABLE_OR_NOT_REQUESTED"
            )

        fallback_draft = self._template_fallback(
            incident=incident,
            request=request,
            grounding_payload=grounding_payload,
        )

        latency_ms = (
            time.perf_counter()
            - started
        ) * 1000

        return self._build_result(
            incident_id=incident.incident_id,
            request=request,
            draft=fallback_draft,
            generated_by=(
                ExplanationSource.TEMPLATE_FALLBACK
            ),
            model=None,
            grounded=True,
            safety_validated=True,
            provider_data_redacted=(
                provider_data_redacted
            ),
            fallback_reason=fallback_reason,
            latency_ms=latency_ms,
            usage=ExplanationUsage(),
        )

    def _build_grounding_payload(
        self,
        *,
        incident: OperationalIncident,
        request: GenerateExplanationRequest,
        coordination_case: CoordinationCase | None,
    ) -> tuple[dict[str, Any], bool]:
        provider_scope = [
            provider.value
            for provider in incident.provider_scope
        ]

        expose_transaction_ids = (
            request.audience
            == ExplanationAudience.RISK_REVIEWER
        )

        provider_data_redacted = False

        visible_provider_scope: list[str] = list(
            provider_scope
        )

        if (
            request.audience
            == ExplanationAudience.PROVIDER_OPERATIONS
        ):
            if request.viewer_provider_id is None:
                raise ExplanationProviderBoundaryError(
                    "viewer_provider_id is required for "
                    "PROVIDER_OPERATIONS explanations."
                )

            if (
                incident.provider_scope
                and request.viewer_provider_id
                not in incident.provider_scope
            ):
                raise ExplanationProviderBoundaryError(
                    "The selected provider is outside the "
                    "incident provider scope."
                )

        if request.audience in {
            ExplanationAudience.AGENT,
            ExplanationAudience.FIELD_OFFICER,
            ExplanationAudience.MANAGEMENT,
        }:
            expose_transaction_ids = False

        if (
            request.audience
            == ExplanationAudience.PROVIDER_OPERATIONS
            and request.viewer_provider_id is not None
        ):
            other_provider_exists = any(
                provider
                != request.viewer_provider_id.value
                for provider in provider_scope
            )

            visible_provider_scope = [
                request.viewer_provider_id.value
            ]

            if other_provider_exists:
                visible_provider_scope.append(
                    "OTHER_PROVIDER_CONTEXT"
                )

                provider_data_redacted = True

            expose_transaction_ids = False

        evidence = []

        for item in incident.evidence[:8]:
            evidence.append(
                {
                    "source": item.source.value,
                    "code": item.code,
                    "message": item.message,
                    "value": item.value,
                    "points": item.points,
                    "transaction_ids": (
                        item.transaction_ids[:10]
                        if expose_transaction_ids
                        else []
                    ),
                }
            )

        case_payload: dict[str, Any] | None = None

        if coordination_case is not None:
            case_payload = {
                "case_id": coordination_case.case_id,
                "status": (
                    coordination_case.status.value
                ),
                "owner_role": (
                    coordination_case
                    .case_owner.role.value
                    if coordination_case.case_owner
                    is not None
                    else None
                ),
                "acknowledged": (
                    coordination_case
                    .acknowledged_at
                    is not None
                ),
                "safe_fallback_active": (
                    coordination_case
                    .safe_fallback_active
                ),
                "advisory_only": (
                    coordination_case.advisory_only
                ),
                "automated_financial_action_allowed": (
                    coordination_case
                    .automated_financial_action_allowed
                ),
            }

        payload = {
            "language": request.language.value,
            "audience": request.audience.value,
            "incident": {
                "incident_id": incident.incident_id,
                "agent_id": incident.agent_id,
                "area": incident.area,
                "provider_scope": (
                    visible_provider_scope
                ),
                "incident_type": (
                    incident.incident_type.value
                ),
                "priority": incident.priority.value,
                "status": incident.status.value,
                "title": incident.title,
                "summary": incident.summary,
                "confidence": incident.confidence,
                "receiver_role": (
                    incident.receiver_role.value
                ),
                "responsible_stakeholder": (
                    incident
                    .responsible_stakeholder.value
                ),
                "human_review_required": (
                    incident.human_review_required
                ),
                "strong_recommendation_allowed": (
                    incident
                    .strong_recommendation_allowed
                ),
                "recommended_next_step": (
                    incident
                    .recommended_next_step
                ),
                "evidence": evidence,
                "uncertainty": (
                    incident.uncertainty[:5]
                ),
                "alternative_explanations": (
                    incident
                    .alternative_explanations[:5]
                ),
            },
            "case": case_payload,
            "hard_boundaries": {
                "advisory_only": True,
                "may_declare_fraud": False,
                "may_block_or_freeze": False,
                "may_execute_financial_action": False,
                "must_require_human_review": True,
                "provider_data_redacted": (
                    provider_data_redacted
                ),
            },
        }

        return payload, provider_data_redacted

    @staticmethod
    def _system_prompt(
        request: GenerateExplanationRequest,
    ) -> str:
        language_instruction = {
            ExplanationLanguage.ENGLISH: (
                "Write clear professional English."
            ),
            ExplanationLanguage.BANGLA: (
                "Write clear natural Bangla using Bengali script."
            ),
            ExplanationLanguage.BANGLISH: (
                "Write simple Banglish using Latin script."
            ),
        }[request.language]

        audience_instruction = {
            ExplanationAudience.AGENT: (
                "Use simple operational language for an agent."
            ),
            ExplanationAudience.FIELD_OFFICER: (
                "Focus on agent contact, verification and coordination."
            ),
            ExplanationAudience.PROVIDER_OPERATIONS: (
                "Focus on provider-scoped evidence and approved coordination."
            ),
            ExplanationAudience.RISK_REVIEWER: (
                "Focus on explainable indicators, uncertainty and human review."
            ),
            ExplanationAudience.MANAGEMENT: (
                "Use concise management-level operational language."
            ),
        }[request.audience]

        return f"""
You are a grounded financial-operations explanation assistant.

{language_instruction}
{audience_instruction}

Use only facts supplied in the JSON payload.

Mandatory rules:
1. Do not invent balances, times, percentages, transaction counts,
   providers, people, causes, or actions.
2. Keep all numbers in Arabic numerals, even in Bangla.
3. Never state or imply that fraud is confirmed.
4. Use language such as unusual, requires review, may, or could.
5. Never recommend blocking accounts, freezing funds, transferring
   money, automatic wallet refill, reversal, or another financial action.
6. Explain the situation, evidence, uncertainty and safe next step.
7. Preserve provider boundaries.
8. If another provider is represented as OTHER_PROVIDER_CONTEXT,
   do not guess or name that provider.
9. Make clear that the output is advisory and requires human review.
10. Return only the structured fields required by the response schema.
""".strip()

    def _validate_ai_draft(
        self,
        *,
        draft: AIExplanationDraft,
        grounding_payload: dict[str, Any],
        request: GenerateExplanationRequest,
    ) -> None:
        combined_text = " ".join(
            [
                draft.headline,
                draft.situation,
                *draft.evidence,
                draft.uncertainty,
                draft.safe_next_step,
                draft.provider_boundary_notice,
            ]
        )

        normalized = (
            combined_text
            .translate(
                self.BENGALI_DIGIT_TRANSLATION
            )
            .casefold()
        )

        for phrase in self.FORBIDDEN_PHRASES:
            if phrase.casefold() in normalized:
                raise AIExplanationValidationError(
                    "AI output contained forbidden language: "
                    f"{phrase}"
                )

        self._validate_numbers(
            output_text=normalized,
            grounding_payload=grounding_payload,
        )

        self._validate_provider_names(
            output_text=normalized,
            grounding_payload=grounding_payload,
            request=request,
        )

        uncertainty_text = (
            draft.uncertainty.casefold()
        )

        uncertainty_markers = {
            "uncertain",
            "may",
            "could",
            "not proof",
            "human review",
            "অনিশ্চিত",
            "হতে পারে",
            "নিশ্চিত নয়",
            "মানব পর্যালোচনা",
            "hote pare",
            "nishchit noy",
            "human review",
        }

        if not any(
            marker in uncertainty_text
            for marker in uncertainty_markers
        ):
            raise AIExplanationValidationError(
                "AI uncertainty section does not clearly "
                "express uncertainty or human review."
            )

    @classmethod
    def _validate_numbers(
        cls,
        *,
        output_text: str,
        grounding_payload: dict[str, Any],
    ) -> None:
        grounding_text = json.dumps(
            grounding_payload,
            ensure_ascii=False,
            default=str,
        ).translate(
            cls.BENGALI_DIGIT_TRANSLATION
        )

        number_pattern = (
            r"(?<![\w])\d+(?:\.\d+)?"
        )

        allowed_numbers = set(
            re.findall(
                number_pattern,
                grounding_text,
            )
        )

        output_numbers = set(
            re.findall(
                number_pattern,
                output_text,
            )
        )

        unsupported_numbers = (
            output_numbers - allowed_numbers
        )

        if unsupported_numbers:
            raise AIExplanationValidationError(
                "AI output introduced unsupported numbers: "
                + ", ".join(
                    sorted(unsupported_numbers)
                )
            )

    @staticmethod
    def _validate_provider_names(
        *,
        output_text: str,
        grounding_payload: dict[str, Any],
        request: GenerateExplanationRequest,
    ) -> None:
        visible_scope = set(
            grounding_payload["incident"][
                "provider_scope"
            ]
        )

        known_providers = {
            provider.value
            for provider in ProviderID
        }

        allowed_provider_names = {
            provider
            for provider in visible_scope
            if provider
            != "OTHER_PROVIDER_CONTEXT"
        }

        if request.viewer_provider_id is not None:
            allowed_provider_names.add(
                request.viewer_provider_id.value
            )

        forbidden_providers = (
            known_providers
            - allowed_provider_names
        )

        for provider in forbidden_providers:
            if provider.casefold() in output_text:
                raise AIExplanationValidationError(
                    "AI output exposed a provider outside "
                    f"the visible scope: {provider}"
                )

    def _template_fallback(
        self,
        *,
        incident: OperationalIncident,
        request: GenerateExplanationRequest,
        grounding_payload: dict[str, Any],
    ) -> AIExplanationDraft:
        evidence = self._fallback_evidence(
            grounding_payload=grounding_payload,
            language=request.language,
        )

        if request.language == ExplanationLanguage.BANGLA:
            return AIExplanationDraft(
                headline=self._bangla_headline(
                    incident.incident_type
                ),
                situation=self._bangla_situation(
                    incident.incident_type
                ),
                evidence=evidence,
                uncertainty=(
                    "এটি একটি পরামর্শমূলক মূল্যায়ন। "
                    "এটি নিশ্চিত ফল বা প্রতারণার সিদ্ধান্ত নয়; "
                    "মানব পর্যালোচনা প্রয়োজন।"
                ),
                safe_next_step=(
                    self._bangla_next_step(
                        incident.incident_type
                    )
                ),
                provider_boundary_notice=(
                    "প্রতিটি প্রদানকারীর ব্যালেন্স, তথ্য ও "
                    "সিদ্ধান্তের সীমা আলাদা রাখতে হবে।"
                ),
            )

        if (
            request.language
            == ExplanationLanguage.BANGLISH
        ):
            return AIExplanationDraft(
                headline=self._banglish_headline(
                    incident.incident_type
                ),
                situation=self._banglish_situation(
                    incident.incident_type
                ),
                evidence=evidence,
                uncertainty=(
                    "Eta advisory assessment. Eta kono "
                    "confirmed result ba fraud decision noy; "
                    "human review dorkar."
                ),
                safe_next_step=(
                    self._banglish_next_step(
                        incident.incident_type
                    )
                ),
                provider_boundary_notice=(
                    "Prottek provider-er balance, data ebong "
                    "decision boundary alada rakhte hobe."
                ),
            )

        return AIExplanationDraft(
            headline=incident.title,
            situation=incident.summary,
            evidence=evidence,
            uncertainty=(
                "This is an advisory assessment, not proof "
                "of fraud or a confirmed outcome. Human review "
                "is required before operational action."
            ),
            safe_next_step=(
                incident.recommended_next_step
            ),
            provider_boundary_notice=(
                "Provider balances, data and operational "
                "authority must remain separate."
            ),
        )

    @staticmethod
    def _fallback_evidence(
        *,
        grounding_payload: dict[str, Any],
        language: ExplanationLanguage,
    ) -> list[str]:
        evidence_items = (
            grounding_payload["incident"][
                "evidence"
            ]
        )

        labels = {
            ExplanationLanguage.ENGLISH: {
                "CURRENT_BALANCE": "Current balance",
                "SAFETY_RESERVE": "Safety reserve",
                "RECENT_ACTIVITY": "Recent activity",
                "CASH_FLOW": "Recent cash flow",
                "DATA_TRUST": "Data trust",
                "NET_DEPLETION_RATE": "Depletion rate",
                "TRANSACTION_VELOCITY": "Transaction velocity",
                "NEAR_IDENTICAL_AMOUNTS": "Similar amounts",
                "ACCOUNT_CONCENTRATION": "Account concentration",
                "CROSS_PROVIDER_LINK": "Cross-provider pattern",
                "ABNORMAL_FAILURE_RATE": "Failure rate",
            },
            ExplanationLanguage.BANGLA: {
                "CURRENT_BALANCE": "বর্তমান ব্যালেন্স",
                "SAFETY_RESERVE": "নিরাপদ রিজার্ভ",
                "RECENT_ACTIVITY": "সাম্প্রতিক কার্যক্রম",
                "CASH_FLOW": "সাম্প্রতিক ক্যাশ প্রবাহ",
                "DATA_TRUST": "ডেটার নির্ভরযোগ্যতা",
                "NET_DEPLETION_RATE": "ব্যালেন্স কমার হার",
                "TRANSACTION_VELOCITY": "লেনদেনের গতি",
                "NEAR_IDENTICAL_AMOUNTS": "প্রায় একই পরিমাণ",
                "ACCOUNT_CONCENTRATION": "অল্প অ্যাকাউন্টে ঘনত্ব",
                "CROSS_PROVIDER_LINK": "ক্রস-প্রোভাইডার প্যাটার্ন",
                "ABNORMAL_FAILURE_RATE": "ব্যর্থতার হার",
            },
            ExplanationLanguage.BANGLISH: {
                "CURRENT_BALANCE": "Current balance",
                "SAFETY_RESERVE": "Safe reserve",
                "RECENT_ACTIVITY": "Recent activity",
                "CASH_FLOW": "Recent cash flow",
                "DATA_TRUST": "Data trust",
                "NET_DEPLETION_RATE": "Balance komar rate",
                "TRANSACTION_VELOCITY": "Transaction velocity",
                "NEAR_IDENTICAL_AMOUNTS": "Almost same amount",
                "ACCOUNT_CONCENTRATION": "Kom account-e concentration",
                "CROSS_PROVIDER_LINK": "Cross-provider pattern",
                "ABNORMAL_FAILURE_RATE": "Failure rate",
            },
        }[language]

        rendered: list[str] = []

        for item in evidence_items[:4]:
            code = str(item["code"])

            label = labels.get(
                code,
                str(item["message"]),
            )

            value = item.get("value")

            rendered.append(
                f"{label}: {value}"
                if value not in {None, ""}
                else label
            )

        if rendered:
            return rendered

        generic = {
            ExplanationLanguage.ENGLISH: (
                "The alert was produced from the current "
                "structured incident evidence."
            ),
            ExplanationLanguage.BANGLA: (
                "বর্তমান কাঠামোবদ্ধ ঘটনার প্রমাণের ভিত্তিতে "
                "এই সতর্কতা তৈরি হয়েছে।"
            ),
            ExplanationLanguage.BANGLISH: (
                "Current structured incident evidence-er "
                "base-e ei alert toiri hoyeche."
            ),
        }[language]

        return [generic]

    @staticmethod
    def _bangla_headline(
        incident_type: IncidentType,
    ) -> str:
        return {
            IncidentType.LIQUIDITY_PRESSURE: (
                "তারল্য চাপ শনাক্ত হয়েছে"
            ),
            IncidentType.UNUSUAL_ACTIVITY: (
                "অস্বাভাবিক কার্যক্রম পর্যালোচনা প্রয়োজন"
            ),
            IncidentType.COMBINED_PRIORITY: (
                "তারল্য চাপ ও অস্বাভাবিক কার্যক্রম"
            ),
            IncidentType.DATA_QUALITY: (
                "প্রদানকারীর ডেটা সমস্যা"
            ),
        }[incident_type]

    @staticmethod
    def _bangla_situation(
        incident_type: IncidentType,
    ) -> str:
        return {
            IncidentType.LIQUIDITY_PRESSURE: (
                "সাম্প্রতিক লেনদেনের ধারায় একটি অপারেশনাল "
                "ব্যালেন্স নিরাপদ সীমার দিকে কমছে।"
            ),
            IncidentType.UNUSUAL_ACTIVITY: (
                "সাম্প্রতিক লেনদেনে এমন কিছু প্যাটার্ন দেখা "
                "গেছে যা মানব পর্যালোচনা প্রয়োজন।"
            ),
            IncidentType.COMBINED_PRIORITY: (
                "তারল্য কমার পাশাপাশি অস্বাভাবিক লেনদেনের "
                "প্যাটার্নও দেখা গেছে।"
            ),
            IncidentType.DATA_QUALITY: (
                "প্রদানকারীর ডেটা দেরিতে, অনুপস্থিত বা "
                "অসঙ্গত হওয়ায় নির্ভরযোগ্য সিদ্ধান্ত সীমিত।"
            ),
        }[incident_type]

    @staticmethod
    def _bangla_next_step(
        incident_type: IncidentType,
    ) -> str:
        if incident_type == IncidentType.DATA_QUALITY:
            return (
                "প্রথমে সংশ্লিষ্ট ফিড পুনরুদ্ধার বা যাচাই করুন। "
                "ডেটা যাচাই না হওয়া পর্যন্ত শক্তিশালী সিদ্ধান্ত দেবেন না।"
            )

        if (
            incident_type
            == IncidentType.UNUSUAL_ACTIVITY
        ):
            return (
                "তালিকাভুক্ত প্রমাণ ও স্থানীয় পরিস্থিতি মানবভাবে "
                "পর্যালোচনা করুন। কোনো অ্যাকাউন্ট ব্লক বা তহবিল "
                "ফ্রিজ করবেন না।"
            )

        return (
            "এজেন্টের সাথে যোগাযোগ করে বর্তমান চাহিদা যাচাই করুন "
            "এবং শুধুমাত্র অনুমোদিত অপারেশনাল চ্যানেলে সহায়তা "
            "সমন্বয় করুন।"
        )

    @staticmethod
    def _banglish_headline(
        incident_type: IncidentType,
    ) -> str:
        return {
            IncidentType.LIQUIDITY_PRESSURE: (
                "Liquidity pressure detect hoyeche"
            ),
            IncidentType.UNUSUAL_ACTIVITY: (
                "Unusual activity review dorkar"
            ),
            IncidentType.COMBINED_PRIORITY: (
                "Liquidity pressure ebong unusual activity"
            ),
            IncidentType.DATA_QUALITY: (
                "Provider data issue"
            ),
        }[incident_type]

    @staticmethod
    def _banglish_situation(
        incident_type: IncidentType,
    ) -> str:
        return {
            IncidentType.LIQUIDITY_PRESSURE: (
                "Recent transaction flow onujayi ekta operational "
                "balance safe limit-er dike komche."
            ),
            IncidentType.UNUSUAL_ACTIVITY: (
                "Recent transaction-e emon pattern dekha geche "
                "jeta human review dorkar."
            ),
            IncidentType.COMBINED_PRIORITY: (
                "Liquidity komar sathe unusual transaction "
                "pattern-o dekha geche."
            ),
            IncidentType.DATA_QUALITY: (
                "Provider data late, missing ba conflicting howay "
                "reliable conclusion deya jacche na."
            ),
        }[incident_type]

    @staticmethod
    def _banglish_next_step(
        incident_type: IncidentType,
    ) -> str:
        if incident_type == IncidentType.DATA_QUALITY:
            return (
                "Age affected feed restore ba verify korun. "
                "Data verify na howa porjonto strong recommendation diben na."
            )

        if (
            incident_type
            == IncidentType.UNUSUAL_ACTIVITY
        ):
            return (
                "Listed evidence ebong local context human review korun. "
                "Kono account block ba fund freeze korben na."
            )

        return (
            "Agent-er sathe contact kore current demand verify korun "
            "ebong shudhu approved operational channel-e support "
            "coordinate korun."
        )

    def _build_result(
        self,
        *,
        incident_id: str,
        request: GenerateExplanationRequest,
        draft: AIExplanationDraft,
        generated_by: ExplanationSource,
        model: str | None,
        grounded: bool,
        safety_validated: bool,
        provider_data_redacted: bool,
        fallback_reason: str | None,
        latency_ms: float,
        usage: ExplanationUsage,
    ) -> GroundedExplanation:
        full_text = self._compose_full_text(
            draft=draft,
            language=request.language,
        )

        return GroundedExplanation(
            incident_id=incident_id,
            language=request.language,
            audience=request.audience,
            generated_by=generated_by,
            model=model,
            headline=draft.headline,
            situation=draft.situation,
            evidence=draft.evidence,
            uncertainty=draft.uncertainty,
            safe_next_step=draft.safe_next_step,
            provider_boundary_notice=(
                draft.provider_boundary_notice
            ),
            full_text=full_text,
            grounded=grounded,
            safety_validated=safety_validated,
            provider_data_redacted=(
                provider_data_redacted
            ),
            fallback_reason=fallback_reason,
            latency_ms=round(latency_ms, 3),
            usage=usage,
        )

    @staticmethod
    def _compose_full_text(
        *,
        draft: AIExplanationDraft,
        language: ExplanationLanguage,
    ) -> str:
        headings = {
            ExplanationLanguage.ENGLISH: {
                "situation": "Situation",
                "evidence": "Evidence",
                "uncertainty": "Uncertainty",
                "next_step": "Safe next step",
                "boundary": "Provider boundary",
            },
            ExplanationLanguage.BANGLA: {
                "situation": "পরিস্থিতি",
                "evidence": "প্রমাণ",
                "uncertainty": "অনিশ্চয়তা",
                "next_step": "নিরাপদ পরবর্তী পদক্ষেপ",
                "boundary": "প্রদানকারীর সীমা",
            },
            ExplanationLanguage.BANGLISH: {
                "situation": "Situation",
                "evidence": "Evidence",
                "uncertainty": "Uncertainty",
                "next_step": "Safe next step",
                "boundary": "Provider boundary",
            },
        }[language]

        evidence_lines = "\n".join(
            f"• {item}"
            for item in draft.evidence
        )

        return (
            f"{draft.headline}\n\n"
            f"{headings['situation']}: "
            f"{draft.situation}\n\n"
            f"{headings['evidence']}:\n"
            f"{evidence_lines}\n\n"
            f"{headings['uncertainty']}: "
            f"{draft.uncertainty}\n\n"
            f"{headings['next_step']}: "
            f"{draft.safe_next_step}\n\n"
            f"{headings['boundary']}: "
            f"{draft.provider_boundary_notice}"
        )
    
class ExplanationProviderBoundaryError(
    ExplanationServiceError
):
    """Raised when a viewer requests unauthorized provider data."""