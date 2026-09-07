"""Single-chapter editorial quality check."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.editorial.metrics import chapter_editorial_findings, editorial_metrics_payload
from novel_forge.editorial.schemas import EditorialAuditReport, EditorialContract, EditorialFinding
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.token_budget import route_max_output_budget


@dataclass
class EditorialCheckInput:
    """Inputs for CHECK_EDITORIAL."""

    chapter_number: int
    chapter_text: str
    editorial_contract: EditorialContract | dict[str, Any]
    chapter_contract: dict[str, Any] = field(default_factory=dict)
    forbidden_reveal_boundaries: list[dict[str, Any]] = field(default_factory=list)
    extra_context: dict[str, Any] = field(default_factory=dict)


class EditorialCheckStep(PipelineStep[EditorialCheckInput, EditorialAuditReport]):
    """Run local + LLM editorial checks for one chapter."""

    @property
    def step_name(self) -> str:
        return "check_editorial"

    async def _execute(self, input_data: EditorialCheckInput) -> EditorialAuditReport:
        contract = (
            input_data.editorial_contract
            if isinstance(input_data.editorial_contract, EditorialContract)
            else EditorialContract.model_validate(input_data.editorial_contract)
        )
        local_findings = chapter_editorial_findings(
            chapter_number=input_data.chapter_number,
            chapter_text=input_data.chapter_text,
            contract=contract,
        )
        metrics = editorial_metrics_payload(
            chapter_text=input_data.chapter_text,
            contract=contract,
        )
        payload = await self._call_with_retry(
            TaskType.CHECK_EDITORIAL,
            {
                "chapter_number": input_data.chapter_number,
                "chapter_text": input_data.chapter_text,
                "editorial_contract": contract.model_dump(mode="json"),
                "chapter_contract": dict(input_data.chapter_contract or {}),
                "forbidden_reveal_boundaries": list(input_data.forbidden_reveal_boundaries or []),
                "local_findings": [item.model_dump(mode="json") for item in local_findings],
                "local_metrics": metrics,
                **input_data.extra_context,
            },
            max_tokens=route_max_output_budget(
                self._router,
                TaskType.CHECK_EDITORIAL,
                min_tokens=4096,
            ),
            temperature=0.2,
            required_keys=("summary", "findings", "revision_plan", "metrics"),
        )
        report = EditorialAuditReport.model_validate(_normalize_report_payload(payload))
        merged = _merge_findings(local_findings, report.findings)
        return report.model_copy(
            update={"findings": merged, "metrics": {**metrics, **report.metrics}}
        )


def _normalize_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["findings"] = [
        item for item in normalized.get("findings", []) or [] if isinstance(item, dict)
    ]
    normalized["revision_plan"] = [
        str(item) for item in normalized.get("revision_plan", []) or [] if str(item).strip()
    ]
    normalized["metrics"] = (
        normalized.get("metrics", {}) if isinstance(normalized.get("metrics"), dict) else {}
    )
    return normalized


def _merge_findings(
    local_findings: list[EditorialFinding],
    llm_findings: list[EditorialFinding],
) -> list[EditorialFinding]:
    merged: list[EditorialFinding] = []
    seen: set[tuple[str, int, str]] = set()
    for item in [*local_findings, *llm_findings]:
        key = (item.issue_type, item.chapter_number, item.summary[:80])
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged
