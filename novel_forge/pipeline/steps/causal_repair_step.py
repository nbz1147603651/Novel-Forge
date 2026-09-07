"""CausalRepairStep — targeted repair for causal chain issues."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, cast

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
from novel_forge.core.schemas.continuity import ChapterBridge
from novel_forge.core.utils.issue_prompt_blocks import build_issues_prompt_block
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.repair.base import RepairStepBase
from novel_forge.pipeline.steps.repair.forbidden_checker import check_forbidden
from novel_forge.pipeline.steps.repair.text_utils import non_space_len

_logger = get_logger("pipeline.causal_repair")


@dataclass(frozen=True)
class CausalRepairInput:
    """Input for targeted causal chain repair."""

    chapter_number: int
    chapter_text: str
    causal_link: dict[str, Any]
    chapter_bridge: ChapterBridge
    causal_report: CausalValidationReport
    must_fix_summaries: tuple[str, ...] = field(default_factory=tuple)
    """Critical/high-severity issue summaries that must be resolved — injected into patch prompt."""
    must_fix_issue_ids: tuple[str, ...] = field(default_factory=tuple)
    """Issue IDs for precise targeting. Preferred over must_fix_summaries when available."""
    previous_chapter_ending: str = ""
    """Tail text of the previous chapter (~800 chars). Provides real narrative context for
    opening_causal_gap and event_without_cause repair when the chapter bridge alone is insufficient."""
    repair_round: int = 1
    """Current repair round (1-based). Round 2+ triggers escalation: patch-type issues go
    directly to full-text edit with an explicit escalation note to try a different strategy."""
    prev_round_issues: tuple[str, ...] | None = None
    """Summaries of issues that persisted after the previous repair round. Injected into the
    escalation note so the LLM knows what was already attempted."""
    character_profiles: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    """Character profiles extracted from packet (name, identity, gender, abilities, etc.).
    Provides character context for event_without_cause and unmotivated_decision repairs."""
    per_issue_rounds: dict[str, int] = field(default_factory=dict)
    """Mapping from issue signature → individual attempt count for that issue.
    When set, overrides the global ``repair_round`` for escalation decisions
    so that each issue receives an escalation note matching *its own* history."""
    escalate_issue_signatures: tuple[str, ...] = field(default_factory=tuple)
    """Issue signatures that should be escalated to full-text repair in this run.
    Used by manual repair flows to escalate only repeatedly-unresolved issues
    instead of escalating all issues in a batch."""
    chapter_plan_scenes: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    """Compact scene-intent summaries from the chapter plan (scene_id, summary, purpose).
    Provides plan-level context for missing_causal_transition repairs so the LLM
    knows the intended scene boundaries and transition points."""
    forbidden_elements: tuple[str, ...] = field(default_factory=tuple)
    """Hard-forbidden elements (imagery, phrases, sensory channels) that must NOT
    appear in the repaired text. Injected into repair context so the LLM avoids them."""
    forbidden_elements_soft: tuple[str, ...] = field(default_factory=tuple)
    """Soft-forbidden elements — discouraged but not strictly prohibited.
    Injected alongside hard-forbidden list for nuanced guidance."""
    cumulative_change_ratio: float = 0.0
    """Cumulative text change ratio across all repair dimensions (for fulltext upgrade gate)."""
    time_convention: str = ""
    address_rules: str = ""
    world_context_rules: str = ""
    style: str = "literary"
    style_profile: dict[str, Any] | None = None
    editorial_contract: Any | None = None
    memory_guidance: dict[str, Any] | None = None
    repair_attempt_guidance: dict[str, Any] | None = None
    kernel_context: dict[str, Any] = field(default_factory=dict)
    """Minimal field slice from StoryKernel via ContextComposer.
    Contains causal-relevant fields: entities, relationships, timeline,
    knowledge_ledger, business_dependencies, promise_ledger.
    Injected into the repair prompt so the model can reference canonical state."""
    chapter_state_packet: Any | None = None
    """Full chapter packet for contract, bridge, and narrative-context cards."""
    chapter_outline: Any | None = None
    """Chapter outline used to scope POV, involved characters, and knowledge cards."""
    chapter_plan: Any | None = None
    """Chapter plan payload used to keep causal repair within intended scene boundaries."""
    chapter_source_slice: Any | None = None


@dataclass(frozen=True)
class CausalRepairResult:
    """Repair output: revised text and metadata."""

    revised_text: str
    issues: tuple[CausalIssue, ...]
    applied: bool = False
    patches_applied: int = 0
    patches_attempted: int = 0
    failure_reason: str | None = None
    repaired_issue_types: tuple[str, ...] = field(default_factory=tuple)
    forbidden_element_warnings: list[str] = field(default_factory=list)


class CausalRepairStep(RepairStepBase[CausalRepairInput, CausalRepairResult]):
    """Repair causal chain problems without re-running the whole chapter pipeline."""

    _ISSUE_TYPE_FIXES = {
        "opening_causal_gap": "在开头用角色的动作、感官或正在进行的行为自然续上上一章余波，确保读者能感到「因为上章那件事，所以现在这样」的因果承接。禁止使用「承接上章」「情绪标签」「POV」等说明腔。",
        "missing_causal_transition": "在关键转折处添加因果铺垫（动作/场景/心理暗示），使转折显得自然合理，避免突兀的事件跳跃。",
        "event_without_cause": "为「凭空发生」的重要情节添加合理的情境触发或信息曝光，让事件有内在因果逻辑支撑。",
        "unmotivated_decision": "为角色的重大决策补充内在动机支撑（心理、处境、信息），使决策显得合理而非随机。",
        "question_resolved_too_early": "确保悬念问题的解答时机合理，不要在伏笔尚未充分展开时过早揭晓答案。",
        "question_ignored": "确保本章回应了上一章或历史章节中提出的待回答问题，不要忽略读者的期待。",
        "causal_contradiction": "修正与历史因果链矛盾的内容，确保本章事件与已建立的因果逻辑一致。",
    }
    _PATCH_ONLY_TYPES = frozenset(
        {
            "opening_causal_gap",
        }
    )
    _WINDOW_ISSUE_TYPES = frozenset(
        {
            "missing_causal_transition",
            "event_without_cause",
            "unmotivated_decision",
            "question_resolved_too_early",
            "question_ignored",
        }
    )
    _OPENING_HINTS = ("开头", "首段", "起首")
    _CLOSING_HINTS = ("结尾", "末段", "尾段", "最后")

    # ── RepairStepBase hook implementations ──────────────────────────────────

    def repair_task_type(self) -> TaskType:
        return TaskType.REPAIR_CAUSAL

    def issue_type_fixes(self) -> dict[str, str]:
        return self._ISSUE_TYPE_FIXES

    def build_repair_context(
        self,
        input_data: CausalRepairInput,
        *,
        issues: list[CausalIssue] | None = None,
        window_before: str = "",
        window_after: str = "",
        current_text: str = "",
    ) -> dict[str, Any]:
        """Build the context dict for the LLM prompt.

        Extracted from the original ``_fulltext_repair`` method.
        """
        _issues = issues or []
        text = current_text or input_data.chapter_text

        # Build per-type issue groups
        issue_types: set[str] = set()
        typed_issues: dict[str, list[dict[str, str]]] = {}
        for issue in _issues:
            itype = (issue.issue_type or "").lower() or "event_without_cause"
            issue_types.add(itype)
            if itype not in typed_issues:
                typed_issues[itype] = []
            typed_issues[itype].append(
                {
                    "issue_id": getattr(issue, "issue_id", "") or "",
                    "summary": issue.summary or "",
                    "evidence": issue.evidence or "",
                    "evidence_quote": getattr(issue, "evidence_quote", "") or "",
                    "fix_suggestion": self._fix_suggestion_for_issue(issue),
                    "location": issue.location or "",
                    "severity": issue.severity or "high",
                    "paragraph_start": str(getattr(issue, "paragraph_start", 0) or ""),
                    "paragraph_end": str(getattr(issue, "paragraph_end", 0) or ""),
                    "fix_mode": getattr(issue, "fix_mode", "") or "",
                }
            )

        # Numbered chapter text for precise paragraph-level targeting.
        # Mirrors the format used by CausalValidationStep so that paragraph references
        # in typed_issues (e.g. "第3段") map directly to visible markers in the text.
        numbered_text = self._number_paragraphs(text)

        prev_ending = input_data.previous_chapter_ending or ""

        # Word count bounds
        word_count_min, word_count_max, _, _ = self.compute_word_bounds(
            non_space_len(text),
            issue_types,
        )
        from novel_forge.pipeline.long.services.constraints.constraint_router import (
            build_repair_cards,
        )

        stage_cards = build_repair_cards(
            stage="causal_repair",
            packet=input_data.chapter_state_packet,
            chapter_outline=input_data.chapter_outline,
            bridge=input_data.chapter_bridge,
            plan=input_data.chapter_plan,
            style_profile=input_data.style_profile,
            editorial_contract=input_data.editorial_contract,
            kernel_context=input_data.kernel_context,
            chapter_source_slice=input_data.chapter_source_slice,
            settings=self.settings,
        )
        if input_data.chapter_plan_scenes:
            plan_card = stage_cards.setdefault("plan", {})
            plan_card["scene_intents"] = list(input_data.chapter_plan_scenes)
            plan_card["scene_count"] = len(input_data.chapter_plan_scenes)
            plan_card["forbidden_elements"] = list(input_data.forbidden_elements or [])
            plan_card["forbidden_elements_soft"] = list(input_data.forbidden_elements_soft or [])
        elif input_data.forbidden_elements or input_data.forbidden_elements_soft:
            plan_card = stage_cards.setdefault("plan", {})
            plan_card["forbidden_elements"] = list(input_data.forbidden_elements or [])
            plan_card["forbidden_elements_soft"] = list(input_data.forbidden_elements_soft or [])

        # Escalation note — per-issue when available, otherwise global fallback.
        escalation_note = ""
        if input_data.per_issue_rounds:
            escalated_lines: list[str] = []
            for issue in _issues:
                sig = self.issue_signature(issue)
                rounds = input_data.per_issue_rounds.get(sig, 0)
                if rounds >= 2:
                    issue_id = getattr(issue, "issue_id", "") or ""
                    id_tag = f"[{issue_id}] " if issue_id else ""
                    escalated_lines.append(
                        f"- {id_tag}[{issue.issue_type}] {issue.summary or ''}（已尝试 {rounds} 轮未解决）"
                    )
            if escalated_lines:
                escalation_note = (
                    "⚠️ 以下问题已经历多轮修复仍未解决，请采用**完全不同**的修复策略"
                    "（例如：改写更长的段落、调整叙事视角、改用对话或动作而非描写来建立因果、"
                    "或将静态描写改为动态场景）：\n" + "\n".join(escalated_lines)
                )
        elif input_data.repair_round > 1 and input_data.prev_round_issues:
            escalation_note = (
                "⚠️ 这是第 {} 轮修复。上一轮修复已尝试处理以下问题但未成功：\n".format(
                    input_data.repair_round
                )
                + "\n".join(f"- {s}" for s in input_data.prev_round_issues)
                + "\n请采用与上轮**完全不同**的修复策略（例如：改写更长的段落、"
                "调整叙事视角、改用对话或动作而非描写来建立因果、"
                "或将静态描写改为动态场景）。"
            )

        # ── 角色白名单约束 ──
        _char_names = sorted(
            {
                str(p.get("name", "")).strip()
                for p in (input_data.character_profiles or [])
                if isinstance(p, dict) and p.get("name")
            }
        )
        _char_whitelist_note = ""
        if _char_names:
            _char_whitelist_note = (
                "本章仅允许出现以下角色："
                + "、".join(_char_names[:12])
                + "。修复时严禁引入任何新角色名，如需补充人物可用泛称。"
            )

        structured_issues_block = build_issues_prompt_block(
            _issues,
            header="待修复问题详情：",
            include_evidence=True,
            include_fix_actions=True,
        )

        return {
            "chapter_number": input_data.chapter_number,
            "chapter_text": text,
            "numbered_chapter_text": numbered_text,
            "issue_types": list(issue_types),
            "typed_issues": typed_issues,
            "structured_issues_block": structured_issues_block,
            "memory_guidance": input_data.memory_guidance or {},
            "repair_attempt_guidance": input_data.repair_attempt_guidance or {},
            "causal_link": input_data.causal_link or {},
            "previous_chapter_ending": prev_ending,
            "word_count_min": word_count_min,
            "word_count_max": word_count_max,
            "escalation_note": escalation_note,
            "character_whitelist_note": _char_whitelist_note,
            "chapter_plan_scenes": input_data.chapter_plan_scenes or [],
            "stage_cards": stage_cards,
            "kernel_context": input_data.kernel_context or {},
        }

    def compute_word_bounds(
        self,
        orig_len: int,
        issue_types: set[str],
    ) -> tuple[int, int, float, float]:
        """Fixed bounds: 0.85x–1.15x for LLM prompt, 0.60–1.55 for guard."""
        word_count_min = max(int(orig_len * 0.85), 800)
        word_count_max = int(orig_len * 1.15)
        return word_count_min, word_count_max, 0.60, 1.55

    def adapt_issue_for_patch(self, issue: Any) -> Any:
        """Causal issues are already PatchInput-compatible; identity adapter."""
        return issue

    def validate_revised_text(
        self,
        original: str,
        revised: str,
    ) -> tuple[bool, str]:
        """Check revised text is not suspiciously short."""
        if not revised or len(revised) < len(original) * 0.5:
            return False, "修复返回文本过短或为空"
        return True, ""

    def should_skip(self, input_data: CausalRepairInput) -> bool | None:
        """Skip first chapter — no previous causal chain to repair."""
        if input_data.chapter_number == 1:
            return True
        return None

    # ── Original class methods (preserved) ───────────────────────────────────

    @property
    def step_name(self) -> str:
        return "repair_causal"

    @classmethod
    def patch_only_issue_types(cls) -> frozenset[str]:
        """Issue types that are repaired via patch mode only."""
        return cls._PATCH_ONLY_TYPES

    @staticmethod
    def _norm_issue_text(value: Any) -> str:
        text = "" if value is None else str(value)
        return "".join(ch for ch in text.lower() if ch.isalnum())

    @classmethod
    def issue_signature(cls, issue: Any) -> str:
        """Stable issue signature preferring issue_id over content fingerprint.

        Kept as a classmethod because causal callers use it without instantiating
        the repair step.
        """
        issue_id = str(getattr(issue, "issue_id", "") or "").strip()
        if issue_id:
            return f"id:{issue_id}"
        return cls._content_signature(issue)

    @classmethod
    def _content_signature(cls, issue: Any) -> str:
        """Build a stable content-based signature for causal issues.

        Multi-layer anchor strategy (priority from high to low):
        1. {issue_type}|{content_hash_8}|{scope_key}  ← Preferred (content-based)
        2. {issue_type}|p{para_number}                ← Fallback (position-based)

        Layer 1 uses evidence_quote or summary text hash, which remains stable
        even when paragraph numbers change after repair. Layer 2 falls back to
        paragraph number only when content hash is unavailable.

        Note: issue_id-based signature is handled by ``issue_signature()``.
        """
        issue_type = (
            cls._norm_issue_text(getattr(issue, "issue_type", "") or "unknown") or "unknown"
        )

        # Layer 1: Text content hash anchor (most stable)
        evidence_quote = (
            getattr(issue, "evidence_quote", "") or getattr(issue, "evidence", "") or ""
        )
        if evidence_quote:
            content = str(evidence_quote)[:60]
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:8]
            scope = getattr(issue, "fix_mode", getattr(issue, "fix_suggestion", "")) or "unknown"
            scope_key = str(scope)[:10]
            return f"{issue_type}|{content_hash}|{scope_key}"

        # Layer 2: Summary normalization anchor
        summary = getattr(issue, "summary", "") or ""
        if summary:
            content = str(summary)[:40]
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:8]
            return f"{issue_type}|{content_hash}"

        # Layer 3: Paragraph number anchor (degraded)
        location = getattr(issue, "location", "") or ""
        para_match = re.search(r"第\s*(\d+)", location)
        if para_match:
            return f"{issue_type}|p{para_match.group(1)}"

        # Final fallback: use normalized summary
        norm_summary = cls._norm_issue_text(summary) or cls._norm_issue_text(location) or "unknown"
        return f"{issue_type}|{norm_summary[:80]}"

    @classmethod
    def _fix_suggestion_for_issue(cls, issue: CausalIssue) -> str:
        issue_type = (issue.issue_type or "").lower()
        generic = cls._ISSUE_TYPE_FIXES.get(issue_type, "")
        original = (issue.fix_suggestion or "").strip()
        if original and generic:
            # Keep the LLM's specific suggestion as primary, append generic methodology
            return f"{original}\n【修复方法参考】{generic}"
        if original:
            return original
        if generic:
            return generic
        return "修复因果链断裂，确保事件推进逻辑合理。"

    @classmethod
    def _sanitize_issue(cls, issue: CausalIssue) -> CausalIssue:
        return issue.model_copy(
            update={
                "fix_suggestion": cls._fix_suggestion_for_issue(issue),
            }
        )

    @classmethod
    def _build_issue_context(cls, issues: list[CausalIssue]) -> list[dict[str, Any]]:
        result = []
        for issue in issues:
            sanitized = cls._sanitize_issue(issue)
            result.append(
                {
                    "severity": sanitized.severity or "medium",
                    "location": sanitized.location or "",
                    "summary": sanitized.summary or "",
                    "fix_suggestion": sanitized.fix_suggestion or "",
                    "issue_type": sanitized.issue_type or "",
                    "paragraph_start": getattr(sanitized, "paragraph_start", 0) or 0,
                    "paragraph_end": getattr(sanitized, "paragraph_end", 0) or 0,
                    "evidence_quote": getattr(sanitized, "evidence_quote", "") or "",
                    "fix_mode": getattr(sanitized, "fix_mode", "") or "",
                }
            )
        return result

    @classmethod
    def _prepare_patch_issue(cls, issue: CausalIssue) -> CausalIssue:
        issue_type = (issue.issue_type or "").lower()
        if issue_type != "opening_causal_gap":
            return issue
        extra_rule = (
            "【硬性要求】关键因果句必须出现在第1段，直接写出“上一章事件→当前处境变化”"
            "，不得把这句延后到第2段及之后。"
        )
        base_fix = (issue.fix_suggestion or "").strip()
        if extra_rule not in base_fix:
            base_fix = f"{base_fix}\n{extra_rule}" if base_fix else extra_rule
        return issue.model_copy(
            update={
                "location": "第1段（开头承接处）",
                "fix_suggestion": base_fix,
            }
        )

    @classmethod
    def _is_opening_issue(cls, issue: Any) -> bool:
        issue_type = (getattr(issue, "issue_type", "") or "").lower()
        location = getattr(issue, "location", "") or ""
        return issue_type == "opening_causal_gap" or any(h in location for h in cls._OPENING_HINTS)

    @classmethod
    def _is_closing_issue(cls, issue: Any) -> bool:
        location = getattr(issue, "location", "") or ""
        return any(h in location for h in cls._CLOSING_HINTS)

    @classmethod
    def _build_boundary_context(
        cls, input_data: CausalRepairInput, issues: list[Any]
    ) -> dict[str, Any]:
        has_opening = any(cls._is_opening_issue(issue) for issue in issues)
        has_closing = any(cls._is_closing_issue(issue) for issue in issues)
        focus = "开头"
        if has_opening and has_closing:
            focus = "开头+结尾"
        elif has_closing:
            focus = "结尾"

        prev_ending = (input_data.previous_chapter_ending or "").strip()
        if len(prev_ending) > 500:
            prev_ending = prev_ending[-500:]
        bridge_handoff = ""
        if input_data.chapter_bridge is not None:
            bridge_handoff = (
                getattr(input_data.chapter_bridge, "action_handoff", "") or ""
            ).strip()
            if not bridge_handoff:
                bridge_dump: dict[str, Any] = {}
                dump_fn = getattr(input_data.chapter_bridge, "model_dump", None)
                if callable(dump_fn):
                    try:
                        bridge_dump = dump_fn(mode="python") or {}
                    except Exception:
                        bridge_dump = {}
                bridge_handoff = str(bridge_dump.get("action_handoff", "") or "").strip()
            if not bridge_handoff:
                bridge_handoff = (
                    getattr(input_data.chapter_bridge, "bridge_summary", "") or ""
                ).strip()

        return {
            "focus": focus,
            "previous_chapter_ending": prev_ending,
            "opening_contract": bridge_handoff,
            "closing_contract": "",
        }

    async def _execute(self, input_data: CausalRepairInput) -> CausalRepairResult:
        if self.should_skip(input_data):
            return CausalRepairResult(
                revised_text=input_data.chapter_text,
                issues=tuple(),
                applied=False,
                failure_reason="首章无上一章因果链，无需修复。",
            )

        all_issues = cast(
            list[CausalIssue],
            getattr(input_data.causal_report, "issues", []) or [],
        )
        selected_targets = {
            self._norm_issue_text(item)
            for item in (input_data.must_fix_summaries or [])
            if self._norm_issue_text(item)
        }
        # Prefer ID-based selection when available
        target_issue_ids = {
            str(item).strip()
            for item in (input_data.must_fix_issue_ids or [])
            if str(item).strip()
        }
        high_issues = [
            issue
            for issue in all_issues
            if getattr(issue, "severity", "").lower() in {"critical", "high"}
        ]
        def _is_selected_target(issue: CausalIssue) -> bool:
            issue_id = str(getattr(issue, "issue_id", "") or "").strip()
            if target_issue_ids and issue_id:
                return issue_id in target_issue_ids
            return self._norm_issue_text(getattr(issue, "summary", "")) in selected_targets

        selected_issues = [issue for issue in all_issues if _is_selected_target(issue)]
        candidate_issues: list[CausalIssue] = []
        seen_signatures: set[str] = set()
        for issue in [*high_issues, *selected_issues]:
            sig = self.issue_signature(issue)
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)
            candidate_issues.append(issue)

        if not candidate_issues:
            return CausalRepairResult(
                revised_text=input_data.chapter_text,
                issues=tuple(),
                applied=False,
                failure_reason="未发现需修复的因果链问题（无 high/critical，且未命中手动选中项）。",
            )

        candidate_issues = [self._sanitize_issue(issue) for issue in candidate_issues]

        # ── Multi-strategy dispatch ──
        # 1) patch   : precise text substitution / boundary opening patch
        # 2) window  : localized insertion/transition repair around issue location
        # 3) fulltext: global rewrite (fallback + structural issues)
        patch_issues: list[Any] = []
        window_issues: list[Any] = []
        fulltext_issues: list[Any] = []
        escalate_signatures = {
            str(sig).strip()
            for sig in (input_data.escalate_issue_signatures or [])
            if str(sig).strip()
        }
        targeted_escalation = bool(escalate_signatures)
        patch_first_enabled = bool(getattr(self.settings, "patch_first_repair", True))
        for issue in candidate_issues:
            issue_type = (getattr(issue, "issue_type", "") or "").lower()
            issue_sig = self.issue_signature(issue)
            _anchor_degraded = getattr(issue, "anchor_type", "") == "anchor_degraded"
            escalate_to_fulltext = (
                issue_sig in escalate_signatures
                or _anchor_degraded
                or (
                    not targeted_escalation
                    and input_data.repair_round > 1
                    and issue_type in self._PATCH_ONLY_TYPES
                )
            )
            # Fulltext upgrade budget gate: block escalation when cumulative change
            # ratio already consumes too much of the change budget.
            if escalate_to_fulltext:
                change_budget = getattr(self.settings, "change_budget_threshold", 0.15)
                budget_fraction = getattr(
                    self.settings,
                    "long_fulltext_upgrade_change_budget_fraction",
                    0.50,
                )
                if input_data.cumulative_change_ratio > change_budget * budget_fraction:
                    escalate_to_fulltext = False
            if (
                patch_first_enabled
                and issue_type in self._PATCH_ONLY_TYPES
                and not escalate_to_fulltext
            ):
                patch_issues.append(self._prepare_patch_issue(issue))
            elif (
                patch_first_enabled
                and issue_type in self._WINDOW_ISSUE_TYPES
                and not escalate_to_fulltext
            ):
                window_issues.append(issue)
            else:
                fulltext_issues.append(issue)

        revised_text = input_data.chapter_text
        total_patches_applied = 0
        total_patches_attempted = 0

        # ── Phase 1: Patch mode ──
        if patch_issues:
            from novel_forge.pipeline.steps.patch_step import ChapterPatchStep, PatchInput

            patch_step = ChapterPatchStep(
                self._router,
                self._builder,
                settings=self.settings,
                trace=self._trace,
            )
            _enriched_link = dict(input_data.causal_link or {})
            if input_data.character_profiles:
                _enriched_link["character_profiles"] = list(input_data.character_profiles)
            if input_data.memory_guidance:
                _enriched_link["memory_guidance"] = input_data.memory_guidance
            _boundary_context = self._build_boundary_context(input_data, patch_issues)
            from novel_forge.pipeline.long.services.constraints.cognitive_constraints import (
                cognitive_constraints_from_contract,
            )

            patch_result = await patch_step.run(
                PatchInput(
                    chapter_number=input_data.chapter_number,
                    chapter_text=revised_text,
                    issues=patch_issues,
                    causal_link=_enriched_link,
                    context_size=3,
                    must_fix_summaries=input_data.must_fix_summaries,
                    style=input_data.style,
                    style_profile=input_data.style_profile,
                    boundary_context=_boundary_context,
                    cognitive_constraints=cognitive_constraints_from_contract(
                        getattr(input_data.chapter_state_packet, "chapter_contract", {})
                    ),
                )
            )
            total_patches_applied = patch_result.patches_applied
            total_patches_attempted = patch_result.patches_attempted

            if patch_result.revised_text.strip():
                revised_text = patch_result.revised_text

            # Patch escape hatch: if 0 patches applied (regardless of fallback reason),
            # push issues to fulltext repair.  This covers both the case where the model
            # returned an empty response (token limit, JSONDecodeError → fallback=True)
            # and the case where all patch exact-matches failed.
            if patch_result.patches_applied == 0:
                # V2 diagnostics: log specific failure codes for observability
                _failure_codes = getattr(patch_result, "failure_codes", [])
                _ambiguous = getattr(patch_result, "ambiguous_match_count", 0)
                _logger.info(
                    "causal_repair patch精确匹配失败(0/%d applied)，合并到全文修复 | "
                    "chapter=%d | failure_codes=%s | ambiguous=%d",
                    patch_result.patches_attempted,
                    input_data.chapter_number,
                    _failure_codes[:5] if _failure_codes else "[]",
                    _ambiguous,
                )
                fulltext_issues = patch_issues + fulltext_issues
                revised_text = input_data.chapter_text  # reset; patch changed nothing
            elif patch_result.patches_applied < patch_result.patches_attempted:
                # Partial success: escalate only the failed patches
                _failure_codes = getattr(patch_result, "failure_codes", [])
                _logger.info(
                    "causal_repair partial patch: %d/%d applied | chapter=%d | "
                    "unmatched failures escalated to fulltext | codes=%s",
                    patch_result.patches_applied,
                    patch_result.patches_attempted,
                    input_data.chapter_number,
                    _failure_codes[:5] if _failure_codes else "[]",
                )

        # ── Phase 2: Window repair ──
        if window_issues:
            try:
                window_revised = await self._window_repair(
                    input_data,
                    window_issues,
                    revised_text,
                )
                if window_revised != revised_text:
                    revised_text = window_revised
                else:
                    _logger.info(
                        "causal_repair window未产生改动，升级到全文修复 | chapter=%d",
                        input_data.chapter_number,
                    )
                    fulltext_issues = window_issues + fulltext_issues
            except Exception as exc:
                _logger.warning(
                    "causal_repair window failed | chapter=%d | error=%s",
                    input_data.chapter_number,
                    exc,
                )
                fulltext_issues = window_issues + fulltext_issues

        # ── Phase 3: Fulltext repair ──
        if fulltext_issues:
            try:
                revised_text = await self._fulltext_repair(
                    input_data,
                    fulltext_issues,
                    revised_text,
                )
            except Exception as exc:
                _logger.warning(
                    "causal_repair fulltext failed | chapter=%d | error=%s",
                    input_data.chapter_number,
                    exc,
                )

        applied = revised_text != input_data.chapter_text
        failure_reason: str | None = None
        if not applied:
            if total_patches_attempted > 0 and total_patches_applied == 0:
                failure_reason = "patch精确匹配失败且全文编辑未改动正文。"
            elif total_patches_attempted == 0 and not fulltext_issues:
                failure_reason = "未生成任何修复动作。"
            else:
                failure_reason = "修复结果与原文相同。"

        forbidden_warnings: list[str] = []
        if revised_text != input_data.chapter_text:
            # Soft constraints remain prompt-level guidance. Only hard
            # constraints belong in the post-repair warning contract, so a
            # causal repair never upgrades advisory language into an error.
            new_hits = check_forbidden(revised_text, list(input_data.forbidden_elements))
            if new_hits:
                _logger.warning(
                    "causal_repair: hard forbidden elements detected after repair: %s", new_hits
                )
                forbidden_warnings = new_hits

        return CausalRepairResult(
            revised_text=revised_text,
            issues=tuple() if applied else tuple(candidate_issues),
            applied=applied,
            patches_applied=total_patches_applied,
            patches_attempted=total_patches_attempted,
            failure_reason=failure_reason,
            repaired_issue_types=tuple(sorted(
                {
                    (getattr(issue, "issue_type", "") or "").lower()
                    for issue in candidate_issues
                    if (getattr(issue, "issue_type", "") or "").strip()
                }
            )),
            forbidden_element_warnings=forbidden_warnings,
        )

    async def _fulltext_repair(
        self,
        input_data: "CausalRepairInput",
        issues: "list[CausalIssue]",
        current_text: str | None = None,
        *,
        window_before: str = "",
        window_after: str = "",
    ) -> str:
        """Full-text repair using TaskType.REPAIR_CAUSAL.

        Delegates context building to ``build_repair_context`` and LLM calling
        to the base class ``_run_fulltext_repair``.
        """
        text = current_text if current_text is not None else input_data.chapter_text

        context = self.build_repair_context(
            input_data,
            issues=issues,
            window_before=window_before,
            window_after=window_after,
            current_text=text,
        )

        _max_tokens_limit = getattr(self.settings, "continuity_repair_max_tokens", 16384)
        max_tokens = self._dynamic_max_tokens(
            TaskType.REPAIR_CAUSAL,
            len(text),
            prompt_overhead=3000,
            max_cap=_max_tokens_limit,
            safety_margin=0.80,
            min_tokens=6144,
        )
        revised, _ = await self._run_fulltext_repair(
            context,
            max_tokens=max_tokens,
            temperature=getattr(self.settings, "temp_repair_causal", 0.35),
        )

        is_valid, _ = self.validate_revised_text(text, revised)
        if not is_valid:
            _logger.warning(
                "causal_repair fulltext返回文本过短或为空(%d vs %d)，保留当前文本 | chapter=%d",
                len(revised),
                len(text),
                input_data.chapter_number,
            )
            return text

        return revised

    # ── Text numbering helper ──────────────────────────────────────────────────

    @staticmethod
    def _number_paragraphs(text: str) -> str:
        """Prepend ``[第N段]`` markers to each paragraph for precise LLM targeting.

        Mirrors ``CausalValidationStep._number_paragraphs`` so the repair LLM receives
        the same numbered format the validator used when reporting issue locations.
        """
        paragraphs = [p.strip() for p in str(text or "").split("\n\n") if p.strip()]
        if not paragraphs:
            return str(text or "")
        return "\n\n".join(f"[第{idx}段] {para}" for idx, para in enumerate(paragraphs, 1))

    # ── Window repair helpers ──────────────────────────────────────────────────

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        """Split chapter text into paragraphs, preserving blank-line separators."""
        return [p for p in text.split("\n\n") if p.strip()]

    @staticmethod
    def _location_to_para_idx(location: str, paragraphs: list[str]) -> int | None:
        """Parse a location string (e.g. "第5段", "第3-4段") into a 0-based paragraph index.

        Returns None when the location cannot be parsed.
        """
        if not location or not paragraphs:
            return None

        loc = location.strip()

        # "第N-M段" or "第N—M段" → use first number
        m = re.search(r"第\s*(\d+)", loc)
        if m:
            n = int(m.group(1)) - 1  # 1-based → 0-based
            return max(0, min(n, len(paragraphs) - 1))

        # keywords
        if any(k in loc for k in ("开头", "首段", "起首")):
            return 0
        if any(k in loc for k in ("结尾", "末段", "尾段", "最后")):
            return len(paragraphs) - 1

        return None

    @staticmethod
    def _locate_issue_paragraph(
        issue: "CausalIssue",
        paragraphs: list[str],
    ) -> int | None:
        """Locate the paragraph index for an issue using the unified resolver.

        Returns 0-based paragraph index or None.
        """
        from novel_forge.core.utils.patch_utils import resolve_paragraph_locally

        if getattr(issue, "anchor_type", "") == "anchor_degraded":
            return None

        paragraph_start = getattr(issue, "paragraph_start", 0) or 0
        if paragraph_start:
            try:
                idx = int(paragraph_start) - 1
            except (TypeError, ValueError):
                idx = -1
            if 0 <= idx < len(paragraphs):
                return idx

        evidence = (
            getattr(issue, "evidence_quote", "") or getattr(issue, "evidence", "") or ""
        ).strip()
        location = (getattr(issue, "location", "") or "").strip()

        targets, anchor_type, confidence = resolve_paragraph_locally(
            paragraphs,
            evidence=evidence,
            location=location,
        )
        if anchor_type == "fallback":
            return None
        return targets[0] if targets else None

    async def _window_repair(
        self,
        input_data: "CausalRepairInput",
        issues: "list[CausalIssue]",
        current_text: str,
        window_size: int = 2,
    ) -> str:
        """Targeted window repair for issues that require *inserting* new content.

        Instead of rewriting the full chapter, this:
        1. Resolves each issue's location to a paragraph index.
        2. Groups issues into non-overlapping windows (issues whose windows
           would overlap are merged into one window).
        3. Processes each window independently in reverse order so that
           paragraph indices remain valid after splicing.
        4. Splices each revised window back into the full chapter.

        Falls back to ``_fulltext_repair`` if location cannot be determined.
        """
        paragraphs = self._split_paragraphs(current_text)
        if not paragraphs:
            return current_text

        # Resolve each issue to a paragraph index and pair them.
        located: list[tuple[int, "CausalIssue"]] = []
        unlocated: list["CausalIssue"] = []
        for issue in issues:
            idx = self._locate_issue_paragraph(issue, paragraphs)
            if idx is not None:
                located.append((idx, issue))
            else:
                unlocated.append(issue)

        if not located:
            _logger.info(
                "causal_repair window: no parseable locations, falling back to fulltext | chapter=%d",
                input_data.chapter_number,
            )
            return await self._fulltext_repair(input_data, issues, current_text)

        # Group into non-overlapping window clusters.
        # Two issues belong to the same cluster if their windows (±window_size)
        # overlap.  Sort by paragraph index, then greedily merge.
        located.sort(key=lambda x: x[0])
        clusters: list[list[tuple[int, "CausalIssue"]]] = []
        cur_cluster: list[tuple[int, "CausalIssue"]] = [located[0]]
        cur_end = located[0][0] + window_size
        for idx, issue in located[1:]:
            if idx - window_size <= cur_end:
                # Overlaps with current cluster → merge
                cur_cluster.append((idx, issue))
                cur_end = max(cur_end, idx + window_size)
            else:
                clusters.append(cur_cluster)
                cur_cluster = [(idx, issue)]
                cur_end = idx + window_size
        clusters.append(cur_cluster)

        _logger.info(
            "causal_repair window: %d issues → %d independent window(s) | chapter=%d",
            len(located),
            len(clusters),
            input_data.chapter_number,
        )

        # Process clusters in REVERSE order so splicing doesn't shift indices
        # of earlier (lower-index) clusters.
        result_paragraphs = list(paragraphs)
        failed_issues: list["CausalIssue"] = list(unlocated)

        for cluster in reversed(clusters):
            cluster_indices = [idx for idx, _ in cluster]
            cluster_issues = [iss for _, iss in cluster]

            raw_start = min(cluster_indices) - window_size
            raw_end = max(cluster_indices) + window_size
            window_start = max(0, raw_start)
            window_end = min(len(result_paragraphs) - 1, raw_end)

            prefix_paras = result_paragraphs[:window_start]
            window_paras = result_paragraphs[window_start : window_end + 1]
            suffix_paras = result_paragraphs[window_end + 1 :]

            if not window_paras:
                failed_issues.extend(cluster_issues)
                continue

            window_text = "\n\n".join(window_paras)
            window_before = "\n\n".join(prefix_paras[-5:]) if prefix_paras else ""
            window_after = "\n\n".join(suffix_paras[:5]) if suffix_paras else ""

            _logger.info(
                "causal_repair window: cluster paragraphs [%d-%d]/%d (%d issues) | chapter=%d",
                window_start,
                window_end,
                len(result_paragraphs) - 1,
                len(cluster_issues),
                input_data.chapter_number,
            )

            try:
                revised_window = await self._fulltext_repair(
                    input_data,
                    cluster_issues,
                    window_text,
                    window_before=window_before,
                    window_after=window_after,
                )
            except Exception as exc:
                _logger.warning(
                    "causal_repair window cluster [%d-%d] failed | chapter=%d | error=%s",
                    window_start,
                    window_end,
                    input_data.chapter_number,
                    exc,
                )
                failed_issues.extend(cluster_issues)
                continue

            # Sanity-check: revised window must not be suspiciously short.
            if not revised_window or len(revised_window) < len(window_text) * 0.4:
                _logger.warning(
                    "causal_repair window: LLM returned suspiciously short window (%d vs %d), "
                    "skipping cluster [%d-%d] | chapter=%d",
                    len(revised_window),
                    len(window_text),
                    window_start,
                    window_end,
                    input_data.chapter_number,
                )
                failed_issues.extend(cluster_issues)
                continue

            # Splice revised window back.
            revised_window_paras = [p for p in revised_window.split("\n\n") if p.strip()]
            result_paragraphs = prefix_paras + revised_window_paras + suffix_paras

        final_text = "\n\n".join(result_paragraphs)

        # Handle failed cluster issues.
        if failed_issues:
            if final_text == current_text:
                # All clusters failed → fulltext fallback with all issues.
                _logger.info(
                    "causal_repair window: all clusters failed, falling back to fulltext | chapter=%d",
                    input_data.chapter_number,
                )
                return await self._fulltext_repair(input_data, issues, current_text)
            else:
                # Partial success: some clusters applied, some failed.
                # Run a fulltext pass for just the unresolved issues on the
                # already-patched text so they are not silently dropped.
                _logger.info(
                    "causal_repair window: %d unresolved issue(s) after partial cluster success, "
                    "running targeted fulltext pass | chapter=%d",
                    len(failed_issues),
                    input_data.chapter_number,
                )
                final_text = await self._fulltext_repair(
                    input_data,
                    failed_issues,
                    final_text,
                )

        return final_text
