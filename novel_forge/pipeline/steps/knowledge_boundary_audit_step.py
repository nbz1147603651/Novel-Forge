"""LLM adjudication for chapter knowledge-boundary audit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.pipeline.steps.base import PipelineStep


@dataclass(frozen=True)
class KnowledgeBoundaryAuditInput:
    """Input payload for LLM knowledge-boundary adjudication."""

    chapter_number: int
    chapter_text: str
    pov_character: str = ""
    audit_stage: str = "review"
    audit_candidates: list[dict[str, Any]] = field(default_factory=list)
    prescreen_hits: list[dict[str, Any]] = field(default_factory=list)
    allowed_current_ops: list[dict[str, Any]] = field(default_factory=list)
    cognitive_constraints: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class KnowledgeBoundaryAuditResult:
    """Structured adjudication result from the knowledge-boundary audit step."""

    verdict: str
    issues: list[dict[str, Any]]
    raw_payload: dict[str, Any]


class KnowledgeBoundaryAuditStep(
    PipelineStep[KnowledgeBoundaryAuditInput, KnowledgeBoundaryAuditResult]
):
    """Ask a dedicated audit LLM to judge local hidden-knowledge candidates."""

    @property
    def step_name(self) -> str:
        return "knowledge_boundary_audit"

    async def _execute(
        self,
        input_data: KnowledgeBoundaryAuditInput,
    ) -> KnowledgeBoundaryAuditResult:
        if not input_data.audit_candidates or not input_data.prescreen_hits:
            return KnowledgeBoundaryAuditResult(
                verdict="pass",
                issues=[],
                raw_payload={"verdict": "pass", "issues": []},
            )

        context = {
            "chapter_number": input_data.chapter_number,
            "pov_character": input_data.pov_character,
            "audit_stage": input_data.audit_stage,
            "numbered_text": _number_paragraphs(input_data.chapter_text),
            "audit_candidates": input_data.audit_candidates,
            "prescreen_hits": input_data.prescreen_hits,
            "allowed_current_ops": input_data.allowed_current_ops,
            "cognitive_constraints": input_data.cognitive_constraints,
        }
        data = await self._call_with_retry(
            TaskType.KNOWLEDGE_BOUNDARY_AUDIT,
            context,
            max_tokens=self._dynamic_max_tokens(
                TaskType.KNOWLEDGE_BOUNDARY_AUDIT,
                max(1800, len(input_data.prescreen_hits) * 420),
                prompt_overhead=3600 + sum(len(str(item)) for item in input_data.audit_candidates),
                min_tokens=2048,
                max_cap=8192,
            ),
            temperature=float(getattr(self.settings, "temp_knowledge_boundary_audit", 0.1) or 0.1),
            required_keys=("verdict", "issues"),
            max_retries=2,
        )
        issues_raw = data.get("issues") if isinstance(data, dict) else []
        issues = [dict(item) for item in issues_raw if isinstance(item, dict)]
        return KnowledgeBoundaryAuditResult(
            verdict=str(data.get("verdict", "") or "pass") if isinstance(data, dict) else "pass",
            issues=issues,
            raw_payload=dict(data) if isinstance(data, dict) else {"verdict": "pass", "issues": []},
        )


def _number_paragraphs(text: str) -> str:
    """Return chapter text with stable one-based paragraph labels."""

    paragraphs = [part.strip() for part in str(text or "").split("\n\n") if part.strip()]
    if not paragraphs:
        paragraphs = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not paragraphs:
        return ""
    return "\n\n".join(f"[P{idx}] {paragraph}" for idx, paragraph in enumerate(paragraphs, 1))
