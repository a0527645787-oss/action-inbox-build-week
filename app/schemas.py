from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceResult(StrictModel):
    id: str
    exact_quote: str
    start_offset: int
    end_offset: int


class EmailFactResult(StrictModel):
    id: str
    type: Literal["deadline", "amount", "required_document", "important_link", "meeting_time", "other"]
    value: str
    normalized_value: str | None
    confidence: Literal["high", "medium", "low"]
    uncertainty: str | None
    evidence: EvidenceResult


class TaskResult(StrictModel):
    id: str
    title: str
    due_at: str | None
    due_text: str | None
    uncertainty: str | None
    evidence_ids: list[str]


class ResourceEvidenceResult(StrictModel):
    exact_quote: str
    section: str | None
    start_offset: int
    end_offset: int


class ResourceGuidanceResult(StrictModel):
    id: str
    resource_id: str
    resource_title: str
    instruction: str
    related_fact_ids: list[str]
    resource_evidence: ResourceEvidenceResult


class AISuggestionResult(StrictModel):
    type: Literal["next_step", "reply_draft"]
    text: str
    supporting_fact_ids: list[str]
    supporting_guidance_ids: list[str]
    uncertainty: str | None


class ExecutionItemResult(StrictModel):
    text: str
    source: Literal["EMAIL_FACT", "BUSINESS_GUIDANCE", "AI_RECOMMENDATION", "MISSING_UNCERTAIN"]
    supporting_fact_ids: list[str]
    supporting_guidance_ids: list[str]


class ExecutionGuidanceResult(StrictModel):
    outcome: ExecutionItemResult
    ordered_steps: list[ExecutionItemResult]
    required_inputs: list[ExecutionItemResult]
    missing_information: list[str]
    safety_checks: list[ExecutionItemResult]
    proposed_deliverable: ExecutionItemResult
    recommended_executor: Literal["USER", "ACTIONINBOX", "CHATGPT_WORK", "CODEX", "FUTURE_CONNECTOR", "UNSUPPORTED"]
    executor_explanation: str
    readiness: Literal["READY_TO_PREPARE", "NEEDS_INFORMATION", "NEEDS_APPROVAL", "INTEGRATION_REQUIRED", "UNSUPPORTED"]


class EmailAnalysisResult(StrictModel):
    primary_classification: Literal["action_required", "informational", "newsletter_noise", "invoice", "meeting"]
    action_required: bool
    summary: str
    tasks: list[TaskResult]
    email_facts: list[EmailFactResult]
    resource_guidance: list[ResourceGuidanceResult]
    ai_suggestions: list[AISuggestionResult]
    missing_information: list[str]
    execution_guidance: ExecutionGuidanceResult | None
