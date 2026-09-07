"""Whole-book editorial audit step."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.shared_anchor import build_shared_evidence_anchor
from novel_forge.editorial.metrics import detect_denouement_overrun, detect_title_repetition
from novel_forge.editorial.revision_planner import build_structured_revision_plan
from novel_forge.editorial.schemas import EditorialAuditReport, EditorialContract, EditorialFinding
from novel_forge.editorial.validators import validate_editorial_contract
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.book_audit_runner import BookAuditChapter, BookAuditRunner

_EDITORIAL_AUDIT_DIMENSIONS: tuple[tuple[str, TaskType, str], ...] = (
    (
        "structure",
        TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT,
        "本次只审结构节奏、高潮、余波、标题复用和时间桥；不要展开声纹或语言重复。",
    ),
    (
        "voice",
        TaskType.BOOK_EDITORIAL_VOICE_AUDIT,
        "本次只审角色声纹、对话区分度、叙述视角稳定性；不要展开结构重组。",
    ),
    (
        "language",
        TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT,
        "本次只审语言重复、解释腔、身体反应模板、长段视觉疲劳和情绪密度。",
    ),
    (
        "theme_symbol",
        TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT,
        "本次只审主题、象征、意象回收是否过度解释或兑现不足。",
    ),
    (
        "element",
        TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT,
        "本次只审 editorial_element_directives 的场景功能是否真正落地。",
    ),
)


@dataclass
class BookEditorialAuditInput:
    """Inputs for BOOK_EDITORIAL_AUDIT."""

    chapters: list[BookAuditChapter]
    editorial_contract: EditorialContract | dict[str, Any]
    prompt_hint: str = ""
    batch_size: int = 12
    max_tokens: int = 8192
    temperature: float = 0.2
    checkpoint_path: Path | None = None
    batch_timeout_s: float = 0.0


class BookEditorialAuditStep(PipelineStep[BookEditorialAuditInput, EditorialAuditReport]):
    """Audit publication-level issues across completed chapters."""

    @property
    def step_name(self) -> str:
        return "book_editorial_audit"

    async def _call_dimension_payloads(
        self,
        *,
        contract: EditorialContract,
        chapters: list[BookAuditChapter],
        batch: list[BookAuditChapter],
        local_findings: list[EditorialFinding],
        prompt_hint: str,
        batch_index: int,
        max_tokens: int,
        temperature: float,
    ) -> list[dict[str, Any]]:
        max_parallel = max(1, int(getattr(self.settings, "book_audit_max_parallel", 3) or 3))
        sem = asyncio.Semaphore(max_parallel)
        chapter_summaries = [chapter.to_summary_payload() for chapter in chapters]
        local_finding_payloads = [item.model_dump(mode="json") for item in local_findings]
        shared_anchor = build_shared_evidence_anchor(
            "book_editorial_audit.batch",
            {
                "chapter_summaries": chapter_summaries,
                "local_findings": local_finding_payloads,
                "shared_contract_anchor": _project_editorial_contract(
                    contract,
                    dimension="structure",
                ).get("shared_contract_anchor", {}),
                "batch_index": batch_index,
            },
            max_string_chars=900,
        )

        async def _call_one(
            dimension: str,
            task_type: TaskType,
            dimension_hint: str,
        ) -> dict[str, Any]:
            async with sem:
                payload = await self._call_with_retry(
                    task_type,
                    {
                        "editorial_contract": _project_editorial_contract(
                            contract,
                            dimension=dimension,
                        ),
                        "chapter_summaries": chapter_summaries,
                        "chapter_texts": [
                            chapter.to_text_payload() for chapter in batch if chapter.text.strip()
                        ],
                        "local_findings": local_finding_payloads,
                        "shared_evidence_anchor": shared_anchor,
                        "prompt_hint": "\n\n".join(
                            item
                            for item in (prompt_hint, dimension_hint)
                            if str(item or "").strip()
                        ),
                        "audit_dimension": dimension,
                        "batch_index": batch_index,
                    },
                    max_tokens=max(1024, max_tokens),
                    temperature=min(0.1, max(0.0, temperature)),
                    required_keys=("summary", "findings", "revision_plan", "metrics"),
                )
                normalized = _normalize_report_payload(payload)
                metrics = dict(normalized.get("metrics", {}))
                metrics["audit_dimension"] = dimension
                normalized["metrics"] = metrics
                return normalized

        return list(
            await asyncio.gather(
                *[
                    _call_one(dimension, task_type, hint)
                    for dimension, task_type, hint in _EDITORIAL_AUDIT_DIMENSIONS
                ]
            )
        )

    async def _execute(self, input_data: BookEditorialAuditInput) -> EditorialAuditReport:
        contract = (
            input_data.editorial_contract
            if isinstance(input_data.editorial_contract, EditorialContract)
            else EditorialContract.model_validate(input_data.editorial_contract)
        )
        contract_validation = validate_editorial_contract(
            contract,
            total_chapters=max(
                [chapter.chapter_number for chapter in input_data.chapters],
                default=0,
            ),
            outline_titles=[chapter.title for chapter in input_data.chapters],
        )
        local_findings = [
            *detect_title_repetition(
                [chapter.title for chapter in input_data.chapters],
                contract=contract,
            ),
            *detect_denouement_overrun(
                completed_chapters=[chapter.chapter_number for chapter in input_data.chapters],
                contract=contract,
            ),
            *contract_validation.findings,
        ]

        runner = BookAuditRunner(
            audit_name="book_editorial_audit",
            chapters=input_data.chapters,
            batch_size=input_data.batch_size,
            checkpoint_path=input_data.checkpoint_path,
            batch_timeout_s=input_data.batch_timeout_s,
        )

        async def _call_chunk(batch: list[BookAuditChapter], batch_index: int) -> dict[str, Any]:
            if bool(getattr(self.settings, "split_tasks_enabled", True)):
                dimension_payloads = await self._call_dimension_payloads(
                    contract=contract,
                    chapters=input_data.chapters,
                    batch=batch,
                    local_findings=local_findings,
                    prompt_hint=input_data.prompt_hint,
                    batch_index=batch_index,
                    max_tokens=input_data.max_tokens,
                    temperature=input_data.temperature,
                )
                return _merge_reports(
                    dimension_payloads,
                    local_findings=[],
                ).model_dump(mode="json")

            payload = await self._call_with_retry(
                TaskType.BOOK_EDITORIAL_AUDIT,
                {
                    "editorial_contract": contract.model_dump(mode="json"),
                    "chapter_summaries": [
                        chapter.to_summary_payload() for chapter in input_data.chapters
                    ],
                    "chapter_texts": [
                        chapter.to_text_payload() for chapter in batch if chapter.text.strip()
                    ],
                    "local_findings": [
                        item.model_dump(mode="json") for item in local_findings
                    ],
                    "prompt_hint": input_data.prompt_hint,
                    "batch_index": batch_index,
                },
                max_tokens=max(1024, input_data.max_tokens),
                temperature=input_data.temperature,
                required_keys=("summary", "findings", "revision_plan", "metrics"),
            )
            return _normalize_report_payload(payload)

        chunk_payloads = await runner.run(_call_chunk)
        report = _merge_reports(chunk_payloads, local_findings=local_findings)
        structured_plan = build_structured_revision_plan(
            findings=report.findings,
            chapters=input_data.chapters,
            contract=contract,
        )
        metrics = dict(report.metrics)
        metrics["structured_revision_plan"] = structured_plan
        revision_plan = list(report.revision_plan)
        for action in structured_plan.get("actions", []):
            instruction = str(action.get("instruction", "")).strip()
            if instruction and instruction not in revision_plan:
                revision_plan.append(instruction)
        return report.model_copy(update={"metrics": metrics, "revision_plan": revision_plan})


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


def _project_editorial_contract(
    contract: EditorialContract,
    *,
    dimension: str,
) -> dict[str, Any]:
    """Project the contract to one audit dimension without losing the shared anchor."""
    payload = contract.model_dump(mode="json")
    shared = {
        "title_policy": payload.get("title_policy", {}),
        "denouement_budget": payload.get("denouement_budget", {}),
    }
    keys_by_dimension: dict[str, tuple[str, ...]] = {
        "structure": (
            "climax_markers",
            "denouement_budget",
            "revelation_ladder",
            "time_bridge_policies",
            "title_policy",
        ),
        "voice": ("character_voices", "scene_resistance_rules"),
        "language": (
            "scene_resistance_rules",
            "expression_channel_budget",
            "body_signal_budget_per_high_emotion_scene",
            "forbidden_confirmation_phrases",
            "revision_priorities",
        ),
        "theme_symbol": ("theme_policies", "symbol_policies", "revision_priorities"),
        "element": (
            "editorial_element_directives",
            "climax_markers",
            "theme_policies",
            "symbol_policies",
        ),
    }
    projected = {
        key: payload.get(key)
        for key in keys_by_dimension.get(dimension, ())
        if key in payload
    }
    projected["shared_contract_anchor"] = shared
    projected["audit_dimension"] = dimension
    return projected


def _merge_reports(
    payloads: list[dict[str, Any]],
    *,
    local_findings: list[EditorialFinding],
) -> EditorialAuditReport:
    findings: list[EditorialFinding] = list(local_findings)
    revision_plan: list[str] = []
    summaries: list[str] = []
    metrics: dict[str, Any] = {"batches": len(payloads)}
    seen: set[tuple[str, int, str]] = {
        (item.issue_type, item.chapter_number, item.summary[:80]) for item in findings
    }
    for payload in payloads:
        if payload.get("summary"):
            summaries.append(str(payload.get("summary")))
        revision_plan.extend(
            str(item) for item in payload.get("revision_plan", []) or [] if str(item).strip()
        )
        if isinstance(payload.get("metrics"), dict):
            metrics.update(payload["metrics"])
        for raw in payload.get("findings", []) or []:
            try:
                finding = EditorialFinding.model_validate(raw)
            except ValueError:
                continue
            key = (finding.issue_type, finding.chapter_number, finding.summary[:80])
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return EditorialAuditReport(
        summary=summaries[0] if summaries else "全书编辑审计完成。",
        findings=findings,
        revision_plan=list(dict.fromkeys(revision_plan)),
        metrics=metrics,
    )
