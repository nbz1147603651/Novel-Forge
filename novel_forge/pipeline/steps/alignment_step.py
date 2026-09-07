"""AlignmentStep — checks chapter alignment against outline and chapter plan."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.review.review_contracts import (
    compile_repair_tickets_from_findings,
    normalize_issue_to_finding,
)
from novel_forge.core.schemas.chapter import AlignmentReport
from novel_forge.core.schemas.continuity import ChapterPlan, NarrativeBlueprintContext
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.utils.field_extractor import field as extract_field
from novel_forge.core.utils.string import clean_str
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.pipeline.steps.base import PipelineStep


@dataclass
class AlignmentInput:
    """Input payload for chapter-outline alignment check."""

    chapter_outline: ChapterOutline
    chapter_plan: ChapterPlan
    chapter_text: str
    # Kept for call-site compatibility. CHECK_ALIGNMENT deliberately does not
    # forward these broad context fields because they can bias outline scoring.
    narrative_context: NarrativeBlueprintContext | None = None
    genre: str = ""
    pov_hint: str = ""
    kernel_context: dict[str, Any] | None = None
    """Optional StoryKernel field slice from ContextComposer.

    When provided, the fields are merged into the LLM context as
    ``kernel_context``, giving the model access to entities, relationships,
    timeline, world_rules, promise_ledger, motif_protocols, and
    chapter_summaries from the unified field pool.
    """


class AlignmentStep(PipelineStep[AlignmentInput, AlignmentReport]):
    """LLM-based alignment check: chapter text vs outline/plan."""

    _RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
    _RISK_ALIAS = {
        "low": "low",
        "minor": "low",
        "safe": "low",
        "medium": "medium",
        "mid": "medium",
        "moderate": "medium",
        "high": "high",
        "severe": "high",
        "critical": "high",
    }
    _DETAIL_MISSING_MARKERS = (
        "台词",
        "低语",
        "一句",
        "一行",
        "细节",
        "描写",
        "描述",
        "发烫",
        "发光",
        "闪回",
        "口述",
        "对话",
        "视角",
        "比例",
        "名字时",
        "完整呈现",
        "未呈现",
        "未体现",
    )
    _CORE_MISSING_MARKERS = (
        "核心事件",
        "主线事件",
        "关键结果",
        "结果完全",
        "完全缺失",
        "完全未",
        "未发生",
        "没有发生",
        "未达成",
        "缺位",
        "合作关系正式确立",
        "身份定位明确",
    )
    _PERSPECTIVE_DETAIL_MARKERS = (
        "视角",
        "POV",
        "pov",
        "旁观",
        "主视角",
        "副视角",
        "比例",
        "占比",
        "份额",
    )
    _COVERAGE_ALIASES = {
        "missing": "missing",
        "absent": "missing",
        "缺失": "missing",
        "缺位": "missing",
        "conflict": "conflict",
        "contradiction": "conflict",
        "冲突": "conflict",
        "矛盾": "conflict",
        "partial": "partial",
        "weak": "partial",
        "部分覆盖": "partial",
        "弱覆盖": "partial",
        "uncertain": "uncertain",
        "verify": "uncertain",
        "待核验": "uncertain",
        "不确定": "uncertain",
        "advisory": "advisory",
        "preference": "advisory",
        "建议": "advisory",
        "偏好": "advisory",
    }
    _VALID_SEVERITIES = {"critical", "high", "medium", "low"}

    @property
    def step_name(self) -> str:
        return "check_alignment"

    @classmethod
    def _to_string_list(cls, value: Any) -> list[str]:
        items: list[str] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    # A structured finding represents one missing point.  Its
                    # id, evidence, impact, and repair actions must not be
                    # counted as independent mainline misses during calibration.
                    text = cls._structured_item_text(item)
                    if text:
                        items.append(text)
                else:
                    text = clean_str(item)
                    if text:
                        items.append(text)
        elif isinstance(value, dict):
            text = cls._structured_item_text(value)
            if text:
                items.append(text)
        elif isinstance(value, str):
            fragments = [part.strip() for part in re.split(r"[;\n；]+", value)]
            items.extend(fragment for fragment in fragments if fragment)

        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    @staticmethod
    def _structured_item_text(value: dict[Any, Any]) -> str:
        """Render one structured model finding as one checklist item."""
        parts: list[str] = []
        for raw_key, raw_value in value.items():
            key = clean_str(raw_key)
            if isinstance(raw_value, list):
                values = [clean_str(item) for item in raw_value]
                text = "；".join(item for item in values if item)
            elif isinstance(raw_value, dict):
                text = "；".join(
                    f"{clean_str(nested_key)}: {clean_str(nested_value)}"
                    for nested_key, nested_value in raw_value.items()
                    if clean_str(nested_key) or clean_str(nested_value)
                )
            else:
                text = clean_str(raw_value)
            if key and text:
                parts.append(f"{key}: {text}")
            elif key:
                parts.append(key)
            elif text:
                parts.append(text)
        return "；".join(parts)

    @staticmethod
    def _coerce_score(value: Any, default: float = 7.5) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = default
        return max(0.0, min(10.0, score))

    @classmethod
    def _normalize_risk_level(cls, value: Any) -> str:
        lowered = clean_str(value).lower()
        return cls._RISK_ALIAS.get(lowered, "medium")

    @classmethod
    def _calibrate_risk_level(
        cls,
        *,
        model_risk: str,
        calibrated_score: float,
        core_missing_count: int,
        detail_missing_count: int,
        weak_count: int,
    ) -> str:
        rank = cls._RISK_ORDER.get(model_risk, 1)
        if calibrated_score < 8.0:
            rank = max(rank, 1)
        if calibrated_score < 6.5:
            rank = max(rank, 2)
        if core_missing_count >= 1:
            rank = max(rank, 1)
        if core_missing_count >= 2:
            rank = max(rank, 2)
        if detail_missing_count >= 3:
            rank = max(rank, 1)
        if weak_count >= 3:
            rank = max(rank, 1)
        if weak_count >= 5:
            rank = max(rank, 2)
        return ("low", "medium", "high")[rank]

    @classmethod
    def _is_detail_missing_point(cls, value: str) -> bool:
        text = clean_str(value)
        if not text:
            return False
        if any(marker in text for marker in cls._PERSPECTIVE_DETAIL_MARKERS):
            return True
        if any(marker in text for marker in cls._CORE_MISSING_MARKERS):
            return False
        return any(marker in text for marker in cls._DETAIL_MISSING_MARKERS)

    @classmethod
    def _missing_point_counts(cls, values: list[str]) -> tuple[int, int]:
        detail = sum(1 for item in values if cls._is_detail_missing_point(item))
        return len(values) - detail, detail

    @staticmethod
    def _compact_evidence(value: Any) -> str:
        return "".join(clean_str(value).split()).lower()

    @classmethod
    def _source_reference_map(cls, outline: Any, plan: Any) -> dict[str, str]:
        """Build stable references for the exact evidence admitted to alignment review."""

        references: dict[str, str] = {}

        def add(ref: str, value: Any) -> None:
            text = clean_str(value)
            if text:
                references[ref] = text

        add("outline.goal", extract_field(outline, "goal"))
        for field_name in ("main_plot_points", "subplot_points", "beats_summary"):
            for index, item in enumerate(
                list(extract_field(outline, field_name, []) or []),
                start=1,
            ):
                add(f"outline.{field_name}[{index}]", item)

        for scene_index, scene in enumerate(
            list(extract_field(plan, "scene_intents", []) or []),
            start=1,
        ):
            prefix = f"plan.scene_intents[{scene_index}]"
            add(f"{prefix}.summary", extract_field(scene, "summary"))
            add(f"{prefix}.required_outcome", extract_field(scene, "required_outcome"))
            for field_name in ("owned_events", "owned_revelations", "owned_state_changes"):
                for item_index, item in enumerate(
                    list(extract_field(scene, field_name, []) or []),
                    start=1,
                ):
                    add(f"{prefix}.{field_name}[{item_index}]", item)

        for field_name in ("required_state_transitions",):
            for index, item in enumerate(
                list(extract_field(plan, field_name, []) or []),
                start=1,
            ):
                add(f"plan.{field_name}[{index}]", item)
        for field_name in ("opening_contract", "closing_contract"):
            value = extract_field(plan, field_name, None)
            if value not in (None, "", [], {}):
                add(f"plan.{field_name}", value)
        for index, item in enumerate(
            list(extract_field(plan, "required_literals", []) or []),
            start=1,
        ):
            add(
                f"plan.required_literals[{index}]",
                extract_field(item, "literal", item),
            )
        return references

    @classmethod
    def _resolve_source_reference(
        cls,
        raw_ref: Any,
        raw_evidence: Any,
        source_map: dict[str, str],
    ) -> tuple[str, str, bool]:
        source_ref = clean_str(raw_ref)
        if source_ref in source_map:
            return source_ref, source_map[source_ref], True

        evidence = cls._compact_evidence(raw_evidence)
        if evidence:
            matches = [
                (ref, text)
                for ref, text in source_map.items()
                if evidence == cls._compact_evidence(text)
            ]
            if len(matches) == 1:
                return matches[0][0], matches[0][1], True
        return source_ref, clean_str(raw_evidence), False

    @staticmethod
    def _paragraph_for_evidence(chapter_text: str, evidence: str) -> int:
        if not evidence:
            return 0
        offset = chapter_text.find(evidence)
        if offset < 0:
            return 0
        prefix = chapter_text[:offset]
        paragraphs = [part for part in re.split(r"\n\s*\n", prefix) if part.strip()]
        return len(paragraphs) + 1

    @classmethod
    def _normalize_structured_findings(
        cls,
        raw: Any,
        *,
        chapter_outline: Any,
        chapter_plan: Any,
        chapter_text: str,
        chapter_number: int,
    ) -> tuple[list[Any], list[Any]]:
        """Admit only source-bound, actionable findings into the repair loop."""

        if not isinstance(raw, list):
            return [], []
        source_map = cls._source_reference_map(chapter_outline, chapter_plan)
        current_hash = source_text_hash(chapter_text)
        findings: list[Any] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            coverage = cls._COVERAGE_ALIASES.get(
                clean_str(item.get("coverage_status") or item.get("coverage")).lower(),
                "uncertain",
            )
            severity = clean_str(item.get("severity")).lower()
            if severity not in cls._VALID_SEVERITIES:
                severity = "medium"
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
            except (TypeError, ValueError):
                confidence = 0.0

            source_ref, source_evidence, source_verified = cls._resolve_source_reference(
                item.get("source_ref"),
                item.get("source_evidence"),
                source_map,
            )
            chapter_evidence = clean_str(
                item.get("chapter_evidence") or item.get("evidence_quote")
            )
            chapter_evidence_verified = bool(
                chapter_evidence and chapter_evidence in chapter_text
            )
            if not source_verified or (
                coverage == "conflict" and not chapter_evidence_verified
            ):
                coverage = "uncertain"
                severity = "low"
                confidence = min(confidence, 0.35)

            blocking = bool(
                coverage in {"missing", "conflict"}
                and source_verified
                and severity in {"critical", "high"}
                and confidence >= 0.75
                and (coverage == "missing" or chapter_evidence_verified)
            )
            summary = clean_str(item.get("summary")) or (
                f"{source_ref or '上游要求'}的对齐状态需要复核"
            )
            repair_action = clean_str(
                item.get("repair_action") or item.get("fix_suggestion")
            )
            paragraph = cls._paragraph_for_evidence(chapter_text, chapter_evidence)
            finding = normalize_issue_to_finding(
                {
                    "issue_type": f"alignment_{coverage}",
                    "severity": severity,
                    "confidence": confidence,
                    "summary": summary,
                    "evidence": chapter_evidence if chapter_evidence_verified else "",
                    "fix_suggestion": repair_action,
                    "paragraph_start": paragraph,
                    "paragraph_end": paragraph,
                    "postconditions": [
                        {
                            "kind": "alignment_source_coverage",
                            "source_ref": source_ref,
                            "description": f"复审时 {source_ref or '该上游要求'} 不再成立为缺失或冲突。",
                        }
                    ],
                },
                source_module="alignment",
                chapter_number=chapter_number,
                dimension="alignment",
                current_text_hash=current_hash,
                metadata={
                    "coverage_status": coverage,
                    "source_ref": source_ref,
                    "source_evidence": source_evidence,
                    "source_evidence_verified": source_verified,
                    "chapter_evidence_verified": chapter_evidence_verified,
                    "model_blocks_finalize": bool(item.get("blocks_finalize", False)),
                    "repair_boundary": "只修复该上游要求与当前正文之间的局部缺失或冲突。",
                },
            )
            finding = finding.model_copy(
                update={
                    "finding_id": (
                        f"alignment_ch{chapter_number}_"
                        f"{source_text_hash(f'alignment|{chapter_number}|{source_ref}')[:16]}"
                    ),
                    "blocks_finalize": blocking,
                    "confidence": confidence,
                    "severity": severity,
                    "anchor_type": "evidence_match"
                    if chapter_evidence_verified
                    else "source_requirement",
                }
            )
            findings.append(finding)

        blocking_findings = [finding for finding in findings if finding.blocks_finalize]
        tickets = compile_repair_tickets_from_findings(blocking_findings)
        return findings, tickets

    @classmethod
    def _calibrate_score(
        cls,
        *,
        core_missing_count: int,
        detail_missing_count: int,
        weak_count: int,
        supportive_count: int,
    ) -> float:
        mainline_deduction = min(8.0, 2.0 * core_missing_count + 1.2 * detail_missing_count)
        disruptive_subplot_deduction = min(2.0, 0.4 * max(0, weak_count - 1))
        supportive_bonus = min(0.6, 0.2 * supportive_count)
        calibrated = 10.0 - mainline_deduction - disruptive_subplot_deduction + supportive_bonus
        return round(calibrated, 1)

    @classmethod
    def _normalize_alignment_payload(
        cls,
        raw: Any,
        *,
        chapter_outline: Any | None = None,
        chapter_plan: Any | None = None,
        chapter_text: str = "",
        chapter_number: int = 0,
    ) -> dict[str, Any]:
        payload = raw if isinstance(raw, dict) else {}
        structured_contract = isinstance(payload.get("findings"), list)
        review_findings: list[Any] = []
        repair_tickets: list[Any] = []
        if structured_contract and chapter_outline is not None and chapter_plan is not None:
            review_findings, repair_tickets = cls._normalize_structured_findings(
                payload.get("findings"),
                chapter_outline=chapter_outline,
                chapter_plan=chapter_plan,
                chapter_text=chapter_text,
                chapter_number=chapter_number,
            )
        if structured_contract:
            blocking_findings = [
                finding for finding in review_findings if finding.blocks_finalize
            ]
            missing_main_points = [finding.summary for finding in blocking_findings]
        else:
            blocking_findings = []
            missing_main_points = cls._to_string_list(payload.get("missing_main_points", []))
        supportive_subplot_points = cls._to_string_list(
            payload.get(
                "supportive_subplot_points",
                payload.get("supportive_subplots", payload.get("positive_subplot_points", [])),
            )
        )
        weak_subplot_points = cls._to_string_list(payload.get("weak_subplot_points", []))
        if structured_contract:
            repair_actions = cls._to_string_list(
                [finding.repair_goal for finding in blocking_findings]
            )
        else:
            repair_actions = cls._to_string_list(payload.get("repair_actions", []))
        summary = clean_str(payload.get("summary"))

        model_score = cls._coerce_score(payload.get("alignment_score"), default=8.5)
        model_risk = cls._normalize_risk_level(payload.get("risk_level"))
        core_missing_count, detail_missing_count = cls._missing_point_counts(missing_main_points)
        rule_score = cls._calibrate_score(
            core_missing_count=core_missing_count,
            detail_missing_count=detail_missing_count,
            weak_count=len(weak_subplot_points),
            supportive_count=len(supportive_subplot_points),
        )
        if core_missing_count:
            calibrated_score = rule_score
        elif detail_missing_count:
            calibrated_score = round((0.5 * model_score) + (0.5 * rule_score), 1)
            calibrated_score = max(0.0, min(10.0, calibrated_score))
        else:
            calibrated_score = round((0.4 * model_score) + (0.6 * rule_score), 1)
            calibrated_score = max(0.0, min(10.0, calibrated_score))

        calibrated_risk = cls._calibrate_risk_level(
            model_risk=model_risk,
            calibrated_score=calibrated_score,
            core_missing_count=core_missing_count,
            detail_missing_count=detail_missing_count,
            weak_count=len(weak_subplot_points),
        )

        if not summary:
            if len(missing_main_points) >= 1:
                summary = "章节推进与大纲存在关键缺口，建议优先修复主线点。"
            elif len(weak_subplot_points) >= 1:
                summary = "章节总体可用，但支线推进偏弱，建议补充承接细节。"
            else:
                summary = "章节与大纲总体一致。"

        return {
            "review_contract_version": 1 if structured_contract else 0,
            "alignment_score": calibrated_score,
            "risk_level": calibrated_risk,
            "summary": summary,
            "missing_main_points": missing_main_points,
            "supportive_subplot_points": supportive_subplot_points,
            "weak_subplot_points": weak_subplot_points,
            "repair_actions": repair_actions,
            "review_findings": review_findings,
            "repair_tickets": repair_tickets,
            "_model_score": model_score,
        }

    @classmethod
    def _scope_outline(cls, outline: Any) -> SimpleNamespace:
        """Return only outline fields consumed by CHECK_ALIGNMENT."""
        return SimpleNamespace(
            chapter_number=extract_field(outline, "chapter_number", 0),
            title=clean_str(extract_field(outline, "title")),
            goal=clean_str(extract_field(outline, "goal")),
            main_plot_points=[
                clean_str(item)
                for item in list(extract_field(outline, "main_plot_points", []) or [])
                if clean_str(item)
            ],
            subplot_points=[
                clean_str(item)
                for item in list(extract_field(outline, "subplot_points", []) or [])
                if clean_str(item)
            ],
            beats_summary=[
                clean_str(item)
                for item in list(extract_field(outline, "beats_summary", []) or [])
                if clean_str(item)
            ],
        )

    @classmethod
    def _scope_plan(cls, plan: Any) -> SimpleNamespace:
        """Return the plan's compact, executable acceptance checklist for review."""
        scenes: list[SimpleNamespace] = []
        for scene in list(extract_field(plan, "scene_intents", []) or []):
            scenes.append(
                SimpleNamespace(
                    scene_id=clean_str(extract_field(scene, "scene_id")),
                    summary=clean_str(extract_field(scene, "summary")),
                    required_outcome=clean_str(extract_field(scene, "required_outcome")),
                    owned_events=[
                        clean_str(item)
                        for item in list(extract_field(scene, "owned_events", []) or [])
                        if clean_str(item)
                    ],
                    owned_revelations=[
                        clean_str(item)
                        for item in list(extract_field(scene, "owned_revelations", []) or [])
                        if clean_str(item)
                    ],
                    owned_state_changes=[
                        clean_str(item)
                        for item in list(extract_field(scene, "owned_state_changes", []) or [])
                        if clean_str(item)
                    ],
                )
            )
        required_literals: list[SimpleNamespace] = []
        for item in list(extract_field(plan, "required_literals", []) or []):
            required_literals.append(
                SimpleNamespace(
                    contract_id=clean_str(extract_field(item, "contract_id")),
                    scene_id=clean_str(extract_field(item, "scene_id")),
                    literal=clean_str(extract_field(item, "literal", item)),
                    reason=clean_str(extract_field(item, "reason")),
                    placement_hint=clean_str(extract_field(item, "placement_hint")),
                )
            )
        return SimpleNamespace(
            scene_intents=scenes,
            required_state_transitions=list(
                extract_field(plan, "required_state_transitions", []) or []
            ),
            required_literals=required_literals,
            opening_contract=extract_field(plan, "opening_contract", None),
            closing_contract=extract_field(plan, "closing_contract", None),
        )

    async def _execute(self, input_data: AlignmentInput) -> AlignmentReport:
        context: dict[str, Any] = {
            "chapter_outline": self._scope_outline(input_data.chapter_outline),
            "chapter_plan": self._scope_plan(input_data.chapter_plan),
            "chapter_text": input_data.chapter_text,
        }
        kernel_context = getattr(input_data, "kernel_context", None)
        if kernel_context:
            context["kernel_context"] = kernel_context

        data = await self._call_with_retry(
            TaskType.CHECK_ALIGNMENT,
            context,
            max_tokens=self._dynamic_max_tokens(
                TaskType.CHECK_ALIGNMENT,
                max(2500, len(input_data.chapter_text) // 2),
                prompt_overhead=3000,
                min_tokens=4096,
            ),
            temperature=self.settings.temp_check_alignment,
        )
        normalized = self._normalize_alignment_payload(
            data,
            chapter_outline=input_data.chapter_outline,
            chapter_plan=input_data.chapter_plan,
            chapter_text=input_data.chapter_text,
            chapter_number=int(extract_field(input_data.chapter_outline, "chapter_number", 0) or 0),
        )

        # ── Degraded-response detection ───────────────────────────────────
        # When the LLM returns alignment_score as 0 or a near-zero value while
        # also reporting missing_main_points, the response is likely truncated
        # or partially decoded.  A genuine low score with real misses would
        # still carry a non-trivial model_score (> 1.0).  Mark the report as
        # fallback so downstream gates exempt it from hard-blocking.
        raw_model_score = data.get("alignment_score") if isinstance(data, dict) else None
        if (
            raw_model_score is not None
            and normalized.get("_model_score", 8.5) < 1.0
            and len(normalized.get("missing_main_points", [])) > 0
        ):
            normalized["evaluation_status"] = "degraded"
            normalized["is_fallback"] = True
            normalized["fallback_reason"] = "model_score_near_zero_with_misses"

        normalized.pop("_model_score", None)
        # ── Evidence binding ─────────────────────────────────────────────
        # Stamp the source text hash so downstream archive gates and the
        # repair-evidence binding check (_report_is_bound_to_text) can verify
        # this report was produced against the exact text being archived.
        # Without it the archive gate may block on a low score while the retry
        # mechanism treats the report as unbound evidence and refuses to
        # auto-repair, forcing an unnecessary human pause in book-auto runs.
        normalized["source_text_hash"] = source_text_hash(input_data.chapter_text)
        return AlignmentReport.model_validate(normalized)
