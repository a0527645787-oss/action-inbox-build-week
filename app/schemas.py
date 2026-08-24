from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    evidence_ids: list[str] = Field(
        description="Identifiers copied exactly from email_facts[].id in this same response; do not invent relationship IDs."
    )


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
    schema_version: Literal["1", "2"] = "1"
    primary_classification: Literal["action_required", "informational", "newsletter_noise", "invoice", "meeting"] = Field(
        description="If action_required, action_required must be true and tasks must contain at least one fully evidence-backed task."
    )
    action_required: bool = Field(
        description="True exactly when at least one task is returned; every returned task requires this to be true."
    )
    summary: str
    tasks: list[TaskResult] = Field(
        description="Complete evidence-backed tasks. Conditional checks remain tasks and must preserve the condition in their wording."
    )
    email_facts: list[EmailFactResult]
    resource_guidance: list[ResourceGuidanceResult]
    ai_suggestions: list[AISuggestionResult]
    missing_information: list[str]
    execution_guidance: ExecutionGuidanceResult | None


class ModelEvidenceResultV2(StrictModel):
    exact_quote: str = Field(description="Exact, unmodified contiguous substring of the bounded email body; the application computes offsets.")


class ModelEmailFactResultV2(StrictModel):
    type: Literal["deadline", "amount", "required_document", "important_link", "meeting_time", "other"]
    value: str
    normalized_value: str | None
    confidence: Literal["high", "medium", "low"]
    uncertainty: str | None
    evidence: ModelEvidenceResultV2


class ModelTaskResultV2(StrictModel):
    title: str
    due_at: str | None
    due_text: str | None
    uncertainty: str | None
    fact_indices: list[int] = Field(description="Unique zero-based positions in email_facts from this same complete response.")


class ModelResourceGuidanceResultV2(StrictModel):
    resource_id: str
    resource_title: str
    instruction: str
    related_fact_indices: list[int]
    resource_evidence: ResourceEvidenceResult


class ModelAISuggestionResultV2(StrictModel):
    type: Literal["next_step", "reply_draft"]
    text: str
    supporting_fact_indices: list[int]
    supporting_guidance_indices: list[int]
    uncertainty: str | None


class ModelExecutionItemResultV2(StrictModel):
    text: str
    source: Literal["EMAIL_FACT", "BUSINESS_GUIDANCE", "AI_RECOMMENDATION", "MISSING_UNCERTAIN"]
    supporting_fact_indices: list[int]
    supporting_guidance_indices: list[int]


class ModelExecutionGuidanceResultV2(StrictModel):
    outcome: ModelExecutionItemResultV2
    ordered_steps: list[ModelExecutionItemResultV2]
    required_inputs: list[ModelExecutionItemResultV2]
    missing_information: list[str]
    safety_checks: list[ModelExecutionItemResultV2]
    proposed_deliverable: ModelExecutionItemResultV2
    recommended_executor: Literal["USER", "ACTIONINBOX", "CHATGPT_WORK", "CODEX", "FUTURE_CONNECTOR", "UNSUPPORTED"]
    executor_explanation: str
    readiness: Literal["READY_TO_PREPARE", "NEEDS_INFORMATION", "NEEDS_APPROVAL", "INTEGRATION_REQUIRED", "UNSUPPORTED"]


class ModelEmailAnalysisResultV2(StrictModel):
    schema_version: Literal["2"]
    primary_classification: Literal["action_required", "informational", "newsletter_noise", "invoice", "meeting"]
    action_required: bool
    summary: str
    tasks: list[ModelTaskResultV2]
    email_facts: list[ModelEmailFactResultV2]
    resource_guidance: list[ModelResourceGuidanceResultV2]
    ai_suggestions: list[ModelAISuggestionResultV2]
    missing_information: list[str]
    execution_guidance: ModelExecutionGuidanceResultV2 | None
