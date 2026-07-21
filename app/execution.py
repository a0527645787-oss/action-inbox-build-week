import json
import os
import re
from typing import Any
from urllib.parse import urlencode

from .models import Task
from .schemas import EmailAnalysisResult


PACKAGE_EXECUTORS = {"CHATGPT_WORK", "CODEX"}
_SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\bBearer\s+[^\s,;]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|database[_ -]?url)\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
)


def parse_structured_result(value: str | None) -> EmailAnalysisResult:
    data = json.loads(value or "{}")
    data.setdefault("execution_guidance", None)
    return EmailAnalysisResult.model_validate(data)


def _redact(value: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _safe(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    return value


def build_execution_package(task: Task, result: EmailAnalysisResult, target_executor: str) -> dict:
    if target_executor not in PACKAGE_EXECUTORS:
        raise ValueError("Unsupported package executor")
    guidance = result.execution_guidance
    if guidance is None:
        raise ValueError("Execution guidance is not available; re-analyze this task first")
    package = {
        "package_version": "1.0",
        "task": {"title": task.title, "intended_outcome": guidance.outcome.model_dump()},
        "verified_email_facts": [
            {"type": fact.type, "value": fact.value, "evidence": {"exact_quote": fact.evidence.exact_quote, "start_offset": fact.evidence.start_offset, "end_offset": fact.evidence.end_offset}}
            for fact in result.email_facts
        ],
        "business_guidance": [
            {"resource_title": item.resource_title, "instruction": item.instruction, "evidence": {"exact_quote": item.resource_evidence.exact_quote, "start_offset": item.resource_evidence.start_offset, "end_offset": item.resource_evidence.end_offset}}
            for item in result.resource_guidance
        ],
        "ordered_execution_steps": [item.model_dump() for item in guidance.ordered_steps],
        "required_inputs": [item.model_dump() for item in guidance.required_inputs],
        "missing_information": guidance.missing_information,
        "safety_constraints": [item.model_dump() for item in guidance.safety_checks]
        + [{"text": "Do not perform external side effects without explicit user review and approval.", "source": "AI_RECOMMENDATION", "supporting_fact_ids": [], "supporting_guidance_ids": []}],
        "proposed_deliverable": guidance.proposed_deliverable.model_dump(),
        "recommended_executor": guidance.recommended_executor,
        "requested_handoff_target": target_executor,
        "executor_explanation": guidance.executor_explanation,
        "execution_readiness": guidance.readiness,
        "approval_required": True,
        "handoff_instruction": "Prepare the proposed deliverable for review. Do not send, publish, pay, deploy, modify external systems, or perform another external side effect without explicit user approval and a supported integration.",
    }
    return _safe(package)


def package_as_text(package: dict) -> str:
    return json.dumps(package, indent=2, ensure_ascii=False)


def build_review_prompt(task: Task, result: EmailAnalysisResult, target: str) -> str:
    package = build_execution_package(task, result, target)
    facts = "\n".join(
        f"- {item['type']}: {item['value']} — exact evidence: {item['evidence']['exact_quote']}"
        for item in package["verified_email_facts"]
    )
    return _redact(
        f"Review and prepare this ActionInbox task; do not execute external actions.\n\n"
        f"Task: {task.title}\nSummary: {task.email.analysis.summary}\n\nVerified evidence:\n{facts}\n\n"
        "Prepare the proposed deliverable using the supplied facts and safety constraints. "
        "Clearly identify missing information. Stop before sending, publishing, paying, deploying, "
        "or modifying any external system and ask for explicit user approval."
    )


def codex_deep_link(task: Task, result: EmailAnalysisResult) -> str:
    origin = os.getenv("REPOSITORY_ORIGIN", "https://github.com/a0527645787-oss/action-inbox-build-week.git")
    return "codex://new?" + urlencode({"prompt": build_review_prompt(task, result, "CODEX"), "repo": origin})
