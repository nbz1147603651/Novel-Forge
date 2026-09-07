"""ContinuityRepairStep — targeted partial rewrite for continuity issues."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.parse_utils import strip_markdown_fences
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityIssue,
    ContinuityRepairDirective,
    ContinuityReport,
    RepairPlan,
    TargetSection,
)
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.utils.boundary_windows import (
    DEFAULT_OPENING_PARAGRAPHS,
    DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
    coerce_paragraph_count,
    paragraph_range_label,
)
from novel_forge.core.utils.issue_prompt_blocks import build_issues_prompt_block
from novel_forge.core.utils.string import carry_forward_text
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.repair.base import RepairStepBase
from novel_forge.pipeline.steps.repair.patch_bridge import (
    PatchCompatIssue,
    adapt_issue_for_patch,
    build_patch_boundary_context,
)
from novel_forge.pipeline.steps.repair.text_utils import (
    extract_revised_text,
    non_space_len,
    paragraph_count,
)

_logger = get_logger("pipeline.continuity_repair")


@dataclass(frozen=True)
class ContinuityRepairInput:
    """Input for targeted continuity repair."""

    chapter_number: int
    chapter_text: str
    chapter_state_packet: ChapterStatePacket
    chapter_bridge: ChapterBridge
    chapter_plan: ChapterPlan
    continuity_report: ContinuityReport
    chapter_outline: ChapterOutline | None = field(default=None)
    style: str = "literary"
    style_profile: dict[str, Any] | None = None
    editorial_contract: Any | None = None
    must_fix_issues: tuple[ContinuityIssue, ...] = field(default_factory=tuple)
    memory_context: dict[str, Any] | None = None
    time_convention: str = ""
    address_rules: str = ""
    world_context_rules: str = ""
    cumulative_change_ratio: float = 0.0
    """Cumulative text change ratio across all repair dimensions (for fulltext upgrade gate)."""
    boundary_prev_tail_paragraphs: int = DEFAULT_PREVIOUS_TAIL_PARAGRAPHS
    """上一章末尾只读上下文段落数，用于开场边界修复。"""
    boundary_opening_paragraphs: int = DEFAULT_OPENING_PARAGRAPHS
    """本章开头目标窗口段落数，用于开场边界修复。"""
    kernel_context: dict[str, Any] | None = None
    """Optional field slice from ContextComposer (StoryKernel).
    Contains kernel fields like entities, relationships, timeline, world_rules,
    knowledge_ledger, object_ledger, chapter_summaries, promise_ledger, motif_protocols.
    Merged into repair context with 'kernel_' prefix."""
    chapter_source_slice: Any | None = None


@dataclass(frozen=True)
class ContinuityRepairResult:
    """Repair output: plan plus revised text."""

    revised_text: str
    repair_plan: RepairPlan
    applied: bool = False
    failure_reason: str | None = None  # 若 applied=False，记录原因（字数守卫/无修复内容等）
    warnings: tuple[str, ...] = field(default_factory=tuple)
    patch_only: bool = False  # True 时表示所有问题均走 patch 路径，未触发全文重写
    repaired_issue_types: tuple[str, ...] = field(
        default_factory=tuple
    )  # 本次修复涉及的 issue_type 列表
    deferred_issue_count: int = 0
    """Issues skipped by text repair because they belong to another repair surface."""

    deferred_surfaces: tuple[str, ...] = field(default_factory=tuple)
    """Non-text repair surfaces still pending after this text repair step."""

    revised_bridge: ChapterBridge | None = None
    """Updated bridge artifact when an upstream artifact repair already ran."""


class ContinuityRepairStep(RepairStepBase[ContinuityRepairInput, ContinuityRepairResult]):
    """Repair continuity problems without re-running the whole chapter pipeline."""

    @classmethod
    def issue_signature(cls, issue: Any) -> str:
        import hashlib

        issue_id = str(getattr(issue, "issue_id", "") or "").strip()
        if issue_id:
            return f"id:{issue_id}"

        parts = [
            str(getattr(issue, "issue_type", "") or ""),
            str(getattr(issue, "summary", "") or ""),
            str(getattr(issue, "location", "") or ""),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]

    _GENERIC_FIX_ACTIONS = {
        "opening_gap": "在开头用角色的动作、感官或正在进行的行为自然续上上一章余波，禁止使用\u201c承接上章\u201d\u201cPOV\u201d\u201c情绪标签\u201d等说明腔。",
        "carry_forward_missing": "把上一章遗留事实分散融入动作、对话或环境反馈，不要用摘要式回顾来补课。",
        "prompt_leak": "删除规划词、修补说明和合同措辞，改成具体的场景描写或人物反应。特别注意：删除任何规划层标记如opening_contract、closing_contract、scene_intent、bridge_summary、time_marker、location、pov、required_outcome、exit_target_state等，这些都不能出现在小说正文中。",
        "forbidden_element_violation": "将硬禁元素'{element}'替换为完全不同的意象/句式。该意象的叙事功能是'{function}'，请确保新意象能实现同样的功能。不得使用近义变体。",
        "location_jump": "在场景切换处添加过渡段落，交代角色如何从上一地点移动到当前地点，不得跳切。",
        "custody_break": "补充角色交接过程，明确谁在何时何地从谁手中接过了叙事焦点，避免视角断层。",
        "pov_jump": "统一本章 POV 视角，删除非 POV 角色的内心独白和不可知感知描写，改为 POV 角色的推测或外部观察。",
        "bridge_contract_not_followed": "核查桥接合同中承诺的叙事要素（时间/地点/视角/事件），将缺失的情节推进补入正文。",
        "time_marker_invalid": "修正不合理的时辰/时间标记，确保与上一章时间线连续，如有时间跳跃需在正文中明确交代。",
        "redundancy": "删除或压缩重复信息，保留一次有效表达，并用新的动作、视角或推进细节承接下文。",
        "text_repetition": "改写与上一章重复的整句或整段，用不同的叙事手法表达相同信息，避免读者感受到复读。",
        "forbidden_element_usage": "识别软性避重复提示的使用场景，优先用新意象保留叙事功能；若它承担剧情锚点、动作承接或有意回环功能，应保留其功能并强化意义，不做机械删改。",
        "pov_intrusion": "删除 POV 角色不可能知道的信息描写，将全知视角内容改为 POV 角色的推测、感知或旁观者转述。",
        "world_rule_conflict": "只修复命中片段，使行为满足世界规则的触发条件、禁止边界与代价；不得通过新增例外、改写既有结果或扩展新设定来规避。",
    }
    _EXAMPLE_CLAUSE_RE = re.compile(r'(?:例如|比如|如)\s*[：:]\s*["\u201c].*?["\u201d]')

    # 能用 patch 精确定位的问题类型（位置确定，不需要感知全文）
    _PATCHABLE_TYPES = frozenset(
        {
            "opening_gap",
            "location_jump",
            "pov_jump",
            "time_marker_invalid",
            "closing_gap",
            "ending_ambiguity",
            "bridge_contract_not_followed",
            "custody_break",
            "carry_forward_missing",
            "redundancy",
            "text_repetition",
            "sensory_anchor_repetition",
            "forbidden_element_violation",
            "forbidden_element_usage",
            "address_form_mismatch",
            "character_not_in_plan",
            "pov_intrusion",
            "prompt_leak",
        }
    )
    # 章节边界问题：即使 rewrite_scope=chapter，也允许先用窗口补丁修复。
    _BOUNDARY_PATCHABLE_TYPES = frozenset(
        {
            "opening_gap",
            "closing_gap",
            "ending_ambiguity",
            "bridge_contract_not_followed",
        }
    )
    _OPENING_PATCH_TYPES = frozenset({"opening_gap", "bridge_contract_not_followed"})
    _CLOSING_PATCH_TYPES = frozenset({"closing_gap", "ending_ambiguity"})
    _UNPATCHABLE_SCOPES = frozenset({"chapter", "全章", "全文", ""})
    _TEXT_REPAIR_SURFACES = frozenset({"", "chapter_text", "manual"})

    # Guard bounds stored for validate_revised_text
    _guard_lo: float = 0.60
    _guard_hi: float = 1.55

    @property
    def step_name(self) -> str:
        return "repair_continuity"

    # ── 7 abstract hooks ──────────────────────────────────────────────

    def repair_task_type(self) -> TaskType:
        return TaskType.REPAIR_CONTINUITY

    def issue_type_fixes(self) -> dict[str, str]:
        return dict(self._GENERIC_FIX_ACTIONS)

    def build_repair_context(self, input_data: ContinuityRepairInput) -> dict[str, Any]:
        """Build the LLM prompt context for continuity repair."""
        # Build repair plan (needed for context)
        repair_plan = self._build_repair_plan(input_data)
        from novel_forge.pipeline.long.services.constraints.constraint_router import (
            build_repair_cards,
        )

        # Determine which issues go to fulltext path
        _filtered_issues = repair_plan.issues
        _fulltext_issues = [iss for iss in _filtered_issues if not self._is_patch_candidate(iss)]

        if _fulltext_issues:
            _fulltext_report = input_data.continuity_report.model_copy(
                update={"issues": _fulltext_issues}
            )
        else:
            _fulltext_report = input_data.continuity_report.model_copy(
                update={"issues": _filtered_issues}
            )

        # Build forbidden element → function mapping
        _item_function: dict[str, str] = {}
        _forbidden_plan = getattr(input_data.chapter_plan, "forbidden_elements", []) or []
        if _forbidden_plan:
            _fe_issue_types = {"forbidden_element_violation", "forbidden_element_usage"}
            for _iss in _fulltext_report.issues:
                if (getattr(_iss, "issue_type", "") or "").lower() not in _fe_issue_types:
                    continue
                _func = ""
                for _action in _iss.fix_actions:
                    _m = re.search(r"叙事功能是'([^']+)'", str(_action or ""))
                    if _m:
                        _func = _m.group(1)
                        break
                if not _func:
                    _func = "保留原叙事功能，用替代性手法落地"
                _evidence_text = (
                    getattr(_iss, "evidence_quote", "") or _iss.evidence or _iss.summary or ""
                ).lower()
                for _fe in _forbidden_plan:
                    if _fe.lower() in _evidence_text and _fe not in _item_function:
                        _item_function[_fe] = _func
            for _fe in _forbidden_plan:
                _item_function.setdefault(_fe, "保留原叙事功能，用替代性手法落地")

        result: dict[str, Any] = {
            "chapter_number": input_data.chapter_number,
            "chapter_text": input_data.chapter_text,
            "continuity_report": _fulltext_report,
            "chapter_plan": input_data.chapter_plan,
            "repair_plan": repair_plan,
            "memory_context": input_data.memory_context,
            "item_function": _item_function,
            "structured_issues_block": build_issues_prompt_block(
                _filtered_issues or _fulltext_report.issues,
                header="待修复连续性问题详情：",
                include_evidence=True,
                include_fix_actions=True,
            ),
            "stage_cards": build_repair_cards(
                stage="continuity_repair",
                packet=input_data.chapter_state_packet,
                chapter_outline=input_data.chapter_outline,
                bridge=input_data.chapter_bridge,
                plan=input_data.chapter_plan,
                style_profile=input_data.style_profile,
                editorial_contract=input_data.editorial_contract,
                memory_hints=input_data.memory_context,
                kernel_context=input_data.kernel_context,
                chapter_source_slice=input_data.chapter_source_slice,
                settings=self.settings,
            ),
        }

        if input_data.kernel_context:
            for key, value in input_data.kernel_context.items():
                if value is None:
                    continue
                prefixed = f"kernel_{key}"
                if prefixed not in result:
                    result[prefixed] = value

        return result

    def compute_word_bounds(
        self,
        orig_len: int,
        issue_types: set[str],
    ) -> tuple[int, int, float, float]:
        """Compute adaptive word-count bounds based on issue nature."""
        _ADDITIVE = {"carry_forward_missing", "information_consistency", "ending_ambiguity"}
        _SUBTRACTIVE = {"text_repetition", "sensory_anchor_repetition", "repetition"}
        _has_additive = any(any(a in it for a in _ADDITIVE) for it in issue_types)
        _has_subtractive = any(any(s in it for s in _SUBTRACTIVE) for it in issue_types)

        if _has_additive and not _has_subtractive:
            word_count_min = int(orig_len * 0.80)
            word_count_max = int(orig_len * 1.40)
            guard_lo, guard_hi = 0.65, 1.60
        elif _has_subtractive and not _has_additive:
            word_count_min = int(orig_len * 0.60)
            word_count_max = int(orig_len * 1.15)
            guard_lo, guard_hi = 0.50, 1.30
        else:
            word_count_min = int(orig_len * 0.75)
            word_count_max = int(orig_len * 1.35)
            guard_lo, guard_hi = 0.60, 1.55

        # Store for validate_revised_text
        self._guard_lo = guard_lo
        self._guard_hi = guard_hi

        return word_count_min, word_count_max, guard_lo, guard_hi

    def adapt_issue_for_patch(self, issue: ContinuityIssue) -> PatchCompatIssue:
        return adapt_issue_for_patch(
            issue,
            infer_location_func=self._infer_patch_location,
            fix_suggestions=self._GENERIC_FIX_ACTIONS,
        )

    def validate_revised_text(
        self,
        original: str,
        revised: str,
    ) -> tuple[bool, str]:
        """Validate revised text against word-count guard bounds."""
        from novel_forge.pipeline.steps.repair.word_guard import apply_word_guard

        _, triggered = apply_word_guard(original, revised, self._guard_lo, self._guard_hi)
        if triggered:
            orig_len = non_space_len(original)
            revised_len = non_space_len(revised)
            ratio = revised_len / orig_len if orig_len > 0 else 1.0
            reason = (
                f"字数守卫触发：修复结果字数偏差超出允许范围（"
                f"原文 {orig_len} 字，修复 {revised_len} 字，比例 {ratio:.0%}），"
                f"允许范围 {self._guard_lo:.0%}–{self._guard_hi:.0%}。"
            )
            return False, reason
        return True, ""

    def should_skip(self, input_data: ContinuityRepairInput) -> bool | None:
        return None

    # ── Domain-specific helpers (retained) ────────────────────────────

    @staticmethod
    def _extract_revised_text(content: str) -> tuple[str, bool]:
        """Wrapper around shared extract_revised_text for backward compatibility."""
        return extract_revised_text(content)

    @staticmethod
    def _is_json_truncated(content: str) -> bool:
        """Detect if JSON response was likely truncated by max_tokens limit."""
        cleaned = strip_markdown_fences(content).strip()
        if not cleaned.startswith("{"):
            return False
        try:
            json.loads(cleaned)
            return False
        except (json.JSONDecodeError, ValueError):
            pass
        unescaped = cleaned
        try:
            import codecs

            unescaped = codecs.decode(cleaned, "unicode_escape")
        except Exception:
            pass
        if unescaped.count('"') > unescaped.count('\\"') + 10:
            return True
        if unescaped.count("{") != unescaped.count("}"):
            return True
        return False

    @classmethod
    def _generic_fix_action(cls, issue_type: str) -> str:
        return cls._GENERIC_FIX_ACTIONS.get(
            issue_type.lower(),
            "只修正该问题对应的叙事功能，用具体情境落地，不复述合同、计划或修补措辞。",
        )

    @classmethod
    def _sanitize_fix_actions(cls, issue: ContinuityIssue) -> list[str]:
        issue_type = (issue.issue_type or "").lower()
        generic = cls._GENERIC_FIX_ACTIONS.get(issue_type, "")

        # Substitute placeholders for forbidden_element_violation generic.
        if issue_type == "forbidden_element_violation" and (
            "{element}" in generic or "{function}" in generic
        ):
            element = issue.evidence_quote or ""
            function = ""
            for action in issue.fix_actions:
                action_str = str(action or "")
                if "叙事功能是'" in action_str:
                    m = re.search(r"叙事功能是'([^']+)'", action_str)
                    if m:
                        function = m.group(1)
                        break
            # Fallback: if function extraction failed, infer from issue location
            if not function:
                location = getattr(issue, "location", "") or ""
                if "开头" in location or "第1段" in location or "第2段" in location:
                    function = "承接上一章余波"
                elif "结尾" in location or "末段" in location:
                    function = "落实章末契约"
                elif "对话" in location or "说" in (getattr(issue, "evidence", "") or ""):
                    function = "表达角色情绪"
                else:
                    function = "营造氛围"
            if element or function:
                generic = generic.format(element=element, function=function)

        actions: list[str] = []
        for raw in issue.fix_actions:
            cleaned = cls._EXAMPLE_CLAUSE_RE.sub("", str(raw or "")).strip(" ，。；：")
            if not cleaned:
                continue
            actions.append(cleaned)

        if actions and generic:
            actions.append(f"【修复方法参考】{generic}")
            return actions
        if actions:
            return actions
        if generic:
            return [generic]
        return ["只修正该问题对应的叙事功能，用具体情境落地，不复述合同、计划或修补措辞。"]

    @staticmethod
    def _summarize_fact(text: str, max_chars: int = 72) -> str:
        cleaned = " ".join(str(text or "").split()).strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 1].rstrip("，。、；： ") + "…"

    @classmethod
    def _boundary_prev_tail_paragraphs(cls, input_data: ContinuityRepairInput) -> int:
        return coerce_paragraph_count(
            getattr(
                input_data,
                "boundary_prev_tail_paragraphs",
                DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
            ),
            default=DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
        )

    @classmethod
    def _boundary_opening_paragraphs(cls, input_data: ContinuityRepairInput) -> int:
        return coerce_paragraph_count(
            getattr(input_data, "boundary_opening_paragraphs", DEFAULT_OPENING_PARAGRAPHS),
            default=DEFAULT_OPENING_PARAGRAPHS,
            maximum=8,
        )

    @classmethod
    def _sanitize_issue(cls, issue: ContinuityIssue) -> ContinuityIssue:
        return issue.model_copy(
            update={
                "fix_actions": cls._sanitize_fix_actions(issue),
            }
        )

    @classmethod
    def _ensure_opening_repair_directive(
        cls,
        issue: ContinuityIssue,
        input_data: ContinuityRepairInput,
    ) -> ContinuityIssue:
        if not cls._is_opening_boundary_issue(issue):
            return issue

        total_paragraphs = max(1, paragraph_count(input_data.chapter_text))
        opening_paragraphs = min(cls._boundary_opening_paragraphs(input_data), total_paragraphs)
        prev_tail_paragraphs = cls._boundary_prev_tail_paragraphs(input_data)
        start = max(1, int(getattr(issue, "paragraph_start", 0) or 1))
        end = max(
            start,
            int(
                getattr(issue, "paragraph_end", 0)
                or min(start + opening_paragraphs - 1, opening_paragraphs)
            ),
        )
        if start <= opening_paragraphs:
            start = 1
            end = min(total_paragraphs, max(end, opening_paragraphs))
        if getattr(issue, "repair_directive", None) is not None:
            existing_directive = getattr(issue, "repair_directive", None)
            if hasattr(existing_directive, "model_dump"):
                directive_data = existing_directive.model_dump(mode="json")  # type: ignore[union-attr]
            elif isinstance(existing_directive, dict):
                directive_data = dict(existing_directive)
            else:
                directive_data = {}
            required_context = [
                str(item).strip()
                for item in list(directive_data.get("required_context") or [])
                if str(item).strip()
                and not ("上一章结尾" in str(item) and "段" in str(item))
                and not ("本章开头" in str(item) and "段" in str(item))
            ]
            for context_item in (
                f"上一章结尾 {prev_tail_paragraphs} 段（只读）",
                f"本章开头 {opening_paragraphs} 段（目标窗口）",
            ):
                if context_item not in required_context:
                    required_context.append(context_item)
            directive_data.update(
                {
                    "target_window": f"本章开头{paragraph_range_label(start, end)}",
                    "required_context": required_context,
                }
            )
            return issue.model_copy(
                update={
                    "repair_directive": ContinuityRepairDirective.model_validate(directive_data),
                    "paragraph_start": start,
                    "paragraph_end": end,
                    "fix_mode": "window",
                }
            )

        anchors: list[str] = []
        for anchor in getattr(issue, "missing_anchors", []) or []:
            text = (getattr(anchor, "text", "") or "").strip()
            role = (getattr(anchor, "role", "") or "").strip()
            if text:
                anchors.append(f"{role}：{text}" if role else text)
        for label, value in (
            ("开场时间", getattr(input_data.chapter_bridge, "opening_time", "")),
            ("开场地点", getattr(input_data.chapter_bridge, "opening_location", "")),
            ("开场视角", getattr(input_data.chapter_bridge, "opening_pov", "")),
            ("动作接力", getattr(input_data.chapter_bridge, "action_handoff", "")),
            ("情绪余波", getattr(input_data.chapter_bridge, "emotional_carryover", "")),
        ):
            cleaned = str(value or "").strip()
            if cleaned:
                anchors.append(f"{label}：{cleaned}")
        seen: set[str] = set()
        unique_anchors: list[str] = []
        for anchor in anchors:
            if anchor not in seen:
                seen.add(anchor)
                unique_anchors.append(anchor)
        directive = ContinuityRepairDirective(
            target_window=f"本章开头{paragraph_range_label(start, end)}",
            repair_strategy="窗口级改写开头：重排时空落点、情绪余波和动作接力，避免只补关键词。",
            recommended_rewrite=(
                "以正文语气重写开头目标窗口：先接住上一章结尾余波，再落到本章开场"
                "时间、地点和视角，最后把 bridge.action_handoff 写成角色正在进行的动作。"
            ),
            required_context=[
                f"上一章结尾 {prev_tail_paragraphs} 段（只读）",
                f"本章开头 {opening_paragraphs} 段（目标窗口）",
                "chapter_bridge.action_handoff / emotional_carryover",
                "chapter_plan.opening_contract",
            ],
            required_anchors=unique_anchors,
            validation_focus=[
                "修后开头读起来像上一章动作的自然后果",
                "时空、POV、情绪和动作接力都在正文中自然落地",
            ],
            conflict_policy=(
                "若与同一开头窗口内的其他修复重叠，先合并事实/因果修复，再最后统一改写开场衔接。"
            ),
            repair_order="late_if_conflict",
        )
        return issue.model_copy(
            update={
                "repair_directive": directive,
                "fix_mode": "window",
                "paragraph_start": start,
                "paragraph_end": end,
            }
        )

    @classmethod
    def _issue_surface(cls, issue: ContinuityIssue) -> str:
        return (getattr(issue, "repair_surface", "") or "").strip().lower()

    @classmethod
    def _is_text_repair_issue(cls, issue: ContinuityIssue) -> bool:
        status = (getattr(issue, "status", "open") or "open").strip().lower()
        if status != "open":
            return False
        return cls._issue_surface(issue) in cls._TEXT_REPAIR_SURFACES

    @classmethod
    def _deferred_surface_counts(cls, issues: list[ContinuityIssue]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in issues:
            surface = cls._issue_surface(issue) or "chapter_text"
            if surface in cls._TEXT_REPAIR_SURFACES:
                continue
            counts[surface] = counts.get(surface, 0) + 1
        return counts

    @classmethod
    def _target_for_issue(cls, issue: ContinuityIssue, total_paragraphs: int) -> TargetSection:
        issue_type = issue.issue_type.lower()
        para_start = int(getattr(issue, "paragraph_start", 0) or 0)
        para_end = int(getattr(issue, "paragraph_end", 0) or para_start)
        if para_start > 0:
            para_start = max(1, min(para_start, total_paragraphs))
            para_end = max(para_start, min(para_end or para_start, total_paragraphs))
            return TargetSection(
                section_type="paragraph",
                start_paragraph=para_start,
                end_paragraph=para_end,
                reason=issue.summary,
            )
        if any(token in issue_type for token in ("opening", "bridge", "location", "time", "pov")):
            return TargetSection(
                section_type="opening",
                start_paragraph=1,
                end_paragraph=min(4, total_paragraphs),
                reason=issue.summary,
            )
        if any(token in issue_type for token in ("ending", "handoff", "closing", "ambiguity")):
            start = max(1, total_paragraphs - 4)
            return TargetSection(
                section_type="closing",
                start_paragraph=start,
                end_paragraph=total_paragraphs,
                reason=issue.summary,
            )
        if any(
            token in issue_type
            for token in (
                "carry_forward",
                "missing",
                "consistency",
                "repetition",
                "repeat",
                "anchor",
                "forbidden",
            )
        ):
            return TargetSection(
                section_type="targeted_rewrite",
                start_paragraph=1,
                end_paragraph=total_paragraphs,
                reason=issue.summary,
            )
        return TargetSection(
            section_type="targeted_rewrite",
            start_paragraph=1,
            end_paragraph=min(max(6, total_paragraphs // 2), total_paragraphs),
            reason=issue.summary,
        )

    @classmethod
    def _build_repair_plan(cls, input_data: ContinuityRepairInput) -> RepairPlan:
        all_issues = list(input_data.continuity_report.issues)
        deferred_issues = [
            issue
            for issue in all_issues
            if (getattr(issue, "status", "open") or "open").strip().lower() == "open"
            and not cls._is_text_repair_issue(issue)
        ]
        issues = [issue for issue in all_issues if cls._is_text_repair_issue(issue)]
        _SKIP_IF_LOW = {"text_repetition", "sensory_anchor_repetition", "repetition"}
        _filtered = [
            i
            for i in issues
            if not (
                (i.severity or "").lower() in ("low", "轻微")
                and any(t in (i.issue_type or "").lower() for t in _SKIP_IF_LOW)
            )
        ]
        if _filtered:
            issues = _filtered
        issues = [
            cls._ensure_opening_repair_directive(cls._sanitize_issue(issue), input_data)
            for issue in issues
        ]
        if not issues:
            surface_counts = cls._deferred_surface_counts(deferred_issues)
            deferred_note = (
                "；".join(f"{surface}={count}" for surface, count in sorted(surface_counts.items()))
                if surface_counts
                else ""
            )
            return RepairPlan(
                issues=[],
                deferred_issues=deferred_issues,
                repair_surfaces=sorted(surface_counts),
                target_sections=[],
                must_keep=[
                    "保留现有章结构与主线推进。",
                    "不做整章重写。",
                ],
                must_change=[],
                expected_outcome=(
                    "未发现需正文修复的 continuity 问题，本次文本修复为 no-op。"
                    + (f" 待其他修复面处理：{deferred_note}。" if deferred_note else "")
                ),
                no_op=True,
            )

        total_paragraphs = paragraph_count(input_data.chapter_text)
        target_sections = [cls._target_for_issue(issue, total_paragraphs) for issue in issues]
        must_change = [issue.summary for issue in issues if issue.summary]
        if input_data.must_fix_issues:
            must_fix_labels = [
                f"《阻断归档·必须解决》{iss.summary}"
                for iss in input_data.must_fix_issues
                if iss.summary
            ]
            _mf_summaries = {iss.summary for iss in input_data.must_fix_issues if iss.summary}
            must_change = [item for item in must_change if item not in _mf_summaries]
            must_change = must_fix_labels + must_change
        must_keep = [
            "保留现有章结构与主线推进。",
            "保留已建立的时间、地点、视角与主要事件顺序。",
        ]
        _allowed_chars: list[str] = []
        co = input_data.chapter_outline
        if co is not None:
            outline_chars = getattr(co, "required_characters", None)
            if not outline_chars:
                outline_chars = getattr(co, "involved_characters", []) or []
            _allowed_chars.extend(list(outline_chars))
        _allowed_chars.extend(input_data.chapter_state_packet.known_characters)
        _seen: set[str] = set()
        _unique_chars: list[str] = []
        for c in _allowed_chars:
            if c and c not in _seen:
                _seen.add(c)
                _unique_chars.append(c)
        if _unique_chars:
            must_keep.append(
                f"本章仅允许出现以下角色：{'、'.join(_unique_chars)}。"
                "修复时严禁引入任何新角色名，如需补充人物可用'护卫''路人'等泛称。"
            )
        if input_data.chapter_plan.opening_contract:
            must_keep.append(
                "开场仍需自然承接上一章余波，但不能写成\u201c这是在承接上一章\u201d的说明腔。"
            )
        if input_data.chapter_plan.closing_contract:
            must_keep.append("章末仍需留下下一章可直接接续的行动、状态或悬念出口。")
        for item in input_data.chapter_state_packet.must_carry_forward:
            item_text = carry_forward_text(item)
            if item_text:
                must_keep.append(f"保留并自然化落地：{cls._summarize_fact(item_text)}")
        co = input_data.chapter_outline
        if co is not None:
            if co.goal:
                must_keep.append(f"本章故事目标：{co.goal}")
            for pt in co.main_plot_points:
                if pt:
                    must_keep.append(f"主线推进点：{pt}")
        for scene in input_data.chapter_plan.scene_intents:
            if scene.required_outcome:
                must_keep.append(f"场景「{scene.summary}」结果：{scene.required_outcome}")
            elif scene.summary:
                must_keep.append(f"场景存在：{scene.summary}")
        _forbidden = getattr(input_data.chapter_plan, "forbidden_elements", []) or []
        if _forbidden:
            from novel_forge.pipeline.steps.continuity_eval_step import _detect_forbidden_elements

            _found = _detect_forbidden_elements(input_data.chapter_text, _forbidden)
            if _found:
                for fe, match in _found:
                    _label = (
                        f"【禁用元素】正文出现'{match}'（禁用：'{fe}'），必须替换为完全不同的意象"
                    )
                    if _label not in must_change:
                        must_change.insert(0, _label)
            _clean_keep: list[str] = []
            for keep_text in must_keep:
                hits = _detect_forbidden_elements(keep_text, _forbidden)
                if hits:
                    _hit_names = "、".join(f"'{fe}'" for fe, _ in hits)
                    _clean_keep.append(
                        f"{keep_text}（注意：此描述含禁用元素{_hit_names}，保留场景目标但必须替换相关意象）"
                    )
                else:
                    _clean_keep.append(keep_text)
            must_keep = _clean_keep
        return RepairPlan(
            issues=issues,
            deferred_issues=deferred_issues,
            repair_surfaces=sorted(
                {cls._issue_surface(issue) or "chapter_text" for issue in issues}
            ),
            target_sections=target_sections,
            must_keep=[item for item in must_keep if item],
            must_change=must_change,
            expected_outcome="定向修复 continuity 问题，保留已有文本、剧情推进与字数，不引入'承接上章/POV/情绪标签'等元语言。",
            no_op=False,
        )

    @classmethod
    def _issue_type_lower(cls, issue: ContinuityIssue) -> str:
        return (issue.issue_type or "").lower()

    @classmethod
    def _is_opening_boundary_issue(cls, issue: ContinuityIssue) -> bool:
        issue_type = cls._issue_type_lower(issue)
        if issue_type == "opening_gap":
            return True
        if issue_type != "bridge_contract_not_followed":
            return False
        if cls._issue_surface(issue) not in cls._TEXT_REPAIR_SURFACES:
            return False
        scope = (getattr(issue, "rewrite_scope", "") or "").strip().lower()
        validator = (getattr(issue, "validator_id", "") or "").strip().lower()
        location = (getattr(issue, "location", "") or "").strip()
        summary = (getattr(issue, "summary", "") or "").strip()
        opening_text = location + " " + summary
        if validator == "opening_transition_validator":
            return True
        if scope in {"opening", "开头", "开场", "开篇"}:
            return True
        if any(token in opening_text for token in ("开头", "开场", "开篇", "开局")):
            return True
        if scope in {"paragraph", "段落", "段"}:
            return False
        para_start = int(getattr(issue, "paragraph_start", 0) or 0)
        return scope in {"", "chapter", "全章", "全文"} and para_start <= 3

    @classmethod
    def _patch_issue_sort_key(cls, issue: ContinuityIssue) -> tuple[int, str]:
        # Opening boundary fixes are final seam work: if their window overlaps
        # factual/causal repairs, let those land first, then rewrite the opening
        # handoff once.
        return (1 if cls._is_opening_boundary_issue(issue) else 0, cls._issue_type_lower(issue))

    @classmethod
    def _patch_context_size(
        cls,
        issues: list[ContinuityIssue],
        input_data: ContinuityRepairInput | None = None,
    ) -> int:
        if any(cls._is_opening_boundary_issue(issue) for issue in issues):
            opening_paragraphs = (
                cls._boundary_opening_paragraphs(input_data)
                if input_data is not None
                else DEFAULT_OPENING_PARAGRAPHS
            )
            return max(3, min(5, opening_paragraphs + 2))
        return 3

    @classmethod
    def _must_fix_summaries(cls, input_data: ContinuityRepairInput) -> list[str]:
        summaries: list[str] = []
        seen: set[str] = set()
        for issue in input_data.must_fix_issues:
            summary = (getattr(issue, "summary", "") or "").strip()
            if not summary or summary in seen:
                continue
            seen.add(summary)
            summaries.append(summary)
        return summaries

    @classmethod
    def _is_patch_candidate(cls, issue: ContinuityIssue) -> bool:
        from novel_forge.pipeline.long.decisions import decide_patch_routing

        issue_type = cls._issue_type_lower(issue)
        scope = (issue.rewrite_scope or "").strip().lower()
        fix_mode = (getattr(issue, "fix_mode", "") or "").strip().lower()
        evidence = (issue.evidence or "").strip()
        try:
            paragraph_start = int(getattr(issue, "paragraph_start", 0) or 0)
        except (TypeError, ValueError):
            paragraph_start = 0
        try:
            location_confidence = float(getattr(issue, "location_confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            location_confidence = 0.0

        return decide_patch_routing(
            issue_type=issue_type,
            scope=scope,
            fix_mode=fix_mode,
            evidence=evidence,
            paragraph_start=paragraph_start,
            location_confidence=location_confidence,
        )

    @classmethod
    def _infer_patch_location(cls, issue: ContinuityIssue) -> str:
        scope = (issue.rewrite_scope or "").strip()
        if scope and scope.lower() not in cls._UNPATCHABLE_SCOPES:
            return scope
        para_start = int(getattr(issue, "paragraph_start", 0) or 0)
        para_end = int(getattr(issue, "paragraph_end", 0) or para_start)
        if para_start > 0:
            return f"第{para_start}段" if para_end <= para_start else f"第{para_start}-{para_end}段"
        location = (issue.location or "").strip()
        if location:
            return location
        issue_type = cls._issue_type_lower(issue)
        if issue_type in cls._OPENING_PATCH_TYPES:
            return "开头1-3段"
        if issue_type in cls._CLOSING_PATCH_TYPES:
            return "结尾1-3段"
        return "开头"

    @classmethod
    def _build_patch_continuity_context(
        cls,
        input_data: ContinuityRepairInput,
    ) -> dict[str, Any]:
        packet = input_data.chapter_state_packet
        prev_exit = packet.previous_exit_state
        prev_exit_summary = ""
        if prev_exit is not None:
            parts: list[str] = []
            if prev_exit.time_marker:
                parts.append(f"时间：{prev_exit.time_marker}")
            if prev_exit.location:
                parts.append(f"地点：{prev_exit.location}")
            if prev_exit.pov:
                parts.append(f"视角：{prev_exit.pov}")
            if prev_exit.active_goals:
                parts.append(f"行动目标：{prev_exit.active_goals[0]}")
            prev_exit_summary = "；".join(parts)
        if not prev_exit_summary:
            prev_exit_summary = (
                getattr(input_data.chapter_bridge, "action_handoff", "") or ""
            ).strip()

        character_states: dict[str, dict[str, str]] = {}
        if prev_exit is not None and prev_exit.character_end_states:
            for name, state in prev_exit.character_end_states.items():
                status = (
                    getattr(getattr(state, "emotional", None), "primary_emotion", "") or ""
                ).strip()
                if not status:
                    status = (
                        getattr(getattr(state, "motivation", None), "current_drive", "") or ""
                    ).strip()
                if not status:
                    status = (
                        getattr(getattr(state, "physical", None), "location", "") or ""
                    ).strip()
                character_states[name] = {"status": status or "状态待续"}

        context = {
            "previous_exit_state": prev_exit_summary or "无",
            "must_carry_forward": [
                item.text
                for item in packet.must_carry_forward
                if item.status == "open" and item.text.strip()
            ],
            "character_states": character_states,
        }
        memory_context = input_data.memory_context or {}
        if memory_context.get("escalation_note"):
            context["escalation_note"] = memory_context["escalation_note"]
        if memory_context.get("memory_guidance"):
            context["memory_guidance"] = memory_context["memory_guidance"]
        return context

    @classmethod
    def _build_patch_boundary_context(
        cls,
        input_data: ContinuityRepairInput,
        patch_issues: list[ContinuityIssue],
    ) -> dict[str, Any]:
        issue_types = {cls._issue_type_lower(iss) for iss in patch_issues}
        return build_patch_boundary_context(
            issue_types=issue_types,
            opening_patch_types=set(cls._OPENING_PATCH_TYPES),
            closing_patch_types=set(cls._CLOSING_PATCH_TYPES),
            previous_chapter_ending=getattr(
                input_data.chapter_state_packet, "previous_chapter_ending", ""
            ),
            opening_contract=getattr(input_data.chapter_plan, "opening_contract", ""),
            closing_contract=getattr(input_data.chapter_plan, "closing_contract", ""),
            max_prev_ending_paragraphs=cls._boundary_prev_tail_paragraphs(input_data),
        )

    # ── Main execution ────────────────────────────────────────────────

    async def _execute(self, input_data: ContinuityRepairInput) -> ContinuityRepairResult:
        input_data = replace(
            input_data,
            boundary_prev_tail_paragraphs=coerce_paragraph_count(
                getattr(
                    self.settings,
                    "long_boundary_prev_tail_paragraphs",
                    input_data.boundary_prev_tail_paragraphs,
                ),
                default=DEFAULT_PREVIOUS_TAIL_PARAGRAPHS,
            ),
            boundary_opening_paragraphs=coerce_paragraph_count(
                getattr(
                    self.settings,
                    "long_boundary_opening_paragraphs",
                    input_data.boundary_opening_paragraphs,
                ),
                default=DEFAULT_OPENING_PARAGRAPHS,
                maximum=8,
            ),
        )
        repair_plan = self._build_repair_plan(input_data)
        deferred_surface_counts = self._deferred_surface_counts(repair_plan.deferred_issues)
        deferred_surfaces = tuple(sorted(deferred_surface_counts))
        deferred_warning = (
            "非正文连贯性问题已跳过文本修复，等待对应修复面处理："
            + "；".join(
                f"{surface}={count}" for surface, count in sorted(deferred_surface_counts.items())
            )
            if deferred_surface_counts
            else ""
        )
        if repair_plan.no_op:
            return ContinuityRepairResult(
                revised_text=input_data.chapter_text,
                repair_plan=repair_plan,
                applied=False,
                failure_reason=(
                    "non_text_continuity_issues_pending" if deferred_surface_counts else None
                ),
                warnings=(deferred_warning,) if deferred_warning else (),
                deferred_issue_count=len(repair_plan.deferred_issues),
                deferred_surfaces=deferred_surfaces,
            )

        # Split issues: patchable vs fulltext
        # anchor_degraded issues skip patch — their location is unreliable
        _filtered_issues = repair_plan.issues
        if bool(getattr(self.settings, "patch_first_repair", True)):
            _patch_issues = [
                iss
                for iss in _filtered_issues
                if self._is_patch_candidate(iss)
                and getattr(iss, "anchor_type", "") != "anchor_degraded"
            ]
            _patch_issues.sort(key=self._patch_issue_sort_key)
            _fulltext_issues = [
                iss
                for iss in _filtered_issues
                if not self._is_patch_candidate(iss)
                or getattr(iss, "anchor_type", "") == "anchor_degraded"
            ]
        else:
            _patch_issues = []
            _fulltext_issues = list(_filtered_issues)

        working_text = input_data.chapter_text
        _patch_applied = False

        # ── Patch path ────────────────────────────────────────────────
        if _patch_issues:
            _compat_issues = [self.adapt_issue_for_patch(iss) for iss in _patch_issues]
            _continuity_context = self._build_patch_continuity_context(input_data)
            _boundary_context = self._build_patch_boundary_context(input_data, _patch_issues)
            _context_size = self._patch_context_size(_patch_issues, input_data)
            _must_fix_summaries = self._must_fix_summaries(input_data)
            from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
                cognitive_constraints_from_contract,
            )

            revised, patches_applied, patches_attempted, fallback = await self._run_patch_repair(
                chapter_number=input_data.chapter_number,
                chapter_text=working_text,
                issues=_compat_issues,
                context_size=_context_size,
                style=input_data.style,
                style_profile=input_data.style_profile,
                continuity_context=_continuity_context,
                boundary_context=_boundary_context,
                must_fix_summaries=_must_fix_summaries,
                cognitive_constraints=cognitive_constraints_from_contract(
                    input_data.chapter_state_packet.chapter_contract
                ),
            )

            if not fallback and patches_applied > 0:
                working_text = revised
                _patch_applied = True
                self._repair_logger.info(
                    "continuity_repair: patch path applied %d/%d patches for patchable issues",
                    patches_applied,
                    patches_attempted,
                )
            elif patches_attempted > 0:
                self._repair_logger.info(
                    "continuity_repair: patch path failed (0/%d applied) | chapter=%d",
                    patches_attempted,
                    input_data.chapter_number,
                )

        # If all issues handled by patch and it succeeded, skip fulltext.
        if _patch_applied and not _fulltext_issues:
            return ContinuityRepairResult(
                revised_text=working_text,
                repair_plan=repair_plan,
                applied=True,
                warnings=(deferred_warning,) if deferred_warning else (),
                patch_only=True,
                repaired_issue_types=tuple((iss.issue_type or "").lower() for iss in _patch_issues),
                deferred_issue_count=len(repair_plan.deferred_issues),
                deferred_surfaces=deferred_surfaces,
            )

        # If patch failed, escalate all remaining patch issues to fulltext.
        # Don't filter by severity — even medium/low issues can block archiving
        # if they prevent the continuity score from reaching the threshold.
        # ── Force patch-only mode: skip fulltext escalation after regression ──
        _memory_ctx = input_data.memory_context or {}
        if _memory_ctx.get("force_patch_only") and not _fulltext_issues:
            self._repair_logger.info(
                "continuity_repair: force_patch_only active, skipping fulltext escalation | chapter=%d",
                input_data.chapter_number,
            )
            return ContinuityRepairResult(
                revised_text=working_text,
                repair_plan=repair_plan,
                applied=_patch_applied,
                warnings=(deferred_warning,) if deferred_warning else (),
                patch_only=True,
                failure_reason="force_patch_only_after_regression",
                repaired_issue_types=tuple((iss.issue_type or "").lower() for iss in _patch_issues),
                deferred_issue_count=len(repair_plan.deferred_issues),
                deferred_surfaces=deferred_surfaces,
            )
        # ── Fulltext upgrade gate: block escalation if cumulative change is high ──
        if not _fulltext_issues and not _patch_applied:
            _change_budget = getattr(self.settings, "change_budget_threshold", 0.15)
            _budget_fraction = getattr(
                self.settings, "long_fulltext_upgrade_change_budget_fraction", 0.50
            )
            if input_data.cumulative_change_ratio > _change_budget * _budget_fraction:
                self._repair_logger.info(
                    "continuity_repair: fulltext upgrade blocked by change budget gate "
                    "(cumulative=%.4f, budget=%.4f, fraction=%.2f)",
                    input_data.cumulative_change_ratio,
                    _change_budget,
                    _budget_fraction,
                )
                # Keep in window mode instead of escalating to fulltext
                _window_issues = list(_patch_issues)
                _patch_issues = []
            else:
                _fulltext_issues = list(_patch_issues)
                self._repair_logger.info(
                    "continuity_repair: escalating all %d patch failures to fulltext",
                    len(_fulltext_issues),
                )

        # ── Fulltext path ─────────────────────────────────────────────
        orig_len = non_space_len(working_text)

        # Compute adaptive word bounds
        _issue_types = {(issue.issue_type or "").lower() for issue in _fulltext_issues}
        word_count_min, word_count_max, guard_lo, guard_hi = self.compute_word_bounds(
            orig_len, _issue_types
        )

        # Output token budget
        estimated_tokens = self._dynamic_max_tokens(
            TaskType.REPAIR_CONTINUITY,
            len(input_data.chapter_text),
            prompt_overhead=3000,
            max_cap=getattr(self.settings, "continuity_repair_max_tokens", 16384),
            safety_margin=0.80,
            min_tokens=6144,
        )
        temperature = getattr(self.settings, "temp_repair_continuity", 0.7)

        # Build repair context (uses working_text as base)
        repair_ctx = self.build_repair_context(input_data)
        repair_ctx["chapter_text"] = working_text
        repair_ctx["word_count"] = orig_len
        repair_ctx["word_count_min"] = word_count_min
        repair_ctx["word_count_max"] = word_count_max

        # Call LLM
        revised_text, was_truncated = await self._run_fulltext_repair(
            repair_ctx,
            max_tokens=estimated_tokens,
            temperature=temperature,
        )
        revised_text = revised_text.strip()
        if not revised_text:
            revised_text = working_text

        # Truncation detection + retry
        _truncation_detected = was_truncated or self._is_json_truncated(
            getattr(self, "_last_response_content", "")
        )
        if _truncation_detected or (
            orig_len > 0 and non_space_len(revised_text) / orig_len < guard_lo
        ):
            if _truncation_detected:
                _retry_warning = (
                    "上一次返回的 JSON 被截断了（JSON 结构不完整）。"
                    "请返回完整的修复结果，必须包含有效的 revised_text 字段和正确的 JSON 结构。"
                    f"字数须在 {word_count_min}–{word_count_max} 字内。"
                )
            else:
                revised_len = non_space_len(revised_text)
                _retry_warning = (
                    f"上一次返回的 revised_text 只有 {revised_len} 字，"
                    f"远少于原文 {orig_len} 字，已被判定为摘要并丢弃。"
                    f"请返回修复后的完整章节全文，字数须在 {word_count_min}–{word_count_max} 字内。"
                )
            retry_text, retry_truncated = await self._retry_with_warning(
                repair_ctx,
                _retry_warning,
                max_tokens=estimated_tokens,
                temperature=temperature,
            )
            retry_text = retry_text.strip()
            if retry_text and not retry_truncated:
                revised_text = retry_text
                _truncation_detected = False

        # Word guard validation
        _word_guard_triggered = False
        valid, failure_reason = self.validate_revised_text(working_text, revised_text)
        if not valid:
            self._repair_logger.warning(
                "continuity_repair: word count guard triggered, reverting to original"
            )
            revised_text = working_text
            _word_guard_triggered = True

        # ── Forbidden element post-validation ─────────────────────────
        _warnings: list[str] = []
        if deferred_warning:
            _warnings.append(deferred_warning)
        _forbidden = getattr(input_data.chapter_plan, "forbidden_elements", []) or []
        if _forbidden and not _word_guard_triggered and revised_text != working_text:
            revised_text, new_warnings = await self._check_and_retry_forbidden(
                revised_text,
                _forbidden,
                repair_ctx,
                orig_len=orig_len,
                word_count_min=word_count_min,
                word_count_max=word_count_max,
                guard_lo=guard_lo,
                guard_hi=guard_hi,
                max_tokens=estimated_tokens,
                temperature=temperature,
            )
            _warnings.extend(new_warnings)

        # Determine applied status
        _applied = revised_text != input_data.chapter_text or _patch_applied
        _failure_reason: str | None = None
        if not _applied:
            if _word_guard_triggered:
                _failure_reason = failure_reason or (
                    f"字数守卫触发：修复结果字数偏差超出允许范围（原文 {orig_len} 字），"
                    "已回滚至原文。建议手动编辑或调整字数限制后重试。"
                )
            elif _truncation_detected:
                _failure_reason = (
                    "JSON 截断：模型输出被 max_tokens 限制截断，无法解析有效结果。"
                    "已回滚至原文。建议增大 continuity_repair_max_tokens 配置。"
                )
            else:
                _failure_reason = "修复结果与原文相同，未产生实质性修改。"

        _all_issue_types = [(iss.issue_type or "").lower() for iss in _patch_issues] + [
            (iss.issue_type or "").lower() for iss in _fulltext_issues
        ]

        return ContinuityRepairResult(
            revised_text=revised_text,
            repair_plan=repair_plan,
            applied=_applied,
            failure_reason=_failure_reason,
            warnings=tuple(_warnings),
            patch_only=False,
            repaired_issue_types=tuple(_all_issue_types),
            deferred_issue_count=len(repair_plan.deferred_issues),
            deferred_surfaces=deferred_surfaces,
        )


# === 改进3: 修复质量预筛选函数 ===


def prescreen_revised_text(
    original_text: str,
    revised_text: str,
    pre_issues: list[Any],
    repair_plan: Any,
) -> tuple[bool, str]:
    """Lightweight prescreening of repair output before full quality checks.

    Uses deterministic rules (no LLM) to catch obviously broken repairs.
    Returns (pass, reason) tuple.

    Checks:
    a) Word count change within reasonable bounds (±20%)
    b) Key paragraphs preserved (fuzzy matching)
    c) No obvious format corruption
    d) Forbidden elements quick scan
    """
    import difflib

    # Check a: Word count change
    orig_len = len(original_text)
    revised_len = len(revised_text)
    if orig_len > 0:
        change_ratio = abs(revised_len - orig_len) / orig_len
        if change_ratio > 0.30:  # ±30% tolerance (宽松)
            return False, f"Word count change too large: {change_ratio:.1%} (limit 30%)"

    # Check b: Key paragraphs preserved
    # Extract paragraphs that had issues and verify they still exist in some form
    if pre_issues and not repair_plan.no_op:
        # Sample a few key paragraphs from original
        orig_paragraphs = [p.strip() for p in original_text.split("\n\n") if len(p.strip()) > 50]
        if len(orig_paragraphs) > 0:
            # Check at least 70% of substantial paragraphs still exist
            preserved_count = 0
            checked_count = 0
            for para in orig_paragraphs[:10]:  # Check up to 10 paragraphs
                if len(para) < 100:  # Skip short paragraphs
                    continue
                checked_count += 1
                # Fuzzy match: at least 60% similarity
                for rev_para in revised_text.split("\n\n"):
                    if len(rev_para) < 100:
                        continue
                    similarity = difflib.SequenceMatcher(None, para[:200], rev_para[:200]).ratio()
                    if similarity > 0.6:
                        preserved_count += 1
                        break

            if checked_count > 0 and preserved_count < checked_count * 0.5:
                return False, f"Key paragraphs not preserved: {preserved_count}/{checked_count}"

    # Check c: No obvious format corruption
    # Check for unclosed quotes, broken markdown, etc.
    quote_count = revised_text.count('"') + revised_text.count('"') + revised_text.count("'")
    if quote_count % 2 != 0:
        # Odd number of quotes - might be broken
        if quote_count > 20:  # Only flag if many quotes (likely broken)
            return False, f"Possible format issue: odd number of quotes ({quote_count})"

    # Check for very long lines (likely missing newlines)
    lines = revised_text.split("\n")
    max_line_len = max(len(line) for line in lines) if lines else 0
    if max_line_len > 5000:
        return False, f"Possible format corruption: very long line ({max_line_len} chars)"

    # Check d: Forbidden elements quick scan (common ones)
    forbidden_markers = [
        "【修复",
        "[修复",
        "REPAIR_",
        "PATCH_",
        "no_op=True",
        "repair_plan",
    ]
    for marker in forbidden_markers:
        if marker in revised_text:
            return False, f"Forbidden marker detected: '{marker}'"

    # All checks passed
    return True, "Prescreen passed"
